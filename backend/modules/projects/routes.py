import os
import shutil
import zipfile
import logging
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.utils import secure_filename

from . import session_capture

logger = logging.getLogger(__name__)

projects_bp = Blueprint('projects', __name__)
mysql = None

MAX_ZIP_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_ENVS = {'dev', 'staging', 'prod'}
UPLOAD_BASE = os.path.join('static', 'uploads', 'projects')
SESSIONS_DIR = os.path.join('static', 'uploads', 'sessions')
PUBLIC_HOST = 'http://localhost:5000'  # used to build base_url for uploaded ZIPs


def init_projects(app_mysql):
    global mysql
    mysql = app_mysql


# ---------- helpers ----------

def _uid():
    return int(get_jwt_identity())


def _row_to_dict(r):
    # Cursor is DictCursor (see config.MYSQL_CURSORCLASS) -> rows are dicts keyed by column.
    return {
        'id': r['id'],
        'name': r['name'],
        'description': r['description'],
        'base_url': r['base_url'],
        'environment': r['environment'],
        'source_type': r['source_type'],
        'upload_path': r['upload_path'],
        'created_at': r['created_at'].isoformat() if r['created_at'] else None,
        'has_active_session': bool(r['has_active_session']) if r['has_active_session'] is not None else False,
        'session_captured_at': r['session_captured_at'].isoformat() if r['session_captured_at'] else None,
    }


def _get_owned(project_id, user_id):
    cur = mysql.connection.cursor()
    cur.execute(
        """SELECT id, name, description, base_url, environment,
                  source_type, upload_path, created_at,
                  has_active_session, session_captured_at
           FROM projects
           WHERE id=%s AND user_id=%s""",
        (project_id, user_id)
    )
    row = cur.fetchone()
    cur.close()
    return _row_to_dict(row) if row else None


def _clear_session_file(project_id):
    path = os.path.join(SESSIONS_DIR, f'{project_id}.json')
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# ---------- LIST ----------

@projects_bp.route('', methods=['GET'])
@projects_bp.route('/', methods=['GET'])
@jwt_required()
def list_projects():
    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    
    # Validate pagination parameters
    page = max(1, page)
    per_page = min(100, max(1, per_page))  # Limit to max 100 per page
    
    offset = (page - 1) * per_page
    user_id = _uid()
    
    # Get total count
    cur = mysql.connection.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM projects WHERE user_id=%s", (user_id,))
    total_count = cur.fetchone()['c']
    
    # Get paginated results
    cur.execute(
        """SELECT id, name, description, base_url, environment,
                  source_type, upload_path, created_at,
                  has_active_session, session_captured_at
           FROM projects
           WHERE user_id=%s
           ORDER BY created_at DESC
           LIMIT %s OFFSET %s""",
        (user_id, per_page, offset)
    )
    rows = cur.fetchall()
    cur.close()
    
    return jsonify({
        'data': [_row_to_dict(r) for r in rows],
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': total_count,
            'pages': (total_count + per_page - 1) // per_page
        }
    }), 200


# ---------- CREATE (URL) ----------

@projects_bp.route('', methods=['POST'])
@projects_bp.route('/', methods=['POST'])
@jwt_required()
def create_project():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    base_url = (data.get('base_url') or '').strip()
    description = (data.get('description') or '').strip()
    environment = (data.get('environment') or 'dev').strip().lower()

    if len(name) < 3:
        return jsonify({'error': 'Name must be at least 3 characters'}), 400
    if not (base_url.startswith('http://') or base_url.startswith('https://')):
        return jsonify({'error': 'Valid URL required (http:// or https://)'}), 400
    if environment not in ALLOWED_ENVS:
        return jsonify({'error': 'Invalid environment'}), 400

    cur = mysql.connection.cursor()
    cur.execute(
        """INSERT INTO projects
           (user_id, name, description, base_url, environment, source_type, has_active_session)
           VALUES (%s, %s, %s, %s, %s, 'url', FALSE)""",
        (_uid(), name, description, base_url, environment)
    )
    project_id = cur.lastrowid
    mysql.connection.commit()
    cur.close()
    return jsonify({'id': project_id, 'message': 'Project created'}), 201


# ---------- UPDATE ----------

@projects_bp.route('/<int:project_id>', methods=['PUT'])
@jwt_required()
def update_project(project_id):
    project = _get_owned(project_id, _uid())
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = request.get_json() or {}
    name = (data.get('name') or project['name']).strip()
    description = (data.get('description') or '').strip()
    environment = (data.get('environment') or project['environment']).strip().lower()

    if len(name) < 3:
        return jsonify({'error': 'Name must be at least 3 characters'}), 400
    if environment not in ALLOWED_ENVS:
        return jsonify({'error': 'Invalid environment'}), 400

    new_base_url = project['base_url']
    url_changed = False
    if project['source_type'] == 'url' and 'base_url' in data:
        candidate = (data.get('base_url') or '').strip()
        if not (candidate.startswith('http://') or candidate.startswith('https://')):
            return jsonify({'error': 'Valid URL required'}), 400
        if candidate != project['base_url']:
            url_changed = True
        new_base_url = candidate

    cur = mysql.connection.cursor()
    if url_changed:
        # URL changed -> existing session no longer valid for new origin
        _clear_session_file(project_id)
        cur.execute(
            """UPDATE projects
               SET name=%s, description=%s, base_url=%s, environment=%s,
                   has_active_session=FALSE, session_captured_at=NULL
               WHERE id=%s""",
            (name, description, new_base_url, environment, project_id)
        )
    else:
        cur.execute(
            """UPDATE projects
               SET name=%s, description=%s, base_url=%s, environment=%s
               WHERE id=%s""",
            (name, description, new_base_url, environment, project_id)
        )
    mysql.connection.commit()
    cur.close()

    msg = 'Project updated (session cleared - URL changed)' if url_changed else 'Project updated'
    return jsonify({'message': msg}), 200


# ---------- DELETE ----------

@projects_bp.route('/<int:project_id>', methods=['DELETE'])
@jwt_required()
def delete_project(project_id):
    user_id = _uid()
    project = _get_owned(project_id, user_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    cur = mysql.connection.cursor()
    try:
        # Delete child rows first (no FK cascade assumed).
        # bugs / test_runs / crawled_pages reference the project indirectly,
        # via test_case_id / test_run_id - only test_cases & ai_suggestions
        # have a direct project_id column.
        cur.execute(
            "DELETE FROM bugs WHERE test_case_id IN "
            "(SELECT id FROM test_cases WHERE project_id=%s)",
            (project_id,)
        )
        cur.execute(
            """DELETE FROM crawled_pages WHERE test_run_id IN (
                   SELECT tr.id FROM test_runs tr
                   JOIN test_cases tc ON tr.test_case_id = tc.id
                   WHERE tc.project_id=%s)""",
            (project_id,)
        )
        cur.execute(
            "DELETE FROM test_runs WHERE test_case_id IN "
            "(SELECT id FROM test_cases WHERE project_id=%s)",
            (project_id,)
        )
        cur.execute("DELETE FROM ai_suggestions WHERE project_id=%s", (project_id,))
        cur.execute("DELETE FROM test_cases WHERE project_id=%s", (project_id,))
        cur.execute("DELETE FROM projects WHERE id=%s AND user_id=%s",
                    (project_id, user_id))
        mysql.connection.commit()
    except Exception as e:
        mysql.connection.rollback()
        cur.close()
        return jsonify({'error': f'Failed to delete: {str(e)}'}), 500
    cur.close()

    # Best-effort file cleanup
    project_dir = os.path.join(UPLOAD_BASE, str(project_id))
    if os.path.isdir(project_dir):
        shutil.rmtree(project_dir, ignore_errors=True)
    _clear_session_file(project_id)

    return jsonify({'message': 'Project deleted'}), 200


# ---------- UPLOAD (ZIP) ----------

@projects_bp.route('/upload', methods=['POST'])
@jwt_required()
def upload_project():
    user_id = _uid()

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    name = (request.form.get('name') or '').strip()
    description = (request.form.get('description') or '').strip()
    environment = (request.form.get('environment') or 'dev').strip().lower()

    if len(name) < 3:
        return jsonify({'error': 'Name must be at least 3 characters'}), 400
    if environment not in ALLOWED_ENVS:
        return jsonify({'error': 'Invalid environment'}), 400
    if not file or not file.filename.lower().endswith('.zip'):
        return jsonify({'error': 'Only ZIP files allowed'}), 400

    # Size check
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > MAX_ZIP_SIZE:
        return jsonify({'error': 'File too large (50MB max)'}), 400

    # Insert row first to get an ID for the folder name
    cur = mysql.connection.cursor()
    cur.execute(
        """INSERT INTO projects
           (user_id, name, description, base_url, environment, source_type, has_active_session)
           VALUES (%s, %s, %s, %s, %s, 'upload', FALSE)""",
        (user_id, name, description, '', environment)
    )
    project_id = cur.lastrowid
    mysql.connection.commit()

    project_dir = os.path.join(UPLOAD_BASE, str(project_id))
    extracted_dir = os.path.join(project_dir, 'extracted')
    os.makedirs(extracted_dir, exist_ok=True)
    zip_path = os.path.join(project_dir, secure_filename(file.filename))

    try:
        file.save(zip_path)

        with zipfile.ZipFile(zip_path, 'r') as zf:
            # Reject path traversal
            for member in zf.namelist():
                if member.startswith('/') or '..' in member.replace('\\', '/').split('/'):
                    raise ValueError('ZIP contains unsafe paths')
            zf.extractall(extracted_dir)

        # Find index.html (root or one level deep)
        index_dir = None
        for root, _, files in os.walk(extracted_dir):
            if 'index.html' in files:
                index_dir = root
                break

        if index_dir is None:
            raise ValueError('ZIP must contain an index.html file')

        # Build paths
        rel_to_static = os.path.relpath(index_dir, 'static').replace('\\', '/')
        upload_path = '/static/' + rel_to_static
        base_url_local = f'{PUBLIC_HOST}{upload_path}/index.html'

        cur.execute(
            "UPDATE projects SET upload_path=%s, base_url=%s WHERE id=%s",
            (upload_path, base_url_local, project_id)
        )
        mysql.connection.commit()
        cur.close()

        return jsonify({
            'id': project_id,
            'upload_path': upload_path,
            'base_url': base_url_local,
            'message': 'Project uploaded'
        }), 201

    except (zipfile.BadZipFile, ValueError) as e:
        cur.execute("DELETE FROM projects WHERE id=%s", (project_id,))
        mysql.connection.commit()
        cur.close()
        shutil.rmtree(project_dir, ignore_errors=True)
        return jsonify({'error': str(e) or 'Invalid ZIP file'}), 400
    except Exception as e:
        cur.execute("DELETE FROM projects WHERE id=%s", (project_id,))
        mysql.connection.commit()
        cur.close()
        shutil.rmtree(project_dir, ignore_errors=True)
        return jsonify({'error': f'Upload failed: {str(e)}'}), 500


# ---------- STATS ----------

@projects_bp.route('/stats', methods=['GET'])
@jwt_required()
def get_stats():
    """Get overall dashboard stats for the user"""
    user_id = _uid()
    cur = mysql.connection.cursor()
    
    try:
        # Total projects
        cur.execute("SELECT COUNT(*) as c FROM projects WHERE user_id=%s", (user_id,))
        total_projects = cur.fetchone()['c']
        
        # Total test cases
        cur.execute(
            """SELECT COUNT(*) as c FROM test_cases tc
               JOIN projects p ON tc.project_id = p.id
               WHERE p.user_id=%s""",
            (user_id,)
        )
        total_testcases = cur.fetchone()['c']
        
        # Open bugs
        cur.execute(
            """SELECT COUNT(*) as c FROM bugs b
               LEFT JOIN test_cases tc ON b.test_case_id = tc.id
               LEFT JOIN projects p ON tc.project_id = p.id
               WHERE (p.user_id=%s OR b.assigned_to=%s)
               AND b.status IN ('Open', 'In Progress')""",
            (user_id, user_id)
        )
        open_bugs = cur.fetchone()['c']
        
        # Tests passed (count of test_runs with status='Pass')
        cur.execute(
            """SELECT COUNT(*) as c FROM test_runs tr
               JOIN test_cases tc ON tr.test_case_id = tc.id
               JOIN projects p ON tc.project_id = p.id
               WHERE p.user_id=%s AND tr.status='Pass'""",
            (user_id,)
        )
        tests_passed = cur.fetchone()['c']
        
        cur.close()
        
        return jsonify({
            'total_projects': total_projects,
            'total_testcases': total_testcases,
            'open_bugs': open_bugs,
            'tests_passed': tests_passed
        }), 200
    
    except Exception as e:
        cur.close()
        return jsonify({'error': f'Failed to fetch stats: {str(e)}'}), 500


# ============================================================
# MANUAL LOGIN + SESSION CAPTURE (Chunk D-2)
# ============================================================

@projects_bp.route('/<int:project_id>/start-login', methods=['POST'])
@jwt_required()
def start_login(project_id):
    """Open a headed Chromium window so the user can log in manually."""
    project = _get_owned(project_id, _uid())
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    if project['source_type'] != 'url':
        return jsonify({'error': 'Login capture is only available for URL projects'}), 400
    if not project['base_url']:
        return jsonify({'error': 'Project has no URL to open'}), 400

    logger.info("start_login: project %s ('%s'), base_url=%s",
                project_id, project.get('name'), project['base_url'])
    ok, message = session_capture.start_login(project_id, project['base_url'])
    if not ok:
        logger.warning("start_login failed for project %s: %s", project_id, message)
        return jsonify({'error': message}), 500
    return jsonify({'message': message}), 200


@projects_bp.route('/<int:project_id>/save-session', methods=['POST'])
@jwt_required()
def save_session(project_id):
    """User clicked 'I am logged in' - persist cookies and mark the project."""
    user_id = _uid()
    project = _get_owned(project_id, user_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    logger.info("save_session: project %s ('%s')", project_id, project.get('name'))
    ok, message, _ = session_capture.save_session(project_id)
    if not ok:
        logger.warning("save_session failed for project %s: %s", project_id, message)
        return jsonify({'error': message}), 409

    cur = mysql.connection.cursor()
    cur.execute(
        """UPDATE projects
           SET has_active_session=TRUE, session_captured_at=NOW()
           WHERE id=%s AND user_id=%s""",
        (project_id, user_id)
    )
    mysql.connection.commit()
    cur.close()

    return jsonify({'message': message, 'has_active_session': True}), 200


@projects_bp.route('/<int:project_id>/cancel-login', methods=['POST'])
@jwt_required()
def cancel_login(project_id):
    """User closed the login flow without saving - close the browser."""
    project = _get_owned(project_id, _uid())
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    _, message = session_capture.cancel_login(project_id)
    return jsonify({'message': message}), 200


@projects_bp.route('/<int:project_id>/login-status', methods=['GET'])
@jwt_required()
def login_status(project_id):
    """Poll the state of an in-progress login window."""
    project = _get_owned(project_id, _uid())
    if not project:
        return jsonify({'error': 'Project not found'}), 404
    return jsonify(session_capture.login_status(project_id)), 200