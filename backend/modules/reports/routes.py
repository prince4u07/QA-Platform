"""
Reports & Analytics API.

Endpoints:
  GET /api/reports/summary           - Top-level stats (cards on the page)
  GET /api/reports/health-trend      - Health score over time
  GET /api/reports/bug-breakdown     - Bug status + severity distributions
  GET /api/reports/top-categories    - Most common bug categories
  GET /api/reports/test-pass-rate    - Test pass/fail rate over weeks
  GET /api/reports/recent-runs       - Last 10 test runs
  GET /api/reports/export-pdf        - Download PDF report
"""

from flask import Blueprint, jsonify, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timedelta
from io import BytesIO
import json

reports_bp = Blueprint('reports', __name__)
mysql = None


def init_reports(app_mysql):
    global mysql
    mysql = app_mysql


def _safe_json(value):
    if not value:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _latest_detected_findings(user_id, source_filter=None):
    """Return the findings from each user's latest run per test case.

    `source_filter` optionally restricts to 'MANUAL' or 'AUTOMATED'.
    """
    cursor = mysql.connection.cursor()
    cursor.execute(
        """SELECT tr.id, tr.findings_evidence, tc.title AS test_case_title,
                  p.name AS project_name
           FROM test_runs tr
           JOIN test_cases tc ON tr.test_case_id = tc.id
           JOIN projects p ON tc.project_id = p.id
           JOIN (
             SELECT tr2.test_case_id, MAX(tr2.id) AS latest_id
             FROM test_runs tr2
             JOIN test_cases tc2 ON tr2.test_case_id = tc2.id
             JOIN projects p2 ON tc2.project_id = p2.id
             WHERE p2.user_id = %s
             GROUP BY tr2.test_case_id
           ) latest ON latest.latest_id = tr.id
           WHERE p.user_id = %s""",
        (user_id, user_id)
    )
    runs = cursor.fetchall()
    cursor.close()

    findings = []
    bookkeeping = {
        'manual', 'note', 'pages', 'steps', 'summary',
        'expected_result', 'expected_met', 'run_type', 'execution_mode',
        'source', 'verdict_reason',
    }
    for run in runs:
        try:
            evidence = json.loads(run.get('findings_evidence') or '{}')
        except (TypeError, ValueError):
            continue
        for category, items in evidence.items():
            if category in bookkeeping:
                continue
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict):
                    label = item.get('issue') or item.get('display') or 'Issue detected'
                    severity = item.get('severity', 'moderate').capitalize()
                    location = item.get('url') or item.get('page_url') or ''
                    source = (item.get('source') or 'AUTOMATED').upper()
                    if source == 'TESTER':
                        source = 'MANUAL'
                else:
                    label = str(item)
                    severity = 'Moderate'
                    location = ''
                    source = 'AUTOMATED'
                if source_filter and source != source_filter:
                    continue
                findings.append({
                    'id': f"{run['id']}-{category}-{len(findings)}",
                    'category': category,
                    'issue': label,
                    'severity': severity,
                    'location': location,
                    'source': source,
                    'test_case_title': run['test_case_title'],
                    'project_name': run['project_name'],
                })
    return findings


# ============================================================
# HELPER: Build summary data (used by both /summary and PDF)
# ============================================================

def _build_summary(user_id):
    cursor = mysql.connection.cursor()

    cursor.execute("SELECT COUNT(*) AS c FROM projects WHERE user_id = %s", (user_id,))
    total_projects = cursor.fetchone()['c']

    cursor.execute(
        """SELECT COUNT(*) AS c FROM test_cases tc
           JOIN projects p ON tc.project_id = p.id
           WHERE p.user_id = %s""",
        (user_id,)
    )
    total_testcases = cursor.fetchone()['c']

    cursor.execute(
        """SELECT COUNT(*) AS c FROM test_runs tr
           JOIN test_cases tc ON tr.test_case_id = tc.id
           JOIN projects p ON tc.project_id = p.id
           WHERE p.user_id = %s""",
        (user_id,)
    )
    total_runs = cursor.fetchone()['c']

    cursor.execute(
        """SELECT AVG(health_score) AS avg_score FROM (
              SELECT tr.health_score FROM test_runs tr
              JOIN test_cases tc ON tr.test_case_id = tc.id
              JOIN projects p ON tc.project_id = p.id
              WHERE p.user_id = %s AND tr.health_score IS NOT NULL
              ORDER BY tr.run_at DESC LIMIT 20
           ) recent""",
        (user_id,)
    )
    avg_row = cursor.fetchone()
    avg_health = round(float(avg_row['avg_score'] or 0))

    cursor.execute(
        """SELECT COUNT(*) AS c FROM bugs b
           LEFT JOIN test_cases tc ON b.test_case_id = tc.id
           LEFT JOIN projects p ON tc.project_id = p.id
           WHERE p.user_id = %s OR b.assigned_to = %s""",
        (user_id, user_id)
    )
    total_bugs = cursor.fetchone()['c']

    cursor.execute(
        """SELECT COUNT(*) AS c FROM bugs b
           LEFT JOIN test_cases tc ON b.test_case_id = tc.id
           LEFT JOIN projects p ON tc.project_id = p.id
           WHERE (p.user_id = %s OR b.assigned_to = %s)
             AND b.status IN ('Resolved', 'Closed')""",
        (user_id, user_id)
    )
    resolved = cursor.fetchone()['c']

    cursor.execute(
        """SELECT AVG(TIMESTAMPDIFF(HOUR, b.created_at, b.resolved_at)) AS avg_hours
           FROM bugs b
           LEFT JOIN test_cases tc ON b.test_case_id = tc.id
           LEFT JOIN projects p ON tc.project_id = p.id
           WHERE (p.user_id = %s OR b.assigned_to = %s)
             AND b.resolved_at IS NOT NULL""",
        (user_id, user_id)
    )
    time_row = cursor.fetchone()
    avg_hours = float(time_row['avg_hours'] or 0)
    avg_resolution_days = round(avg_hours / 24, 1) if avg_hours > 0 else 0
    resolution_rate = round((resolved / total_bugs * 100) if total_bugs > 0 else 0, 1)

    # Run-type split: guarded so databases without the new columns
    # (pre-migration) still return the base summary.
    manual_runs = automated_runs = passed = failed = critical = 0
    automation_coverage = 0
    try:
        cursor.execute(
            """SELECT
                 SUM(CASE WHEN tr.run_type = 'MANUAL' THEN 1 ELSE 0 END) AS manual,
                 SUM(CASE WHEN tr.run_type != 'MANUAL' THEN 1 ELSE 0 END) AS automated,
                 SUM(CASE WHEN tr.status = 'Pass' THEN 1 ELSE 0 END) AS passed,
                 SUM(CASE WHEN tr.status = 'Fail' THEN 1 ELSE 0 END) AS failed
               FROM test_runs tr
               JOIN test_cases tc ON tr.test_case_id = tc.id
               JOIN projects p ON tc.project_id = p.id
               WHERE p.user_id = %s""",
            (user_id,)
        )
        row = cursor.fetchone() or {}
        manual_runs = int(row.get('manual') or 0)
        automated_runs = int(row.get('automated') or 0)
        passed = int(row.get('passed') or 0)
        failed = int(row.get('failed') or 0)
        total = manual_runs + automated_runs
        automation_coverage = round(automated_runs / total * 100, 1) if total else 0
    except Exception:
        try:
            cursor.execute(
                """SELECT
                     SUM(CASE WHEN tr.status = 'Pass' THEN 1 ELSE 0 END) AS passed,
                     SUM(CASE WHEN tr.status = 'Fail' THEN 1 ELSE 0 END) AS failed,
                     COUNT(*) AS total
                   FROM test_runs tr
                   JOIN test_cases tc ON tr.test_case_id = tc.id
                   JOIN projects p ON tc.project_id = p.id
                   WHERE p.user_id = %s""",
                (user_id,)
            )
            row = cursor.fetchone() or {}
            passed = int(row.get('passed') or 0)
            failed = int(row.get('failed') or 0)
            automated_runs = int(row.get('total') or 0)
        except Exception:
            pass
    try:
        cursor.execute(
            """SELECT COUNT(*) AS c FROM bugs b
               LEFT JOIN test_cases tc ON b.test_case_id = tc.id
               LEFT JOIN projects p ON tc.project_id = p.id
               WHERE (p.user_id = %s OR b.assigned_to = %s)
                 AND b.severity = 'Critical' AND b.status NOT IN ('Resolved', 'Closed')""",
            (user_id, user_id)
        )
        critical = int((cursor.fetchone() or {}).get('c') or 0)
    except Exception:
        pass

    cursor.close()
    return {
        'total_projects': total_projects,
        'total_testcases': total_testcases,
        'total_runs': total_runs,
        'manual_runs': manual_runs,
        'automated_runs': automated_runs,
        'passed': passed,
        'failed': failed,
        'open_bugs': total_bugs - resolved,
        'critical_issues': critical,
        'avg_health_score': avg_health,
        'automation_coverage': automation_coverage,
        'total_bugs': total_bugs,
        'resolved_bugs': resolved,
        'resolution_rate': resolution_rate,
        'avg_resolution_days': avg_resolution_days,
    }


# ============================================================
# SUMMARY
# ============================================================

@reports_bp.route('/summary', methods=['GET'])
@jwt_required()
def summary():
    user_id = int(get_jwt_identity())
    try:
        return jsonify(_build_summary(user_id)), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@reports_bp.route('/detected-issues', methods=['GET'])
@jwt_required()
def detected_issues():
    from flask import request
    user_id = int(get_jwt_identity())
    source = (request.args.get('source') or '').upper() or None
    if source not in (None, 'MANUAL', 'AUTOMATED'):
        return jsonify({'error': 'source must be MANUAL or AUTOMATED'}), 400
    try:
        return jsonify(_latest_detected_findings(user_id, source)), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@reports_bp.route('/run-stats', methods=['GET'])
@jwt_required()
def run_stats():
    """Dashboard statistics with filters.

    Query params: run_type (MANUAL|AUTOMATED), project_id, test_case_id,
    status (Pass|Fail), date_from, date_to (YYYY-MM-DD).
    """
    from flask import request
    user_id = int(get_jwt_identity())
    run_type = (request.args.get('run_type') or '').upper() or None
    if run_type not in (None, 'MANUAL', 'AUTOMATED'):
        return jsonify({'error': 'run_type must be MANUAL or AUTOMATED'}), 400
    project_id = request.args.get('project_id')
    test_case_id = request.args.get('test_case_id')
    status = request.args.get('status')
    severity = (request.args.get('severity') or '').lower() or None
    if severity not in (None, 'critical', 'serious', 'moderate', 'minor'):
        return jsonify({'error': 'severity must be critical, serious, moderate or minor'}), 400
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    try:
        cursor = mysql.connection.cursor()
        conds = ["p.user_id = %s"]
        params = [user_id]
        has_run_type = True
        has_severity = True
        try:
            cursor.execute("SELECT run_type, worst_severity FROM test_runs LIMIT 0")
        except Exception:
            has_run_type = has_severity = False
        if run_type and has_run_type:
            conds.append("tr.run_type = %s")
            params.append(run_type)
        if project_id:
            conds.append("p.id = %s")
            params.append(project_id)
        if test_case_id:
            conds.append("tc.id = %s")
            params.append(test_case_id)
        if status:
            conds.append("tr.status = %s")
            params.append(status)
        if severity and has_severity:
            conds.append("tr.worst_severity = %s")
            params.append(severity)
        if date_from:
            conds.append("tr.run_at >= %s")
            params.append(date_from)
        if date_to:
            conds.append("tr.run_at < DATE_ADD(%s, INTERVAL 1 DAY)")
            params.append(date_to)
        where = " AND ".join(conds)
        cursor.execute(
            f"""SELECT COUNT(*) AS total,
                       SUM(CASE WHEN tr.status = 'Pass' THEN 1 ELSE 0 END) AS passed,
                       SUM(CASE WHEN tr.status = 'Fail' THEN 1 ELSE 0 END) AS failed,
                       AVG(tr.health_score) AS avg_score
                FROM test_runs tr
                JOIN test_cases tc ON tr.test_case_id = tc.id
                JOIN projects p ON tc.project_id = p.id
                WHERE {where}""",
            tuple(params)
        )
        row = cursor.fetchone() or {}
        cursor.close()
        total = int(row.get('total') or 0)
        return jsonify({
            'total': total,
            'passed': int(row.get('passed') or 0),
            'failed': int(row.get('failed') or 0),
            'avg_health_score': round(float(row.get('avg_score') or 0)),
            'filters': {
                'run_type': run_type, 'project_id': project_id,
                'test_case_id': test_case_id, 'status': status,
                'severity': severity,
                'date_from': date_from, 'date_to': date_to,
            },
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# HEALTH TREND
# ============================================================

@reports_bp.route('/health-trend', methods=['GET'])
@jwt_required()
def health_trend():
    user_id = int(get_jwt_identity())
    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """SELECT tr.id, tr.health_score, tr.run_at, tr.issues_found,
                      tc.title AS test_case_title
               FROM test_runs tr
               JOIN test_cases tc ON tr.test_case_id = tc.id
               JOIN projects p ON tc.project_id = p.id
               WHERE p.user_id = %s AND tr.health_score IS NOT NULL
               ORDER BY tr.run_at DESC LIMIT 20""",
            (user_id,)
        )
        runs = cursor.fetchall()
        cursor.close()
        runs.reverse()
        data = []
        for i, run in enumerate(runs):
            data.append({
                'run_number': i + 1,
                'health_score': run['health_score'],
                'issues_found': run['issues_found'] or 0,
                'date': run['run_at'].isoformat() if run['run_at'] else '',
                'test_case': (run['test_case_title'] or 'Unknown')[:30],
            })
        return jsonify(data), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# BUG BREAKDOWN
# ============================================================

@reports_bp.route('/bug-breakdown', methods=['GET'])
@jwt_required()
def bug_breakdown():
    user_id = int(get_jwt_identity())
    try:
        cursor = mysql.connection.cursor()
        base = """FROM bugs b
                  LEFT JOIN test_cases tc ON b.test_case_id = tc.id
                  LEFT JOIN projects p ON tc.project_id = p.id
                  WHERE (p.user_id = %s OR b.assigned_to = %s)"""
        params = (user_id, user_id)

        cursor.execute(f"SELECT b.status, COUNT(*) AS count {base} GROUP BY b.status", params)
        by_status = [{'name': row['status'], 'value': row['count']} for row in cursor.fetchall()]

        cursor.execute(f"SELECT b.severity, COUNT(*) AS count {base} GROUP BY b.severity", params)
        by_severity = [{'name': row['severity'], 'value': row['count']} for row in cursor.fetchall()]

        cursor.close()
        return jsonify({'by_status': by_status, 'by_severity': by_severity}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# TOP CATEGORIES
# ============================================================

@reports_bp.route('/top-categories', methods=['GET'])
@jwt_required()
def top_categories():
    user_id = int(get_jwt_identity())
    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """SELECT b.category, COUNT(*) AS count
               FROM bugs b
               LEFT JOIN test_cases tc ON b.test_case_id = tc.id
               LEFT JOIN projects p ON tc.project_id = p.id
               WHERE p.user_id = %s OR b.assigned_to = %s
               GROUP BY b.category ORDER BY count DESC LIMIT 8""",
            (user_id, user_id)
        )
        rows = cursor.fetchall()
        cursor.close()

        label_map = {
            'broken-link': 'Broken Link',
            'console-error': 'JS Error',
            'missing-alt': 'Missing Alt',
            'seo': 'SEO',
            'security': 'Security',
            'accessibility': 'Accessibility',
            'mobile': 'Mobile',
            'other': 'Other',
        }

        data = [{
            'category': label_map.get(row['category'], row['category']),
            'count': row['count']
        } for row in rows]
        return jsonify(data), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# TEST PASS RATE
# ============================================================

@reports_bp.route('/test-pass-rate', methods=['GET'])
@jwt_required()
def test_pass_rate():
    user_id = int(get_jwt_identity())
    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """SELECT DATE(tr.run_at) AS run_date,
                      SUM(CASE WHEN tr.status = 'Pass' THEN 1 ELSE 0 END) AS passed,
                      SUM(CASE WHEN tr.status = 'Fail' THEN 1 ELSE 0 END) AS failed
               FROM test_runs tr
               JOIN test_cases tc ON tr.test_case_id = tc.id
               JOIN projects p ON tc.project_id = p.id
               WHERE p.user_id = %s AND tr.run_at >= DATE_SUB(CURDATE(), INTERVAL 14 DAY)
               GROUP BY DATE(tr.run_at) ORDER BY run_date ASC""",
            (user_id,)
        )
        rows = cursor.fetchall()
        cursor.close()

        data = [{
            'date': row['run_date'].strftime('%b %d') if row['run_date'] else '',
            'passed': int(row['passed'] or 0),
            'failed': int(row['failed'] or 0),
        } for row in rows]
        return jsonify(data), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# RECENT RUNS
# ============================================================

@reports_bp.route('/recent-runs', methods=['GET'])
@jwt_required()
def recent_runs():
    user_id = int(get_jwt_identity())
    try:
        cursor = mysql.connection.cursor()
        try:
            cursor.execute(
                """SELECT tr.id, tr.status, tr.health_score, tr.issues_found,
                          tr.duration_ms, tr.run_at, tr.run_type, tr.worst_severity,
                          tr.regression, tr.coverage,
                          tc.title AS test_case_title,
                          p.name AS project_name
                   FROM test_runs tr
                   JOIN test_cases tc ON tr.test_case_id = tc.id
                   JOIN projects p ON tc.project_id = p.id
                   WHERE p.user_id = %s
                   ORDER BY tr.run_at DESC LIMIT 10""",
                (user_id,)
            )
        except Exception:
            cursor.execute(
                """SELECT tr.id, tr.status, tr.health_score, tr.issues_found,
                          tr.duration_ms, tr.run_at,
                          tc.title AS test_case_title,
                          p.name AS project_name
                   FROM test_runs tr
                   JOIN test_cases tc ON tr.test_case_id = tc.id
                   JOIN projects p ON tc.project_id = p.id
                   WHERE p.user_id = %s
                   ORDER BY tr.run_at DESC LIMIT 10""",
                (user_id,)
            )
        runs = cursor.fetchall()
        cursor.close()
        data = []
        for run in runs:
            data.append({
                'id': run['id'],
                'status': run['status'],
                'health_score': run['health_score'] or 0,
                'issues_found': run['issues_found'] or 0,
                'duration_ms': run['duration_ms'] or 0,
                'run_at': run['run_at'].isoformat() if run['run_at'] else '',
                'run_type': run.get('run_type', 'AUTOMATED'),
                'worst_severity': run.get('worst_severity'),
                'regression': _safe_json(run.get('regression')),
                'coverage': _safe_json(run.get('coverage')),
                'test_case_title': run['test_case_title'],
                'project_name': run['project_name'],
            })
        return jsonify(data), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# PDF EXPORT - The crown jewel!
# ============================================================

@reports_bp.route('/export-pdf', methods=['GET'])
@jwt_required()
def export_pdf():
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    )

    user_id = int(get_jwt_identity())

    try:
        # Get username for the report
        cursor = mysql.connection.cursor()
        cursor.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        user_row = cursor.fetchone()
        username = user_row['username'] if user_row else 'User'
        cursor.close()

        # Build all data needed for PDF
        summary_data = _build_summary(user_id)

        # Bug breakdown
        cursor = mysql.connection.cursor()
        base = """FROM bugs b
                  LEFT JOIN test_cases tc ON b.test_case_id = tc.id
                  LEFT JOIN projects p ON tc.project_id = p.id
                  WHERE (p.user_id = %s OR b.assigned_to = %s)"""
        params = (user_id, user_id)
        cursor.execute(f"SELECT b.status, COUNT(*) AS count {base} GROUP BY b.status", params)
        bug_by_status = list(cursor.fetchall())
        cursor.execute(f"SELECT b.severity, COUNT(*) AS count {base} GROUP BY b.severity", params)
        bug_by_severity = list(cursor.fetchall())

        # Top categories
        cursor.execute(
            """SELECT b.category, COUNT(*) AS count
               FROM bugs b
               LEFT JOIN test_cases tc ON b.test_case_id = tc.id
               LEFT JOIN projects p ON tc.project_id = p.id
               WHERE p.user_id = %s OR b.assigned_to = %s
               GROUP BY b.category ORDER BY count DESC LIMIT 8""",
            (user_id, user_id)
        )
        top_cats = list(cursor.fetchall())

        # Recent runs
        cursor.execute(
            """SELECT tr.status, tr.health_score, tr.issues_found, tr.run_at,
                      tc.title AS test_case_title, p.name AS project_name
               FROM test_runs tr
               JOIN test_cases tc ON tr.test_case_id = tc.id
               JOIN projects p ON tc.project_id = p.id
               WHERE p.user_id = %s
               ORDER BY tr.run_at DESC LIMIT 10""",
            (user_id,)
        )
        recent = list(cursor.fetchall())

        # Latest manual + automated runs for the split sections (B/C).
        # Guarded for pre-migration databases without run_type.
        latest_manual = latest_automated = None
        try:
            cursor.execute(
                """SELECT tr.status, tr.health_score, tr.issues_found, tr.run_at,
                          tr.verdict_reason, tc.title AS test_case_title,
                          p.name AS project_name
                   FROM test_runs tr
                   JOIN test_cases tc ON tr.test_case_id = tc.id
                   JOIN projects p ON tc.project_id = p.id
                   WHERE p.user_id = %s AND tr.run_type = 'MANUAL'
                   ORDER BY tr.run_at DESC LIMIT 3""",
                (user_id,)
            )
            latest_manual = list(cursor.fetchall())
            cursor.execute(
                """SELECT tr.status, tr.health_score, tr.issues_found, tr.run_at,
                          tr.verdict_reason, tc.title AS test_case_title,
                          p.name AS project_name
                   FROM test_runs tr
                   JOIN test_cases tc ON tr.test_case_id = tc.id
                   JOIN projects p ON tc.project_id = p.id
                   WHERE p.user_id = %s AND tr.run_type != 'MANUAL'
                   ORDER BY tr.run_at DESC LIMIT 3""",
                (user_id,)
            )
            latest_automated = list(cursor.fetchall())
        except Exception:
            latest_manual, latest_automated = None, None
        # Latest findings split by source for sections D/E/G-J.
        try:
            all_findings = _latest_detected_findings(user_id)
        except Exception:
            all_findings = []
        manual_findings = [f for f in all_findings if f.get('source') == 'MANUAL']
        auto_findings = [f for f in all_findings if f.get('source') != 'MANUAL']
        cursor.close()

        # Build PDF in memory
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter,
                                topMargin=0.5*inch, bottomMargin=0.5*inch,
                                leftMargin=0.7*inch, rightMargin=0.7*inch)

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            'Title', parent=styles['Heading1'],
            fontSize=22, textColor=colors.HexColor('#1e40af'),
            spaceAfter=10, alignment=0
        )
        subtitle_style = ParagraphStyle(
            'Subtitle', parent=styles['Normal'],
            fontSize=10, textColor=colors.HexColor('#6b7280'),
            spaceAfter=20
        )
        section_style = ParagraphStyle(
            'Section', parent=styles['Heading2'],
            fontSize=14, textColor=colors.HexColor('#1f2937'),
            spaceBefore=15, spaceAfter=8
        )

        story = []

        # COVER
        story.append(Paragraph("QA Platform Report", title_style))
        story.append(Paragraph(
            f"Generated for: <b>{username}</b> &nbsp;|&nbsp; "
            f"Date: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}",
            subtitle_style
        ))

        # SUMMARY KPIs
        story.append(Paragraph("A. Test Summary", section_style))
        summary_table_data = [
            ['Metric', 'Value'],
            ['Total Projects', str(summary_data['total_projects'])],
            ['Total Test Cases', str(summary_data['total_testcases'])],
            ['Total Test Runs', str(summary_data['total_runs'])],
            ['Average Health Score', f"{summary_data['avg_health_score']} / 100"],
            ['Total Bugs Logged', str(summary_data['total_bugs'])],
            ['Bugs Resolved', str(summary_data['resolved_bugs'])],
            ['Bug Resolution Rate', f"{summary_data['resolution_rate']}%"],
            ['Avg Time to Resolve', f"{summary_data['avg_resolution_days']} days"],
        ]
        t = Table(summary_table_data, colWidths=[3*inch, 2.5*inch])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e40af')),
            ('TEXTCOLOR', (0,0), (-1,0), colors.white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 11),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#d1d5db')),
            ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#f9fafb')),
            ('FONTSIZE', (0,1), (-1,-1), 10),
            ('PADDING', (0,0), (-1,-1), 8),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f9fafb')]),
        ]))
        story.append(t)

        # BUG STATUS
        if bug_by_status:
            story.append(Paragraph("Bugs by Status", section_style))
            bug_status_data = [['Status', 'Count']] + [[r['status'], str(r['count'])] for r in bug_by_status]
            t = Table(bug_status_data, colWidths=[3*inch, 2.5*inch])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#dc2626')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#d1d5db')),
                ('PADDING', (0,0), (-1,-1), 8),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#fef2f2')]),
            ]))
            story.append(t)

        # BUG SEVERITY
        if bug_by_severity:
            story.append(Paragraph("Bugs by Severity", section_style))
            sev_data = [['Severity', 'Count']] + [[r['severity'], str(r['count'])] for r in bug_by_severity]
            t = Table(sev_data, colWidths=[3*inch, 2.5*inch])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f97316')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#d1d5db')),
                ('PADDING', (0,0), (-1,-1), 8),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#fff7ed')]),
            ]))
            story.append(t)

        # TOP CATEGORIES
        if top_cats:
            story.append(Paragraph("Top Issue Categories", section_style))
            label_map = {
                'broken-link': 'Broken Link', 'console-error': 'JS Error',
                'missing-alt': 'Missing Alt', 'seo': 'SEO', 'security': 'Security',
                'accessibility': 'Accessibility', 'mobile': 'Mobile', 'other': 'Other',
            }
            cat_data = [['Category', 'Count']] + [
                [label_map.get(r['category'], r['category']), str(r['count'])] for r in top_cats
            ]
            t = Table(cat_data, colWidths=[3*inch, 2.5*inch])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#8b5cf6')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#d1d5db')),
                ('PADDING', (0,0), (-1,-1), 8),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f5f3ff')]),
            ]))
            story.append(t)

        # RECENT RUNS
        if recent:
            story.append(PageBreak())
            story.append(Paragraph("Recent Test Runs", section_style))
            runs_data = [['Status', 'Test Case', 'Project', 'Health', 'Issues', 'Date']]
            for r in recent:
                runs_data.append([
                    r['status'] or '-',
                    (r['test_case_title'] or '-')[:30],
                    (r['project_name'] or '-')[:20],
                    f"{r['health_score'] or 0}/100",
                    str(r['issues_found'] or 0),
                    r['run_at'].strftime('%m/%d %H:%M') if r['run_at'] else '-',
                ])
            t = Table(runs_data, colWidths=[0.7*inch, 1.8*inch, 1.3*inch, 0.8*inch, 0.7*inch, 1.1*inch])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#06b6d4')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 8),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#d1d5db')),
                ('PADDING', (0,0), (-1,-1), 6),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#ecfeff')]),
                ('ALIGN', (3,1), (4,-1), 'CENTER'),
            ]))
            story.append(t)

        # ---- B. Manual testing results ----
        story.append(Paragraph("B. Manual Testing Results", section_style))
        if latest_manual:
            rows = [['Test Case', 'Project', 'Status', 'Health', 'Issues', 'Date']]
            for r in latest_manual:
                rows.append([
                    (r['test_case_title'] or '-')[:28],
                    (r['project_name'] or '-')[:18],
                    r['status'] or '-',
                    f"{r['health_score'] or 0}/100",
                    str(r['issues_found'] or 0),
                    r['run_at'].strftime('%m/%d %H:%M') if r['run_at'] else '-',
                ])
            t = Table(rows, colWidths=[1.8*inch, 1.3*inch, 0.7*inch, 0.8*inch, 0.7*inch, 1.1*inch])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0d9488')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 8),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#d1d5db')),
                ('PADDING', (0,0), (-1,-1), 6),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f0fdfa')]),
            ]))
            story.append(t)
            for r in latest_manual[:1]:
                if r.get('verdict_reason'):
                    story.append(Paragraph(
                        f"Manual verdict basis: tester steps + expected-result decision + "
                        f"reported issues. {r['verdict_reason']}", styles['Normal']))
        else:
            story.append(Paragraph("No manual runs recorded yet.", styles['Normal']))

        # ---- C. Automated testing results ----
        story.append(Paragraph("C. Automated Testing Results", section_style))
        if latest_automated:
            rows = [['Test Case', 'Project', 'Status', 'Health', 'Issues', 'Date']]
            for r in latest_automated:
                rows.append([
                    (r['test_case_title'] or '-')[:28],
                    (r['project_name'] or '-')[:18],
                    r['status'] or '-',
                    f"{r['health_score'] or 0}/100",
                    str(r['issues_found'] or 0),
                    r['run_at'].strftime('%m/%d %H:%M') if r['run_at'] else '-',
                ])
            t = Table(rows, colWidths=[1.8*inch, 1.3*inch, 0.7*inch, 0.8*inch, 0.7*inch, 1.1*inch])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e40af')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 8),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#d1d5db')),
                ('PADDING', (0,0), (-1,-1), 6),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#eff6ff')]),
            ]))
            story.append(t)
            for r in latest_automated[:1]:
                if r.get('verdict_reason'):
                    story.append(Paragraph(
                        f"Automated verdict basis: detected issues + severity + "
                        f"health score. {r['verdict_reason']}", styles['Normal']))
        else:
            story.append(Paragraph("No automated runs recorded yet.", styles['Normal']))

        def _finding_rows(items, limit=20):
            rows = [['Source', 'Category', 'Issue', 'Severity']]
            for f in items[:limit]:
                rows.append([
                    f.get('source', '-'),
                    (f.get('category') or '-')[:18],
                    (f.get('issue') or '-')[:60],
                    f.get('severity', '-'),
                ])
            return rows

        # ---- D. Manual findings ----
        story.append(Paragraph(f"D. Manual Findings ({len(manual_findings)})", section_style))
        if manual_findings:
            t = Table(_finding_rows(manual_findings),
                      colWidths=[0.9*inch, 1.2*inch, 3.0*inch, 0.9*inch])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0d9488')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 8),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#d1d5db')),
                ('PADDING', (0,0), (-1,-1), 6),
            ]))
            story.append(t)
        else:
            story.append(Paragraph("No manual findings in the latest runs.", styles['Normal']))

        # ---- E. Automated findings ----
        story.append(Paragraph(f"E. Automated Findings ({len(auto_findings)})", section_style))
        if auto_findings:
            t = Table(_finding_rows(auto_findings),
                      colWidths=[0.9*inch, 1.2*inch, 3.0*inch, 0.9*inch])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1e40af')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 8),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#d1d5db')),
                ('PADDING', (0,0), (-1,-1), 6),
            ]))
            story.append(t)
        else:
            story.append(Paragraph("No automated findings in the latest runs.", styles['Normal']))

        # ---- F. Screenshots / evidence ----
        story.append(Paragraph("F. Screenshots / Evidence", section_style))
        story.append(Paragraph(
            "Per-step screenshots are pinned to each manual step and per-page "
            "screenshots to each automated crawl page. Open the run in the app "
            "to view them; file paths are stored on the run record.",
            styles['Normal']))

        # ---- G-J. Category sections drawn from automated findings ----
        for code, title in (("accessibility", "G. Accessibility Results"),
                            ("seo", "H. SEO Results"),
                            ("security", "I. Security Results"),
                            ("performance", "J. Performance Results")):
            story.append(Paragraph(title, section_style))
            cat_items = [f for f in auto_findings
                         if (f.get('category') or '').startswith(code)]
            if cat_items:
                t = Table(_finding_rows(cat_items, limit=10),
                          colWidths=[0.9*inch, 1.2*inch, 3.0*inch, 0.9*inch])
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#334155')),
                    ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0,0), (-1,-1), 8),
                    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#d1d5db')),
                    ('PADDING', (0,0), (-1,-1), 6),
                ]))
                story.append(t)
            else:
                story.append(Paragraph(f"No {code} findings in the latest runs.",
                                       styles['Normal']))

        # ---- K. Regression ----
        story.append(Paragraph("K. Regression Results", section_style))
        if recent and len(recent) >= 2:
            story.append(Paragraph(
                f"Latest run health {recent[0]['health_score'] or 0}/100 vs previous "
                f"{recent[1]['health_score'] or 0}/100. Open the test case history "
                f"for the new/fixed issue diff.", styles['Normal']))
        else:
            story.append(Paragraph("Not enough runs yet for a regression comparison.",
                                   styles['Normal']))

        # ---- L. Final verdict ----
        story.append(Paragraph("L. Final Verdict", section_style))
        story.append(Paragraph(
            "Manual runs are judged by the tester's steps, expected-result "
            "decision and reported issues; automated evidence never overrides "
            "that verdict. Automated runs pass only when no issues are found; "
            "the verdict reason on each run states the critical count and "
            "health score behind it.", styles['Normal']))

        # FOOTER
        story.append(Spacer(1, 0.3*inch))
        footer_style = ParagraphStyle(
            'Footer', parent=styles['Normal'],
            fontSize=8, textColor=colors.HexColor('#9ca3af'), alignment=1
        )
        story.append(Paragraph(
            "Generated by QA Platform | AI-Assisted Web Application Testing Platform",
            footer_style
        ))

        # Build the PDF
        doc.build(story)

        buffer.seek(0)
        filename = f"qa-platform-report-{datetime.now().strftime('%Y-%m-%d')}.pdf"
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        print(f"[PDF export error]: {e}")
        return jsonify({'error': f'PDF generation failed: {str(e)}'}), 500