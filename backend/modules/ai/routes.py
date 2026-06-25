"""
AI API Routes - Endpoints for AI-powered features.

POST /api/ai/analyze-bug/<bug_id>           - AI explanation for a bug
POST /api/ai/suggest-tests/<project_id>     - AI suggests test cases
POST /api/ai/suggest-fix                    - AI suggests fix for a specific finding
POST /api/ai/chat                           - Conversational AI assistant
"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from modules.ai.service import analyze_bug, suggest_test_cases, suggest_fix, chat
from extensions import limiter

ai_bp = Blueprint('ai', __name__)
mysql = None


def init_ai(app_mysql):
    global mysql
    mysql = app_mysql


# ============================================================
# OWNERSHIP HELPERS
# ============================================================

def get_user_bug(user_id, bug_id):
    """Returns the bug if user owns it (via project chain), else None"""
    cursor = mysql.connection.cursor()
    cursor.execute(
        """SELECT b.id, b.title, b.description, b.severity, b.status, b.category,
                  b.steps_to_reproduce, b.expected_behavior, b.actual_behavior
           FROM bugs b
           LEFT JOIN test_cases tc ON b.test_case_id = tc.id
           LEFT JOIN projects p ON tc.project_id = p.id
           WHERE b.id = %s AND (p.user_id = %s OR b.assigned_to = %s)""",
        (bug_id, user_id, user_id)
    )
    result = cursor.fetchone()
    cursor.close()
    return result


def get_user_project(user_id, project_id):
    """Returns project if user owns it, else None"""
    cursor = mysql.connection.cursor()
    cursor.execute(
        "SELECT id, name, base_url, description FROM projects WHERE id = %s AND user_id = %s",
        (project_id, user_id)
    )
    result = cursor.fetchone()
    cursor.close()
    return result


# ============================================================
# ENDPOINT 1: ANALYZE A BUG
# ============================================================

@ai_bp.route('/analyze-bug/<int:bug_id>', methods=['POST'])
@jwt_required()
@limiter.limit("15 per minute;200 per day")
def analyze_bug_endpoint(bug_id):
    user_id = int(get_jwt_identity())

    bug = get_user_bug(user_id, bug_id)
    if not bug:
        return jsonify({'error': 'Bug not found'}), 404

    try:
        analysis = analyze_bug(bug)
        return jsonify(analysis), 200
    except Exception as e:
        error_str = str(e).lower()
        if 'authentication' in error_str or 'invalid' in error_str and 'key' in error_str:
            return jsonify({'error': 'AI service unavailable: invalid API key'}), 503
        if 'rate' in error_str and 'limit' in error_str:
            return jsonify({'error': 'AI service is busy, please try again in a moment'}), 429
        if 'credit' in error_str or 'balance' in error_str:
            return jsonify({'error': 'AI credits exhausted. Please contact admin.'}), 503
        print(f"[AI analyze_bug error]: {e}")
        return jsonify({'error': 'AI analysis failed. Please try again.'}), 500


# ============================================================
# ENDPOINT 2: SUGGEST TEST CASES FOR A PROJECT
# ============================================================

@ai_bp.route('/suggest-tests/<int:project_id>', methods=['POST'])
@jwt_required()
@limiter.limit("15 per minute;200 per day")
def suggest_tests_endpoint(project_id):
    user_id = int(get_jwt_identity())

    project = get_user_project(user_id, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    try:
        suggestions = suggest_test_cases(project)
        return jsonify({
            'project_id': project_id,
            'project_name': project['name'],
            'suggestions': suggestions
        }), 200
    except Exception as e:
        error_str = str(e).lower()
        if 'rate' in error_str and 'limit' in error_str:
            return jsonify({'error': 'AI service is busy, please try again in a moment'}), 429
        if 'credit' in error_str or 'balance' in error_str:
            return jsonify({'error': 'AI credits exhausted'}), 503
        print(f"[AI suggest_tests error]: {e}")
        return jsonify({'error': 'AI suggestion failed. Please try again.'}), 500


# ============================================================
# ENDPOINT 3: SUGGEST FIX FOR A SPECIFIC FINDING
# ============================================================

@ai_bp.route('/suggest-fix', methods=['POST'])
@jwt_required()
@limiter.limit("15 per minute;200 per day")
def suggest_fix_endpoint():
    user_id = int(get_jwt_identity())
    data = request.json or {}

    category = data.get('category', 'other')
    item = data.get('item', '').strip()
    context = data.get('context', '').strip()

    if not item:
        return jsonify({'error': 'Finding item is required'}), 400

    try:
        result = suggest_fix({
            'category': category,
            'item': item,
            'context': context
        })
        return jsonify(result), 200
    except Exception as e:
        error_str = str(e).lower()
        if 'rate' in error_str and 'limit' in error_str:
            return jsonify({'error': 'AI service is busy, please try again in a moment'}), 429
        if 'credit' in error_str or 'balance' in error_str:
            return jsonify({'error': 'AI credits exhausted'}), 503
        print(f"[AI suggest_fix error]: {e}")
        return jsonify({'error': 'AI fix suggestion failed.'}), 500


# ============================================================
# ENDPOINT 4: CHAT ASSISTANT
# ============================================================

@ai_bp.route('/chat', methods=['POST'])
@jwt_required()
@limiter.limit("20 per minute;300 per day")
def chat_endpoint():
    user_id = int(get_jwt_identity())
    data = request.json or {}

    user_message = data.get('message', '').strip()
    conversation_history = data.get('history', [])  # list of {role, content}
    include_context = data.get('include_context', False)

    if not user_message:
        return jsonify({'error': 'Message is required'}), 400

    if len(user_message) > 2000:
        return jsonify({'error': 'Message too long (max 2000 characters)'}), 400

    # Optionally build context from user's data
    context = None
    if include_context:
        try:
            cursor = mysql.connection.cursor()

            # Count projects
            cursor.execute("SELECT COUNT(*) AS c FROM projects WHERE user_id = %s", (user_id,))
            proj_count = cursor.fetchone()['c']

            # Count test cases
            cursor.execute(
                """SELECT COUNT(*) AS c FROM test_cases tc
                   JOIN projects p ON tc.project_id = p.id
                   WHERE p.user_id = %s""",
                (user_id,)
            )
            tc_count = cursor.fetchone()['c']

            # Recent bugs (last 5)
            cursor.execute(
                """SELECT b.title, b.severity, b.status, b.category
                   FROM bugs b
                   LEFT JOIN test_cases tc ON b.test_case_id = tc.id
                   LEFT JOIN projects p ON tc.project_id = p.id
                   WHERE (p.user_id = %s OR b.assigned_to = %s)
                   ORDER BY b.created_at DESC LIMIT 5""",
                (user_id, user_id)
            )
            recent_bugs = cursor.fetchall()

            cursor.close()

            context = {
                'projects_count': proj_count,
                'test_cases_count': tc_count,
                'recent_bugs': recent_bugs
            }
        except Exception as e:
            print(f"[AI chat context error]: {e}")
            # Continue without context

    # Truncate history to last 10 messages to control token cost
    if len(conversation_history) > 10:
        conversation_history = conversation_history[-10:]

    try:
        response = chat(user_message, context=context, conversation_history=conversation_history)
        return jsonify({'reply': response}), 200
    except Exception as e:
        error_str = str(e).lower()
        if 'rate' in error_str and 'limit' in error_str:
            return jsonify({'error': 'AI is busy, please try again in a moment'}), 429
        if 'credit' in error_str or 'balance' in error_str:
            return jsonify({'error': 'AI credits exhausted'}), 503
        print(f"[AI chat error]: {e}")
        return jsonify({'error': 'AI chat failed. Please try again.'}), 500


# ============================================================
# HEALTH CHECK - GET /api/ai/health
# ============================================================

@ai_bp.route('/health', methods=['GET'])
@jwt_required()
def health_check():
    """Quick check that AI is configured. Doesn't make an actual API call."""
    from flask import current_app
    api_key = current_app.config.get('ANTHROPIC_API_KEY', '')
    model = current_app.config.get('AI_MODEL', '')

    if not api_key:
        return jsonify({'available': False, 'reason': 'API key not configured'}), 200

    return jsonify({
        'available': True,
        'model': model,
        'key_preview': api_key[:15] + '...' if len(api_key) > 15 else 'short'
    }), 200