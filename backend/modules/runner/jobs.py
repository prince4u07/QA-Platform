"""
Background job runner for automated test crawls.

The synchronous /run endpoint blocks the Flask worker for the whole crawl
(seconds to minutes). This manager offloads a crawl to a small thread pool and
exposes a job id the client can poll, so the request returns immediately.

Each worker wraps the crawl in app.app_context() so that mysql.connection
(flask_mysqldb) is valid on the worker thread — flask_mysqldb binds a fresh
connection to whatever app context is active, so no separate DB wiring is needed.

Durability: job state is mirrored to a `run_jobs` MySQL table so a backend
restart no longer makes a poll 404 ("Job not found"). The in-memory dict stays
the fast path / source of truth while the process is alive; the table is a
write-through copy used to answer polls after a restart. Persistence uses its
OWN short-lived MySQLdb connection per write (NOT flask_mysqldb's shared,
single-per-context connection) so concurrent worker/request threads never race
on one cursor. On startup, jobs left 'queued'/'running' by a crashed/restarted
process are reconciled to 'failed' so the client gets a clear interrupted
status instead of polling a job that will never advance.
"""

import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import MySQLdb
from config import Config

logger = logging.getLogger(__name__)

_MAX_JOBS_RETAINED = 200

_TERMINAL = ('done', 'failed', 'cancelled')


class JobManager:
    def __init__(self):
        self._app = None
        self._executor = None
        self._jobs = {}
        self._order = []          # job_ids in insertion order, for eviction
        self._lock = threading.Lock()
        self._db_ok = False       # flips False if the table can't be reached

    def init(self, app, max_workers=2):
        self._app = app
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix='runner'
        )
        self._ensure_table()
        self._reconcile_orphans()
        logger.info("JobManager initialised with %d workers (db persistence: %s)",
                    max_workers, self._db_ok)

    # ----- persistence (own connection per call; best-effort) -----

    def _db_conn(self):
        try:
            return MySQLdb.connect(
                host=Config.MYSQL_HOST, user=Config.MYSQL_USER,
                passwd=Config.MYSQL_PASSWORD, db=Config.MYSQL_DB,
                charset='utf8mb4',
            )
        except Exception as e:
            logger.warning("run_jobs: DB connect failed, persistence off: %s", e)
            return None

    def _ensure_table(self):
        conn = self._db_conn()
        if conn is None:
            return
        try:
            cur = conn.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS run_jobs (
                    job_id VARCHAR(32) PRIMARY KEY,
                    user_id INT NOT NULL,
                    testcase_id INT NULL,
                    status VARCHAR(20) NOT NULL,
                    run_id INT NULL,
                    error TEXT NULL,
                    progress TEXT NULL,
                    result LONGTEXT NULL,
                    created_at DATETIME NULL,
                    finished_at DATETIME NULL
                ) CHARACTER SET utf8mb4"""
            )
            conn.commit()
            cur.close()
            self._db_ok = True
        except Exception as e:
            logger.warning("run_jobs: table create failed, persistence off: %s", e)
        finally:
            conn.close()

    def _reconcile_orphans(self):
        """A job left 'queued'/'running' means the process died mid-crawl — its
        worker thread is gone, so it will never finish. Mark these failed so the
        client sees a clear interrupted message instead of an eternal 'running'."""
        if not self._db_ok:
            return
        conn = self._db_conn()
        if conn is None:
            return
        try:
            cur = conn.cursor()
            cur.execute(
                """UPDATE run_jobs
                   SET status = 'failed',
                       error = 'The run was interrupted — the backend restarted before it finished.',
                       finished_at = %s
                   WHERE status IN ('queued', 'running')""",
                (datetime.utcnow(),),
            )
            conn.commit()
            if cur.rowcount:
                logger.info("run_jobs: reconciled %d interrupted job(s) on startup", cur.rowcount)
            cur.close()
        except Exception as e:
            logger.warning("run_jobs: orphan reconcile failed: %s", e)
        finally:
            conn.close()

    def _persist_locked(self, job_id):
        """Write the current in-memory job snapshot to the table. Caller holds
        the lock so the snapshot is consistent; the DB I/O itself is best-effort
        and must never break the crawl."""
        if not self._db_ok:
            return
        job = self._jobs.get(job_id)
        if not job:
            return
        snapshot = dict(job)
        try:
            conn = self._db_conn()
            if conn is None:
                return
            try:
                cur = conn.cursor()
                cur.execute(
                    """INSERT INTO run_jobs
                        (job_id, user_id, testcase_id, status, run_id, error,
                         progress, result, created_at, finished_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON DUPLICATE KEY UPDATE
                         status=VALUES(status), run_id=VALUES(run_id),
                         error=VALUES(error), progress=VALUES(progress),
                         result=VALUES(result), finished_at=VALUES(finished_at)""",
                    (
                        snapshot['job_id'], snapshot['user_id'], snapshot.get('testcase_id'),
                        snapshot['status'], snapshot.get('run_id'),
                        (snapshot.get('error') or None),
                        json.dumps(snapshot.get('progress')) if snapshot.get('progress') else None,
                        json.dumps(snapshot.get('result')) if snapshot.get('result') else None,
                        _parse_iso(snapshot.get('created_at')),
                        _parse_iso(snapshot.get('finished_at')),
                    ),
                )
                conn.commit()
                cur.close()
            finally:
                conn.close()
        except Exception as e:
            logger.warning("run_jobs: persist failed for %s: %s", job_id, e)

    # ----- public API -----

    def submit(self, user_id, testcase_id, tc):
        if self._executor is None:
            raise RuntimeError("JobManager.init(app) was not called")

        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = {
                'job_id': job_id,
                'user_id': user_id,
                'testcase_id': testcase_id,
                'status': 'queued',          # queued -> running -> done | failed | cancelled
                'run_id': None,
                'error': None,
                'result': None,
                'progress': None,            # {tested, total, current, ...}
                'cancel_requested': False,
                'created_at': datetime.utcnow().isoformat(),
                'finished_at': None,
            }
            self._order.append(job_id)
            self._persist_locked(job_id)
            self._evict_old_locked()

        self._executor.submit(self._run, job_id, testcase_id, tc)
        logger.info("queued run job %s for testcase %s", job_id, testcase_id)
        return job_id

    def _run(self, job_id, testcase_id, tc):
        # Imported lazily to avoid a circular import at module load time.
        from modules.runner.routes import _perform_run

        self._set(job_id, status='running')

        def _progress(p):
            # p = {tested, total, current, issues_so_far, queued}
            self._set(job_id, progress=p)

        def _cancelled():
            with self._lock:
                j = self._jobs.get(job_id)
                return bool(j and j.get('cancel_requested'))

        try:
            with self._app.app_context():
                result, code = _perform_run(
                    testcase_id, tc,
                    progress_cb=_progress, cancelled_check=_cancelled,
                )
            if _cancelled():
                self._set(job_id, status='cancelled',
                          result=result, run_id=(result or {}).get('run_id'),
                          finished_at=datetime.utcnow().isoformat())
            elif code == 200:
                self._set(job_id, status='done',
                          run_id=result.get('run_id'), result=result,
                          finished_at=datetime.utcnow().isoformat())
            else:
                self._set(job_id, status='failed',
                          error=(result or {}).get('error', 'Run failed'),
                          result=result,
                          finished_at=datetime.utcnow().isoformat())
        except Exception as e:
            logger.exception("run job %s crashed: %s", job_id, e)
            self._set(job_id, status='failed', error=str(e)[:500],
                      finished_at=datetime.utcnow().isoformat())

    def _set(self, job_id, **kw):
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(kw)
                self._persist_locked(job_id)

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                return dict(job)
        # Not in memory — likely a poll after a restart. Fall back to the table.
        return self._get_from_db(job_id)

    def _get_from_db(self, job_id):
        if not self._db_ok:
            return None
        conn = self._db_conn()
        if conn is None:
            return None
        try:
            cur = conn.cursor(MySQLdb.cursors.DictCursor)
            cur.execute("SELECT * FROM run_jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            cur.close()
            if not row:
                return None
            return {
                'job_id': row['job_id'],
                'user_id': row['user_id'],
                'testcase_id': row['testcase_id'],
                'status': row['status'],
                'run_id': row['run_id'],
                'error': row['error'],
                'progress': _loads(row['progress']),
                'result': _loads(row['result']),
                'cancel_requested': False,
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                'finished_at': row['finished_at'].isoformat() if row['finished_at'] else None,
            }
        except Exception as e:
            logger.warning("run_jobs: db read failed for %s: %s", job_id, e)
            return None
        finally:
            conn.close()

    def cancel(self, job_id, user_id):
        """Mark a job for cancellation. The worker checks this between pages
        and exits the crawl loop, saving whatever's been captured so far."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get('user_id') != user_id:
                return False, 'Job not found'
            if job['status'] in _TERMINAL:
                return False, f"Job already {job['status']}"
            job['cancel_requested'] = True
        logger.info("cancel requested for job %s", job_id)
        return True, 'Cancel requested'

    def _evict_old_locked(self):
        # Caller holds the lock. Drop oldest finished jobs beyond the cap.
        # Only the in-memory copy is evicted; the DB row is retained so an old
        # job id still resolves after a restart.
        while len(self._order) > _MAX_JOBS_RETAINED:
            old = self._order.pop(0)
            self._jobs.pop(old, None)


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _loads(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


job_manager = JobManager()
