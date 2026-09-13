"""
Backfill run metadata for databases created before the run-type columns existed.

Fills, for every test_runs row with run_type IS NULL:
  run_type, execution_mode, source, verdict_reason, worst_severity
and, for manual runs, the per-step and per-issue evidence rows derived from
the stored findings_evidence JSON.

Run after migrate_db.py (which creates the columns/tables):

    python migrate_db.py
    python backfill_run_metadata.py

It only ever UPDATEs NULL metadata and INSERTs evidence rows that do not
exist yet (checked per test_run_id), so it is safe to run repeatedly.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import MySQLdb

from config import Config

try:
    from modules.runner.routes import (
        SEVERITY_ORDER,
        finding_severity,
        worst_severity,
    )
except Exception as e:
    print(f"x could not import scoring helpers: {e}")
    sys.exit(1)

# Top-level findings_evidence keys that are bookkeeping, not findings.
BOOKKEEPING = {
    'manual', 'run_type', 'execution_mode', 'source', 'note', 'pages',
    'steps', 'functional_issues', 'reported', 'expected_result',
    'expected_met', 'summary', 'verdict_reason',
}

REQUIRED_COLUMNS = ('run_type', 'execution_mode', 'source',
                    'verdict_reason', 'worst_severity')


def has_columns(cursor):
    cursor.execute(
        """SELECT COLUMN_NAME FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'test_runs'""")
    present = {row[0] for row in cursor.fetchall()}
    return all(c in present for c in REQUIRED_COLUMNS)


def has_table(cursor, table):
    cursor.execute(
        """SELECT COUNT(*) FROM information_schema.TABLES
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s""", (table,))
    return bool(cursor.fetchone()[0])


def severity_counts(findings):
    counts = {sev: 0 for sev in SEVERITY_ORDER}
    for category, items in (findings or {}).items():
        if category in BOOKKEEPING:
            continue
        for item in items or []:
            if not isinstance(item, dict):
                continue
            sev = finding_severity(category, item)
            counts[sev] = counts.get(sev, 0) + 1
    return counts


def main():
    conn = MySQLdb.connect(
        host=Config.MYSQL_HOST,
        user=Config.MYSQL_USER,
        passwd=Config.MYSQL_PASSWORD,
        db=Config.MYSQL_DB,
    )
    cursor = conn.cursor()

    if not has_columns(cursor):
        print("x test_runs is missing the new metadata columns.")
        print("  Run 'python migrate_db.py' first, then re-run this script.")
        return 1
    steps_table = has_table(cursor, 'manual_step_evidence')
    issues_table = has_table(cursor, 'manual_issues')
    if not (steps_table and issues_table):
        print("x manual evidence tables are missing.")
        print("  Run 'python migrate_db.py' first, then re-run this script.")
        return 1

    cursor.execute(
        """SELECT id, test_case_id, status, health_score, findings_evidence
           FROM test_runs
           WHERE run_type IS NULL
              OR (verdict_reason IS NULL AND worst_severity IS NULL)
           ORDER BY id ASC""")
    rows = cursor.fetchall()
    print(f"- {len(rows)} run(s) need metadata backfill")

    updated = steps_added = issues_added = 0
    for row in rows:
        run_id = row[0]
        case_id = row[1]
        status = row[2] or 'Fail'
        score = row[3] or 0
        try:
            evidence = json.loads(row[4] or '{}')
        except (TypeError, ValueError):
            evidence = {}
        if not isinstance(evidence, dict):
            evidence = {}

        if evidence.get('manual') is True:
            run_type, execution = 'MANUAL', 'HEADED'
            auto_keys = [k for k, v in evidence.items()
                         if k not in BOOKKEEPING and isinstance(v, list) and v]
            source = 'HYBRID' if auto_keys else 'HUMAN'
            steps = evidence.get('steps') or []
            failed = sum(1 for s in steps
                         if isinstance(s, dict) and s.get('status') == 'failed')
            reported = evidence.get('reported') or []
            if evidence.get('expected_met') is False:
                verdict = 'Tester marked the expected result as not met.'
            elif failed:
                verdict = f'{failed} manual step(s) failed.'
            elif reported:
                verdict = f'{len(reported)} issue(s) reported by the tester.'
            elif status == 'Pass':
                verdict = 'All checked steps passed and no issues were reported.'
            else:
                verdict = 'Tester marked the run as failed.'
            worst = worst_severity({
                'functional_issues': evidence.get('functional_issues') or [],
                'reported_issues': reported,
                **{k: evidence.get(k) or [] for k in auto_keys},
            })
        else:
            run_type, execution, source = 'AUTOMATED', 'HEADLESS', 'MACHINE'
            counts = severity_counts(evidence)
            total = sum(counts.values())
            if status == 'Cancelled':
                verdict = 'Cancelled by the tester before the crawl finished.'
            elif status == 'Pass':
                verdict = f'No issues found (health {score}/100).'
            elif counts.get('critical'):
                verdict = (f"{counts['critical']} critical issue(s) found "
                           f'(health {score}/100).')
            else:
                verdict = f'{total} issue(s) found (health {score}/100).'
            worst = worst_severity(evidence)

        cursor.execute(
            """UPDATE test_runs
               SET run_type = %s, execution_mode = %s, source = %s,
                   verdict_reason = %s, worst_severity = %s
               WHERE id = %s""",
            (run_type, execution, source, verdict[:500], worst, run_id))
        updated += 1

        if run_type == 'MANUAL':
            cursor.execute(
                "SELECT COUNT(*) FROM manual_step_evidence WHERE test_run_id = %s",
                (run_id,))
            if cursor.fetchone()[0] == 0:
                for s in (evidence.get('steps') or []):
                    if not isinstance(s, dict):
                        continue
                    cursor.execute(
                        """INSERT INTO manual_step_evidence (
                            test_run_id, test_case_id, step_index, step_text,
                            result, note, screenshot, issue_ref
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (run_id, case_id, s.get('index', 0),
                         (s.get('text') or '')[:1000],
                         s.get('status', 'pending'),
                         (s.get('note') or '')[:2000],
                         (s.get('screenshot') or '')[:500],
                         str(s.get('issue_ref') or '')[:100] or None))
                    steps_added += 1
            cursor.execute(
                "SELECT COUNT(*) FROM manual_issues WHERE test_run_id = %s",
                (run_id,))
            if cursor.fetchone()[0] == 0:
                for r in (evidence.get('reported') or []):
                    if not isinstance(r, dict):
                        continue
                    cursor.execute(
                        """INSERT INTO manual_issues (
                            test_run_id, test_case_id, title, description, category,
                            severity, expected_result, actual_result, url, screenshot,
                            browser_device, step_index, source
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (run_id, case_id, (r.get('title') or r.get('issue') or '')[:255],
                         (r.get('description') or r.get('note') or '')[:2000],
                         r.get('category', 'other'),
                         r.get('severity', 'moderate'),
                         (r.get('expected_result') or '')[:2000],
                         (r.get('actual_result') or '')[:2000],
                         (r.get('url') or r.get('page_url') or '')[:2000],
                         (r.get('screenshot') or '')[:500],
                         (r.get('browser_device') or '')[:255],
                         r.get('step_index'), 'MANUAL'))
                    issues_added += 1

    conn.commit()
    cursor.close()
    conn.close()

    print("\n" + "=" * 60)
    print(f"runs updated      : {updated}")
    print(f"step rows added   : {steps_added}")
    print(f"issue rows added  : {issues_added}")
    print("\n- backfill complete")
    return 0


if __name__ == '__main__':
    sys.exit(main())
