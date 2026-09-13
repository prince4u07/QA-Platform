"""
Background job runner for automated test crawls.

The synchronous /run endpoint blocks the Flask worker for the whole crawl
(seconds to minutes). This manager offloads a crawl to a small thread pool and
exposes a job id the client can poll, so the request returns immediately.

Each worker wraps the crawl in app.app_context() so that mysql.connection
(flask_mysqldb) is valid on the worker thread — flask_mysqldb binds a fresh
connection to whatever app context is active, so no separate DB wiring is needed.

In-memory job registry: fine for a single-process dev/prod server. For multiple
processes, move job state to a shared store (Redis/DB) and the pool to Celery/RQ.
"""

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

logger = logging.getLogger(__name__)

_MAX_JOBS_RETAINED = 200

# A job in one of these states will never change again, so it is safe to
# evict. Anything else (queued / running) is still live: evicting it makes
# polls 404 with "Job not found" while the worker is still crawling.
_FINISHED_STATES = ('done', 'failed', 'cancelled')


class JobManager:
    def __init__(self):
        self._app = None
        self._executor = None
        self._jobs = {}
        self._order = []          # job_ids in insertion order, for eviction
        self._lock = threading.Lock()

    def init(self, app, max_workers=2):
        self._app = app
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix='runner'
        )
        logger.info("JobManager initialised with %d workers", max_workers)

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
                'error_code': None,
                'result': None,
                'progress': None,            # {phase, tested, total, pages_*, current_page, ...}
                'cancel_requested': False,
                'created_at': datetime.utcnow().isoformat(),
                'started_at': None,
                'finished_at': None,
            }
            self._order.append(job_id)
            self._evict_old_locked()

        try:
            self._executor.submit(self._run, job_id, testcase_id, tc)
        except RuntimeError:
            # Pool is shut down: drop the entry rather than leaving a job
            # stuck in 'queued' forever that every poll reports as live.
            with self._lock:
                self._jobs.pop(job_id, None)
                if job_id in self._order:
                    self._order.remove(job_id)
            raise
        logger.info("queued run job %s for testcase %s", job_id, testcase_id)
        return job_id

    def _run(self, job_id, testcase_id, tc):
        # Imported lazily to avoid a circular import at module load time.
        from modules.runner.routes import _perform_run

        self._set(job_id, status='running', started_at=datetime.utcnow().isoformat())

        def _progress(p):
            # p = {phase, tested, total, pages_discovered, pages_tested,
            #      current_page, current_category, issues_so_far, queued, duration_ms}
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
                          error_code=(result or {}).get('code', 'run_failed'),
                          result=result,
                          finished_at=datetime.utcnow().isoformat())
        except Exception as e:
            logger.exception("run job %s crashed: %s", job_id, e)
            self._set(job_id, status='failed', error=str(e)[:500],
                      error_code='worker_crash',
                      finished_at=datetime.utcnow().isoformat())

    def _set(self, job_id, **kw):
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(kw)

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def cancel(self, job_id, user_id):
        """Mark a job for cancellation. The worker checks this between pages
        and exits the crawl loop, saving whatever's been captured so far."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get('user_id') != user_id:
                return False, 'Job not found'
            if job['status'] in ('done', 'failed', 'cancelled'):
                return False, f"Job already {job['status']}"
            job['cancel_requested'] = True
        logger.info("cancel requested for job %s", job_id)
        return True, 'Cancel requested'

    def _evict_old_locked(self):
        # Caller holds the lock. Drop oldest finished jobs beyond the cap.
        # Live jobs are never evicted, even past the cap: losing a running
        # job's record turns its polls into "Job not found" while the crawl
        # is still going, which is exactly the failure this manager exists
        # to avoid reporting.
        while len(self._order) > _MAX_JOBS_RETAINED:
            victim = None
            for old in self._order:
                if self._jobs.get(old, {}).get('status') in _FINISHED_STATES:
                    victim = old
                    break
            if victim is None:
                break
            self._order.remove(victim)
            self._jobs.pop(victim, None)


job_manager = JobManager()
