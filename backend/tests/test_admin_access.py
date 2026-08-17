"""
The administrator boundary.

Every other module is scoped to the signed-in user. This one is the single
exception, so what matters is not that it returns the right numbers but that
nobody who should not reach it can. These tests are about refusal.
"""

import pytest

import app as app_module
from modules.admin import routes as admin_routes
from modules.auth.routes import role_for_email


@pytest.fixture()
def client():
    app_module.app.config['TESTING'] = True
    return app_module.app.test_client()


ADMIN_ENDPOINTS = [
    ('get', '/api/admin/overview'),
    ('get', '/api/admin/users'),
    ('get', '/api/admin/users/1'),
    ('patch', '/api/admin/users/2'),
    ('delete', '/api/admin/users/2'),
]


# ---------------- who may reach it ----------------

@pytest.mark.parametrize('method,path', ADMIN_ENDPOINTS)
def test_every_admin_endpoint_refuses_an_anonymous_request(client, method, path):
    response = getattr(client, method)(path)
    assert response.status_code == 401, f'{method.upper()} {path} was reachable without a token'


def test_admin_blueprint_exposes_no_unprotected_route():
    """
    A new endpoint added here without the decorator would be a hole. Assert
    on the blueprint itself so that adding one un-decorated fails this test
    rather than waiting to be noticed.
    """
    unprotected = []
    for rule in app_module.app.url_map.iter_rules():
        if not str(rule).startswith('/api/admin'):
            continue
        view = app_module.app.view_functions[rule.endpoint]
        # Both decorators wrap the view; jwt_required marks it, and our own
        # wrapper is what enforces the role.
        if not getattr(view, '__wrapped__', None):
            unprotected.append(str(rule))
    assert unprotected == [], f'admin routes with no decorator: {unprotected}'


def _token_for(user_id):
    """A genuine signed token, so the request goes through the real auth path."""
    from flask_jwt_extended import create_access_token
    with app_module.app.app_context():
        return create_access_token(identity=str(user_id))


def _auth(user_id):
    return {'Authorization': f'Bearer {_token_for(user_id)}'}


@pytest.fixture()
def as_user(monkeypatch):
    """Sign in as an account with a given role, without needing a database."""
    def _sign_in(role='tester', is_active=1, user_id=42):
        monkeypatch.setattr(admin_routes, 'current_user_row',
                            lambda: {'id': user_id, 'username': 'someone',
                                     'email': 'someone@example.com',
                                     'role': role, 'is_active': is_active})
        # The blocklist callback is registered with the JWT manager at import
        # time, so replacing the module attribute would have no effect. It
        # needs no database here; the role check is what is under test.
        monkeypatch.setattr(app_module.jwt, '_token_in_blocklist_callback',
                            lambda *_a, **_k: False)
        return _auth(user_id)
    return _sign_in


@pytest.mark.parametrize('method,path', ADMIN_ENDPOINTS)
def test_a_signed_in_tester_is_refused_everywhere(client, as_user, method, path):
    """
    The central rule. An ordinary user holding a perfectly valid token must
    not reach any administrator view. This is the test that would catch a
    missing decorator on a new endpoint.
    """
    headers = as_user(role='tester')
    response = getattr(client, method)(path, headers=headers, json={})
    assert response.status_code == 403, \
        f'{method.upper()} {path} let a tester through with {response.status_code}'
    assert 'Administrator access required' in response.get_json()['error']


@pytest.mark.parametrize('method,path', ADMIN_ENDPOINTS)
def test_a_deactivated_admin_is_refused_everywhere(client, as_user, method, path):
    """Revoking access must take effect immediately, not when a token expires."""
    headers = as_user(role='admin', is_active=0)
    response = getattr(client, method)(path, headers=headers, json={})
    assert response.status_code == 403
    assert 'deactivated' in response.get_json()['error'].lower()


def test_a_deleted_account_cannot_use_its_old_token(client, monkeypatch):
    """
    Tokens here never expire, so a deleted or deactivated account would keep
    working forever on the token it already holds. The blocklist callback is
    what prevents that, and it runs before any view is reached.
    """
    monkeypatch.setattr(admin_routes, 'current_user_row', lambda: None)
    monkeypatch.setattr(app_module.jwt, '_token_in_blocklist_callback',
                        lambda *_a, **_k: True)     # account is gone
    response = client.get('/api/admin/users', headers=_auth(99))
    assert response.status_code == 401


# ---------------- how admin is granted ----------------

def test_admin_is_granted_only_by_the_configured_email():
    app_module.app.config['ADMIN_EMAIL'] = 'boss@example.com'
    with app_module.app.app_context():
        assert role_for_email('boss@example.com') == 'admin'
        assert role_for_email('BOSS@example.com') == 'admin'   # case-insensitive
        assert role_for_email('someone@example.com') == 'tester'


def test_nobody_is_admin_when_no_admin_email_is_configured():
    app_module.app.config['ADMIN_EMAIL'] = ''
    with app_module.app.app_context():
        assert role_for_email('anyone@example.com') == 'tester'
        assert role_for_email('') == 'tester'


def test_registration_cannot_grant_a_role_from_the_request_body():
    """
    The bug this pins: register() used to read the role out of the posted
    JSON, so anyone could POST {"role": "admin"} to the public signup
    endpoint and become an administrator.
    """
    import inspect
    from modules.auth import routes as auth_routes

    source = inspect.getsource(auth_routes.register)
    assert "data.get('role'" not in source, \
        'register() must never take a role from the request body'
    assert 'role_for_email(' in source, \
        'register() must derive the role from configuration'


# ---------------- protecting the last way in ----------------

def test_an_admin_cannot_lock_themselves_out(client, monkeypatch):
    """
    Demoting, deactivating or deleting your own account would leave the
    platform with no administrator and no route back short of editing the
    database by hand.
    """
    # Called through __wrapped__ so the self-protection rule is tested on its
    # own; that the decorator refuses non-admins is covered separately above.
    monkeypatch.setattr(admin_routes, '_uid', lambda: 7)

    with app_module.app.test_request_context(json={'role': 'tester'}):
        body, status = admin_routes.update_user.__wrapped__(7)
        assert status == 400
        assert 'your own' in body.get_json()['error']

    with app_module.app.test_request_context(json={'confirm_username': 'me'}):
        body, status = admin_routes.delete_user.__wrapped__(7)
        assert status == 400
        assert 'your own' in body.get_json()['error']


def test_only_known_roles_are_accepted(client, monkeypatch):
    monkeypatch.setattr(admin_routes, '_uid', lambda: 1)

    with app_module.app.test_request_context(json={'role': 'superuser'}):
        body, status = admin_routes.update_user.__wrapped__(2)
        assert status == 400
        assert 'Role must be one of' in body.get_json()['error']


def test_deleting_a_user_requires_typing_their_username(monkeypatch):
    """An irreversible action must not be one mistyped id away."""
    class _Cursor:
        def execute(self, *_a, **_k): pass
        def fetchone(self): return {'id': 2, 'username': 'realuser'}
        def close(self): pass

    class _Conn:
        connection = type('C', (), {'cursor': staticmethod(lambda: _Cursor())})()

    monkeypatch.setattr(admin_routes, 'mysql', _Conn())
    monkeypatch.setattr(admin_routes, '_uid', lambda: 1)

    with app_module.app.test_request_context(json={'confirm_username': 'wrongname'}):
        body, status = admin_routes.delete_user.__wrapped__(2)
        assert status == 400
        assert body.get_json()['expected'] == 'realuser'
