"""
Executable steps against a real Chromium browser and a real login workflow.

The unit tests prove the parser reads a step. These prove the steps actually
drive a browser: that a login workflow completes, that a broken one is caught
at the right step, and that a wrong password is reported as a functional
failure rather than passing silently.
"""

import threading
import http.server
import socketserver
import pytest

from playwright.sync_api import sync_playwright

from modules.runner import steps as steps_module
from modules.runner.steps import run_steps, summarise_steps


@pytest.fixture(autouse=True)
def fast_step_timeout(monkeypatch):
    """
    Tests that assert a step correctly FAILS would otherwise sit for the full
    production timeout each, waiting for something that is never going to
    appear. The fixture pages are local and respond instantly, so a short
    timeout proves exactly the same thing in a fraction of the time.
    """
    monkeypatch.setattr(steps_module, 'STEP_TIMEOUT_MS', 1500)


LOGIN_PAGE = b"""<!doctype html>
<html lang="en"><head><title>Sign in</title></head>
<body>
  <h1>Sign in</h1>
  <form method="GET" action="/dashboard">
    <label for="email">Email</label>
    <input id="email" name="email" type="email" placeholder="you@example.com">
    <label for="password">Password</label>
    <input id="password" name="password" type="password">
    <button type="submit">Sign in</button>
  </form>
</body></html>
"""

DASHBOARD_OK = b"""<!doctype html>
<html lang="en"><head><title>Dashboard</title></head>
<body><h1>Welcome back</h1><a href="/settings">Settings</a></body></html>
"""

DASHBOARD_DENIED = b"""<!doctype html>
<html lang="en"><head><title>Sign in</title></head>
<body><h1>Sign in</h1><p>Wrong email or password</p></body></html>
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split('?')[0]
        query = self.path.split('?')[1] if '?' in self.path else ''
        if path == '/dashboard':
            # Only the right password reaches the dashboard, so a workflow
            # that "completes" with bad credentials must still be caught.
            body = DASHBOARD_OK if 'password=hunter2' in query else DASHBOARD_DENIED
        elif path == '/settings':
            body = b'<!doctype html><html lang="en"><head><title>Settings</title></head><body><h1>Settings</h1></body></html>'
        else:
            body = LOGIN_PAGE
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture(scope='module')
def site():
    with socketserver.TCPServer(('127.0.0.1', 0), _Handler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{httpd.server_address[1]}'
        finally:
            httpd.shutdown()


@pytest.fixture()
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        pg = browser.new_page()
        yield pg
        browser.close()


def test_a_real_login_workflow_runs_end_to_end(page, site):
    """The whole point: an automated run that proves the feature works."""
    results, findings = run_steps(page, f"""
        1. Open {site}/
        2. Type alice@example.com into Email
        3. Type hunter2 into Password
        4. Click Sign in
        5. Expect text Welcome back
        6. Expect url contains /dashboard
    """, base_url=site)

    assert findings == [], [f['display'] for f in findings]
    assert [r['status'] for r in results] == ['passed'] * 6
    assert summarise_steps(results)['workflow_completed'] is True


def test_a_wrong_password_is_caught_as_a_functional_failure(page, site):
    """
    This is the case a crawler can never find: every page returns HTTP 200
    and looks healthy, but the feature does not work.
    """
    results, findings = run_steps(page, f"""
        Open {site}/
        Type alice@example.com into Email
        Type wrongpass into Password
        Click Sign in
        Expect text Welcome back
    """, base_url=site)

    assert len(findings) == 1
    assert findings[0]['severity'] == 'critical'
    # It must break on the assertion, not on the click before it.
    assert results[3]['status'] == 'passed'
    assert results[4]['status'] == 'failed'
    assert 'Welcome back' in results[4]['message']


def test_a_missing_element_names_what_it_could_not_find(page, site):
    results, findings = run_steps(page, f"""
        Open {site}/
        Click Checkout
    """, base_url=site)

    assert results[1]['status'] == 'failed'
    assert 'Checkout' in results[1]['message']
    assert 'no element matched' in results[1]['detail']
    assert findings[0]['severity'] == 'critical'


def test_steps_after_a_break_are_skipped_not_re_reported(page, site):
    results, findings = run_steps(page, f"""
        Open {site}/
        Click Nonexistent Button
        Expect text Welcome back
        Expect url contains /dashboard
    """, base_url=site)

    assert [r['status'] for r in results] == ['passed', 'failed', 'skipped', 'skipped']
    assert len(findings) == 1, 'one broken step is one defect, not three'


def test_a_multi_page_workflow_crosses_pages(page, site):
    """An integration workflow is exactly this: state carried across pages."""
    results, findings = run_steps(page, f"""
        Open {site}/
        Type alice@example.com into Email
        Type hunter2 into Password
        Click Sign in
        Click Settings
        Expect url contains /settings
    """, base_url=site)

    assert findings == [], [f['display'] for f in findings]
    assert results[-1]['status'] == 'passed'


def test_each_step_can_be_screenshotted(page, site, tmp_path):
    shots = []

    def _capture(index, _record):
        path = tmp_path / f'step{index}.png'
        page.screenshot(path=str(path))
        shots.append(path)
        return f'/shots/step{index}.png'

    results, _ = run_steps(page, f'Open {site}/\nClick Sign in',
                           base_url=site, on_step=_capture)

    assert all(r['screenshot'] for r in results)
    assert all(p.exists() and p.stat().st_size > 0 for p in shots)
