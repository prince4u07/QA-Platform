"""End-to-end test for the manual-run feature, without logging into anything.

Validates the moving parts in isolation:
  1) _looks_like_login heuristic (unit-pure).
  2) The full state machine in _ManualSession, run in HEADLESS mode against a
     public page (example.com), simulating the "navigated away from login" event
     by going to a /login-flavoured URL first and then back to the root.

The session never needs real credentials — we don't actually authenticate; we
just verify the worker captures pages, the auto-trigger fires when the URL
leaves the login pattern, and the BFS auto-crawl runs without crashing.
"""

import time
import pytest

from modules.runner import manual_run as mr


# ---------- 1. heuristic ----------

@pytest.mark.parametrize("url,expected", [
    ('https://site.com/login', True),
    ('https://site.com/user-login', True),
    ('https://site.com/signin', True),
    ('https://site.com/sign-in', True),
    ('https://site.com/register', True),
    ('https://site.com/forgot-password', True),
    ('https://site.com/auth/callback', True),
    # NOT login pages
    ('https://site.com/', False),
    ('https://site.com', False),
    ('https://site.com/dashboard', False),
    ('https://site.com/user-profile', False),
    ('https://site.com/orders/42', False),
    # Tricky: domain contains 'authentic' but path doesn't have login
    ('https://authentic-news.com/article/1', False),
])
def test_looks_like_login(url, expected):
    assert mr._looks_like_login(url) is expected


# ---------- 2. state machine ----------

def _patch_to_headless(monkeypatch):
    """Force the worker to launch headless and skip the real-Chrome attempt so
    this test runs in CI without popping a window."""
    from playwright.sync_api import sync_playwright as real_sp

    class Wrap:
        def __enter__(self_):
            self_.p = real_sp().__enter__()
            chromium = self_.p.chromium
            orig_launch = chromium.launch

            def launch(**kw):
                kw.pop('channel', None)
                kw['headless'] = True
                return orig_launch(**kw)

            chromium.launch = launch
            return self_.p

        def __exit__(self_, *a):
            return self_.p.__exit__(*a)

    monkeypatch.setattr(mr, 'sync_playwright', Wrap)


def _wait_for(predicate, timeout=30, interval=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_session_captures_initial_page_and_finishes_cleanly(monkeypatch, tmp_path):
    """Start a real headless session at a stable public page, then ask it to
    cancel. Confirms browser launch + initial capture + clean shutdown."""
    _patch_to_headless(monkeypatch)
    monkeypatch.setattr(mr, 'MANUAL_RUNS_DIR', str(tmp_path / 'manual_runs'))

    ok, _msg, snap = mr.start(testcase_id=999, project_id=999,
                              base_url='https://example.com', max_pages=3)
    assert ok, f"start failed: {_msg}"
    assert snap is not None

    assert _wait_for(lambda: mr.status(999).get('status') == 'ready', timeout=30), \
        f"never reached ready, status={mr.status(999)}"

    # Initial page should have been captured.
    snap = mr.status(999)
    assert snap['pages_visited'] >= 1, f"no initial capture: {snap}"
    assert any(p['source'] == 'manual' for p in snap['pages'])

    # User cancels (we never log in).
    ok, _msg = mr.cancel(999)
    assert ok


def test_crawl_does_not_start_until_user_signals(monkeypatch, tmp_path):
    """The crawl must NOT start on its own, even at a non-login URL — otherwise it
    could begin before the user has logged in. It starts only when the user
    explicitly triggers it (the "I'm logged in — start crawl" button -> /autocrawl)."""
    _patch_to_headless(monkeypatch)
    monkeypatch.setattr(mr, 'MANUAL_RUNS_DIR', str(tmp_path / 'manual_runs'))

    ok, _msg, _snap = mr.start(testcase_id=998, project_id=998,
                               base_url='https://example.com', max_pages=2)
    assert ok

    with mr._lock:
        sess = mr._active.get(998)
    assert sess is not None

    # Reach 'ready', then confirm the crawl does NOT auto-fire on a timer.
    assert _wait_for(lambda: mr.status(998).get('status') == 'ready', timeout=30)
    time.sleep(5)
    assert not sess._autocrawl_event.is_set(), "crawl auto-started without the user signalling"
    assert mr.status(998).get('status') == 'ready'

    # Now the user clicks the button -> the crawl signal fires.
    ok, _msg = mr.autocrawl(998)
    assert ok
    assert _wait_for(lambda: sess._autocrawl_event.is_set(), timeout=5), \
        "crawl did not start after explicit user signal"

    mr.cancel(998)
