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

reports_bp = Blueprint('reports', __name__)
mysql = None


def init_reports(app_mysql):
    global mysql
    mysql = app_mysql


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

    cursor.close()
    return {
        'total_projects': total_projects,
        'total_testcases': total_testcases,
        'total_runs': total_runs,
        'avg_health_score': avg_health,
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
        story.append(Paragraph("Summary Statistics", section_style))
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