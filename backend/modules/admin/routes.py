"""
Administrator views.

Every other module in this platform is deliberately scoped to the signed-in
user: a tester can only ever reach their own projects, runs and bugs. This
module is the single, explicit exception, so the rules it enforces matter
more than the queries it runs.

Three rules hold everywhere below:

1. Administrator status is read from the database on every request, never
   from the token. A demoted or deactivated account loses access instantly
   rather than when its token happens to expire, which matters here because
   tokens in this deployment do not expire at all.
2. Nothing accepts a role from a request body. The only route to becoming an
   administrator is registering with the address in ADMIN_EMAIL.
3. An administrator cannot demote, deactivate or delete their own account.
   Doing so would leave the platform with no administrator and no way back in
   short of editing the database by hand.
"""

import logging
from functools import wraps

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__)
mysql = None

VALID_ROLES = ('tester', 'admin')


def init_admin(app_mysql):
    global mysql
    mysql = app_mysql


def _uid():
    return int(get_jwt_identity())


def current_user_row():
    """The signed-in user as stored right now, or None if they no longer exist."""
    cursor = mysql.connection.cursor()
    cursor.execute(
        "SELECT id, username, email, role, is_active FROM users WHERE id = %s",
        (_uid(),)
    )
    row = cursor.fetchone()
    cursor.close()
    return row


def admin_required(fn):
    """
    Allow only an active administrator through.

    Checked against the database rather than the token so that revoking
    someone's access takes effect on their very next request.
    """
    @wraps(fn)
    @jwt_required()
    def wrapper(*args, **kwargs):
        user = current_user_row()
        if not user:
            return jsonify({'error': 'Account no longer exists'}), 401
        if not user.get('is_active'):
            return jsonify({'error': 'This account has been deactivated'}), 403
        if user.get('role') != 'admin':
            # Deliberately the same shape as any other refusal: it tells an
            # ordinary user nothing about what exists behind this endpoint.
            return jsonify({'error': 'Administrator access required'}), 403
        return fn(*args, **kwargs)
    return wrapper


# ============================================================
# PLATFORM OVERVIEW
# ============================================================

@admin_bp.route('/overview', methods=['GET'])
@admin_required
def overview():
    """Totals across every account, for the admin dashboard header."""
    cursor = mysql.connection.cursor()
    try:
        cursor.execute("""
            SELECT
              (SELECT COUNT(*) FROM users)                        AS users_total,
              (SELECT COUNT(*) FROM users WHERE is_active = TRUE) AS users_active,
              (SELECT COUNT(*) FROM projects)                     AS projects,
              (SELECT COUNT(*) FROM test_cases)                   AS test_cases,
              (SELECT COUNT(*) FROM test_runs)                    AS runs,
              (SELECT COUNT(*) FROM bugs)                         AS bugs,
              (SELECT COUNT(*) FROM bugs WHERE status = 'Open')   AS bugs_open,
              (SELECT ROUND(AVG(health_score)) FROM test_runs)    AS avg_health
        """)
        totals = cursor.fetchone() or {}

        # Recent activity across the whole platform, not just one account.
        cursor.execute("""
            SELECT tr.id, tr.status, tr.health_score, tr.issues_found, tr.run_at,
                   tc.title AS test_case, p.name AS project, u.username AS owner
            FROM test_runs tr
            JOIN test_cases tc ON tr.test_case_id = tc.id
            JOIN projects p    ON tc.project_id = p.id
            JOIN users u       ON p.user_id = u.id
            ORDER BY tr.run_at DESC
            LIMIT 15
        """)
        recent = cursor.fetchall()
    finally:
        cursor.close()

    for run in recent:
        if run.get('run_at'):
            run['run_at'] = run['run_at'].isoformat()

    return jsonify({'totals': totals, 'recent_runs': recent}), 200


# ============================================================
# USERS
# ============================================================

@admin_bp.route('/users', methods=['GET'])
@admin_required
def list_users():
    """Every account with a count of what it owns."""
    cursor = mysql.connection.cursor()
    try:
        cursor.execute("""
            SELECT u.id, u.username, u.email, u.role, u.is_active, u.created_at,
                   (SELECT COUNT(*) FROM projects p WHERE p.user_id = u.id) AS projects,
                   (SELECT COUNT(*) FROM test_cases tc
                      JOIN projects p ON tc.project_id = p.id
                     WHERE p.user_id = u.id) AS test_cases,
                   (SELECT COUNT(*) FROM test_runs tr
                      JOIN test_cases tc ON tr.test_case_id = tc.id
                      JOIN projects p ON tc.project_id = p.id
                     WHERE p.user_id = u.id) AS runs
            FROM users u
            ORDER BY u.created_at DESC
        """)
        users = cursor.fetchall()
    finally:
        cursor.close()

    for user in users:
        if user.get('created_at'):
            user['created_at'] = user['created_at'].isoformat()
        user['is_active'] = bool(user['is_active'])
        user['is_self'] = user['id'] == _uid()

    return jsonify(users), 200


@admin_bp.route('/users/<int:user_id>', methods=['GET'])
@admin_required
def user_detail(user_id):
    """One account's projects and recent runs, for looking into a problem."""
    cursor = mysql.connection.cursor()
    try:
        cursor.execute(
            "SELECT id, username, email, role, is_active, created_at "
            "FROM users WHERE id = %s",
            (user_id,)
        )
        user = cursor.fetchone()
        if not user:
            return jsonify({'error': 'User not found'}), 404

        cursor.execute("""
            SELECT p.id, p.name, p.base_url, p.created_at,
                   (SELECT COUNT(*) FROM test_cases tc WHERE tc.project_id = p.id) AS test_cases
            FROM projects p WHERE p.user_id = %s ORDER BY p.created_at DESC
        """, (user_id,))
        projects = cursor.fetchall()

        cursor.execute("""
            SELECT tr.id, tr.status, tr.health_score, tr.issues_found, tr.run_at,
                   tc.title AS test_case
            FROM test_runs tr
            JOIN test_cases tc ON tr.test_case_id = tc.id
            JOIN projects p    ON tc.project_id = p.id
            WHERE p.user_id = %s
            ORDER BY tr.run_at DESC LIMIT 20
        """, (user_id,))
        runs = cursor.fetchall()
    finally:
        cursor.close()

    user['is_active'] = bool(user['is_active'])
    user['is_self'] = user['id'] == _uid()
    for row in (user, *projects, *runs):
        for field in ('created_at', 'run_at'):
            if row.get(field):
                row[field] = row[field].isoformat()

    return jsonify({'user': user, 'projects': projects, 'runs': runs}), 200


@admin_bp.route('/users/<int:user_id>', methods=['PATCH'])
@admin_required
def update_user(user_id):
    """
    Change an account's role or active state.

    Refuses to act on the administrator's own account: demoting or
    deactivating yourself locks everyone out of administration.
    """
    if user_id == _uid():
        return jsonify({'error': 'You cannot change your own role or status'}), 400

    data = request.get_json(silent=True) or {}
    updates, values = [], []

    if 'role' in data:
        role = (data.get('role') or '').strip().lower()
        if role not in VALID_ROLES:
            return jsonify({'error': f'Role must be one of: {", ".join(VALID_ROLES)}'}), 400
        updates.append('role = %s')
        values.append(role)

    if 'is_active' in data:
        updates.append('is_active = %s')
        values.append(bool(data['is_active']))

    if not updates:
        return jsonify({'error': 'Nothing to update'}), 400

    cursor = mysql.connection.cursor()
    try:
        cursor.execute("SELECT id, username FROM users WHERE id = %s", (user_id,))
        target = cursor.fetchone()
        if not target:
            return jsonify({'error': 'User not found'}), 404

        values.append(user_id)
        cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = %s", values)
        mysql.connection.commit()
    finally:
        cursor.close()

    logger.info("admin %s updated user %s (%s)", _uid(), user_id, data)
    return jsonify({'message': f"Updated {target['username']}"}), 200


@admin_bp.route('/users/<int:user_id>', methods=['DELETE'])
@admin_required
def delete_user(user_id):
    """
    Delete an account and everything it owns. Irreversible.

    Deactivating is almost always the better answer, so this requires the
    caller to confirm with the account's exact username. That makes deleting
    the wrong row by a mistyped id effectively impossible.
    """
    if user_id == _uid():
        return jsonify({'error': 'You cannot delete your own account'}), 400

    data = request.get_json(silent=True) or {}
    cursor = mysql.connection.cursor()
    try:
        cursor.execute("SELECT id, username FROM users WHERE id = %s", (user_id,))
        target = cursor.fetchone()
        if not target:
            return jsonify({'error': 'User not found'}), 404

        if (data.get('confirm_username') or '') != target['username']:
            return jsonify({
                'error': 'Confirm the deletion by sending the exact username',
                'expected': target['username'],
            }), 400

        # Children first: the schema uses ON DELETE SET NULL in places, which
        # would otherwise orphan rows rather than remove them.
        cursor.execute("""DELETE FROM bugs WHERE test_case_id IN (
                            SELECT tc.id FROM test_cases tc
                            JOIN projects p ON tc.project_id = p.id
                            WHERE p.user_id = %s)""", (user_id,))
        cursor.execute("""DELETE FROM crawled_pages WHERE test_run_id IN (
                            SELECT tr.id FROM test_runs tr
                            JOIN test_cases tc ON tr.test_case_id = tc.id
                            JOIN projects p ON tc.project_id = p.id
                            WHERE p.user_id = %s)""", (user_id,))
        cursor.execute("""DELETE FROM test_runs WHERE test_case_id IN (
                            SELECT tc.id FROM test_cases tc
                            JOIN projects p ON tc.project_id = p.id
                            WHERE p.user_id = %s)""", (user_id,))
        cursor.execute("""DELETE FROM ai_suggestions WHERE project_id IN (
                            SELECT id FROM projects WHERE user_id = %s)""", (user_id,))
        cursor.execute("""DELETE FROM test_cases WHERE project_id IN (
                            SELECT id FROM projects WHERE user_id = %s)""", (user_id,))
        cursor.execute("DELETE FROM projects WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM bugs WHERE assigned_to = %s", (user_id,))
        cursor.execute("DELETE FROM login_history WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        mysql.connection.commit()
    except Exception as e:
        mysql.connection.rollback()
        cursor.close()
        logger.exception("admin %s failed to delete user %s: %s", _uid(), user_id, e)
        return jsonify({'error': 'Failed to delete the account'}), 500
    cursor.close()

    logger.warning("admin %s DELETED user %s (%s) and all their data",
                   _uid(), user_id, target['username'])
    return jsonify({'message': f"Deleted {target['username']} and all their data"}), 200
