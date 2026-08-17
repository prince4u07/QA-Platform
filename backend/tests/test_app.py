"""App-level smoke tests: routing, error handling, rate limiting."""

import pytest
import MySQLdb

import app as app_module

# Registered at import time on purpose: Flask refuses to accept new routes
# once the app has served its first request, and these must exist before any
# test runs. They give the error handlers something real to catch.
@app_module.app.route('/__test__/db-down')
def _raise_db_down():
    raise MySQLdb.OperationalError(2002, "Can't connect to server on 'localhost' (10061)")


@app_module.app.route('/__test__/real-bug')
def _raise_real_bug():
    raise ValueError('a secret internal detail')


@pytest.fixture(scope="module")
def client():
    app_module.app.config['TESTING'] = True
    return app_module.app.test_client()


def test_unknown_route_returns_404(client):
    resp = client.get('/api/does-not-exist')
    assert resp.status_code == 404


def test_key_routes_registered():
    import app as app_module
    rules = {str(r) for r in app_module.app.url_map.iter_rules()}
    assert any('/api/auth/login' in r for r in rules)
    assert any('/api/runner/run/<int:testcase_id>/async' in r for r in rules)
    assert any('/api/runner/job/<job_id>' in r for r in rules)


def test_database_being_down_says_so_instead_of_internal_server_error(client):
    """
    A stopped MySQL used to produce a 20-line traceback per request and tell
    the user "Internal server error", which names neither the problem nor the
    fix. It is an operational failure, so it must read as one.
    """
    resp = client.get('/__test__/db-down')

    assert resp.status_code == 503, 'the service is unavailable, the request is not invalid'
    body = resp.get_json()
    assert 'MySQL' in body['error']
    assert body['error'] != 'Internal server error'


def test_a_genuine_bug_is_still_a_500_and_leaks_nothing(client):
    """The database handler must not swallow real programming errors."""
    resp = client.get('/__test__/real-bug')

    assert resp.status_code == 500
    assert resp.get_json()['error'] == 'Internal server error'
    assert 'secret internal detail' not in resp.get_data(as_text=True)


def test_login_rate_limit_trips_429(client):
    # The login endpoint is capped at 10/min. A burst should eventually 429.
    statuses = []
    for _ in range(15):
        resp = client.post('/api/auth/login', json={'email': 'x@y.z', 'password': 'nope'})
        statuses.append(resp.status_code)
        if resp.status_code == 429:
            break
    assert 429 in statuses, f"expected a 429 within the burst, got {statuses}"
