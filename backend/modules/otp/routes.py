"""
OTP Session Management - Chunk 4B-2.

When a test run requires OTP, the runner creates an otp_session record
with status='waiting' and polls for the user-submitted code.

Endpoints:
  GET  /api/runner/run/<run_id>/otp-status   - Frontend polls to check state
  POST /api/runner/run/<run_id>/submit-otp   - User submits the OTP code
  POST /api/runner/run/<run_id>/cancel-otp   - User cancels (test will fail)
"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timedelta
import time

otp_bp = Blueprint('otp', __name__)
mysql = None

# How long the runner will wait for an OTP before giving up (seconds)
OTP_WAIT_TIMEOUT = 180  # 3 minutes

# How long an active OTP session is valid before expiring
OTP_SESSION_EXPIRY = 300  # 5 minutes


def init_otp(app_mysql):
    global mysql
    mysql = app_mysql


def _user_owns_run(user_id, run_id):
    """Verify the user owns the test run."""
    cursor = mysql.connection.cursor()
    cursor.execute(
        """SELECT tr.id FROM test_runs tr
           JOIN test_cases tc ON tr.test_case_id = tc.id
           JOIN projects p ON tc.project_id = p.id
           WHERE tr.id = %s AND p.user_id = %s""",
        (run_id, user_id)
    )
    result = cursor.fetchone()
    cursor.close()
    return result is not None


# ============================================================
# HELPER FUNCTIONS - Called by the runner internally (Chunk 4B-4)
# ============================================================

def create_otp_session(run_id, message="Waiting for user to enter OTP"):
    """
    Called by the runner when it detects OTP is needed.
    Creates an otp_sessions row and returns the session id.
    """
    cursor = mysql.connection.cursor()
    expires_at = datetime.now() + timedelta(seconds=OTP_SESSION_EXPIRY)
    cursor.execute(
        """INSERT INTO otp_sessions (test_run_id, status, message, expires_at)
           VALUES (%s, 'waiting', %s, %s)""",
        (run_id, message[:500], expires_at)
    )
    mysql.connection.commit()
    session_id = cursor.lastrowid
    cursor.close()
    print(f"[OTP] Created session {session_id} for run {run_id}, waiting for user...")
    return session_id


def wait_for_otp_code(session_id, timeout=OTP_WAIT_TIMEOUT):
    """
    Blocking call - polls the database every 1 second until:
    - User submits the OTP (returns the code)
    - User cancels (returns None with cancelled=True)
    - Timeout exceeded (returns None with timeout=True)
    """
    print(f"[OTP] Runner waiting for OTP code (session {session_id}, timeout {timeout}s)...")
    start = time.time()
    while time.time() - start < timeout:
        cursor = mysql.connection.cursor()
        cursor.execute(
            "SELECT status, otp_code FROM otp_sessions WHERE id = %s",
            (session_id,)
        )
        row = cursor.fetchone()
        cursor.close()

        if not row:
            return {'success': False, 'code': None, 'reason': 'session_not_found'}

        if row['status'] == 'submitted' and row['otp_code']:
            # User submitted - mark as completed
            mark_otp_completed(session_id)
            print(f"[OTP] Code received for session {session_id}: {row['otp_code'][:2]}***")
            return {'success': True, 'code': row['otp_code'], 'reason': 'submitted'}

        if row['status'] == 'cancelled':
            print(f"[OTP] User cancelled session {session_id}")
            return {'success': False, 'code': None, 'reason': 'cancelled'}

        time.sleep(1.0)

    # Timeout - mark session as timed out
    cursor = mysql.connection.cursor()
    cursor.execute(
        "UPDATE otp_sessions SET status='timeout' WHERE id = %s AND status='waiting'",
        (session_id,)
    )
    mysql.connection.commit()
    cursor.close()
    print(f"[OTP] Session {session_id} timed out after {timeout}s")
    return {'success': False, 'code': None, 'reason': 'timeout'}


def mark_otp_completed(session_id):
    """Mark an OTP session as completed (after runner used the code)."""
    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            "UPDATE otp_sessions SET status='completed' WHERE id = %s",
            (session_id,)
        )
        mysql.connection.commit()
        cursor.close()
    except Exception as e:
        print(f"[OTP] Failed to mark completed: {e}")


# ============================================================
# ENDPOINTS - Called by the frontend
# ============================================================

@otp_bp.route('/run/<int:run_id>/otp-status', methods=['GET'])
@jwt_required()
def get_otp_status(run_id):
    """
    Polled by frontend every ~2 seconds during a test run.
    Returns: { active: true, status: 'waiting', message: '...', session_id: X }
    or:      { active: false }
    """
    user_id = int(get_jwt_identity())
    if not _user_owns_run(user_id, run_id):
        return jsonify({'error': 'Run not found'}), 404

    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """SELECT id, status, message, created_at, expires_at
               FROM otp_sessions
               WHERE test_run_id = %s
               ORDER BY id DESC LIMIT 1""",
            (run_id,)
        )
        session = cursor.fetchone()
        cursor.close()

        if not session:
            return jsonify({'active': False}), 200

        # Auto-expire if past expiry time
        if session.get('expires_at') and datetime.now() > session['expires_at']:
            if session['status'] == 'waiting':
                cursor = mysql.connection.cursor()
                cursor.execute(
                    "UPDATE otp_sessions SET status='timeout' WHERE id = %s",
                    (session['id'],)
                )
                mysql.connection.commit()
                cursor.close()
                session['status'] = 'timeout'

        return jsonify({
            'active': session['status'] == 'waiting',
            'status': session['status'],
            'message': session.get('message', ''),
            'session_id': session['id'],
            'created_at': session['created_at'].isoformat() if session.get('created_at') else None,
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@otp_bp.route('/run/<int:run_id>/submit-otp', methods=['POST'])
@jwt_required()
def submit_otp(run_id):
    """
    Frontend sends the OTP code typed by the user.
    Body: { otp_code: "847362" }
    """
    user_id = int(get_jwt_identity())
    if not _user_owns_run(user_id, run_id):
        return jsonify({'error': 'Run not found'}), 404

    data = request.get_json() or {}
    otp_code = (data.get('otp_code') or '').strip()

    if not otp_code:
        return jsonify({'error': 'OTP code required'}), 400
    if len(otp_code) > 20:
        return jsonify({'error': 'OTP code too long'}), 400

    try:
        cursor = mysql.connection.cursor()
        # Find the active waiting session for this run
        cursor.execute(
            """SELECT id FROM otp_sessions
               WHERE test_run_id = %s AND status = 'waiting'
               ORDER BY id DESC LIMIT 1""",
            (run_id,)
        )
        session = cursor.fetchone()

        if not session:
            cursor.close()
            return jsonify({'error': 'No waiting OTP session for this run'}), 404

        # Submit the code
        cursor.execute(
            """UPDATE otp_sessions
               SET status='submitted', otp_code=%s, submitted_at=NOW()
               WHERE id = %s""",
            (otp_code, session['id'])
        )
        mysql.connection.commit()
        cursor.close()

        return jsonify({
            'message': 'OTP submitted, test will continue',
            'session_id': session['id']
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@otp_bp.route('/run/<int:run_id>/cancel-otp', methods=['POST'])
@jwt_required()
def cancel_otp(run_id):
    """User cancels the OTP wait - test will fail."""
    user_id = int(get_jwt_identity())
    if not _user_owns_run(user_id, run_id):
        return jsonify({'error': 'Run not found'}), 404

    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """UPDATE otp_sessions
               SET status='cancelled'
               WHERE test_run_id = %s AND status = 'waiting'""",
            (run_id,)
        )
        mysql.connection.commit()
        cursor.close()
        return jsonify({'message': 'OTP cancelled'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500