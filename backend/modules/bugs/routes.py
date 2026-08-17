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
        text = finding.get('text', '')[:80]
        return f"🐛 JavaScript error: {text}"

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
        parts.append(f"**What's wrong:**")
        parts.append(f"JavaScript error in browser console:")
        parts.append(f"`{finding.get('text', '')[:300]}`")
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

    else:
        parts.append(finding.get('display') or 'Issue detected')

    # Always include location info if available
    if finding.get('element_selector') and finding['element_selector'] != 'console':
        parts.append(f"\n**Technical location:** `{finding['element_selector']}`")

    return '\n'.join(parts)


def _category_severity_map(category, finding=None):
    """Map test categories to bug severity, more nuanced."""
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
    }.get(category, 'other')


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

    return (title, description, severity, cat_db, evidence, steps, expected, actual)


# ============================================================
# AUTO-CREATE BUGS FROM TEST RUN
# ============================================================

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
            "SELECT title FROM bugs WHERE test_case_id = %s",
            (run['tc_id'],)
        )
        existing_titles = {row['title'] for row in cursor.fetchall()}

        # Use rich findings_evidence
        rich_findings = None
        if run.get('findings_evidence'):
            try:
                rich_findings = json.loads(run['findings_evidence'])
            except Exception as e:
                print(f"[from-test-run] Failed to parse findings_evidence: {e}")
                rich_findings = None

        # Fallback to legacy columns
        if not rich_findings:
            print("[from-test-run] No rich findings, using legacy fields")
            rich_findings = {}
            for legacy_col in ['broken_links', 'console_errors', 'missing_alt_images',
                                'seo_issues', 'security_issues', 'accessibility_issues',
                                'mobile_issues']:
                raw = run.get(legacy_col)
                if raw:
                    try:
                        rich_findings[legacy_col] = json.loads(raw)
                    except Exception:
                        rich_findings[legacy_col] = []
                else:
                    rich_findings[legacy_col] = []

        created = 0
        skipped = 0

        for category, items in rich_findings.items():
            if not items:
                continue

            for finding in items:
                try:
                    title, description, severity, cat_db, evidence, steps, expected, actual = \
                        _build_bug_from_finding(category, finding)

                    if title in existing_titles:
                        skipped += 1
                        continue
                    existing_titles.add(title)

                    evidence_json = json.dumps(evidence) if evidence else None

                    cursor.execute(
                        """INSERT INTO bugs (
                            title, description, severity, status, category,
                            test_case_id, test_run_id,
                            steps_to_reproduce, expected_behavior, actual_behavior,
                            evidence, assigned_to, created_at
                        ) VALUES (%s, %s, %s, 'Open', %s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
                        (title, description, severity, cat_db,
                         run['tc_id'], run_id,
                         steps, expected, actual,
                         evidence_json, user_id)
                    )
                    created += 1
                except Exception as e:
                    print(f"[from-test-run] Failed to create bug for {category}: {e}")
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
        print(f"[from-test-run] Top-level error: {e}")
        return jsonify({'error': str(e)}), 500