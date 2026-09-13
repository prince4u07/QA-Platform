"""
Live end-to-end: real headless Chromium + real MySQL.

Unlike the fixture-fake unit tests, these prove the whole loop works:
browser crawls pages, screenshots land on disk, findings persist with
run_type/source/verdict metadata, manual sessions run the full
start -> pause -> step -> issue -> done flow, and the PDF renders.

Requires MySQL (skips otherwise) and Playwright Chromium. Every temp
row (user, project, test case, runs) is deleted afterwards; temp
screenshots are removed too.
"""

import http.server
import json
import os
import socketserver
import threading
import time

import pytest

PAGE_HTML = b"""<!doctype html>
<html lang="en">
<head><title>Live fixture</title>
<meta name="description" content="A live test page">
<meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body>
  <h1>Live fixture</h1>
  <img src="/photo.png" width="300" height="300">
  <a href="/gone">A link that 404s</a>
  <a href="/">A link that works</a>
  <a href="#nowhere">A fragment that points nowhere</a>
  <form id="signup"><input name="email" required><button>Join</button></form>
  <script>console.error('live boom');</script>
</body>
</html>
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/gone':
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        body, ctype = (PAGE_HTML, 'text/html')
        if self.path == '/photo.png':
            body, ctype = b'\x89PNG\r\n\x1a\n' + b'0' * 2048, 'image/png'
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def _db():
    import MySQLdb
    from dotenv import load_dotenv
    load_dotenv()
    return MySQLdb.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        user=os.getenv('DB_USER', 'root'),
        passwd=os.getenv('DB_PASSWORD', ''),
        db=os.getenv('DB_NAME', 'qa_platform'),
    )


@pytest.fixture(scope='module')
def live_site():
    with socketserver.TCPServer(('127.0.0.1', 0), _Handler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{port}/'
        finally:
            httpd.shutdown()


@pytest.fixture()
def db_conn():
    try:
        conn = _db()
    except Exception as e:
        pytest.skip(f'MySQL unavailable: {e}')
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def temp_case(db_conn, live_site):
    """A throwaway project + automated test case pointing at the fixture."""
    import MySQLdb.cursors
    cur = db_conn.cursor()
    cur.execute("SELECT id FROM users LIMIT 1")
    row = cur.fetchone()
    if not row:
        pytest.skip('no users in dev database')
    user_id = row[0]
    cur.execute(
        "INSERT INTO projects (user_id, name, base_url) VALUES (%s, %s, %s)",
        (user_id, 'e2e-tmp', live_site))
    project_id = cur.lastrowid
    cur.execute(
        """INSERT INTO test_cases (project_id, title, steps, expected_result,
              test_type, crawl_pages, max_pages)
           VALUES (%s, %s, %s, %s, 'automated', TRUE, 3)""",
        (project_id, 'e2e-tmp-case', 'Open ' + live_site, 'page loads'))
    case_id = cur.lastrowid
    db_conn.commit()
    tc = {'id': case_id, 'title': 'e2e-tmp-case', 'test_type': 'automated',
          'crawl_pages': True, 'max_pages': 3, 'steps': 'Open ' + live_site,
          'expected_result': 'page loads', 'base_url': live_site,
          'project_id': project_id, 'project_name': 'e2e-tmp',
          'has_active_session': False, 'check_dead_controls': False}
    yield tc
    cur = db_conn.cursor()
    try:
        cur.execute("DELETE FROM projects WHERE id = %s", (project_id,))
        db_conn.commit()
    except Exception:
        pass


def _dict_cursor(conn):
    import MySQLdb.cursors
    return conn.cursor(MySQLdb.cursors.DictCursor)


class _MysqlShim:
    """Stands in for flask_mysqldb so _perform_run hits the real database."""
    def __init__(self, conn):
        import MySQLdb.cursors
        real_cursor = conn.cursor

        def dict_cursor(*_a, **_k):
            return real_cursor(MySQLdb.cursors.DictCursor)

        conn.cursor = dict_cursor
        self.connection = conn


def test_live_automated_run_persists_everything(db_conn, temp_case):
    import modules.runner.routes as routes

    conn = _db()
    previous_mysql = routes.mysql
    routes.mysql = _MysqlShim(conn)
    try:
        result, code = routes._perform_run(temp_case['id'], dict(temp_case))
    finally:
        routes.mysql = previous_mysql
    assert code == 200, result
    assert result['run_type'] == 'AUTOMATED'
    assert result['execution_mode'] == 'HEADLESS'
    assert result['source'] == 'MACHINE'
    assert result['verdict_reason']
    assert result['worst_severity'] in ('critical', 'serious', 'moderate', 'minor')
    assert result['pages_crawled'] >= 1
    assert result['links_checked'] >= 1
    assert result['status'] == 'Fail'    # fixture has a 404 + console error

    # Screenshots really landed on disk.
    first_shot = result['screenshot']
    assert first_shot.startswith('/static/uploads/runs/')
    disk = os.path.join('static', 'uploads', 'runs',
                        os.path.basename(first_shot))
    assert os.path.exists(disk), f'screenshot missing: {disk}'

    # DB row carries the new metadata.
    cur = _dict_cursor(db_conn)
    cur.execute(
        """SELECT run_type, execution_mode, source, verdict_reason,
                  worst_severity, findings_evidence
           FROM test_runs WHERE id = %s""", (result['run_id'],))
    row = cur.fetchone()
    assert row['run_type'] == 'AUTOMATED'
    assert row['worst_severity'] == result['worst_severity']
    evidence = json.loads(row['findings_evidence'])
    for category, items in evidence.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and 'issue' in item:
                assert item.get('source') == 'AUTOMATED', (category, item)
    cur.execute("SELECT COUNT(*) AS c FROM crawled_pages WHERE test_run_id = %s",
                (result['run_id'],))
    assert cur.fetchone()['c'] >= 1

    # Tidy the evidence files (DB rows cascade with the project delete).
    for rec in (result.get('screenshots') or [first_shot]):
        path = os.path.join('static', 'uploads', 'runs', os.path.basename(rec))
        if os.path.exists(path):
            os.remove(path)
    conn.close()


def test_live_pdf_renders_with_split_sections(db_conn):
    """The PDF (sections A-L) renders against real data."""
    import app as app_module

    app_module.app.config['TESTING'] = True
    client = app_module.app.test_client()
    cur = db_conn.cursor()
    cur.execute("SELECT id FROM users LIMIT 1")
    row = cur.fetchone()
    if not row:
        pytest.skip('no users in dev database')
    from flask_jwt_extended import create_access_token
    with app_module.app.app_context():
        token = create_access_token(identity=str(row[0]))
    resp = client.get('/api/reports/export-pdf',
                      headers={'Authorization': f'Bearer {token}'})
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    assert resp.content_type == 'application/pdf'
    assert resp.data.startswith(b'%PDF')


def test_live_manual_workflow_headless(db_conn, live_site, monkeypatch):
    """Full manual flow through the real HTTP API (browser headless)."""
    import app as app_module

    monkeypatch.setenv('QA_MANUAL_HEADLESS', '1')
    app_module.app.config['TESTING'] = True
    client = app_module.app.test_client()
    cur = db_conn.cursor()
    cur.execute("SELECT id FROM users LIMIT 1")
    row = cur.fetchone()
    if not row:
        pytest.skip('no users in dev database')
    user_id = row[0]
    from flask_jwt_extended import create_access_token
    with app_module.app.app_context():
        token = create_access_token(identity=str(user_id))
    headers = {'Authorization': f'Bearer {token}'}

    cur.execute(
        "INSERT INTO projects (user_id, name, base_url) VALUES (%s, %s, %s)",
        (user_id, 'e2e-manual-tmp', live_site))
    project_id = cur.lastrowid
    cur.execute(
        """INSERT INTO test_cases (project_id, title, steps, expected_result,
              test_type, max_pages)
           VALUES (%s, %s, %s, %s, 'manual', 5)""",
        (project_id, 'e2e-manual-case', 'Open the page\nCheck the heading',
         'Heading is visible'))
    case_id = cur.lastrowid
    db_conn.commit()
    try:
        resp = client.post(f'/api/runner/manual/{case_id}/start', headers=headers)
        assert resp.status_code == 200, resp.get_json()

        # Wait for the worker to reach ready (headless, local page: fast).
        snap = None
        for _ in range(40):
            time.sleep(0.5)
            status = client.get(f'/api/runner/manual/{case_id}/status',
                                headers=headers).get_json()
            if status.get('status') in ('ready', 'error'):
                snap = status
                break
        assert snap and snap['status'] == 'ready', snap
        assert snap['pages_visited'] >= 1
        assert snap['current_url'].startswith('http://127.0.0.1')
        assert snap['current_screenshot'].startswith('/static/uploads/manual_runs/')

        # Pause / resume without closing the browser.
        resp = client.post(f'/api/runner/manual/{case_id}/pause',
                           json={'paused': True}, headers=headers)
        assert resp.get_json()['run']['paused'] is True
        resp = client.post(f'/api/runner/manual/{case_id}/pause',
                           json={'paused': False}, headers=headers)
        assert resp.get_json()['run']['paused'] is False

        # Step verdict + rich issue, then finish as the tester.
        resp = client.post(f'/api/runner/manual/{case_id}/step/0',
                           json={'status': 'passed', 'note': 'heading ok'},
                           headers=headers)
        assert resp.status_code == 200
        resp = client.post(f'/api/runner/manual/{case_id}/issue', json={
            'title': 'Image missing description',
            'description': 'The photo has no alt text',
            'category': 'accessibility', 'severity': 'minor',
            'expected_result': 'Screen reader announces the photo',
            'actual_result': 'Silence', 'step_index': 1,
        }, headers=headers)
        assert resp.status_code == 201
        assert resp.get_json()['issue']['source'] == 'MANUAL'

        resp = client.post(f'/api/runner/manual/{case_id}/done',
                           json={'outcome': 'Pass', 'note': 'looks good',
                                 'expected_met': True}, headers=headers)
        body = resp.get_json()
        assert resp.status_code == 200, body
        assert body['run_type'] == 'MANUAL'
        assert body['execution_mode'] == 'HEADED'
        assert body['status'] == 'Pass'

        # Persistence: run row + step rows + issue rows.
        cur = _dict_cursor(db_conn)
        cur.execute(
            """SELECT run_type, source, verdict_reason FROM test_runs WHERE id = %s""",
            (body['run_id'],))
        saved = cur.fetchone()
        assert saved['run_type'] == 'MANUAL'
        assert saved['verdict_reason']
        cur.execute(
            "SELECT COUNT(*) AS c FROM manual_step_evidence WHERE test_run_id = %s",
            (body['run_id'],))
        assert cur.fetchone()['c'] == 2
        cur.execute(
            """SELECT title, category, expected_result FROM manual_issues
               WHERE test_run_id = %s""", (body['run_id'],))
        issues = cur.fetchall()
        assert len(issues) == 1
        assert issues[0]['expected_result'] == 'Screen reader announces the photo'
    finally:
        try:
            client.post(f'/api/runner/manual/{case_id}/cancel', headers=headers)
        except Exception:
            pass
        cur = db_conn.cursor()
        try:
            cur.execute("DELETE FROM projects WHERE id = %s", (project_id,))
            db_conn.commit()
        except Exception:
            pass
