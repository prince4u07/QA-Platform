from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
import bcrypt
import os
import uuid
import re

from extensions import limiter

auth_bp = Blueprint('auth', __name__)
mysql = None

# In-memory storage for when database is unavailable (for testing)
test_users = {}

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
EMAIL_REGEX = r'^[^\s@]+@[^\s@]+\.[^\s@]+$'


def init_auth(app_mysql):
    global mysql
    mysql = app_mysql


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def is_valid_email(email):
    return bool(re.match(EMAIL_REGEX, email))


def use_database():
    """Check if database is available"""
    return mysql is not None and mysql.connection


# CHECK EMAIL
@auth_bp.route('/check-email', methods=['POST'])
def check_email():
    data = request.json
    email = data.get('email', '').strip().lower()
    if not email or not is_valid_email(email):
        return jsonify({'available': False, 'message': 'Invalid email format'}), 200
    
    try:
        if use_database():
            cursor = mysql.connection.cursor()
            cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
            user = cursor.fetchone()
            cursor.close()
            if user:
                return jsonify({'available': False, 'message': 'Email already registered'}), 200
        else:
            # Check in-memory storage
            if any(u['email'] == email for u in test_users.values()):
                return jsonify({'available': False, 'message': 'Email already registered'}), 200
        
        return jsonify({'available': True, 'message': 'Email available'}), 200
    except Exception as e:
        # NEVER claim "available" on a DB error — that lets a duplicate slip past
        # the uniqueness check and corrupts sign-up. Fail closed instead.
        current_app.logger.exception("check_email failed: %s", e)
        return jsonify({'available': False,
                        'message': 'Could not verify email right now. Please try again.'}), 503


# CHECK USERNAME
@auth_bp.route('/check-username', methods=['POST'])
def check_username():
    data = request.json
    username = data.get('username', '').strip()
    if not username or len(username) < 3:
        return jsonify({'available': False, 'message': 'Username must be at least 3 characters'}), 200
    
    try:
        if use_database():
            cursor = mysql.connection.cursor()
            cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
            user = cursor.fetchone()
            cursor.close()
            if user:
                return jsonify({'available': False, 'message': 'Username already taken'}), 200
        else:
            # Check in-memory storage
            if any(u['username'] == username for u in test_users.values()):
                return jsonify({'available': False, 'message': 'Username already taken'}), 200
        
        return jsonify({'available': True, 'message': 'Username available'}), 200
    except Exception as e:
        # Fail closed: a DB error must not masquerade as "username available".
        current_app.logger.exception("check_username failed: %s", e)
        return jsonify({'available': False,
                        'message': 'Could not verify username right now. Please try again.'}), 503


# REGISTER
@auth_bp.route('/register', methods=['POST'])
@limiter.limit("5 per minute")
def register():
    data = request.json
    username = data.get('username', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    role = data.get('role', 'tester')

    if not username:
        return jsonify({'error': 'Username is required', 'field': 'username'}), 400
    if len(username) < 3:
        return jsonify({'error': 'Username must be at least 3 characters', 'field': 'username'}), 400
    if not email:
        return jsonify({'error': 'Email is required', 'field': 'email'}), 400
    if not is_valid_email(email):
        return jsonify({'error': 'Please enter a valid email', 'field': 'email'}), 400
    if not password:
        return jsonify({'error': 'Password is required', 'field': 'password'}), 400
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters', 'field': 'password'}), 400

    try:
        if use_database():
            cursor = mysql.connection.cursor()
            cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cursor.fetchone():
                cursor.close()
                return jsonify({'error': 'Email already registered', 'field': 'email'}), 409

            cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
            if cursor.fetchone():
                cursor.close()
                return jsonify({'error': 'Username already taken', 'field': 'username'}), 409

            hashed_pw = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
            cursor.execute(
                "INSERT INTO users (username, email, password, role) VALUES (%s, %s, %s, %s)",
                (username, email, hashed_pw.decode('utf-8'), role)
            )
            mysql.connection.commit()
            cursor.close()
        else:
            # Use in-memory storage for testing
            user_id = str(uuid.uuid4())
            hashed_pw = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
            test_users[user_id] = {
                'id': user_id,
                'username': username,
                'email': email,
                'password': hashed_pw.decode('utf-8'),
                'role': role
            }
        
        return jsonify({'message': 'Account created successfully'}), 201
    except Exception as e:
        print(f"Register error: {e}")
        return jsonify({'error': 'Something went wrong. Please try again.'}), 500


# LOGIN
@auth_bp.route('/login', methods=['POST'])
@limiter.limit("10 per minute")
def login():
    data = request.json
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not email:
        return jsonify({'error': 'Email is required', 'field': 'email'}), 400
    if not is_valid_email(email):
        return jsonify({'error': 'Please enter a valid email', 'field': 'email'}), 400
    if not password:
        return jsonify({'error': 'Password is required', 'field': 'password'}), 400

    try:
        user = None
        
        if use_database():
            cursor = mysql.connection.cursor()
            cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
            user = cursor.fetchone()
            cursor.close()
        else:
            # Check in-memory storage
            for u in test_users.values():
                if u['email'] == email:
                    user = u
                    break
        
        if not user:
            return jsonify({'error': 'No account found with this email', 'field': 'email'}), 404

        stored_password = user['password']
        if isinstance(stored_password, str):
            stored_password = stored_password.encode('utf-8')

        if bcrypt.checkpw(password.encode('utf-8'), stored_password):
            access_token = create_access_token(identity=str(user['id']))
            return jsonify({
                'access_token': access_token,
                'user': {
                    'id': user['id'],
                    'username': user['username'],
                    'email': user['email'],
                    'role': user.get('role', 'tester'),
                    'profile_picture': user.get('profile_picture')
                }
            }), 200
        else:
            return jsonify({'error': 'Incorrect password', 'field': 'password'}), 401
    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({'error': 'Something went wrong. Please try again.'}), 500


# PROFILE GET
@auth_bp.route('/profile', methods=['GET'])
@jwt_required()
def profile():
    user_id = int(get_jwt_identity())
    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            "SELECT id, username, email, role, email_verified, profile_picture, created_at, updated_at "
            "FROM users WHERE id = %s",
            (user_id,)
        )
        user = cursor.fetchone()
        cursor.close()
        if not user:
            return jsonify({'error': 'User not found'}), 404
        return jsonify(user), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# PROFILE UPDATE
@auth_bp.route('/profile', methods=['PUT'])
@jwt_required()
def update_profile():
    user_id = int(get_jwt_identity())
    data = request.json
    username = data.get('username', '').strip()
    email = data.get('email', '').strip().lower()

    if not username or len(username) < 3:
        return jsonify({'error': 'Username must be at least 3 characters'}), 400
    if not is_valid_email(email):
        return jsonify({'error': 'Please enter a valid email'}), 400

    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            "SELECT id FROM users WHERE (username = %s OR email = %s) AND id != %s",
            (username, email, user_id)
        )
        if cursor.fetchone():
            cursor.close()
            return jsonify({'error': 'Username or email already taken'}), 409

        cursor.execute(
            "UPDATE users SET username = %s, email = %s WHERE id = %s",
            (username, email, user_id)
        )
        mysql.connection.commit()
        cursor.execute(
            "SELECT id, username, email, role, profile_picture FROM users WHERE id = %s",
            (user_id,)
        )
        updated = cursor.fetchone()
        cursor.close()
        return jsonify({'message': 'Profile updated', 'user': updated}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# PROFILE PICTURE UPLOAD
@auth_bp.route('/profile/picture', methods=['POST'])
@jwt_required()
def upload_picture():
    user_id = int(get_jwt_identity())
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    if not allowed_file(file.filename):
        return jsonify({'error': 'Only PNG, JPG, JPEG, GIF, WEBP allowed'}), 400

    ext = file.filename.rsplit('.', 1)[1].lower()
    new_filename = f"user_{user_id}_{uuid.uuid4().hex[:8]}.{ext}"
    upload_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'avatars')
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, new_filename)
    file.save(file_path)
    db_path = f"/static/uploads/avatars/{new_filename}"

    try:
        cursor = mysql.connection.cursor()
        cursor.execute("UPDATE users SET profile_picture = %s WHERE id = %s", (db_path, user_id))
        mysql.connection.commit()
        cursor.close()
        return jsonify({'message': 'Picture uploaded', 'profile_picture': db_path}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# LOGIN HISTORY
@auth_bp.route('/login-history', methods=['GET'])
@jwt_required()
def login_history():
    user_id = int(get_jwt_identity())
    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            "SELECT ip_address, user_agent, success, logged_in_at FROM login_history "
            "WHERE user_id = %s ORDER BY logged_in_at DESC LIMIT 10",
            (user_id,)
        )
        history = cursor.fetchall()
        cursor.close()
        return jsonify(history), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500