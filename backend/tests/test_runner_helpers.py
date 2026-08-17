"""Unit tests for the runner's pure helper functions (no DB / no network)."""

from modules.runner.routes import (
    calculate_health_score,
    score_breakdown,
    summarise_coverage,
    finding_severity,
    finding_key,
    worst_severity,
    check_performance,
    diff_findings,
    build_regression,
    merge_findings,
    tag_findings_with_page,
    filter_console_errors,
)


def _issues(n, severity):
    return [{'severity': severity} for _ in range(n)]


def test_health_score_perfect_when_no_findings():
    assert calculate_health_score({}) == 100


def test_health_score_stays_above_zero_on_a_badly_broken_site():
    """
    The requirement: a score you can track across releases. The old formula
    (100 - count * weight) hit 0 and stayed there, so a site could never show
    improvement. Any number of findings must still leave a usable score.
    """
    findings = {'accessibility_issues': _issues(200, 'critical')}
    assert calculate_health_score(findings) > 0


def test_worse_site_always_scores_lower_than_bad_site():
    """
    The specific bug: under the old formula 20 serious accessibility issues
    and 200 of them both clamped to 0, so the two sites looked identical.
    """
    bad = {'accessibility_issues': _issues(20, 'serious')}
    worse = {'accessibility_issues': _issues(200, 'serious')}
    assert calculate_health_score(worse) < calculate_health_score(bad)


def test_score_decreases_monotonically_as_issues_pile_up():
    scores = [
        calculate_health_score({'broken_links': _issues(n, 'serious')})
        for n in (0, 1, 5, 20, 100)
    ]
    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) == len(scores)      # every step is distinguishable


def test_critical_finding_costs_more_than_a_minor_one():
    critical = calculate_health_score({'security_issues': _issues(1, 'critical')})
    minor = calculate_health_score({'security_issues': _issues(1, 'minor')})
    assert critical < minor


def test_finding_severity_uses_the_value_the_check_assigned():
    # axe-core grades its own rules; that grade must survive into scoring.
    assert finding_severity('accessibility_issues', {'severity': 'critical'}) == 'critical'


def test_finding_severity_falls_back_to_category_default():
    assert finding_severity('security_issues', {}) == 'serious'
    assert finding_severity('missing_alt_images', {}) == 'minor'
    # an unknown severity string must not be trusted
    assert finding_severity('security_issues', {'severity': 'catastrophic'}) == 'serious'


def test_worst_severity_reports_the_most_serious_present():
    findings = {
        'missing_alt_images': _issues(3, 'minor'),
        'security_issues': _issues(1, 'critical'),
        'seo_issues': _issues(2, 'moderate'),
    }
    assert worst_severity(findings) == 'critical'
    assert worst_severity({}) is None


def test_score_breakdown_explains_where_marks_were_lost():
    findings = {'security_issues': _issues(2, 'critical')}
    breakdown = score_breakdown(findings)
    assert breakdown['security_issues']['issues'] == 2
    assert breakdown['security_issues']['by_severity']['critical'] == 2
    assert breakdown['security_issues']['score'] < 100
    # a category with nothing wrong is still reported, at full marks
    assert breakdown['seo_issues']['score'] == 100


def test_coverage_says_so_when_links_were_capped():
    """A clean link result on a partial check must never read as all-clear."""
    cov = summarise_coverage([
        {'links': {'links_found': 210, 'links_checked': 30, 'truncated': True}},
    ])
    assert cov['complete'] is False
    assert '30 of 210' in cov['notes'][0]


def test_coverage_reports_skipped_mobile_and_a11y_fallback():
    cov = summarise_coverage([
        {'mobile_skipped_reason': 'logged-in crawls run on a desktop viewport',
         'accessibility_engine': 'heuristic-fallback'},
    ])
    joined = ' '.join(cov['notes'])
    assert 'Mobile checks were skipped' in joined
    assert 'axe-core could not run' in joined
    assert cov['mobile_skipped'] is True


def _link(url):
    return {'url': url, 'severity': 'serious'}


def test_first_run_is_a_baseline_not_a_regression():
    """Nothing can be 'new' when there is nothing to compare against."""
    reg = build_regression(None, {'broken_links': [_link('/a')]}, None, 70)
    assert reg['has_baseline'] is False
    assert reg['verdict'] == 'baseline'
    assert reg['new_count'] == 0
    assert reg['score_change'] is None


def test_regression_reports_newly_introduced_issues():
    previous = {'broken_links': [_link('/a')]}
    current = {'broken_links': [_link('/a'), _link('/b')]}
    reg = build_regression(previous, current, 80, 70)
    assert reg['verdict'] == 'regressed'
    assert reg['new_count'] == 1
    assert reg['new']['broken_links'][0]['url'] == '/b'
    assert reg['fixed_count'] == 0
    assert reg['still_open_count'] == 1
    assert reg['score_change'] == -10


def test_regression_reports_fixed_issues():
    previous = {'broken_links': [_link('/a'), _link('/b')]}
    current = {'broken_links': [_link('/a')]}
    reg = build_regression(previous, current, 70, 85)
    assert reg['verdict'] == 'improved'
    assert reg['fixed_count'] == 1
    assert reg['fixed']['broken_links'][0]['url'] == '/b'
    assert reg['score_change'] == 15


def test_regression_reports_mixed_when_some_fixed_and_some_new():
    previous = {'broken_links': [_link('/a')]}
    current = {'broken_links': [_link('/b')]}
    reg = build_regression(previous, current, 80, 80)
    assert reg['verdict'] == 'mixed'
    assert reg['new_count'] == 1 and reg['fixed_count'] == 1
    assert reg['still_open_count'] == 0


def test_regression_says_unchanged_when_nothing_moved():
    same = {'broken_links': [_link('/a')]}
    reg = build_regression(same, same, 80, 80)
    assert reg['verdict'] == 'unchanged'
    assert reg['new_count'] == 0 and reg['fixed_count'] == 0
    assert reg['score_change'] == 0


def test_regression_spots_a_category_that_appeared_from_nothing():
    """A category absent last run must still be diffed, not ignored."""
    previous = {'broken_links': []}
    current = {'security_issues': [{'issue': 'Missing security headers'}]}
    reg = build_regression(previous, current, 90, 70)
    assert reg['new_count'] == 1
    assert 'security_issues' in reg['new']


def test_finding_key_ignores_volatile_fields():
    """
    The same defect must key identically across runs, otherwise every run
    would report everything as new. Screenshots and page order change run
    to run and must not be part of the identity.
    """
    run1 = {'rule': 'color-contrast', 'element_selector': '#buy', 'screenshot': 'a.png'}
    run2 = {'rule': 'color-contrast', 'element_selector': '#buy', 'screenshot': 'b.png'}
    assert finding_key('accessibility_issues', run1) == finding_key('accessibility_issues', run2)


def test_diff_ignores_findings_it_cannot_identify():
    previous = {'broken_links': [{'no_url_field': 1}]}
    current = {'broken_links': [{'no_url_field': 2}]}
    d = diff_findings(previous, current)
    assert d['new_count'] == 0 and d['fixed_count'] == 0


def test_coverage_reports_real_page_weight():
    """Page weight was hardcoded to 0 on every run; it must be measured now."""
    cov = summarise_coverage([
        {'performance': {'page_size_kb': 1800, 'request_count': 40}},
        {'performance': {'page_size_kb': 950, 'request_count': 22}},
    ])
    assert cov['page_size_kb_total'] == 2750
    assert cov['page_size_kb_heaviest'] == 1800
    assert cov['request_count'] == 62


def test_performance_issues_count_towards_the_score():
    clean = calculate_health_score({})
    slow = calculate_health_score({'performance_issues': [{'severity': 'serious'}]})
    assert slow < clean


class _FakePage:
    """Stands in for a Playwright page so the thresholds can be tested
    without launching a browser. Only .evaluate() is used by check_performance."""

    def __init__(self, bytes_=0, count=0, top=None):
        self._payload = {'bytes': bytes_, 'count': count, 'top': top or []}

    def evaluate(self, _script):
        return self._payload


def test_performance_measures_real_page_weight():
    _findings, metrics = check_performance(_FakePage(bytes_=2_048_000, count=31), 900)
    assert metrics['page_size_kb'] == 2000
    assert metrics['request_count'] == 31


def test_performance_flags_a_slow_page():
    findings, _ = check_performance(_FakePage(), page_load_ms=4200)
    assert len(findings) == 1
    assert findings[0]['metric'] == 'load_time_ms'
    assert findings[0]['severity'] == 'moderate'


def test_performance_grades_a_very_slow_page_higher():
    findings, _ = check_performance(_FakePage(), page_load_ms=8000)
    assert findings[0]['severity'] == 'serious'


def test_performance_stays_quiet_on_a_fast_light_page():
    findings, metrics = check_performance(_FakePage(bytes_=400_000, count=12), 800)
    assert findings == []
    assert metrics['page_size_kb'] == 390


def test_performance_flags_a_heavy_page():
    findings, _ = check_performance(_FakePage(bytes_=7_000_000, count=80), 500)
    heavy = [f for f in findings if f['metric'] == 'page_size_kb']
    assert heavy and heavy[0]['severity'] == 'serious'


def test_performance_names_the_oversized_file():
    top = [{'name': 'https://site.com/assets/hero-video.mp4',
            'size_kb': 4200, 'duration_ms': 3100, 'kind': 'video'}]
    findings, _ = check_performance(_FakePage(bytes_=5_000_000, count=9, top=top), 500)
    big = [f for f in findings if f['metric'] == 'resource_size_kb']
    assert len(big) == 1
    assert 'hero-video.mp4' in big[0]['display']


def test_performance_survives_a_page_that_blocks_evaluate():
    class _Broken:
        def evaluate(self, _script):
            raise RuntimeError('CSP blocked it')

    findings, metrics = check_performance(_Broken(), 500)
    assert findings == [] and metrics['page_size_kb'] == 0


def test_coverage_is_clean_when_nothing_was_capped():
    cov = summarise_coverage([
        {'links': {'links_found': 12, 'links_checked': 12, 'truncated': False},
         'mobile': {'tap_targets_found': 8, 'tap_targets_checked': 8, 'truncated': False},
         'accessibility_engine': 'axe-core'},
    ])
    assert cov['complete'] is True
    assert cov['notes'] == []


def test_merge_findings_combines_categories():
    pages = [
        {'broken_links': [{'a': 1}], 'seo_issues': []},
        {'broken_links': [{'a': 2}], 'seo_issues': [{'s': 1}]},
    ]
    merged = merge_findings(pages)
    assert len(merged['broken_links']) == 2
    assert len(merged['seo_issues']) == 1
    # categories not supplied still exist as empty lists
    assert merged['mobile_issues'] == []


def test_tag_findings_with_page_stamps_url():
    findings = {'broken_links': [{'display': 'x'}], 'seo_issues': [{'display': 'y'}]}
    tagged = tag_findings_with_page(findings, 'https://site/page')
    assert tagged['broken_links'][0]['page_url'] == 'https://site/page'
    assert tagged['seo_issues'][0]['page_url'] == 'https://site/page'


def test_filter_console_errors_keeps_only_real_errors():
    msgs = [
        {'type': 'error', 'text': 'Uncaught TypeError: boom'},
        {'type': 'warning', 'text': 'just a warning'},
        {'type': 'error', 'text': 'Failed to load favicon'},       # skip pattern
        {'type': 'error', 'text': 'deprecated API used'},          # skip pattern
    ]
    out = filter_console_errors(msgs)
    assert len(out) == 1
    assert 'boom' in out[0]['text']
