"""Manual/automated separation: run types, sources, verdicts, evidence."""

from modules.runner import manual_run
from modules.runner.manual_run import (
    _ManualSession,
    summarise_manual,
)
from modules.runner.routes import (
    _is_allowed_test_url,
    _verdict_reason,
    tag_findings_with_page,
)


def make_session():
    return _ManualSession(
        1, 1, 'https://shop.test',
        steps_text='Open the shop\nAdd an item',
        expected_result='Basket shows the item',
    )


# ---- manual evidence ----

def test_step_marks_carry_timestamp_and_issue_ref():
    s = make_session()
    assert s.mark_step(0, 'passed', 'looked right', issue_ref=None) is True
    snap = s.snapshot()
    assert snap['steps'][0]['updated_at']
    assert snap['progress_pct'] == 50


def test_issue_accepts_expected_actual_description_and_step():
    s = make_session()
    entry = s.report_issue(
        'ui-ux', 'serious', 'Total is misaligned',
        description='The total overlaps the button on checkout',
        expected_result='Total sits above the button',
        actual_result='Total overlaps the button',
        step_index=1,
    )
    assert entry['source'] == 'MANUAL'
    assert entry['expected_result'] == 'Total sits above the button'
    assert entry['actual_result'] == 'Total overlaps the button'
    assert entry['step_index'] == 1
    assert entry['timestamp']


def test_old_category_slugs_still_resolve():
    s = make_session()
    assert s.report_issue('design', 'minor', 'x marks the spot')['category'] == 'ui-ux'
    assert s.report_issue('business', 'minor', 'y marks the spot')['category'] == 'business-logic'


def test_pause_and_resume_keep_the_session_alive():
    s = make_session()
    s.status = 'ready'
    assert s.set_paused(True) is True
    assert s.snapshot()['paused'] is True
    assert s.snapshot()['status'] == 'paused'
    assert s.set_paused(False) is False
    assert s.snapshot()['status'] == 'ready'


def test_manual_verdict_basis_excludes_automation():
    s = make_session()
    s.mark_step(0, 'passed')
    summary = summarise_manual(s.snapshot())
    assert summary['verdict_basis'] == [
        'step_results', 'expected_result_decision', 'reported_issues']
    assert summary['automated_evidence_excluded_from_verdict'] is True


def test_snapshot_exposes_current_page_and_sections():
    s = make_session()
    snap = s.snapshot()
    assert 'current_url' in snap
    assert 'current_screenshot' in snap
    assert snap['observation_sections'] == ['human_observation', 'automated_evidence']


# ---- automated results ----

def test_automated_findings_are_tagged_at_the_source():
    out = tag_findings_with_page(
        {'seo_issues': [{'issue': 'no title'}]}, 'https://site.test/')
    assert out['seo_issues'][0]['source'] == 'AUTOMATED'
    assert out['seo_issues'][0]['page_url'] == 'https://site.test/'


def test_automated_verdict_reason_names_criticals():
    reason = _verdict_reason('Fail', {'critical': 2, 'serious': 1}, 41)
    assert 'critical' in reason
    assert _verdict_reason('Pass', {}, 100).startswith('No issues')


def test_unsafe_urls_are_rejected():
    ok, _ = _is_allowed_test_url('https://shop.test/')
    assert ok is True
    for bad in ('ftp://shop.test/x', 'javascript:alert(1)',
                'https://user:pass@shop.test/', 'not a url'):
        ok, reason = _is_allowed_test_url(bad)
        assert ok is False and reason


def test_literal_private_ips_are_rejected_without_dns():
    for bad in ('http://192.168.1.5/', 'http://10.0.0.9:8080/admin',
                'http://169.254.169.254/latest/meta-data/'):
        ok, reason = _is_allowed_test_url(bad)
        assert ok is False, bad
        assert 'non-public' in reason


def test_localhost_stays_allowed_for_dev(monkeypatch):
    ok, _ = _is_allowed_test_url('http://127.0.0.1:8000/')
    assert ok is True
    ok, _ = _is_allowed_test_url('http://localhost:3000/')
    assert ok is True


def test_allowlist_env_permits_a_private_host(monkeypatch):
    monkeypatch.setenv('QA_SSRF_ALLOWLIST', 'intranet.local')
    ok, _ = _is_allowed_test_url('http://intranet.local/')
    assert ok is True
    monkeypatch.setenv('QA_ALLOW_PRIVATE_HOSTS', '1')
    ok, _ = _is_allowed_test_url('http://10.9.9.9/')
    assert ok is True
