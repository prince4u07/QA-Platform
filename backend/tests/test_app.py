"""App-level smoke tests: routing, error handling, rate limiting."""

import pytest


@pytest.fixture(scope="module")
def client():
    import app as app_module
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


def test_login_rate_limit_trips_429(client):
    # The login endpoint is capped at 10/min. A burst should eventually 429.
    statuses = []
    for _ in range(15):
        resp = client.post('/api/auth/login', json={'email': 'x@y.z', 'password': 'nope'})
        statuses.append(resp.status_code)
        if resp.status_code == 429:
            break
    assert 429 in statuses, f"expected a 429 within the burst, got {statuses}"
