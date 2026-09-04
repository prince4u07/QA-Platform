"""
Test Cases API - Manage manual and automated test cases.

CHUNK 1 ADDITION: Now supports crawl_pages and max_pages fields
for multi-page testing.
"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

testcases_bp = Blueprint('testcases', __name__)
mysql = None


def init_testcases(app_mysql):
    global mysql
    mysql = app_mysql


def user_owns_project(user_id, project_id):
    cursor = mysql.connection.cursor()
    cursor.execute(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s",
        (project_id, user_id)
    )
    result = cursor.fetchone()
    cursor.close()
    return result is not None


def user_owns_testcase(user_id, testcase_id):
    cursor = mysql.connection.cursor()
    cursor.execute(
        """SELECT tc.id FROM test_cases tc
           JOIN projects p ON tc.project_id = p.id
           WHERE tc.id = %s AND p.user_id = %s""",
        (testcase_id, user_id)
    )
    result = cursor.fetchone()
    cursor.close()
    return result is not None


def validate_test_type(data):
    """Return a valid test_type or raise ValueError.

    There is no framework to choose or store: the platform has exactly one
    runner (Playwright) for automated runs and none for manual ones. The old
    automation_framework column could only ever say 'playwright' or 'none',
    nothing ever read it back, and the UI never showed it, so it was dropped.
    """
    test_type = data.get('test_type') or 'manual'
    if test_type not in ('manual', 'automated'):
        raise ValueError('Test type must be manual or automated')
    return test_type


# ============================================================
# LIST TEST CASES (optional filter by project)
# ============================================================

@testcases_bp.route('', methods=['GET'])
@jwt_required()
def list_testcases():
    user_id = int(get_jwt_identity())
    project_id = request.args.get('project_id')

    try:
        cursor = mysql.connection.cursor()
        if project_id:
            if not user_owns_project(user_id, project_id):
                return jsonify({'error': 'Project not found'}), 404
            cursor.execute(
                """SELECT tc.*, p.name AS project_name
                   FROM test_cases tc
                   JOIN projects p ON tc.project_id = p.id
                   WHERE tc.project_id = %s
                   ORDER BY tc.id DESC""",
                (project_id,)
            )
        else:
            cursor.execute(
                """SELECT tc.*, p.name AS project_name
                   FROM test_cases tc
                   JOIN projects p ON tc.project_id = p.id
                   WHERE p.user_id = %s
                   ORDER BY tc.id DESC""",
                (user_id,)
            )
        rows = cursor.fetchall()
        cursor.close()
        return jsonify(rows), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# CREATE TEST CASE (now accepts crawl_pages and max_pages)
# ============================================================

@testcases_bp.route('', methods=['POST'])
@jwt_required()
def create_testcase():
    user_id = int(get_jwt_identity())
    data = request.json or {}

    project_id = data.get('project_id')
    if not project_id or not user_owns_project(user_id, project_id):
        return jsonify({'error': 'Invalid project'}), 400

    title = (data.get('title') or '').strip()
    if len(title) < 3:
        return jsonify({'error': 'Title must be at least 3 characters'}), 400

    try:
        test_type = validate_test_type(data)
    except ValueError as error:
        return jsonify({'error': str(error)}), 400

    steps = (data.get('steps') or '').strip()
    if test_type == 'manual' and not steps:
        return jsonify({'error': 'Steps are required for manual tests'}), 400

    expected_result = (data.get('expected_result') or '').strip()
    if test_type == 'manual' and not expected_result:
        return jsonify({'error': 'Expected result is required'}), 400

    description = (data.get('description') or '').strip()
    priority = data.get('priority') or 'Medium'

    # Automated runs crawl the site by contract. The UI never exposes crawling
    # as a separate yes/no — an automated test always crawls up to max_pages —
    # so trusting the client here would let a request ask for an automated test
    # that silently audits a single page. Derive it exactly like the UI does.
    crawl_pages = (test_type == 'automated')
    max_pages = int(data.get('max_pages') or 10)
    # Clamp max_pages to safe range
    if max_pages < 1:
        max_pages = 1
    if max_pages > 100:
        max_pages = 100
    # Clicking controls on a live site can submit a form or log the crawler
    # out, so it only ever happens when the test case asks for it.
    check_dead_controls = bool(data.get('check_dead_controls', False))

    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """INSERT INTO test_cases (
                project_id, title, description, steps, expected_result,
                priority, status, test_type,
                crawl_pages, max_pages, check_dead_controls, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
            (project_id, title, description, steps, expected_result,
             priority, 'Pending', test_type,
             crawl_pages, max_pages, check_dead_controls)
        )
        mysql.connection.commit()
        tc_id = cursor.lastrowid
        cursor.close()
        return jsonify({'id': tc_id, 'message': 'Test case created'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# UPDATE TEST CASE
# ============================================================

@testcases_bp.route('/<int:tc_id>', methods=['PUT'])
@jwt_required()
def update_testcase(tc_id):
    user_id = int(get_jwt_identity())
    if not user_owns_testcase(user_id, tc_id):
        return jsonify({'error': 'Test case not found'}), 404

    data = request.json or {}
    title = (data.get('title') or '').strip()
    if len(title) < 3:
        return jsonify({'error': 'Title must be at least 3 characters'}), 400

    steps = (data.get('steps') or '').strip()
    expected_result = (data.get('expected_result') or '').strip()
    test_type = data.get('test_type') or 'manual'
    if test_type == 'manual' and not steps:
        return jsonify({'error': 'Steps are required for manual tests'}), 400
    if test_type == 'manual' and not expected_result:
        return jsonify({'error': 'Expected result is required'}), 400

    try:
        test_type = validate_test_type(data)
    except ValueError as error:
        return jsonify({'error': str(error)}), 400

    # Same derivation as create: an automated test crawls, a manual one is
    # walked by a person. The client's own crawl_pages is never trusted.
    crawl_pages = (test_type == 'automated')
    max_pages = int(data.get('max_pages') or 10)
    if max_pages < 1:
        max_pages = 1
    if max_pages > 100:
        max_pages = 100
    check_dead_controls = bool(data.get('check_dead_controls', False))

    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """UPDATE test_cases SET
                title = %s, description = %s, steps = %s, expected_result = %s,
                priority = %s, test_type = %s,
                crawl_pages = %s, max_pages = %s,
                check_dead_controls = %s
                WHERE id = %s""",
            (
                title,
                (data.get('description') or '').strip(),
                steps, expected_result,
                data.get('priority') or 'Medium',
                test_type,
                crawl_pages, max_pages, check_dead_controls,
                tc_id
            )
        )
        mysql.connection.commit()
        cursor.close()
        return jsonify({'message': 'Test case updated'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# DELETE
# ============================================================

@testcases_bp.route('/<int:tc_id>', methods=['DELETE'])
@jwt_required()
def delete_testcase(tc_id):
    user_id = int(get_jwt_identity())
    if not user_owns_testcase(user_id, tc_id):
        return jsonify({'error': 'Test case not found'}), 404

    try:
        cursor = mysql.connection.cursor()
        cursor.execute("DELETE FROM test_cases WHERE id = %s", (tc_id,))
        mysql.connection.commit()
        cursor.close()
        return jsonify({'message': 'Test case deleted'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# STATS
# ============================================================

@testcases_bp.route('/stats', methods=['GET'])
@jwt_required()
def stats():
    user_id = int(get_jwt_identity())
    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """SELECT tc.status, COUNT(*) AS count
               FROM test_cases tc
               JOIN projects p ON tc.project_id = p.id
               WHERE p.user_id = %s
               GROUP BY tc.status""",
            (user_id,)
        )
        rows = cursor.fetchall()
        cursor.close()

        result = {'Pass': 0, 'Fail': 0, 'Pending': 0, 'total': 0}
        for r in rows:
            result[r['status']] = r['count']
            result['total'] += r['count']

        return jsonify(result), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
