"""Test cases only become Pass or Fail through an actual recorded run."""

import pytest

import app as app_module
from modules.testcases import routes as testcases_routes


class Cursor:
    def __init__(self):
        self.calls = []
        self.lastrowid = 17

    def execute(self, sql, params=()):
        self.calls.append((sql, params))

    def close(self):
        pass


class Connection:
    def __init__(self):
        self.cursor_instance = Cursor()

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        pass


class Database:
    def __init__(self):
        self.connection = Connection()


@pytest.fixture()
def client(monkeypatch):
    database = Database()
    monkeypatch.setattr(testcases_routes, 'mysql', database)
    monkeypatch.setattr(testcases_routes, 'user_owns_project', lambda *_: True)
    monkeypatch.setattr(testcases_routes, 'user_owns_testcase', lambda *_: True)
    monkeypatch.setattr(app_module.jwt, '_token_in_blocklist_callback',
                        lambda *_args, **_kwargs: False)
    app_module.app.config['TESTING'] = True

    from flask_jwt_extended import create_access_token
    with app_module.app.app_context():
        token = create_access_token(identity='42')

    return app_module.app.test_client(), database, {
        'Authorization': f'Bearer {token}',
    }


def testcase(**overrides):
    payload = {
        'project_id': 1,
        'title': 'Checkout remains usable',
        'steps': 'Open /checkout',
        'expected_result': 'Checkout is shown',
        'test_type': 'manual',
        'test_purpose': 'usability',
    }
    payload.update(overrides)
    return payload


def test_a_new_test_starts_pending_and_uses_the_real_runner_framework(client):
    http, database, headers = client

    response = http.post('/api/testcases', headers=headers, json=testcase(
        status='Pass',
        test_type='automated',
        test_purpose='regression',
        automation_framework='selenium',
    ))

    assert response.status_code == 201
    _sql, params = database.connection.cursor_instance.calls[-1]
    assert params[6:10] == ('Pending', 'automated', 'playwright', 'regression')


def test_a_purpose_must_match_how_the_test_will_run(client):
    http, database, headers = client

    response = http.post('/api/testcases', headers=headers, json=testcase(
        test_type='manual',
        test_purpose='regression',
    ))

    assert response.status_code == 400
    assert response.get_json()['error'] == 'Invalid purpose for a manual test'
    assert database.connection.cursor_instance.calls == []


def test_editing_a_test_does_not_rewrite_its_last_run_result(client):
    http, database, headers = client

    response = http.put('/api/testcases/9', headers=headers, json=testcase(
        status='Fail',
    ))

    assert response.status_code == 200
    sql, _params = database.connection.cursor_instance.calls[-1]
    assert 'status = %s' not in sql



def test_pass_or_fail_cannot_be_set_without_a_run(client):
    """The status endpoint is gone: a result must come from a recorded run."""
    http, database, headers = client

    response = http.patch('/api/testcases/9/status', headers=headers,
                          json={'status': 'Pass'})

    assert response.status_code == 404
    assert database.connection.cursor_instance.calls == []
