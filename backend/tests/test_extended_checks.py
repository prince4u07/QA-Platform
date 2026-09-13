"""Extended checks: URL, API quality, resources, UI consistency, code quality."""

from modules.runner.extended_checks import (
    check_url_health,
    check_api_quality,
    check_page_resources,
    check_ui_consistency,
    check_code_quality,
    validate_special_link,
)
from modules.runner.routes import (
    CATEGORY_WEIGHT,
    calculate_health_score,
    finding_key,
    merge_findings,
    test_single_page as run_single_page,
)


class FakePage:
    def __init__(self, script=None):
        self._script = script or {}
        self.context = FakeContext()

    def title(self):
        return 'Fake page'

    def evaluate(self, script):
        s = script or ''
        if 'document.images' in s:
            return self._script.get('resources', {})
        if 'inlineHandlers' in s:
            return self._script.get('code', self._script.get('payload', {}))
        if 'document.forms' in s:
            return []
        if 'performance.getEntriesByType' in s:
            return {'bytes': 0, 'count': 0, 'top': [], 'unmeasured': 0}
        if 'getComputedStyle' in s:
            return {'tag': 'div', 'display': 'block'}
        if 'scrollWidth' in s:
            return 390
        if 'clientWidth' in s:
            return 390
        if 'location.href' in s:
            return {'url': 'https://site.com/', 'html': 1, 'nodes': 1,
                    'requests': 1, 'storage': 0}
        if 'navigator.userAgent' in s:
            return 'FakeAgent/1.0'
        if 'mailto' in s and 'querySelectorAll' in s and 'getAttribute' not in s:
            return []
        if 'querySelectorAll' in s and '[id]' in s and 'new Set' in s:
            return list(self._script.get('ids', []))
        if 'querySelectorAll' in s and '[id]' in s and 'map(e => e.id)' in s:
            return list(self._script.get('ids', []))
        if 'document.scripts' in s:
            return self._script.get('resources', {})
        if 'dups' in s or 'hasViewport' in s:
            return self._script.get('ui', self._script.get('payload', {}))
        if 'getElementsByTagName' in s or 'hasDoctype' in s:
            return self._script.get('code', self._script.get('payload', {}))
        return self._script.get('payload', {})

    def query_selector_all(self, _selector):
        return []

    def query_selector(self, _selector):
        return None


class FakeContext:
    def cookies(self):
        return []


# ---- URL testing ----

def test_error_status_page_is_flagged():
    findings = check_url_health('https://site.com/gone', page_status=404)
    assert len(findings) == 1
    assert findings[0]['status_code'] == 404


def test_server_error_is_critical():
    findings = check_url_health('https://site.com/x', page_status=500)
    assert findings[0]['severity'] == 'critical'


def test_healthy_page_has_no_url_findings():
    assert check_url_health('https://site.com/guide', page_status=200) == []


def test_session_id_in_url_is_serious():
    findings = check_url_health('https://site.com/?jsessionid=abc123', page_status=200)
    assert any('Session ID' in f['display'] for f in findings)


def test_uppercase_path_is_minor_hygiene_note():
    findings = check_url_health('https://site.com/About-Us', page_status=200)
    assert any('uppercase' in f['issue'].lower() for f in findings)


# ---- API testing ----

def test_slow_api_is_flagged():
    findings = check_api_quality(
        [{'url': 'https://site.com/api/items', 'status': 200, 'elapsed_ms': 3500}],
        'https://site.com/',
    )
    assert len(findings) == 1
    assert 'Slow API' in findings[0]['display']


def test_fast_api_is_quiet():
    assert check_api_quality(
        [{'url': 'https://site.com/api/items', 'status': 200, 'elapsed_ms': 120}],
        'https://site.com/',
    ) == []


def test_insecure_api_call_from_https_page():
    findings = check_api_quality(
        [{'url': 'http://site.com/api/items', 'status': 200}],
        'https://site.com/',
    )
    assert any('Insecure' in f['display'] for f in findings)


def test_old_shape_entries_still_work():
    assert check_api_quality(
        [{'url': 'https://site.com/api/items', 'status': 200}],
        'https://site.com/',
    ) == []


def test_html_from_api_endpoint_is_flagged():
    findings = check_api_quality(
        [{'url': 'https://site.com/api/items', 'status': 200,
          'content_type': 'text/html; charset=utf-8'}],
        'https://site.com/',
    )
    assert any('HTML instead of JSON' in f['display'] for f in findings)


# ---- link testing (deep) ----

def test_malformed_mailto_is_flagged():
    assert validate_special_link('mailto:not-an-email') is not None
    assert validate_special_link('mailto:ops@site.com') is None
    assert validate_special_link('tel:+1-555-0100') is None
    assert validate_special_link('tel:abc') is not None


def test_broken_fragment_anchor_is_flagged():
    page = FakePage({'resources': {
        'imgs': [], 'scripts': [], 'styles': [], 'frames': [],
        'ids': [], 'anchors': ['#missing'], 'special': [],
    }, 'ids': ['header']})
    findings, _cov = check_page_resources(page, 'https://site.com/')
    assert any('Broken anchor' in f['display'] for f in findings)


def test_existing_anchor_is_quiet():
    page = FakePage({'resources': {
        'imgs': [], 'scripts': [], 'styles': [], 'frames': [],
        'ids': [], 'anchors': ['#header'], 'special': [],
    }, 'ids': ['header']})
    findings, _cov = check_page_resources(page, 'https://site.com/')
    assert findings == []


# ---- UI testing ----

def test_duplicate_id_is_flagged():
    page = FakePage({'payload': {
        'dups': ['nav'], 'empties': 0, 'hasViewport': True,
        'lang': 'en', 'overflow': 0,
    }})
    findings = check_ui_consistency(page)
    assert any('Duplicate ID' in f['display'] for f in findings)


def test_missing_viewport_and_lang_are_flagged():
    page = FakePage({'payload': {
        'dups': [], 'empties': 0, 'hasViewport': False,
        'lang': '', 'overflow': 0,
    }})
    findings = check_ui_consistency(page)
    assert any('viewport' in f['display'].lower() for f in findings)
    assert any('lang' in f['display'].lower() for f in findings)


def test_clean_ui_is_quiet():
    page = FakePage({'payload': {
        'dups': [], 'empties': 0, 'hasViewport': True,
        'lang': 'en', 'overflow': 0,
    }})
    assert check_ui_consistency(page) == []


# ---- code testing ----

def test_deprecated_tag_and_inline_handler_flagged():
    page = FakePage({'payload': {
        'tags': {'font': 2, 'center': 0, 'marquee': 0, 'blink': 0, 'big': 0, 'tt': 0},
        'inlineHandlers': 3, 'jsUrls': 0, 'scripts': [],
        'hasDoctype': True, 'charset': 'UTF-8', 'jquery': '',
    }})
    findings = check_code_quality(page)
    assert any('<font>' in f['display'] for f in findings)
    assert any('inline event' in f['display'] for f in findings)


def test_outdated_jquery_is_flagged():
    page = FakePage({'payload': {
        'tags': {}, 'inlineHandlers': 0, 'jsUrls': 0, 'scripts': [],
        'hasDoctype': True, 'charset': 'UTF-8',
        'jquery': 'https://cdn.site.com/jquery-1.12.4.min.js',
    }})
    findings = check_code_quality(page)
    assert any('jQuery' in f['display'] for f in findings)


def test_eval_in_inline_script_is_serious():
    page = FakePage({'payload': {
        'tags': {}, 'inlineHandlers': 0, 'jsUrls': 0,
        'scripts': [{'src': '', 'len': 100, 'text': 'var x = eval(userInput);'}],
        'hasDoctype': True, 'charset': 'UTF-8', 'jquery': '',
    }})
    findings = check_code_quality(page)
    assert any('eval' in f['display'] for f in findings)
    assert findings[0]['severity'] == 'serious'


# ---- wiring ----

def test_new_categories_participate_in_scoring_and_merge():
    assert {'url_issues', 'ui_issues', 'code_issues'} <= set(CATEGORY_WEIGHT)
    keyed = finding_key('url_issues', {'issue': 'Page URL returns HTTP 404'})
    assert keyed == 'Page URL returns HTTP 404'
    merged = merge_findings([
        {'url_issues': [{'issue': 'Page URL returns HTTP 404'}]},
        {'url_issues': [{'issue': 'Page URL returns HTTP 404'}]},
    ])
    assert len(merged['url_issues']) == 1
    bad = {'url_issues': [{'severity': 'serious'}]}
    assert calculate_health_score(bad) < calculate_health_score({})


def test_single_page_runs_extended_checks_without_browser():
    page = FakePage({'payload': {
        'dups': [], 'empties': 0, 'hasViewport': True, 'lang': 'en', 'overflow': 0,
    }, 'resources': {
        'imgs': [], 'scripts': [], 'styles': [], 'frames': [],
        'ids': [], 'anchors': [], 'special': [],
    }})
    # Neutralise browser-heavy checks by giving the fake empty results.
    findings, _coverage = run_single_page(
        page, 'https://site.com/About-Us', {}, [],
        page_load_ms=0, api_responses=[], mobile_checked=False, page_status=200,
    )
    assert 'url_issues' in findings
    assert 'ui_issues' in findings
    assert 'code_issues' in findings
