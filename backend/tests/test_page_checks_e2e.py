"""
End-to-end checks against a real Chromium page served over real HTTP.

The unit tests cover the thresholds with a fake page; these prove the checks
actually work against a browser: that performance timings come back non-zero,
that every category is populated, and that coverage reports what it skipped.
"""

import threading
import http.server
import socketserver
import pytest

from playwright.sync_api import sync_playwright

# Imported under an alias: the production function is called test_single_page,
# so importing it by name makes pytest try to collect it as a test case.
from modules.runner.routes import test_single_page as run_page_checks
from modules.runner.routes import check_performance


PAGE_HTML = b"""<!doctype html>
<html lang="en">
<head><title>Fixture page</title></head>
<body>
  <h1>Fixture</h1>
  <img src="/big.png" width="300" height="300">
  <a href="/missing-page">A link that 404s</a>
  <a href="/">A link that works</a>
  <button></button>
  <script>console.error('boom from the fixture page');</script>
  <script src="/big.js"></script>
</body>
</html>
"""

# ~1.2MB so it trips the "single file is large" rule.
BIG_JS = b"//" + b"x" * (1200 * 1024) + b"\n"


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/big.js':
            body, ctype = BIG_JS, 'application/javascript'
        elif self.path == '/big.png':
            body, ctype = b'\x89PNG\r\n\x1a\n' + b'0' * 2048, 'image/png'
        elif self.path == '/missing-page':
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        else:
            body, ctype = PAGE_HTML, 'text/html'
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass        # keep pytest output clean


@pytest.fixture(scope='module')
def site():
    """A throwaway local site, so these tests never depend on the internet."""
    with socketserver.TCPServer(('127.0.0.1', 0), _Handler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{port}/'
        finally:
            httpd.shutdown()


@pytest.fixture(scope='module')
def browser_page(site):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        page.goto(site, wait_until='load')
        yield page
        browser.close()


def test_performance_measures_a_real_page(browser_page):
    findings, metrics = check_performance(browser_page, page_load_ms=1200)
    # The whole point: page weight used to be reported as 0 on every run.
    assert metrics['page_size_kb'] > 0
    assert metrics['request_count'] > 0
    # The 1.2MB script must be named as an oversized file.
    big = [f for f in findings if f['metric'] == 'resource_size_kb']
    assert big, f"expected an oversized-file finding, got {[f['issue'] for f in findings]}"
    assert 'big.js' in big[0]['display']


def test_single_page_returns_every_category_and_coverage(browser_page, site):
    findings, coverage = run_page_checks(
        browser_page, site, {'content-type': 'text/html'},
        console_messages=[{'type': 'error', 'text': 'boom from the fixture page'}],
        is_mobile=True, page_load_ms=1200,
    )

    expected = {'broken_links', 'console_errors', 'missing_alt_images', 'seo_issues',
                'security_issues', 'accessibility_issues', 'mobile_issues',
                'performance_issues', 'functional_issues', 'api_issues',
                'validation_issues', 'url_issues', 'ui_issues', 'code_issues'}
    assert set(findings) == expected
    # A page audit cannot judge whether the feature works; the runner fills
    # this in from the test case's executed steps.
    assert findings['functional_issues'] == []

    # Coverage must report what ran, so a partial audit is never silent.
    assert coverage['accessibility_engine'] in ('axe-core', 'heuristic-fallback')
    assert coverage['performance']['page_size_kb'] > 0
    assert coverage['links']['links_found'] > 0

    # Served over plain HTTP with no security headers, so this must be caught.
    assert any(f['severity'] == 'critical' for f in findings['security_issues'])


def test_every_finding_carries_a_severity(browser_page, site):
    """Scoring depends on per-finding severity; an untagged finding is a bug."""
    findings, _ = run_page_checks(
        browser_page, site, {},
        console_messages=[{'type': 'error', 'text': 'boom'}],
        is_mobile=True, page_load_ms=1200,
    )
    for category, items in findings.items():
        for item in items:
            assert item.get('severity') in ('critical', 'serious', 'moderate', 'minor'), \
                f"{category} produced a finding with no severity: {item}"


def test_manual_auto_check_finds_issues_without_inventing_them(browser_page, site):
    """
    A manual session has no response headers and runs in a desktop window.
    Auditing it with the full page-check suite would report every security
    header as missing and every control as an undersized tap target, on every
    page. Those are not real findings and would drown the tester in noise.
    """
    from modules.runner.manual_run import _ManualSession

    session = _ManualSession(1, 1, site)
    found = session._auto_check(browser_page, site, deep=False)

    assert found > 0, 'the fixture page has real problems; it should find some'
    categories = {f.get('category') for f in session.auto_findings}
    assert 'security_issues' not in categories, 'no headers available, cannot judge security'
    assert 'mobile_issues' not in categories, 'desktop window, tap-target sizes are meaningless'
    # The cheap in-page checks must still run while the tester browses.
    assert 'accessibility_issues' in categories or 'missing_alt_images' in categories
    assert all(f.get('source') == 'AUTOMATED' for f in session.auto_findings)


def test_manual_deep_check_adds_link_checking(browser_page, site):
    """Link checking is deferred to the crawl phase, not run while browsing."""
    from modules.runner.manual_run import _ManualSession

    shallow = _ManualSession(1, 1, site)
    shallow._auto_check(browser_page, site, deep=False)
    assert 'broken_links' not in {f.get('category') for f in shallow.auto_findings}

    deep = _ManualSession(1, 1, site)
    deep._auto_check(browser_page, site, deep=True)
    assert 'broken_links' in {f.get('category') for f in deep.auto_findings}


def test_mobile_checks_are_reported_as_skipped_off_mobile(browser_page, site):
    _findings, coverage = run_page_checks(
        browser_page, site, {}, console_messages=[], is_mobile=False, page_load_ms=100,
    )
    assert coverage['mobile_skipped_reason']
