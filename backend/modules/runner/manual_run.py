"""
Tracked manual test runs.

Opens a HEADED Chromium for the user to walk through a test case by hand.
Every page navigation is auto-captured as a screenshot to
static/uploads/manual_runs/<run_id>/, and a list of visited URLs is kept.
When the user clicks Done (Pass/Fail) in the frontend, the worker shuts
the browser down and the route layer writes a test_runs record.

Thread model mirrors session_capture: Playwright sync API is thread-affine,
so each manual run owns its objects on one background thread; Flask request
threads only signal it via events and read its status.
"""

import os
import re
import time
import uuid
import logging
import threading
from datetime import datetime
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)


MANUAL_RUNS_DIR = os.path.join('static', 'uploads', 'manual_runs')
MANUAL_DEFAULT_MAX_PAGES = 25       # when the test case does not set a real limit
MANUAL_MAX_PAGES_CAP = 100
MAX_DURATION_SECS = 1800            # auto-close window after 30 idle minutes
SCREENSHOT_DEBOUNCE_SECS = 1.0      # collapse rapid same-page redirects
AUTO_TRIGGER_STABLE_SECS = 5.0      # off-login URL must hold this long to auto-crawl
# How often the worker checks whether the tester has navigated. Lower means the
# page tracker keeps up more closely; it is a named constant so tests can poll
# fast instead of sleeping through a real-time interval they do not need.
WATCH_POLL_SECS = 1.5


def _looks_like_login(url):
    """
    Does this URL belong to an unauthenticated login/auth screen?

    Delegates to the crawler's copy so both halves of the product agree on
    what a login page is. Imported lazily because routes.py imports this
    module at load time.
    """
    from modules.runner.routes import looks_like_login
    return looks_like_login(url)


def _settle(page):
    """
    Wait for the page to stop changing before screenshotting or auditing it.

    Replaces the fixed sleeps that used to sit here. A flat sleep is a bet on
    machine speed: too short and the screenshot catches a half-rendered page,
    too long and every capture pays for the worst case. Shares the crawler's
    implementation so both halves settle identically.
    """
    from modules.runner.routes import wait_for_page_settled
    return wait_for_page_settled(page)


# What a manual tester is actually looking for. Judgement calls no
# automated check can make, which is why a human is doing this at all.
# Keys are stable slugs stored in the DB; labels are shown in the UI.
ISSUE_CATEGORIES = {
    'functional': 'Functional',
    'ui-ux': 'UI/UX',
    'layout': 'Layout',
    'accessibility': 'Accessibility',
    'content': 'Content',
    'business-logic': 'Business Logic',
    'performance': 'Performance',
    'broken-link': 'Broken Link',
    'other': 'Other',
}
# Backwards-compatible aliases accepted from older clients/data.
_ISSUE_CATEGORY_ALIASES = {
    'design': 'ui-ux',
    'business': 'business-logic',
    'broken_link': 'broken-link',
    'functional-error': 'functional',
}
ISSUE_SEVERITIES = ('critical', 'serious', 'moderate', 'minor')

_STEP_NUMBERING = re.compile(r'^\s*\d+\s*[.)\]-]\s*')


def resolve_manual_page_limit(stored):
    """
    How many pages one manual run may crawl.

    A stored limit of 0 or 1 is treated as "not set" rather than obeyed. The
    test-case form saved max_pages=1 for every manual test case back when
    manual runs could not crawl at all, and the landing page is captured
    before auto-crawl begins, so a limit of 1 made the crawl loop exit
    immediately and a manual run only ever recorded the page it opened on.
    """
    try:
        value = int(stored or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 1:
        return MANUAL_DEFAULT_MAX_PAGES
    return min(value, MANUAL_MAX_PAGES_CAP)


def _normalise_issue_category(category):
    """Map old/alias slugs to the canonical category set."""
    key = (category or 'other').strip().lower().replace(' ', '-').replace('_', '-')
    key = _ISSUE_CATEGORY_ALIASES.get(key, key)
    return key if key in ISSUE_CATEGORIES else 'other'


def _checklist_from(steps_text):
    """
    Turn the test case's Steps into a checklist the tester ticks off.

    Numbering is stripped because the UI numbers them itself, and blank lines
    are dropped so an empty line never becomes a step nobody can complete.
    """
    items = []
    for line in (steps_text or '').splitlines():
        text = _STEP_NUMBERING.sub('', line).strip()
        if not text:
            continue
        items.append({
            'index': len(items),
            'text': text,
            'status': 'pending',    # pending | passed | failed | skipped
            'note': '',
            'screenshot': '',
            'updated_at': None,
            'issue_ref': None,
        })
    return items


# testcase_id (int) -> _ManualSession
_active = {}
_lock = threading.Lock()


def _same_url(a, b):
    """Same page ignoring a trailing slash or fragment."""
    norm = lambda u: (u or '').rstrip('/').split('#')[0]
    return norm(a) == norm(b)


def should_auto_trigger(current_url, entry_url, navigated, stable_secs,
                        threshold=None):
    """
    Decide whether the manual run may start its auto-crawl on its own.

    The crawl fires only once the tester has actually gone somewhere. The
    old rule — "off the login page and stable for 5s" — also fired when the
    run *started* on a page that merely looks public: a login form served
    at '/' or a custom auth path the login detector does not recognise.
    That crawled before the tester had logged in. Sitting still on the
    entry page is never evidence of being logged in; navigating is.
    """
    if threshold is None:
        threshold = AUTO_TRIGGER_STABLE_SECS
    if _looks_like_login(current_url):
        return False
    if stable_secs < threshold:
        return False
    if not navigated and _same_url(current_url, entry_url):
        return False
    return True


class _ManualSession:
    """Owns one headed Chromium window on its own thread until the user clicks Done."""

    def __init__(self, testcase_id, project_id, base_url, max_pages=25,
                 steps_text='', expected_result=''):
        self.testcase_id = testcase_id
        self.project_id = project_id
        self.base_url = base_url
        self.max_pages = resolve_manual_page_limit(max_pages)
        self.run_id = uuid.uuid4().hex
        self.run_dir = os.path.join(MANUAL_RUNS_DIR, self.run_id)

        # status: starting | ready | crawling | finishing | done | cancelled | timeout | error
        # plus paused (tester paused the session; worker waits, browser stays open)
        self.status = 'starting'
        self.error = None
        self.outcome = None         # 'Pass' | 'Fail'  (set by /done)
        self.note = ''              # optional user note
        self.started_at = datetime.utcnow()
        self.finished_at = None
        self.paused = False
        self.current_url = base_url
        self.current_title = ''
        self.browser_device = ''

        # Console evidence collected while the tester browses (assistance
        # only — never part of the manual verdict).
        self._console = []

        # Live snapshot for the UI to poll.
        self.pages = []             # [{url, screenshot, captured_at, source: 'manual'|'auto'}]

        # The test case's written steps, as a checklist the tester works
        # through. This is what makes Steps worth writing for a manual test.
        self.steps = _checklist_from(steps_text)
        self.expected_result = (expected_result or '').strip()
        self.expected_met = None    # True | False, answered at the end

        # Issues the tester reported while testing: the layout flaws, poor
        # design and business-logic mismatches a machine cannot judge.
        self.reported = []

        # Issues found automatically on the pages the tester walked through,
        # so broken links and accessibility faults are caught for them.
        self.auto_findings = []
        self.auto_checked_pages = 0

        self._action_event = threading.Event()
        self._autocrawl_event = threading.Event()
        self.done_event = threading.Event()
        self.action = None          # 'done' | 'cancel'
        self._lock = threading.Lock()
        self._last_capture = 0.0
        self._thread = threading.Thread(
            target=self._run, name=f'manual-run-{testcase_id}', daemon=True
        )

    # ---- snapshot for the API ----
    def snapshot(self):
        with self._lock:
            steps = [dict(s) for s in self.steps]
            done = sum(1 for s in steps if s['status'] != 'pending')
            failed = sum(1 for s in steps if s['status'] == 'failed')
            total = len(steps)
            progress_pct = int(round(done / total * 100)) if total else 0
            last_page = self.pages[-1] if self.pages else {}
            return {
                'run_id': self.run_id,
                'testcase_id': self.testcase_id,
                'project_id': self.project_id,
                'status': 'paused' if self.paused and self.status == 'ready' else self.status,
                'paused': self.paused,
                'pages_visited': len(self.pages),
                'pages': list(self.pages),
                'current_url': self.current_url,
                'current_title': self.current_title,
                'current_screenshot': last_page.get('screenshot', ''),
                'browser_device': self.browser_device,
                'max_pages': self.max_pages,
                'outcome': self.outcome,
                'error': self.error,
                'started_at': self.started_at.isoformat(),
                'finished_at': self.finished_at.isoformat() if self.finished_at else None,
                # --- tester workspace ---
                'steps': steps,
                'steps_total': len(steps),
                'steps_done': done,
                'steps_failed': failed,
                'expected_result': self.expected_result,
                'expected_met': self.expected_met,
                'reported': list(self.reported),
                'reported_count': len(self.reported),
                'auto_findings': list(self.auto_findings),
                'auto_findings_count': len(self.auto_findings),
                'auto_checked_pages': self.auto_checked_pages,
                'progress_pct': progress_pct,
                'issue_categories': ISSUE_CATEGORIES,
                'observation_sections': ['human_observation', 'automated_evidence'],
            }

    # ---- tester actions (called from Flask request threads) ----
    def mark_step(self, index, status, note='', issue_ref=None):
        """Tick one checklist item off. Returns True if the index existed."""
        if status not in ('pending', 'passed', 'failed', 'skipped'):
            return False
        with self._lock:
            if index < 0 or index >= len(self.steps):
                return False
            self.steps[index]['status'] = status
            self.steps[index]['note'] = (note or '')[:2000]
            self.steps[index]['updated_at'] = datetime.utcnow().isoformat()
            if issue_ref:
                self.steps[index]['issue_ref'] = str(issue_ref)[:100]
            # Pin the most recent page to the step, so a failed step points at
            # the screen it failed on rather than at nothing.
            if self.pages:
                self.steps[index]['screenshot'] = self.pages[-1]['screenshot']
        return True

    def set_paused(self, paused):
        with self._lock:
            self.paused = bool(paused)
        return self.paused

    def report_issue(self, category, severity, title, note='', step_index=None,
                     description='', expected_result='', actual_result='',
                     browser_device=''):
        """
        Record something the tester spotted, against the page they are on.

        This is the part a crawler cannot do: judging that a layout is broken,
        a design is confusing, or the behaviour does not match what the
        business asked for.
        """
        title = (title or '').strip()
        if not title:
            return None
        # Description falls back to the legacy note field.
        description = (description or note or '').strip()
        with self._lock:
            page = self.pages[-1] if self.pages else {}
            entry = {
                'index': len(self.reported),
                'category': _normalise_issue_category(category),
                'severity': severity if severity in ISSUE_SEVERITIES else 'moderate',
                'title': title[:255],
                'description': description[:2000],
                'note': description[:2000],
                'expected_result': (expected_result or '')[:2000],
                'actual_result': (actual_result or '')[:2000],
                'page_url': page.get('url', self.base_url),
                'url': page.get('url', self.base_url),
                'screenshot': page.get('screenshot', ''),
                'browser_device': (browser_device or self.browser_device or '')[:255],
                'step_index': step_index,
                'reported_at': datetime.utcnow().isoformat(),
                'timestamp': datetime.utcnow().isoformat(),
                'source': 'MANUAL',
            }
            self.reported.append(entry)
        logger.info("manual-run %s: tester reported %s (%s)",
                    self.run_id, entry['title'], entry['severity'])
        return entry

    def set_expected_met(self, met):
        self.expected_met = bool(met)

    # ---- worker thread ----
    def _run(self):
        try:
            os.makedirs(self.run_dir, exist_ok=True)
            with sync_playwright() as p:
                # Stealth args so login APIs that gate on automation detection
                # still respond. Without these, vanilla Playwright Chromium ships
                # navigator.webdriver=true and an automation banner that some
                # sites (DigiELV among them) refuse to authenticate against.
                # Prefer the user's real installed Chrome (channel='chrome') so
                # the fingerprint is indistinguishable from a normal browser;
                # fall back to bundled Chromium if Chrome isn't installed.
                stealth_args = ['--start-maximized',
                                '--disable-blink-features=AutomationControlled']
                headed = os.getenv('QA_MANUAL_HEADLESS') != '1'
                try:
                    if not headed:
                        raise RuntimeError('headless test mode')
                    browser = p.chromium.launch(
                        channel='chrome', headless=False, args=stealth_args,
                    )
                    logger.info("manual-run: using installed Chrome")
                except Exception as e:
                    if headed:
                        logger.warning("real Chrome not available (%s); using bundled Chromium", e)
                    browser = p.chromium.launch(headless=not headed, args=stealth_args)
                context = browser.new_context(no_viewport=True)
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
                page = context.new_page()
                try:
                    self.browser_device = context._options.get('user_agent', '')[:255] \
                        if hasattr(context, '_options') else ''
                except Exception:
                    pass
                # Console evidence: assistance only, never drives the verdict.
                try:
                    page.on('console', lambda msg: self._console.append({
                        'type': msg.type,
                        'text': msg.text,
                        'page_url': self.current_url,
                        'display': f"[{msg.type}] {(msg.text or '')[:200]}",
                    }) if msg.type in ('error', 'warning') else None)
                except Exception:
                    pass

                try:
                    page.goto(self.base_url, timeout=60000, wait_until='commit')
                except Exception as e:
                    self.error = f"Website unreachable: {str(e)[:300]}"
                    logger.warning("manual-run goto failed: %s", e)

                # Capture the initial page once it has actually paints.
                try:
                    page.wait_for_load_state('domcontentloaded', timeout=10000)
                except Exception:
                    pass
                _settle(page)
                self._capture(page)

                try:
                    last_url = page.url
                except Exception:
                    last_url = self.base_url

                self.status = 'ready'
                logger.info("manual-run %s ready for testcase %s", self.run_id, self.testcase_id)

                # Phase 1: manual navigation. Poll page.url for changes — works
                # for both real navigations AND SPA route changes (which don't
                # fire framenavigated). The polling also pumps the Playwright
                # event loop so we notice if the user closes the window.
                #
                # Auto-trigger: once the tester has navigated off the entry page
                # onto a non-login URL and it has stayed stable for
                # AUTO_TRIGGER_STABLE_SECS, fire the autocrawl signal
                # automatically — no button needed. Idling on the entry page
                # never triggers: see should_auto_trigger.
                #
                # Exits on: Done/Cancel, autocrawl signal (manual or auto),
                # browser closed, timeout.
                deadline = time.time() + MAX_DURATION_SECS
                stable_since = time.time()
                entry_url = last_url
                navigated = False
                while (not self._action_event.is_set()
                       and not self._autocrawl_event.is_set()
                       and time.time() < deadline):
                    time.sleep(WATCH_POLL_SECS)
                    # Paused: keep the browser open, stop capturing/checking.
                    if self.paused:
                        stable_since = time.time()
                        continue
                    try:
                        current_url = page.url
                    except Exception as e:
                        logger.info("manual-run %s: browser closed (%s)", self.run_id, e)
                        break
                    if current_url != last_url:
                        try:
                            page.wait_for_load_state('domcontentloaded', timeout=3000)
                        except Exception:
                            pass
                        _settle(page)
                        self._capture(page)
                        last_url = current_url
                        stable_since = time.time()       # active navigation resets
                        navigated = True

                    if should_auto_trigger(current_url, entry_url, navigated,
                                           time.time() - stable_since):
                        logger.info("manual-run %s: auto-trigger — '%s' stable for %.1fs",
                                    self.run_id, current_url, AUTO_TRIGGER_STABLE_SECS)
                        self._autocrawl_event.set()
                        break

                # Phase 2 (optional): user signalled "auto-crawl from here". BFS
                # through internal links of the page they're currently on (already
                # logged in), capturing each one. Sits in the SAME browser/context
                # so the auth state carries over.
                if self._autocrawl_event.is_set() and not self._action_event.is_set():
                    self.status = 'crawling'
                    logger.info("manual-run %s: starting auto-crawl (max %d pages)",
                                self.run_id, self.max_pages)
                    try:
                        self._auto_crawl(page)
                    except Exception as e:
                        logger.exception("manual-run %s: auto-crawl error: %s",
                                         self.run_id, e)
                    self.status = 'ready'
                    # Wait for the user to click Done/Cancel after auto-crawl ends.
                    remaining = max(60, int(deadline - time.time()))
                    self._action_event.wait(timeout=remaining)

                if self._action_event.is_set():
                    if self.action == 'done':
                        try:
                            self._capture(page)
                        except Exception:
                            pass
                        self.status = 'done'
                    else:
                        self.status = 'cancelled'
                else:
                    self.status = 'timeout'

                self.finished_at = datetime.utcnow()
                try:
                    browser.close()
                except Exception:
                    pass
        except Exception as e:
            self.status = 'error'
            self.error = str(e)
            self.finished_at = datetime.utcnow()
            logger.exception("manual-run worker error (testcase %s): %s",
                             self.testcase_id, e)
        finally:
            self.done_event.set()

    def _capture(self, page, source='manual'):
        try:
            url = page.url
        except Exception as e:
            logger.warning("manual-run screenshot failed: %s", e)
            return
        try:
            title = (page.title() or '')[:500]
        except Exception:
            title = ''
        with self._lock:
            self.current_url = url
            self.current_title = title
        # Collapse rapid same-page captures (redirect chains, re-renders):
        # without this each bounce records another "page" and the page count
        # and auto-check budget are spent on one screen.
        now = time.monotonic()
        with self._lock:
            last_url = self.pages[-1]['url'] if self.pages else None
            if last_url == url and (now - self._last_capture) < SCREENSHOT_DEBOUNCE_SECS:
                return
            self._last_capture = now
        try:
            idx = len(self.pages) + 1
            filename = f'p{idx:03d}.png'
            filepath = os.path.join(self.run_dir, filename)
            try:
                page.screenshot(path=filepath, full_page=False)
            except Exception as e:
                logger.warning("manual-run screenshot failed (page %d): %s", idx, e)
                return
            entry = {
                'url': url,
                'page_title': title,
                'screenshot': f'/static/uploads/manual_runs/{self.run_id}/{filename}',
                'captured_at': datetime.utcnow().isoformat(),
                'source': source,
                'auto_issues': 0,
            }
            with self._lock:
                self.pages.append(entry)
            logger.info("manual-run %s: captured page %d (%s) [%s]",
                        self.run_id, idx, url, source)

            # Run the machine checks on the page the tester is looking at, so
            # broken links and accessibility faults are caught for them while
            # they concentrate on the judgement calls only a human can make.
            # While they are browsing we run only the fast in-page checks, so
            # the tracker keeps up with them; the link check, which fires
            # dozens of HTTP requests, waits for the auto-crawl phase.
            found = self._auto_check(page, url, deep=(source == 'auto'))
            if found:
                with self._lock:
                    entry['auto_issues'] = found
        except Exception as e:
            logger.warning("manual-run screenshot failed: %s", e)

    def _auto_check(self, page, url, deep=False):
        """
        Audit one page the tester visited. Returns how many issues were found.

        Evidence/assistance only: results are shown next to the tester's own
        observations and never decide the manual verdict.

        - Security headers are skipped. A manual session has no response
          headers to inspect, and running the check with none would report
          every security header as missing on every page, which is false.
        - Mobile checks are skipped. The tester's window is a desktop one,
          where every normal control looks like an undersized tap target.
        - Link checking only runs on `deep` (auto-crawl) pages, because it
          fires up to 30 HTTP requests and would stall the live tracker.

        Best-effort throughout: a failure here must never interrupt the
        tester's session, so everything is caught and logged.
        """
        # Imported here to avoid a circular import at module load.
        from modules.runner.routes import (
            check_missing_alt, check_seo, check_accessibility, check_broken_links,
            filter_console_errors,
        )
        try:
            from modules.runner.extended_checks import (
                check_page_resources, check_ui_consistency, check_code_quality,
                check_url_health,
            )
        except Exception:
            check_page_resources = check_ui_consistency = None
            check_code_quality = check_url_health = None

        findings = {}
        try:
            findings['missing_alt_images'] = check_missing_alt(page)
            findings['seo_issues'] = check_seo(page, url)
            findings['accessibility_issues'], _engine = check_accessibility(page)
            try:
                with self._lock:
                    pending_console = list(self._console)
                    self._console.clear()
                real = filter_console_errors(pending_console)
                findings['console_errors'] = [
                    {**m, 'severity': 'serious', 'element_selector': 'console',
                     'parent_context': ''} for m in real
                ]
            except Exception:
                pass
            if check_url_health is not None:
                try:
                    findings['url_issues'] = check_url_health(url, 200, {})
                except Exception:
                    pass
            if check_ui_consistency is not None:
                try:
                    findings['ui_issues'] = check_ui_consistency(page)
                except Exception:
                    pass
            if check_code_quality is not None:
                try:
                    findings['code_issues'] = check_code_quality(page)
                except Exception:
                    pass
            if deep:
                findings['broken_links'], _cov = check_broken_links(page, url)
                if check_page_resources is not None:
                    try:
                        extra, _rc = check_page_resources(page, url)
                        findings['broken_links'] = (findings['broken_links'] or []) + (extra or [])
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("manual-run auto-check failed on %s: %s", url, e)
            return 0

        count = 0
        with self._lock:
            try:
                title = self.current_title
            except Exception:
                title = ''
            for category, items in findings.items():
                for item in items or []:
                    if isinstance(item, dict):
                        item['category'] = category
                        item['page_url'] = url
                        item['page_title'] = title
                        item['source'] = 'AUTOMATED'
                    self.auto_findings.append(item)
                    count += 1
            self.auto_checked_pages += 1
        if count:
            logger.info("manual-run %s: %d automatic issue(s) on %s",
                        self.run_id, count, url)
        return count

    def _auto_crawl(self, page):
        """Walk the site BFS-style starting from the page's current URL,
        screenshotting each page. Stays in the existing authenticated context.

        Uses SPA-internal click navigation (navigate_spa) instead of page.goto
        so React stays mounted between pages and the user's auth survives —
        the entire point of having the user log in manually first.
        """
        # Imported here to avoid a circular import at module load.
        from modules.runner.routes import (
            discover_internal_links, navigate_spa,
            normalize_crawl_url, crawl_pattern, PATTERN_PAGES_CAP,
        )

        try:
            seed = page.url
        except Exception:
            return
        if not seed:
            return

        visited = {normalize_crawl_url(p['url']) for p in self.pages}
        seed_clean = normalize_crawl_url(seed)
        to_visit = [seed_clean] if seed_clean not in visited else []
        queued = set(to_visit)
        pattern_counts = {crawl_pattern(seed_clean): 1} if to_visit else {}
        queued = set(to_visit)

        def _enqueue(link):
            clean = normalize_crawl_url(link)
            if not clean or clean in queued or clean in visited:
                return
            pat = crawl_pattern(clean)
            if pattern_counts.get(pat, 0) >= PATTERN_PAGES_CAP:
                return
            pattern_counts[pat] = pattern_counts.get(pat, 0) + 1
            queued.add(clean)
            to_visit.append(clean)

        # Always discover from the CURRENT page first, even if it's already in
        # `visited` (e.g. the user manually navigated to the dashboard and that
        # capture is already recorded). Without this seeding, an autocrawl that
        # starts from a previously-captured page does nothing at all.
        try:
            for link in discover_internal_links(page, seed, self.max_pages):
                _enqueue(link)
        except Exception as e:
            logger.warning("manual auto-crawl initial discovery failed: %s", e)

        while to_visit and len(self.pages) < self.max_pages:
            if self._action_event.is_set():
                break
            url = to_visit.pop(0)
            try:
                navigate_spa(page, url)              # click first, goto fallback
                try:
                    page.wait_for_load_state('networkidle', timeout=5000)
                except Exception:
                    pass
                _settle(page)
            except Exception as e:
                logger.warning("manual auto-crawl navigate failed (%s): %s", url, e)
                continue

            self._capture(page, source='auto')

            if len(self.pages) >= self.max_pages:
                break

            try:
                discovered = discover_internal_links(page, seed, self.max_pages)
            except Exception as e:
                logger.warning("manual auto-crawl discovery failed: %s", e)
                discovered = []
            for link in discovered:
                _enqueue(link)
            visited.add(normalize_crawl_url(url))

        logger.info("manual-run %s: auto-crawl finished (%d pages total)",
                    self.run_id, len(self.pages))

    # ---- control ----
    def start(self):
        self._thread.start()

    def is_alive(self):
        return self._thread.is_alive()

    def signal_autocrawl(self):
        self._autocrawl_event.set()

    def signal_done(self, outcome, note=''):
        self.outcome = outcome if outcome in ('Pass', 'Fail') else 'Pass'
        self.note = (note or '')[:1000]
        self.action = 'done'
        self._action_event.set()

    def signal_cancel(self):
        self.action = 'cancel'
        self._action_event.set()


# ============================================================
# Public API used by routes
# ============================================================

def summarise_manual(session_snapshot):
    """
    A verdict for a manual run, in the tester's terms.

    A manual run is not judged by a crawler's issue count; it is judged by
    whether the tester could complete the steps and whether what they saw
    matched what the business asked for.
    """
    steps = session_snapshot.get('steps') or []
    reported = session_snapshot.get('reported') or []
    failed_steps = [s for s in steps if s['status'] == 'failed']
    pending = [s for s in steps if s['status'] == 'pending']

    by_severity = {sev: 0 for sev in ISSUE_SEVERITIES}
    for issue in reported:
        by_severity[issue.get('severity', 'moderate')] = \
            by_severity.get(issue.get('severity', 'moderate'), 0) + 1

    expected_met = session_snapshot.get('expected_met')
    parts = []
    if steps:
        parts.append(f'{len(steps) - len(pending)} of {len(steps)} steps checked')
    if failed_steps:
        parts.append(f'{len(failed_steps)} step(s) failed')
    if reported:
        parts.append(f'{len(reported)} issue(s) reported')
    if expected_met is False:
        parts.append('expected result NOT met')
    summary = ', '.join(parts) if parts else 'Nothing was recorded for this run.'

    return {
        'steps_total': len(steps),
        'steps_failed': len(failed_steps),
        'steps_pending': len(pending),
        'reported_total': len(reported),
        'reported_by_severity': by_severity,
        'auto_findings_total': session_snapshot.get('auto_findings_count', 0),
        'expected_met': expected_met,
        'summary': summary[:1] .upper() + summary[1:] if summary else summary,
        # The manual verdict depends ONLY on these three. Automated
        # background evidence is shown alongside but never overrides them.
        'verdict_basis': ['step_results', 'expected_result_decision', 'reported_issues'],
        'automated_evidence_excluded_from_verdict': True,
    }


def start(testcase_id, project_id, base_url, max_pages=25,
          steps_text='', expected_result=''):
    """Open the headed window for a manual run. Returns (ok, message, snapshot)."""
    if not base_url:
        return False, 'Project has no base URL to open', None
    if not str(base_url).startswith(('http://', 'https://')):
        return False, 'Project URL must start with http:// or https://', None

    with _lock:
        existing = _active.get(testcase_id)
        if existing and existing.is_alive():
            existing.signal_cancel()
        sess = _ManualSession(testcase_id, project_id, base_url, max_pages=max_pages,
                              steps_text=steps_text, expected_result=expected_result)
        _active[testcase_id] = sess

    sess.start()

    # Give the worker a beat to launch and reach 'ready' (or error out loudly).
    deadline = time.time() + 8
    while time.time() < deadline:
        if sess.status in ('ready', 'error'):
            break
        if sess.done_event.wait(0.1):
            break

    if sess.status == 'error':
        with _lock:
            _active.pop(testcase_id, None)
        return False, sess.error or 'Failed to launch browser', None

    return True, 'Manual run started — navigate the site, then click Done.', sess.snapshot()


def get(testcase_id):
    """The live session for a test case, or None. Used by the tester actions."""
    with _lock:
        sess = _active.get(testcase_id)
    return sess if sess and sess.is_alive() else None


def finish(testcase_id, outcome, note='', expected_met=None):
    """User clicked Done. Blocks until the worker closes. Returns (ok, message, snapshot)."""
    with _lock:
        sess = _active.get(testcase_id)
    if not sess or not sess.is_alive():
        return False, 'No active manual run for this test case.', None

    if expected_met is not None:
        sess.set_expected_met(expected_met)
    sess.signal_done(outcome, note)
    sess.done_event.wait(timeout=15)

    with _lock:
        _active.pop(testcase_id, None)

    if sess.status == 'done':
        return True, 'Manual run finished.', sess.snapshot()
    if sess.status == 'error':
        return False, sess.error or 'Failed to finish manual run', sess.snapshot()
    return False, f'Manual run ended with status {sess.status}', sess.snapshot()


def autocrawl(testcase_id):
    """Tell the running worker to take over: BFS-navigate from the current page
    and screenshot each one until max_pages is reached. Returns (ok, message)."""
    with _lock:
        sess = _active.get(testcase_id)
    if not sess or not sess.is_alive():
        return False, 'No active manual run for this test case.'
    if sess.status not in ('ready',):
        return False, f'Cannot start auto-crawl from status: {sess.status}'
    sess.signal_autocrawl()
    return True, 'Auto-crawl started.'


def pause(testcase_id, paused=True):
    """Pause or resume a live manual session without closing the browser."""
    with _lock:
        sess = _active.get(testcase_id)
    if not sess or not sess.is_alive():
        return False, 'No active manual run for this test case.', None
    sess.set_paused(paused)
    return True, ('Paused — capture is suspended, browser stays open.'
                  if paused else 'Resumed — capture restarted.'), sess.snapshot()


def cancel(testcase_id):
    """User cancelled. Closes the window without saving."""
    with _lock:
        sess = _active.get(testcase_id)
    if not sess or not sess.is_alive():
        with _lock:
            _active.pop(testcase_id, None)
        return True, 'No active manual run.'
    sess.signal_cancel()
    sess.done_event.wait(timeout=10)
    with _lock:
        _active.pop(testcase_id, None)
    return True, 'Manual run cancelled.'


def status(testcase_id):
    """Return the current worker snapshot (for UI polling)."""
    with _lock:
        sess = _active.get(testcase_id)
    if not sess:
        return {'active': False, 'status': 'idle'}
    snap = sess.snapshot()
    snap['active'] = sess.is_alive()
    return snap
