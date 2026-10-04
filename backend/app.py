import os
from flask import Flask, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_mysqldb import MySQL
import MySQLdb
from config import Config
import logging

from logging_config import configure_logging
from extensions import limiter

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config.from_object(Config)

configure_logging(app)
logger = logging.getLogger(__name__)

limiter.init_app(app)

# Initialize extensions
CORS(app, resources={r"/api/*": {"origins": ["http://localhost:5173", "http://localhost:5174", "http://localhost:5175", "http://127.0.0.1:5173", "http://127.0.0.1:5174", "http://127.0.0.1:5175"]}},
     supports_credentials=True,
     allow_headers=["Content-Type", "Authorization"],
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])

jwt = JWTManager(app)

# Try to initialize MySQL, but continue if it fails
try:
    mysql = MySQL(app)
except Exception as e:
    logger.warning("MySQL connection failed: %s", e)
    mysql = None

# Register blueprints
from modules.auth.routes import auth_bp, init_auth
from modules.projects.routes import projects_bp, init_projects
from modules.testcases.routes import testcases_bp, init_testcases
from modules.bugs.routes import bugs_bp,  init_bugs
from modules.runner.routes import runner_bp, init_runner
from modules.ai.routes import ai_bp, init_ai
from modules.reports.routes import reports_bp, init_reports
from modules.otp.routes import otp_bp, init_otp
from modules.admin.routes import admin_bp, init_admin
from modules.plugin.routes import plugin_bp, init_plugin

# Initialize modules with MySQL
init_auth(mysql)
init_projects(mysql)
init_testcases(mysql)
init_runner(mysql)
init_ai(mysql)
init_bugs(mysql)
init_reports(mysql)
init_otp(mysql)
init_admin(mysql)
init_plugin(mysql)

# Background job runner for automated crawls (non-blocking /run/<id>/async).
from modules.runner.jobs import job_manager
job_manager.init(app, max_workers=app.config.get('RUNNER_WORKERS', 4))

app.register_blueprint(auth_bp, url_prefix='/api/auth')
app.register_blueprint(projects_bp, url_prefix='/api/projects')
app.register_blueprint(testcases_bp, url_prefix='/api/testcases')
app.register_blueprint(bugs_bp, url_prefix='/api/bugs')
app.register_blueprint(runner_bp, url_prefix='/api/runner')
app.register_blueprint(reports_bp, url_prefix='/api/reports')
app.register_blueprint(ai_bp, url_prefix='/api/ai')
app.register_blueprint(otp_bp, url_prefix='/api/otp')
app.register_blueprint(admin_bp, url_prefix='/api/admin')
app.register_blueprint(plugin_bp, url_prefix='/api/plugin')


@jwt.token_in_blocklist_loader
def reject_deactivated_accounts(_jwt_header, jwt_payload):
    """
    Refuse a token whose account has been deactivated or deleted.

    Tokens in this deployment never expire, so without this a deactivated
    user would keep working indefinitely on the token they already hold.
    Runs on every authenticated request, which is one primary-key lookup.
    """
    identity = jwt_payload.get('sub')
    if not identity or mysql is None:
        return False
    try:
        cursor = mysql.connection.cursor()
        cursor.execute("SELECT is_active FROM users WHERE id = %s", (identity,))
        row = cursor.fetchone()
        cursor.close()
    except Exception:
        # If the database is unreachable, let the request through to the
        # normal handler, which reports the outage properly. Failing closed
        # here would report every outage as an authentication problem.
        return False
    if row is None:
        return True                     # account deleted: the token is void
    return not row.get('is_active', 1)

@app.errorhandler(MySQLdb.OperationalError)
def handle_database_unavailable(error):
    """
    The database being down is an operational problem, not a bug in a request.

    Without this, every single request while MySQL is stopped logged a 20-line
    traceback and told the user "Internal server error", which says nothing
    about what is actually wrong or how to fix it. One clear line each instead.
    """
    logger.error("Database unavailable: %s", error)
    return jsonify({
        'error': 'Cannot reach the database. Is the MySQL service running?',
        'detail': str(error)[:200],
    }), 503


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    # Log the full traceback server-side; never leak internals to the client.
    from werkzeug.exceptions import HTTPException
    if isinstance(error, HTTPException):
        return error
    logger.exception("Unhandled exception: %s", error)
    return jsonify({'error': 'Internal server error'}), 500


@app.errorhandler(500)
def handle_error(error):
    logger.error("500 error: %s", error)
    return jsonify({'error': 'Internal server error'}), 500


@app.errorhandler(429)
def handle_rate_limit(error):
    return jsonify({
        'error': 'Too many requests. Please slow down and try again shortly.',
        'detail': str(error.description),
    }), 429

if __name__ == '__main__':
    app.run(
        debug=app.config.get('DEBUG', False),
        host=os.getenv('FLASK_HOST', '127.0.0.1'),
        port=int(os.getenv('FLASK_PORT', '5000')),
        threaded=True
    )