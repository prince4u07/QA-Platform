"""Tests for the background JobManager (no DB / no Playwright needed)."""

import time
import modules.runner.routes as routes
from modules.runner.jobs import JobManager


def _wait(mgr, job_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = mgr.get(job_id)
        if job and job['status'] in ('done', 'failed'):
            return job
        time.sleep(0.02)
    return mgr.get(job_id)


def test_job_runs_to_done(monkeypatch):
    import app as app_module

    mgr = JobManager()
    mgr.init(app_module.app, max_workers=1)

    # Replace the heavy crawl with a fast stub so no browser/DB is touched.
    monkeypatch.setattr(routes, '_perform_run',
                        lambda tc_id, tc, progress_cb=None, cancelled_check=None:
                        ({'run_id': 42}, 200))

    job_id = mgr.submit(user_id=7, testcase_id=1, tc={'base_url': 'https://x'})
    job = _wait(mgr, job_id)

    assert job['status'] == 'done'
    assert job['run_id'] == 42
    assert job['user_id'] == 7


def test_job_marks_failed_on_error_code(monkeypatch):
    import app as app_module

    mgr = JobManager()
    mgr.init(app_module.app, max_workers=1)
    monkeypatch.setattr(routes, '_perform_run',
                        lambda tc_id, tc, progress_cb=None, cancelled_check=None:
                        ({'error': 'boom'}, 500))

    job_id = mgr.submit(user_id=1, testcase_id=2, tc={})
    job = _wait(mgr, job_id)

    assert job['status'] == 'failed'
    assert job['error'] == 'boom'


def test_unknown_job_returns_none():
    mgr = JobManager()
    assert mgr.get('does-not-exist') is None
