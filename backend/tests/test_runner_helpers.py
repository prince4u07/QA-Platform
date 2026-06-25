"""Unit tests for the runner's pure helper functions (no DB / no network)."""

from modules.runner.routes import (
    calculate_health_score,
    merge_findings,
    tag_findings_with_page,
    filter_console_errors,
)


def test_health_score_perfect_when_no_findings():
    assert calculate_health_score({}) == 100


def test_health_score_subtracts_severity_weighted_penalties():
    # Scoring is now severity-weighted, not per-category. A 'critical' finding
    # costs 12 pts, 'major' 5, 'minor' 1.5, 'info' 0.3. Findings missing a
    # severity default to 'minor'. Each category's penalty is capped at 25.
    # Below: 1 critical (12) + 2 minors (3) = 15 penalty -> score 85.
    findings = {
        'security_issues': [{'severity': 'critical'}],
        'broken_links': [{'severity': 'minor'}, {'severity': 'minor'}],
    }
    assert calculate_health_score(findings) == 85


def test_health_score_caps_each_category():
    # 50 critical findings in one category would otherwise dwarf the score —
    # CATEGORY_CAP = 25 ensures one noisy category can't single-handedly zero
    # the total. With everything in one category: penalty capped at 25 -> 75.
    findings = {'security_issues': [{'severity': 'critical'} for _ in range(50)]}
    assert calculate_health_score(findings) == 75


def test_health_score_never_below_zero():
    # Spread 50 critical findings across enough distinct categories to push
    # past 100 pts of total penalty (each category caps at 25, so >=5 cats).
    findings = {
        f'cat_{i}': [{'severity': 'critical'} for _ in range(10)]
        for i in range(6)
    }
    assert calculate_health_score(findings) == 0


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


def test_filter_console_errors_drops_tracker_noise():
    # Ad/analytics/tracker failures are dropped outright — the site owner can't
    # act on someone else's pixel failing to load.
    msgs = [
        {'type': 'error',
         'text': 'Failed to load resource: https://www.google-analytics.com/g/collect 404'},
        {'type': 'error',
         'text': 'Failed to load resource: https://connect.facebook.net/en_US/fbevents.js'},
        {'type': 'error', 'text': 'Uncaught TypeError: real bug'},
    ]
    out = filter_console_errors(msgs, base_url='https://example.com/page')
    assert len(out) == 1
    assert 'real bug' in out[0]['text']


def test_filter_console_errors_third_party_resource_is_info():
    # A non-tracker third-party CDN 404 is surfaced but only as 'info' so it
    # barely touches the health score; a first-party 404 stays 'minor'.
    msgs = [
        {'type': 'error',
         'text': 'Failed to load resource: https://cdn.somevendor.com/lib.js 404 (Not Found)'},
        {'type': 'error',
         'text': 'Failed to load resource: https://example.com/img/logo.png 404 (Not Found)'},
    ]
    out = filter_console_errors(msgs, base_url='https://example.com/page')
    by_sev = {f['severity'] for f in out}
    assert by_sev == {'info', 'minor'}
    third = next(f for f in out if 'somevendor' in f['text'])
    first = next(f for f in out if 'example.com' in f['text'])
    assert third['severity'] == 'info'
    assert first['severity'] == 'minor'
