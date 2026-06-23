"""
Manual login + session capture (Chunk D-2).

Flow:
  1. /start-login  -> launches a HEADED Chromium window pointed at the project URL.
                      The browser stays open in a background thread waiting for the user.
  2. User logs in manually in that window (email / phone / OTP / captcha / OAuth - anything).
  3. /save-session -> signals the thread to dump cookies + localStorage to
                      static/uploads/sessions/<project_id>.json, then closes the browser.
  4. /cancel-login -> closes the browser without saving.

Playwright's sync API is thread-affine: each login window owns its Playwright
objects on a single background thread, and all browser calls happen on that thread.
The Flask request threads only signal the worker via events and read its status.
DB writes are done by the request thread (where mysql.connection is valid), not here.
"""

import os
import threading
import time
import logging
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)


def _origin(url):
    """scheme://host for a URL, or '' if it can't be parsed."""
    try:
        p = urlparse(url)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    except Exception:
        pass
    return ''


def _open_login_page(page, base_url):
    """
    Navigate the login window tolerantly.

    Login URLs often redirect (to SSO/OAuth), which cancels a strict
    'domcontentloaded' navigation with net::ERR_ABORTED. We wait only until
    the navigation commits, and fall back to the site root if the deep link
    aborts, so the user always sees a usable page to log in on.
    """
    origin = _origin(base_url)
    targets = [base_url]
    if origin and origin != base_url:
        targets.append(origin)

    for target in targets:
        try:
            page.goto(target, timeout=60000, wait_until='commit')
            return True
        except Exception as e:
            logger.warning("session-capture goto '%s' failed: %s", target, e)
    logger.warning("session-capture auto-load failed; user can navigate manually in the open window.")
    return False

SESSIONS_DIR = os.path.join('static', 'uploads', 'sessions')
LOGIN_TIMEOUT_SECS = 600          # auto-close the window after 10 idle minutes
SAVE_WAIT_SECS = 45               # how long /save-session waits for the worker to finish

# project_id (int) -> _LoginSession
_active = {}
_lock = threading.Lock()


class _LoginSession:
    """Owns one headed Chromium window on its own thread until the user is done."""

    def __init__(self, project_id, base_url):
        self.project_id = project_id
        self.base_url = base_url
        self._action_event = threading.Event()   # set when user clicks Done/Cancel
        self.done_event = threading.Event()       # set when the worker has fully finished
        self.action = None                        # 'save' | 'cancel'
        self.status = 'starting'                  # starting|ready|saving|saved|cancelled|timeout|error
        self.error = None
        self.saved_path = None
        self._thread = threading.Thread(target=self._run, name=f'login-{project_id}', daemon=True)

    # ----- worker thread -----
    def _run(self):
        try:
            with sync_playwright() as p:
                # Stealth args: hide the "Chrome is being controlled by automated
                # test software" banner and the navigator.webdriver flag. Some sites
                # (DigiELV among them) gate their login API on automation detection
                # and silently hang the request from a vanilla Playwright window.
                # Prefer the user's real installed Chrome (channel='chrome'); the
                # fingerprint is indistinguishable from a normal browser. Fall back
                # to bundled Chromium if Chrome isn't installed.
                stealth_args = ['--start-maximized',
                                '--disable-blink-features=AutomationControlled']
                try:
                    browser = p.chromium.launch(
                        channel='chrome', headless=False, args=stealth_args,
                    )
                    logger.info("session-capture: using installed Chrome")
                except Exception as e:
                    logger.warning("real Chrome not available (%s); using bundled Chromium", e)
                    browser = p.chromium.launch(headless=False, args=stealth_args)
                context = browser.new_context(no_viewport=True)
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
                page = context.new_page()

                # Tolerant navigation: handles login pages that redirect to SSO/OAuth
                # (which would otherwise abort a strict 'domcontentloaded' load).
                _open_login_page(page, self.base_url)

                self.status = 'ready'

                triggered = self._action_event.wait(timeout=LOGIN_TIMEOUT_SECS)

                if not triggered:
                    self.status = 'timeout'
                elif self.action == 'save':
                    self.status = 'saving'
                    os.makedirs(SESSIONS_DIR, exist_ok=True)
                    path = os.path.join(SESSIONS_DIR, f'{self.project_id}.json')
                    context.storage_state(path=path)

                    # Capture sessionStorage too. Playwright's storage_state only
                    # saves cookies + localStorage; many SPAs (digielv, lots of
                    # React apps) keep their auth flag in sessionStorage, so
                    # without this the crawler appears anonymous to the SPA.
                    try:
                        session_storage = page.evaluate(
                            "() => { const o={}; for (let i=0; i<sessionStorage.length; i++)"
                            " { const k=sessionStorage.key(i); o[k]=sessionStorage.getItem(k); } return o; }"
                        ) or {}
                    except Exception as e:
                        session_storage = {}
                        logger.warning("could not read sessionStorage: %s", e)
                    if session_storage:
                        ss_path = os.path.join(SESSIONS_DIR, f'{self.project_id}.session_storage.json')
                        try:
                            import json as _json
                            with open(ss_path, 'w', encoding='utf-8') as f:
                                _json.dump(session_storage, f)
                            logger.info("session-capture: saved %d sessionStorage keys", len(session_storage))
                        except Exception as e:
                            logger.warning("could not save sessionStorage: %s", e)

                    # Also remember where the user landed after logging in. That's
                    # the authenticated dashboard URL — the crawler will start from
                    # there instead of the configured base_url (often a login page,
                    # which on this site stays public even when authenticated).
                    try:
                        landing_url = page.url or ''
                    except Exception:
                        landing_url = ''
                    if landing_url:
                        landing_path = os.path.join(SESSIONS_DIR, f'{self.project_id}.landing')
                        try:
                            with open(landing_path, 'w', encoding='utf-8') as f:
                                f.write(landing_url)
                            logger.info("session-capture: saved landing URL = %s", landing_url)
                        except Exception as e:
                            logger.warning("could not save landing URL: %s", e)
                    self.saved_path = path
                    self.status = 'saved'
                else:
                    self.status = 'cancelled'

                browser.close()
        except Exception as e:
            self.status = 'error'
            self.error = str(e)
            logger.exception("session-capture worker error (project %s): %s", self.project_id, e)
        finally:
            self.done_event.set()

    # ----- control (called from Flask request threads) -----
    def start(self):
        self._thread.start()

    def is_alive(self):
        return self._thread.is_alive()

    def signal_save(self):
        self.action = 'save'
        self._action_event.set()

    def signal_cancel(self):
        self.action = 'cancel'
        self._action_event.set()


# ============================================================
# Public API used by routes
# ============================================================

def start_login(project_id, base_url):
    """Open a headed login window. Returns (ok, message)."""
    with _lock:
        existing = _active.get(project_id)
        if existing and existing.is_alive():
            # Replace any previous (possibly stale) window for this project.
            existing.signal_cancel()
        session = _LoginSession(project_id, base_url)
        _active[project_id] = session

    session.start()

    # Give the worker a moment to launch / reach 'ready' so we can report failures.
    deadline = time.time() + 8
    while time.time() < deadline:
        if session.status in ('ready', 'error'):
            break
        if session.done_event.is_set():
            break
        time.sleep(0.1)

    if session.status == 'error':
        with _lock:
            _active.pop(project_id, None)
        return False, session.error or 'Failed to launch browser'

    return True, 'Browser opened - log in, then click "I am logged in".'


def save_session(project_id):
    """Signal the worker to persist the session. Blocks until done. Returns (ok, message, path)."""
    with _lock:
        session = _active.get(project_id)

    if not session or not session.is_alive():
        return False, 'No active login window. Click "Open & Login" first.', None

    session.signal_save()
    session.done_event.wait(timeout=SAVE_WAIT_SECS)

    with _lock:
        _active.pop(project_id, None)

    if session.status == 'saved' and session.saved_path:
        return True, 'Session saved.', session.saved_path
    if session.status == 'error':
        return False, session.error or 'Failed to save session', None
    return False, 'Timed out saving session. Please try again.', None


def cancel_login(project_id):
    """Signal the worker to close without saving. Returns (ok, message)."""
    with _lock:
        session = _active.get(project_id)

    if not session or not session.is_alive():
        with _lock:
            _active.pop(project_id, None)
        return True, 'No active login window.'

    session.signal_cancel()
    session.done_event.wait(timeout=10)
    with _lock:
        _active.pop(project_id, None)
    return True, 'Login cancelled.'


def login_status(project_id):
    """Return the current worker status for polling. Always returns a dict."""
    with _lock:
        session = _active.get(project_id)
    if not session:
        return {'active': False, 'status': 'idle'}
    return {
        'active': session.is_alive(),
        'status': session.status,
        'error': session.error,
    }
