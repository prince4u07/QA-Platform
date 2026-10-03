"""
Bugs API - Track and manage bugs.

UPDATE: Plain-English titles. Instead of:
  "🔗 Broken link: https://example.com/x (404)"
Now generates:
  "🔗 'About Us' link is broken (returns 404 Not Found)"
"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
import json
import logging
import re
import hashlib

logger = logging.getLogger(__name__)

bugs_bp = Blueprint('bugs', __name__)
mysql = None


def init_bugs(app_mysql):
    global mysql
    mysql = app_mysql


# ============================================================
# OWNERSHIP
# ============================================================

def user_owns_bug(user_id, bug_id):
    cursor = mysql.connection.cursor()
    cursor.execute(
        """SELECT b.id FROM bugs b
           LEFT JOIN test_cases tc ON b.test_case_id = tc.id
           LEFT JOIN projects p ON tc.project_id = p.id
           WHERE b.id = %s AND (p.user_id = %s OR b.assigned_to = %s)""",
        (bug_id, user_id, user_id)
    )
    result = cursor.fetchone()
    cursor.close()
    return result is not None


def user_owns_test_run(user_id, run_id):
    cursor = mysql.connection.cursor()
    cursor.execute(
        """SELECT tr.*, tc.id AS tc_id, tc.title AS tc_title, p.user_id AS owner_id
           FROM test_runs tr
           JOIN test_cases tc ON tr.test_case_id = tc.id
           JOIN projects p ON tc.project_id = p.id
           WHERE tr.id = %s AND p.user_id = %s""",
        (run_id, user_id)
    )
    result = cursor.fetchone()
    cursor.close()
    return result


# ============================================================
# CRUD ENDPOINTS
# ============================================================

@bugs_bp.route('', methods=['GET'])
@jwt_required()
def list_bugs():
    user_id = int(get_jwt_identity())
    status_filter = request.args.get('status')
    severity_filter = request.args.get('severity')
    category_filter = request.args.get('category')
    project_filter = request.args.get('project_id')

    try:
        cursor = mysql.connection.cursor()
        sql = """SELECT b.*, tc.title AS test_case_title, p.name AS project_name
                 FROM bugs b
                 LEFT JOIN test_cases tc ON b.test_case_id = tc.id
                 LEFT JOIN projects p ON tc.project_id = p.id
                 WHERE (p.user_id = %s OR b.assigned_to = %s)"""
        params = [user_id, user_id]

        if status_filter:
            sql += " AND b.status = %s"
            params.append(status_filter)
        if severity_filter:
            sql += " AND b.severity = %s"
            params.append(severity_filter)
        if category_filter:
            sql += " AND b.category = %s"
            params.append(category_filter)
        if project_filter:
            sql += " AND p.id = %s"
            params.append(project_filter)

        sql += " ORDER BY b.created_at DESC"
        cursor.execute(sql, tuple(params))
        bugs = cursor.fetchall()
        cursor.close()

        for b in bugs:
            if b.get('created_at'):
                b['created_at'] = b['created_at'].isoformat()
            if b.get('updated_at'):
                b['updated_at'] = b['updated_at'].isoformat()
            if b.get('resolved_at'):
                b['resolved_at'] = b['resolved_at'].isoformat()

        return jsonify(bugs), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bugs_bp.route('/<int:bug_id>', methods=['GET'])
@jwt_required()
def get_bug(bug_id):
    user_id = int(get_jwt_identity())
    if not user_owns_bug(user_id, bug_id):
        return jsonify({'error': 'Bug not found'}), 404

    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """SELECT b.*, tc.title AS test_case_title, p.name AS project_name
               FROM bugs b
               LEFT JOIN test_cases tc ON b.test_case_id = tc.id
               LEFT JOIN projects p ON tc.project_id = p.id
               WHERE b.id = %s""",
            (bug_id,)
        )
        bug = cursor.fetchone()
        cursor.close()

        if bug:
            if bug.get('created_at'):
                bug['created_at'] = bug['created_at'].isoformat()
            if bug.get('updated_at'):
                bug['updated_at'] = bug['updated_at'].isoformat()
            if bug.get('resolved_at'):
                bug['resolved_at'] = bug['resolved_at'].isoformat()
            if bug.get('evidence'):
                try:
                    if isinstance(bug['evidence'], str):
                        bug['evidence'] = json.loads(bug['evidence'])
                except Exception:
                    pass

        return jsonify(bug), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bugs_bp.route('', methods=['POST'])
@jwt_required()
def create_bug():
    user_id = int(get_jwt_identity())
    data = request.json or {}

    title = (data.get('title') or '').strip()
    if not title or len(title) < 3:
        return jsonify({'error': 'Title must be at least 3 characters'}), 400

    description = (data.get('description') or '').strip()
    severity = data.get('severity') or 'Major'
    status = data.get('status') or 'Open'
    category = data.get('category') or 'other'
    test_case_id = data.get('test_case_id')
    steps_to_reproduce = (data.get('steps_to_reproduce') or '').strip()
    expected_behavior = (data.get('expected_behavior') or '').strip()
    actual_behavior = (data.get('actual_behavior') or '').strip()
    evidence = data.get('evidence')
    evidence_json = json.dumps(evidence) if evidence else None

    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """INSERT INTO bugs (
                title, description, severity, status, category, test_case_id,
                steps_to_reproduce, expected_behavior, actual_behavior, evidence,
                assigned_to, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
            (title, description, severity, status, category, test_case_id,
             steps_to_reproduce, expected_behavior, actual_behavior, evidence_json, user_id)
        )
        mysql.connection.commit()
        bug_id = cursor.lastrowid
        cursor.close()
        return jsonify({'id': bug_id, 'message': 'Bug created'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bugs_bp.route('/<int:bug_id>', methods=['PUT'])
@jwt_required()
def update_bug(bug_id):
    user_id = int(get_jwt_identity())
    if not user_owns_bug(user_id, bug_id):
        return jsonify({'error': 'Bug not found'}), 404

    data = request.json or {}
    title = (data.get('title') or '').strip()
    if not title or len(title) < 3:
        return jsonify({'error': 'Title must be at least 3 characters'}), 400

    try:
        cursor = mysql.connection.cursor()
        status = data.get('status') or 'Open'
        resolved_at_clause = ", resolved_at = NOW()" if status in ('Resolved', 'Closed') else ", resolved_at = NULL"

        cursor.execute(
            f"""UPDATE bugs SET
                title = %s, description = %s, severity = %s, status = %s,
                category = %s, steps_to_reproduce = %s,
                expected_behavior = %s, actual_behavior = %s
                {resolved_at_clause}
                WHERE id = %s""",
            (
                title,
                (data.get('description') or '').strip(),
                data.get('severity') or 'Major',
                status,
                data.get('category') or 'other',
                (data.get('steps_to_reproduce') or '').strip(),
                (data.get('expected_behavior') or '').strip(),
                (data.get('actual_behavior') or '').strip(),
                bug_id
            )
        )
        mysql.connection.commit()
        cursor.close()
        return jsonify({'message': 'Bug updated'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bugs_bp.route('/<int:bug_id>/status', methods=['PATCH'])
@jwt_required()
def update_status(bug_id):
    user_id = int(get_jwt_identity())
    if not user_owns_bug(user_id, bug_id):
        return jsonify({'error': 'Bug not found'}), 404

    data = request.json or {}
    new_status = data.get('status')
    if new_status not in ('Open', 'In Progress', 'Resolved', 'Closed'):
        return jsonify({'error': 'Invalid status'}), 400

    try:
        cursor = mysql.connection.cursor()
        if new_status in ('Resolved', 'Closed'):
            cursor.execute(
                "UPDATE bugs SET status = %s, resolved_at = NOW() WHERE id = %s",
                (new_status, bug_id)
            )
        else:
            cursor.execute(
                "UPDATE bugs SET status = %s, resolved_at = NULL WHERE id = %s",
                (new_status, bug_id)
            )
        mysql.connection.commit()
        cursor.close()
        return jsonify({'message': 'Status updated', 'status': new_status}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bugs_bp.route('/<int:bug_id>', methods=['DELETE'])
@jwt_required()
def delete_bug(bug_id):
    user_id = int(get_jwt_identity())
    if not user_owns_bug(user_id, bug_id):
        return jsonify({'error': 'Bug not found'}), 404

    try:
        cursor = mysql.connection.cursor()
        cursor.execute("DELETE FROM bugs WHERE id = %s", (bug_id,))
        mysql.connection.commit()
        cursor.close()
        return jsonify({'message': 'Bug deleted'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bugs_bp.route('/stats', methods=['GET'])
@jwt_required()
def stats():
    user_id = int(get_jwt_identity())
    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """SELECT b.status, COUNT(*) AS count
               FROM bugs b
               LEFT JOIN test_cases tc ON b.test_case_id = tc.id
               LEFT JOIN projects p ON tc.project_id = p.id
               WHERE p.user_id = %s OR b.assigned_to = %s
               GROUP BY b.status""",
            (user_id, user_id)
        )
        rows = cursor.fetchall()
        cursor.close()

        result = {'Open': 0, 'In Progress': 0, 'Resolved': 0, 'Closed': 0, 'total': 0}
        for r in rows:
            result[r['status']] = r['count']
            result['total'] += r['count']

        return jsonify(result), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# PLAIN-ENGLISH TITLE & DESCRIPTION GENERATION
# ============================================================

def _http_status_label(code):
    """Convert HTTP status code to user-friendly label."""
    labels = {
        400: 'Bad Request', 401: 'Unauthorized', 403: 'Forbidden',
        404: 'Not Found', 500: 'Server Error', 502: 'Bad Gateway',
        503: 'Service Unavailable', 504: 'Timeout',
    }
    if code == 0:
        return 'unreachable'
    return labels.get(code, f'HTTP {code}')


def _smart_title_for_finding(category, finding):
    """
    Generate a plain-English title that explains WHAT and WHERE.
    """
    # If finding isn't a dict (legacy fallback)
    if not isinstance(finding, dict):
        return f"🐞 Issue: {str(finding)[:120]}"

    # === BROKEN LINKS ===
    if category == 'broken_links':
        link_text = finding.get('element_text', '').strip()
        url = finding.get('url', '')
        status = finding.get('status_code', 0)
        status_label = _http_status_label(status)

        if link_text and link_text != '(no text)' and len(link_text) < 50:
            return f"🔗 \"{link_text}\" link is broken ({status_label})"
        else:
            # Use last part of URL
            short_url = url.rstrip('/').split('/')[-1][:40] if url else 'link'
            return f"🔗 Broken link to \"{short_url}\" ({status_label})"

    # === CONSOLE ERRORS ===
    if category == 'console_errors':
        text = finding.get('text', '')
        status_match = re.search(r'(?:status of|HTTP\s*)(\d{3})', text, re.IGNORECASE)
        if 'failed to load resource' in text.lower() and status_match:
            status = int(status_match.group(1))
            meaning = {
                400: 'the request was invalid',
                401: 'sign-in may be required',
                403: 'access was denied',
                404: 'the resource was not found',
                500: 'the server encountered an error',
                502: 'the server is unavailable',
                503: 'the service is temporarily unavailable',
            }.get(status, f'the server returned HTTP {status}')
            return f"🐛 The page could not load a required resource ({meaning})"
        return f"🐛 JavaScript error affecting the page: {text[:80]}"

    # === MISSING ALT ===
    if category == 'missing_alt_images':
        src = finding.get('src', '')
        # Get last part of image path
        img_name = src.rstrip('/').split('/')[-1][:40] if src else 'image'
        # Strip query params
        if '?' in img_name:
            img_name = img_name.split('?')[0]
        return f"🖼 Image \"{img_name}\" has no description (alt text missing)"

    # === SEO ===
    if category == 'seo_issues':
        return f"🔍 {finding.get('issue', 'SEO issue')}"

    # === SECURITY ===
    if category == 'security_issues':
        return f"🔒 {finding.get('issue', 'Security issue')}"

    # === ACCESSIBILITY ===
    if category == 'accessibility_issues':
        return f"♿ {finding.get('issue', 'Accessibility issue')}"

    # === MOBILE ===
    if category == 'mobile_issues':
        return f"📱 {finding.get('issue', 'Mobile issue')}"

    if category == 'api_issues':
        url = finding.get('url', '')
        try:
            short = url.split('?')[0].rstrip('/').rsplit('/', 1)[-1][:60] or url[:60]
        except Exception:
            short = url[:60]
        status = finding.get('status')
        detail = f' (HTTP {status})' if status else ''
        return f"🔌 Data request to \"{short}\" failed{detail}"

    if category == 'validation_issues':
        form_idx = finding.get('form_index')
        form_id = finding.get('form_id')
        if form_id:
            form_ref = f'form "#{form_id}"'
        elif isinstance(form_idx, int):
            form_ref = f'form #{form_idx + 1}'
        else:
            form_ref = 'a form'
        count = finding.get('required_count')
        count_txt = f' ({count} required field(s) unchecked)' if count else ''
        return f"📝 {form_ref} accepts empty submission{count_txt}"

    if category == 'performance_issues':
        return f"⏱ {finding.get('issue') or finding.get('display') or 'Page is slow or heavy'}"

    if category == 'functional_issues':
        step = finding.get('step') or finding.get('issue') or 'workflow step'
        return f"❌ Workflow step failed: {str(step)[:100]}"

    if category == 'execution_errors':
        return f"⚠️ Page audit incomplete: {finding.get('error') or finding.get('issue', 'audit error')}"

    # Default
    return f"🐞 {finding.get('issue') or finding.get('display') or 'Issue detected'}"


def _smart_description_for_finding(category, finding):
    """Generate a clear What/Where/Why description."""
    if not isinstance(finding, dict):
        return str(finding)

    parts = []

    # === WHAT (the core issue) ===
    if category == 'broken_links':
        url = finding.get('url', '')
        status = finding.get('status_code', 0)
        link_text = finding.get('element_text', '').strip()

        parts.append(f"**What's wrong:**")
        if link_text and link_text != '(no text)':
            parts.append(f"The link with text \"{link_text}\" points to {url}")
        else:
            parts.append(f"A link points to {url}")
        parts.append(f"But that URL returns {_http_status_label(status)} (HTTP {status})")

        if finding.get('parent_context'):
            parts.append(f"\n**Where to find it on the page:**")
            parts.append(f"In this section: \"{finding['parent_context'][:200]}\"")

        parts.append(f"\n**Why this matters:**")
        parts.append("Users clicking this link will hit an error page. This breaks trust and makes the site look unmaintained.")

    elif category == 'missing_alt_images':
        src = finding.get('src', '')
        parts.append(f"**What's wrong:**")
        parts.append(f"This image has no alt text: {src}")

        if finding.get('parent_context'):
            parts.append(f"\n**Where to find it:**")
            parts.append(f"Near this content: \"{finding['parent_context'][:200]}\"")

        parts.append(f"\n**Why this matters:**")
        parts.append("Screen readers (used by blind/visually impaired users) can't describe this image. Search engines also can't understand it.")

    elif category == 'console_errors':
        text = finding.get('text', '')
        status_match = re.search(r'(?:status of|HTTP\s*)(\d{3})', text, re.IGNORECASE)
        parts.append(f"**What's wrong:**")
        if 'failed to load resource' in text.lower() and status_match:
            status = int(status_match.group(1))
            status_label = _http_status_label(status)
            parts.append(f"The page tried to load a required resource, but the server returned HTTP {status} ({status_label}).")
            if status == 401:
                parts.append("This usually means the user needs to sign in again or the session has expired.")
            elif status == 400:
                parts.append("This usually means the page sent information the server could not accept.")
            elif status == 403:
                parts.append("This usually means the current user is not allowed to access it.")
            elif status == 404:
                parts.append("This usually means the requested page or file no longer exists at that address.")
        else:
            parts.append("A JavaScript error occurred while the page was running:")
            parts.append(f"`{text[:300]}`")
        parts.append("\n**Where to change:**")
        if finding.get('page_url'):
            parts.append(f"Start on this page: `{finding['page_url']}`")
        if (finding.get('source_location') or {}).get('url'):
            location = finding['source_location']
            source = location['url']
            line = location.get('lineNumber')
            column = location.get('columnNumber')
            suffix = f":{line + 1}" if isinstance(line, int) else ''
            if isinstance(column, int):
                suffix += f":{column + 1}"
            parts.append(f"Inspect the JavaScript file at `{source}{suffix}`. Fix the request or code at that location.")
        elif status_match:
            parts.append("Open browser DevTools > Network, select the failed request, and fix the frontend request or the backend API route that serves it.")
        else:
            parts.append("Open browser DevTools > Console, select the error's source link, and fix the JavaScript file and line shown there.")
        parts.append(f"\n**Why this matters:**")
        parts.append("JavaScript errors can break interactive features (buttons, forms, animations). Users may experience unexpected behavior.")

    elif category == 'security_issues':
        parts.append(f"**What's wrong:**")
        parts.append(finding.get('issue', ''))

        if finding.get('missing_headers'):
            parts.append("\n**Specifically these are missing:**")
            for h in finding['missing_headers']:
                parts.append(f"- **{h['header']}**: {h['purpose']}")

        parts.append(f"\n**Why this matters:**")
        parts.append("These security headers protect users from common web attacks like clickjacking, XSS, and protocol downgrade attacks.")

    elif category == 'accessibility_issues':
        parts.append(f"**What's wrong:**")
        parts.append(finding.get('issue', ''))

        if finding.get('parent_context'):
            parts.append(f"\n**Where to find it:**")
            parts.append(f"Near: \"{finding['parent_context'][:200]}\"")

        parts.append(f"\n**Why this matters:**")
        parts.append("Users with disabilities (using screen readers, keyboard navigation) cannot use this element properly. This may also violate accessibility laws.")

    elif category == 'mobile_issues':
        parts.append(f"**What's wrong:**")
        parts.append(finding.get('issue', ''))

        if finding.get('examples') and len(finding['examples']) > 1:
            parts.append(f"\n**Examples of small targets found:**")
            for ex in finding['examples'][:5]:
                text = ex.get('text', '(unlabeled)')
                w = ex.get('width', 0)
                h = ex.get('height', 0)
                parts.append(f"- \"{text}\" — only {w}×{h}px (needs 44×44px minimum)")

        parts.append(f"\n**Why this matters:**")
        parts.append("Mobile users will have difficulty tapping these elements accurately, leading to frustration and missed taps. Apple and Google both recommend at least 44×44px tap targets.")

    elif category == 'seo_issues':
        parts.append(f"**What's wrong:**")
        parts.append(finding.get('issue', ''))
        parts.append(f"\n**Why this matters:**")
        parts.append("This affects how your site appears in search engines and browser tabs. Lower visibility = fewer visitors.")

    elif category == 'performance_issues':
        parts.append("**What's wrong:**")
        parts.append(finding.get('display') or finding.get('issue', 'Page is slow or heavy'))
        metric = finding.get('metric')
        value = finding.get('value')
        threshold = finding.get('threshold')
        if metric and value is not None:
            parts.append(f"Measured {value} vs target {threshold} for {metric}.")
        resource = finding.get('resource')
        if resource:
            parts.append(f"Biggest file: `{str(resource)[:200]}`")
        if finding.get('summary'):
            parts.append(finding['summary'])
        parts.append("\n**Why this matters:**")
        parts.append("Slow pages make users leave. Pages over ~3s load or ~3MB lose mobile users first.")

    elif category == 'api_issues':
        parts.append("**What's wrong:**")
        parts.append(finding.get('summary') or finding.get('issue', ''))
        if finding.get('url'):
            parts.append(f"Endpoint: `{finding['url'][:300]}`")
        parts.append("\n**Why this matters:**")
        parts.append("The page could not load its data, so users see missing content or broken features.")

    elif category == 'validation_issues':
        parts.append("**What's wrong:**")
        parts.append(finding.get('summary') or finding.get('issue', ''))
        parts.append("\n**Why this matters:**")
        parts.append("Users can submit incomplete forms, which creates bad data and confusing errors later.")

    elif category == 'functional_issues':
        parts.append("**What's wrong:**")
        parts.append(finding.get('issue') or finding.get('display') or 'A workflow step failed')
        if finding.get('step'):
            parts.append(f"Failed step: `{str(finding['step'])[:300]}`")
        parts.append("\n**Why this matters:**")
        parts.append("A core user workflow does not complete — this is the most severe kind of defect.")

    else:
        parts.append(finding.get('display') or 'Issue detected')

    # Always include location info if available
    if finding.get('element_selector') and finding['element_selector'] != 'console':
        parts.append(f"\n**Technical location:** `{finding['element_selector']}`")

    change_guidance = {
        'broken_links': 'Update the href in the link element, or restore/fix the destination URL shown above.',
        'missing_alt_images': 'Add descriptive alt text to this image element in the page template or component.',
        'seo_issues': 'Update the matching element inside the page <head> or page template.',
        'security_issues': 'Update the web server, reverse proxy, or application security-header configuration.',
        'accessibility_issues': 'Update the HTML/component identified by the selector to meet the accessibility requirement.',
        'mobile_issues': 'Update the CSS or component styles for the elements listed in this finding.',
        'api_issues': 'Fix the frontend API call or the backend endpoint serving the request URL.',
        'validation_issues': 'Update the form control or validation handler identified by this finding.',
        'performance_issues': 'Compress images, split large JS bundles, and lazy-load below-the-fold content.',
        'functional_issues': 'Fix the failing workflow step in the app, then re-run the test.',
        'execution_errors': 'Re-run the audit; if it repeats, check login session and page load stability.',
    }
    if category != 'console_errors':
        parts.append("\n**Where to change:**")
        parts.append(change_guidance.get(category, 'Open the page and update the code identified by the technical location above.'))

    return '\n'.join(parts)


def _category_severity_map(category, finding=None):
    """Map test categories to bug severity, more nuanced."""
    # A finding that arrived with its own severity keeps it. The runner
    # grades every finding critical/serious/moderate/minor, and a workflow
    # failure graded critical must not become a Minor bug just because its
    # category has no mapping here.
    if isinstance(finding, dict):
        own = finding.get('severity')
        if own in ('critical', 'serious'):
            return 'Critical' if own == 'critical' else 'Major'
        if own in ('moderate', 'minor'):
            return 'Minor'
    if category == 'security_issues':
        return 'Critical'
    if category == 'console_errors':
        return 'Major'
    if category == 'broken_links':
        return 'Major'
    if category == 'accessibility_issues':
        return 'Major'
    if category == 'mobile_issues':
        # If it's the aggregate "many tap targets" finding, Major
        if isinstance(finding, dict) and finding.get('count', 0) > 10:
            return 'Major'
        return 'Minor'
    if category in ('api_issues', 'validation_issues', 'execution_errors',
                      'functional_issues', 'performance_issues'):
        return 'Major'
    return 'Minor'


def _category_db_value(category):
    return {
        'broken_links': 'broken-link',
        'console_errors': 'console-error',
        'missing_alt_images': 'missing-alt',
        'seo_issues': 'seo',
        'security_issues': 'security',
        'accessibility_issues': 'accessibility',
        'mobile_issues': 'mobile',
        'performance_issues': 'performance',
        'functional_issues': 'functional',
        'api_issues': 'api',
        'validation_issues': 'validation',
        'execution_errors': 'execution',
    }.get(category, 'other')


def _issue_fingerprint(category, finding):
    """Create a stable key for grouping the same finding across runs."""
    if not isinstance(finding, dict):
        value = str(finding)
    else:
        value = '|'.join(str(finding.get(key) or '').strip().lower()
                         for key in ('url', 'page_url', 'element_selector',
                                     'element_text', 'issue', 'status_code'))
    return hashlib.sha256(f'{category}|{value}'.encode('utf-8')).hexdigest()


def _issue_quality(category, finding):
    """Return a user-facing score, confidence, root cause and suggested fix."""
    severity = _category_severity_map(category, finding)
    score = {'Critical': 10, 'Major': 7, 'Minor': 3}.get(severity, 1)
    confidence = 0.65
    root_cause = ''
    suggested_fix = ''
    if isinstance(finding, dict):
        if finding.get('status_code'):
            confidence = 0.98
        elif finding.get('element_selector') or finding.get('url'):
            confidence = 0.9
        root_cause = finding.get('root_cause') or finding.get('summary') or ''
        suggested_fix = finding.get('fix') or ''
    return score, confidence, root_cause, suggested_fix


def _build_bug_from_finding(category, finding):
    """
    Build a bug record from a rich finding with plain-English title/description.
    """
    title = _smart_title_for_finding(category, finding)
    description = _smart_description_for_finding(category, finding)
    severity = _category_severity_map(category, finding)
    cat_db = _category_db_value(category)

    # If string fallback
    if isinstance(finding, str):
        return (title, description, severity, cat_db, None, '', '', finding)

    evidence = finding

    # Steps / expected / actual
    steps = ''
    expected = ''
    actual = finding.get('display', title) if isinstance(finding, dict) else str(finding)

    if category == 'broken_links':
        url = finding.get('url', '')
        steps = f"1. Open the page\n2. Look for the link \"{finding.get('element_text', '')}\"\n3. Click it"
        expected = "Link should load the target page successfully (HTTP 200)"
        actual = f"Link returns {_http_status_label(finding.get('status_code', 0))} - the destination page doesn't load"
    elif category == 'missing_alt_images':
        steps = "1. Open the page using a screen reader\n2. Navigate to the image\n3. The screen reader announces the image"
        expected = "Screen reader should read a descriptive alt text explaining what the image shows"
        actual = "Screen reader skips the image or just says 'image' with no description"
    elif category == 'security_issues':
        steps = "1. Visit the page\n2. Open browser DevTools > Network tab\n3. Click the main HTML response\n4. Look at Response Headers"
        expected = "All recommended security headers should be present"
        actual = finding.get('issue', '')
    elif category == 'performance_issues':
        steps = "1. Open the page on a throttled (Slow 4G) connection\n2. Measure load time and total weight in DevTools > Network"
        expected = "Page loads in under 3s and weighs under 3MB"
        actual = finding.get('display', '')
    elif category == 'api_issues':
        steps = f"1. Open the page\n2. Open DevTools > Network\n3. Replay the request to `{finding.get('url', '')[:200]}`"
        expected = "API returns HTTP 2xx with the expected data"
        actual = finding.get('issue', '')
    elif category == 'validation_issues':
        steps = "1. Open the page\n2. Leave all required fields empty\n3. Submit the form"
        expected = "Form blocks submission and highlights the missing fields"
        actual = finding.get('issue', '')
    elif category == 'functional_issues':
        steps = "1. Follow the test case steps in order\n2. Observe where the workflow stops matching the expected result"
        expected = "Each workflow step completes as described in the test case"
        actual = finding.get('issue') or finding.get('display', '')

    return (title, description, severity, cat_db, evidence, steps, expected, actual)


# ============================================================
# AUTO-CREATE BUGS FROM TEST RUN
# ============================================================

# Categories whose values are runner findings. Everything else inside
# findings_evidence is bookkeeping and must never be read as a defect: a
# manual run stores its note as a STRING there, and iterating a string
# would "create" one bug per character.
RUN_FINDING_CATEGORIES = {
    'broken_links', 'console_errors', 'missing_alt_images', 'seo_issues',
    'security_issues', 'accessibility_issues', 'mobile_issues',
    'performance_issues', 'functional_issues', 'api_issues',
    'validation_issues', 'execution_errors',
}

# Manual runs keep their record under these keys: metadata, the pages
# visited, the whole checklist (passed steps included) and the run summary.
# None of that is a defect.
MANUAL_BOOKKEEPING_KEYS = {
    'manual', 'note', 'pages', 'steps', 'summary',
    'expected_result', 'expected_met',
}

# Manual runs keep the tester's own findings under 'reported', already
# carrying the category and severity the tester chose.
# Aligned with _category_severity_map: moderate/minor -> Minor.
TESTER_SEVERITY_TO_BUG = {
    'critical': 'Critical', 'serious': 'Major',
    'moderate': 'Minor', 'minor': 'Minor',
}

TESTER_CATEGORY_TO_DB = {
    'broken-link': 'broken-link',
    'functional': 'functional',
    'layout': 'other',
    'design': 'other',
    'content': 'other',
    'business': 'other',
    'other': 'other',
}

LEGACY_FINDING_COLUMNS = (
    'broken_links', 'console_errors', 'missing_alt_images',
    'seo_issues', 'security_issues', 'accessibility_issues',
    'mobile_issues', 'performance_issues', 'api_issues',
    'validation_issues',
)


def _load_run_findings(run):
    """Return the run's findings as {category: [findings]}.

    Prefers the rich findings_evidence JSON; falls back to legacy
    per-category columns so old runs still convert to bugs.
    """
    if run.get('findings_evidence'):
        try:
            parsed = json.loads(run['findings_evidence'])
            if isinstance(parsed, dict):
                return parsed
        except Exception as e:
            logger.warning("[from-test-run] Failed to parse findings_evidence: %s", e)
    logger.info("[from-test-run] No rich findings, using legacy fields")
    fallback = {}
    for col in LEGACY_FINDING_COLUMNS:
        raw = run.get(col)
        if not raw:
            fallback[col] = []
            continue
        try:
            fallback[col] = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            fallback[col] = []
    return fallback


def _insert_finding_bug(cursor, title, description, severity, cat_db,
                        tc_id, run_id, finding, assigned_to=None):
    """Insert one bug built from a finding-shaped dict."""
    evidence = finding if isinstance(finding, dict) else None
    category = next((key for key, value in RUN_FINDING_CATEGORIES.items()
                     if _category_db_value(key) == cat_db), 'other')
    quality = _issue_quality(category, finding)
    fingerprint = _issue_fingerprint(category, finding)
    cursor.execute(
        """INSERT INTO bugs (
            title, description, severity, status, category,
            test_case_id, test_run_id,
            steps_to_reproduce, expected_behavior, actual_behavior,
            evidence, severity_score, confidence, fingerprint, occurrences,
            first_seen_run_id, last_seen_run_id, root_cause, suggested_fix,
            assigned_to, created_at
        ) VALUES (%s, %s, %s, 'Open', %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, 1, %s, %s, %s, %s, %s, NOW())""",
        (title, description, severity, cat_db,
         tc_id, run_id,
         '', '',
         finding.get('issue', '') if isinstance(finding, dict) else str(finding),
         json.dumps(evidence) if evidence else None,
         quality[0], quality[1], fingerprint, run_id, run_id,
         quality[2], quality[3], assigned_to)
    )


@bugs_bp.route('/from-test-run/<int:run_id>', methods=['POST'])
@jwt_required()
def create_bugs_from_run(run_id):
    user_id = int(get_jwt_identity())
    run = user_owns_test_run(user_id, run_id)

    if not run:
        return jsonify({'error': 'Test run not found'}), 404

    try:
        cursor = mysql.connection.cursor()

        # Get existing bug titles for this test case (de-dupe)
        cursor.execute(
            "SELECT title, fingerprint, occurrences FROM bugs WHERE test_case_id = %s",
            (run['tc_id'],)
        )
        existing_rows = cursor.fetchall()
        existing_titles = {row['title'] for row in existing_rows}
        existing_fingerprints = {
            row['fingerprint']: row.get('occurrences') or 1
            for row in existing_rows if row.get('fingerprint')
        }

        # Use rich findings_evidence, falling back to legacy columns.
        rich_findings = _load_run_findings(run)

        created = 0
        skipped = 0

        for category, items in rich_findings.items():
            # Bookkeeping keys of a manual-run record: metadata, pages,
            # checklist and summary are not defects.
            if category in MANUAL_BOOKKEEPING_KEYS:
                continue
            if not isinstance(items, list):
                continue

            if category == 'reported':
                # A manual tester's own findings, carrying the category and
                # severity they chose while looking at the page.
                for finding in items:
                    if not isinstance(finding, dict):
                        continue
                    title = (finding.get('issue')
                             or finding.get('display') or '').strip()
                    if len(title) < 3:
                        continue
                    if title in existing_titles:
                        skipped += 1
                        continue
                    existing_titles.add(title)

                    severity = TESTER_SEVERITY_TO_BUG.get(
                        finding.get('severity'), 'Major')
                    cat_db = TESTER_CATEGORY_TO_DB.get(
                        finding.get('category'), 'other')
                    fingerprint = _issue_fingerprint(
                        finding.get('category') or 'other', finding)
                    if fingerprint in existing_fingerprints:
                        cursor.execute(
                            """UPDATE bugs SET occurrences = occurrences + 1,
                               last_seen_run_id = %s WHERE fingerprint = %s""",
                            (run_id, fingerprint)
                        )
                        skipped += 1
                        continue
                    note = finding.get('note') or ''
                    page_url = finding.get('page_url') or ''
                    description = (
                        "Reported by a manual tester during a tracked run.\n\n"
                        f"**What was observed:** {title}\n"
                        + (f"\n**Tester's note:** {note}" if note else '')
                        + (f"\n\n**Where:** {page_url}" if page_url else '')
                    )
                    _insert_finding_bug(cursor, title, description, severity,
                                        cat_db, run['tc_id'], run_id, finding,
                                        assigned_to=user_id)
                    existing_fingerprints[fingerprint] = 1
                    created += 1
                continue

            if category not in RUN_FINDING_CATEGORIES:
                # Neither a known finding category nor manual bookkeeping.
                # Skip rather than guess what it is.
                continue

            for finding in items:
                try:
                    title, description, severity, cat_db, evidence, steps, expected, actual = \
                        _build_bug_from_finding(category, finding)
                    fingerprint = _issue_fingerprint(category, finding)

                    if fingerprint in existing_fingerprints:
                        cursor.execute(
                            """UPDATE bugs SET occurrences = occurrences + 1,
                               last_seen_run_id = %s WHERE fingerprint = %s""",
                            (run_id, fingerprint)
                        )
                        skipped += 1
                        continue
                    if title in existing_titles:
                        skipped += 1
                        continue
                    existing_titles.add(title)
                    existing_fingerprints[fingerprint] = 1
                    quality = _issue_quality(category, finding)

                    evidence_json = json.dumps(evidence) if evidence else None

                    cursor.execute(
                        """INSERT INTO bugs (
                            title, description, severity, status, category,
                            test_case_id, test_run_id,
                            steps_to_reproduce, expected_behavior, actual_behavior,
                            evidence, severity_score, confidence, fingerprint,
                            occurrences, first_seen_run_id, last_seen_run_id,
                            root_cause, suggested_fix, assigned_to, created_at
                        ) VALUES (%s, %s, %s, 'Open', %s, %s, %s, %s, %s, %s, %s,
                                  %s, %s, %s, 1, %s, %s, %s, %s, %s, NOW())""",
                        (title, description, severity, cat_db,
                         run['tc_id'], run_id,
                         steps, expected, actual,
                         evidence_json, quality[0], quality[1], fingerprint,
                         run_id, run_id, quality[2], quality[3], user_id)
                    )
                    created += 1
                except Exception as e:
                    logger.warning("[from-test-run] Failed to create bug for %s: %s", category, e)
                    continue

        mysql.connection.commit()
        cursor.close()

        return jsonify({
            'created': created,
            'skipped_duplicates': skipped,
            'test_run_id': run_id,
            'message': f'Created {created} bug(s), skipped {skipped} duplicate(s)'
        }), 201

    except Exception as e:
        logger.exception("[from-test-run] Top-level error: %s", e)
        return jsonify({'error': str(e)}), 500