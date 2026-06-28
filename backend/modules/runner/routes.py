"""
Test Runner - Advanced Playwright-based automated testing.

Authenticated crawling (manual login + session capture, Chunk D-4):
- If the user captured a session via "Open & Login" (Chunk D-2), a cookie/
  localStorage bundle exists at static/uploads/sessions/<project_id>.json.
- When present, the crawl context is created with that storage_state, so the
  entire crawl runs as the logged-in user.
- If no session file exists, the crawl runs anonymously (no login attempted).
"""

from flask import Blueprint, jsonify, request, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from playwright.sync_api import sync_playwright
from PIL import Image
from urllib.parse import urlparse, urljoin
from datetime import datetime
import os
import re
import json
import time
import uuid
import logging
import requests

from modules.runner.jobs import job_manager
from modules.runner import manual_run

logger = logging.getLogger(__name__)

runner_bp = Blueprint('runner', __name__)
mysql = None


def init_runner(app_mysql):
    global mysql
    mysql = app_mysql


def user_owns_testcase(user_id, testcase_id):
    cursor = mysql.connection.cursor()
    cursor.execute(
        """SELECT tc.id, tc.title, tc.test_type, tc.crawl_pages, tc.max_pages,
                  p.base_url, p.id AS project_id, p.name AS project_name,
                  p.has_active_session, p.requires_login
           FROM test_cases tc
           JOIN projects p ON tc.project_id = p.id
           WHERE tc.id = %s AND p.user_id = %s""",
        (testcase_id, user_id)
    )
    result = cursor.fetchone()
    cursor.close()
    return result


# ============================================================
# HELPERS — RICH FINDINGS, BOUNDING BOXES, CROPS
# ============================================================

def _safe_get_selector(element):
    try:
        return element.evaluate("""el => {
            const path = [];
            let cur = el;
            while (cur && cur.nodeType === 1 && path.length < 5) {
                let selector = cur.nodeName.toLowerCase();
                if (cur.id) {
                    selector += '#' + cur.id;
                    path.unshift(selector);
                    break;
                }
                if (cur.className && typeof cur.className === 'string') {
                    const classes = cur.className.trim().split(/\\s+/).slice(0, 2).join('.');
                    if (classes) selector += '.' + classes;
                }
                path.unshift(selector);
                cur = cur.parentElement;
            }
            return path.join(' > ');
        }""")
    except Exception:
        return 'unknown'


def _safe_get_bounding_box(element):
    try:
        box = element.bounding_box()
        if box and box['width'] > 0 and box['height'] > 0:
            return {
                'x': round(box['x']),
                'y': round(box['y']),
                'width': round(box['width']),
                'height': round(box['height'])
            }
    except Exception:
        pass
    return None


def _safe_get_parent_text(element, max_len=100):
    try:
        text = element.evaluate("""el => {
            const parent = el.parentElement;
            if (!parent) return '';
            return (parent.innerText || parent.textContent || '').trim();
        }""")
        if text and len(text) > max_len:
            text = text[:max_len] + '...'
        return text or ''
    except Exception:
        return ''


def _crop_evidence(full_screenshot_path, evidence_dir, run_id, page_idx, category, index, bbox, padding=25):
    if not bbox:
        return None
    try:
        with Image.open(full_screenshot_path) as img:
            img_w, img_h = img.size
            left = max(0, bbox['x'] - padding)
            top = max(0, bbox['y'] - padding)
            right = min(img_w, bbox['x'] + bbox['width'] + padding)
            bottom = min(img_h, bbox['y'] + bbox['height'] + padding)

            if left >= img_w or top >= img_h or right <= 0 or bottom <= 0:
                return None
            if (right - left) < 10 or (bottom - top) < 10:
                return None

            cropped = img.crop((left, top, right, bottom))
            filename = f"run{run_id}_p{page_idx}_{category}_{index}.png"
            filepath = os.path.join(evidence_dir, filename)
            cropped.save(filepath, optimize=True)
            return f"/static/uploads/evidence/{filename}"
    except Exception as e:
        logger.warning("evidence crop error %s[%s]: %s", category, index, e)
        return None


def _enrich_with_crops(findings, category, full_screenshot_path, evidence_dir, run_id, page_idx):
    for i, finding in enumerate(findings):
        bbox = finding.get('bounding_box')
        if bbox:
            crop_url = _crop_evidence(full_screenshot_path, evidence_dir, run_id, page_idx, category, i, bbox)
            if crop_url:
                finding['screenshot_crop'] = crop_url
    return findings


# ============================================================
# CAPTURED SESSION (manual login - Chunk D-4)
# ============================================================

SESSIONS_DIR = os.path.join('static', 'uploads', 'sessions')


def _session_state_path(project_id):
    """Return the storage_state path for a project if a captured session exists, else None."""
    path = os.path.join(SESSIONS_DIR, f'{project_id}.json')
    return path if os.path.exists(path) else None


def _session_landing_url(project_id):
    """Where the user landed after manual login. Used as the crawl start when
    the configured base_url (often /user-login) is a public page even for
    authenticated users."""
    path = os.path.join(SESSIONS_DIR, f'{project_id}.landing')
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read().strip() or None
    except Exception:
        return None


def _session_storage_dict(project_id):
    """sessionStorage captured during manual login. Many SPAs put auth flags
    here — without replaying it the crawler is treated as logged-out even when
    cookies + localStorage are loaded."""
    path = os.path.join(SESSIONS_DIR, f'{project_id}.session_storage.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) and data else None
    except Exception:
        return None


_ROUTE_RE = re.compile(r'(?:https?://[^\s"\'<>]+|/[A-Za-z0-9][A-Za-z0-9/_\-]*)')
_ROUTE_SKIP_EXT = ('.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.css', '.js',
                   '.ico', '.woff', '.woff2', '.ttf', '.map', '.json', '.pdf')
_ROUTE_SKIP_KW = ('/logout', '/signout', '/sign-out', '/log-out',
                  '/api/', '/static/', '/assets/')


def _routes_from_captured_storage(project_id, base_url):
    """Mine internal route paths the authenticated SPA stored in its captured
    session/localStorage (e.g. a `set_menus` array of dashboard routes) and
    return them as absolute same-origin URLs to seed the crawl.

    Many SPAs render authenticated navigation as button/onClick router links, NOT
    plain <a href>, so the normal link discovery never reaches the dashboard /
    account / bank-details pages. Those routes are, however, present in the menu
    payload the app keeps in storage — so we mine them directly. We only walk
    JSON-structured values (menus), never raw token/JWT strings, to avoid turning
    base64 gibberish into bogus URLs.
    """
    try:
        parsed = urlparse(base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        host = (parsed.netloc or '').lower()
    except Exception:
        return []
    if not origin or not host:
        return []

    blobs = []
    ss = _session_storage_dict(project_id)
    if isinstance(ss, dict):
        blobs.extend(str(v) for v in ss.values() if v is not None)
    sp = _session_state_path(project_id)
    if sp:
        try:
            with open(sp, 'r', encoding='utf-8') as f:
                state = json.load(f)
            for o in state.get('origins') or []:
                for kv in o.get('localStorage') or []:
                    if kv.get('value') is not None:
                        blobs.append(str(kv['value']))
        except Exception:
            pass

    def _walk(obj, out):
        if isinstance(obj, str):
            out.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                _walk(v, out)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v, out)

    leaves = []
    for blob in blobs:
        try:
            parsed_json = json.loads(blob)
        except Exception:
            parsed_json = None
        if parsed_json is not None and not isinstance(parsed_json, (str, int, float, bool)):
            _walk(parsed_json, leaves)          # structured menu data
        elif blob.startswith('/') or blob.startswith('http'):
            leaves.append(blob)                  # a bare path/url value
        # else: raw token/JWT/etc. — ignore

    found = set()
    for leaf in leaves:
        for m in _ROUTE_RE.findall(leaf):
            cand = m.strip()
            if cand.startswith('http'):
                p = urlparse(cand)
                if (p.netloc or '').lower() != host:
                    continue
                path = p.path
            else:
                path = cand
            if not path or path == '/' or ' ' in path or len(path) > 120:
                continue
            low = path.lower()
            if any(low.endswith(e) for e in _ROUTE_SKIP_EXT):
                continue
            if any(k in low for k in _ROUTE_SKIP_KW):
                continue
            found.add(f"{origin}{path}".split('#')[0].rstrip('/'))
    return sorted(found)


def _session_expiry_info(session_path):
    """
    Inspect captured cookies and return (valid_now, warning_message_or_None).

    Many auth providers issue access tokens that live only ~30 minutes. If the
    captured session is past that window, loading storage_state still works but
    the app treats the user as anonymous — so the crawl tests only public pages
    and the user has no idea why. We surface that situation explicitly.
    """
    AUTH_KEYWORDS = ('token', 'session', 'auth', 'jwt', 'sid')
    try:
        with open(session_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return True, None

    now = time.time()
    soonest_expiry = None
    expired_names = []
    for c in data.get('cookies') or []:
        name = (c.get('name') or '').lower()
        if not any(k in name for k in AUTH_KEYWORDS):
            continue
        exp = c.get('expires')
        if not exp or exp <= 0:
            continue                      # session cookie, no expiry
        if exp < now:
            expired_names.append(c.get('name'))
        elif soonest_expiry is None or exp < soonest_expiry:
            soonest_expiry = exp

    if expired_names:
        return False, (
            f"Captured session has expired auth cookies "
            f"({', '.join(expired_names)}). Re-capture the session via "
            f"'Open & Login' and run the test immediately afterwards."
        )
    if soonest_expiry is not None:
        mins_left = int((soonest_expiry - now) // 60)
        if mins_left < 5:
            return True, f"Session expires in {mins_left} min — re-capture if the test stalls."
    return True, None


# ============================================================
# CHECK FUNCTIONS
# ============================================================

def check_broken_links(page, base_url):
    """
    Fan-out HEAD requests in parallel. Sequential 50 × 5 s on a heavy site like
    amazon.in used to take minutes per page and gave the user a "Network Error"
    because axios timed out long before the crawl finished. Parallelising with
    a small thread pool cuts each page's link check from ~minutes to a few
    seconds while keeping the per-request timeout strict.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    PER_REQUEST_TIMEOUT = 8       # working-but-slow links were timing out at 3s
    LINK_CAP = 30
    POOL_SIZE = 8
    # Browser-like headers cut down on bot-protection refusing our plain HTTP client.
    BROWSERY_HEADERS = {
        'Accept': ('text/html,application/xhtml+xml,application/xml;q=0.9,'
                   'image/avif,image/webp,image/apng,*/*;q=0.8'),
        'Accept-Language': 'en-US,en;q=0.9',
    }

    findings = []
    candidates = []     # (full_url, link_handle)

    try:
        links = page.query_selector_all('a[href]')
        seen_urls = set()
        for link in links:
            if len(candidates) >= LINK_CAP:
                break
            try:
                href = link.get_attribute('href')
                if not href or href.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
                    continue
                if href.startswith('/'):
                    parsed = urlparse(base_url)
                    full_url = f"{parsed.scheme}://{parsed.netloc}{href}"
                elif href.startswith('http'):
                    full_url = href
                else:
                    continue
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)
                candidates.append((full_url, link))
            except Exception:
                continue
    except Exception as e:
        logger.warning("broken_links: link enumeration error: %s", e)
        return findings

    # Send the crawl browser's cookies + User-Agent with every check so links
    # behind a login are tested AS THE LOGGED-IN USER. Plain requests.head() has
    # no session, so on authenticated pages every protected link came back
    # 302/401/403 and got wrongly flagged "broken". (cookies()/evaluate() run on
    # this crawl thread where `page` lives; the pool threads below only touch the
    # requests Session, never a Playwright object.)
    base_host = (urlparse(base_url).netloc or '').lower()

    session = requests.Session()
    session.headers.update(BROWSERY_HEADERS)
    try:
        for c in page.context.cookies():
            try:
                session.cookies.set(
                    c.get('name'), c.get('value'),
                    domain=(c.get('domain') or '').lstrip('.'),
                    path=c.get('path') or '/',
                )
            except Exception:
                continue
    except Exception:
        pass
    ua = ''
    try:
        ua = page.evaluate("navigator.userAgent") or ''
        if ua:
            session.headers['User-Agent'] = ua
    except Exception:
        pass

    # Cookie-less session for EXTERNAL links. The authenticated `session` above
    # carries the logged-in user's cookies (incl. auth/JWT) — sending those to a
    # third-party host while checking an outbound link would leak the session.
    # External links are checked anonymously; only the User-Agent is shared.
    ext_session = requests.Session()
    ext_session.headers.update(BROWSERY_HEADERS)
    if ua:
        ext_session.headers['User-Agent'] = ua

    def _same_site(url):
        try:
            h = (urlparse(url).netloc or '').lower()
        except Exception:
            return False
        if not h or not base_host:
            return False
        return h == base_host or h.endswith('.' + base_host) or base_host.endswith('.' + h)

    def _check(url):
        sess = session if _same_site(url) else ext_session
        try:
            r = sess.head(url, timeout=PER_REQUEST_TIMEOUT, allow_redirects=True)
            # Many servers mishandle HEAD (answer 4xx/5xx) even when GET is fine —
            # confirm ANY HEAD failure with a lightweight GET before judging.
            if r.status_code >= 400:
                r = sess.get(url, timeout=PER_REQUEST_TIMEOUT,
                             allow_redirects=True, stream=True)
                r.close()
            return r.status_code
        except requests.RequestException:
            # HEAD blocked/refused — some hosts only allow GET; try that too.
            try:
                r = sess.get(url, timeout=PER_REQUEST_TIMEOUT,
                             allow_redirects=True, stream=True)
                r.close()
                return r.status_code
            except requests.RequestException:
                return 0

    def _is_broken(status):
        # 401/403/429/503 are auth / anti-bot / rate-limit / transient responses,
        # NOT broken links — flagging them was pure noise. A genuinely broken link
        # is a definitive 404/410/5xx, or a total connection failure (0).
        if status == 0:
            return True
        if status in (401, 403, 429, 503):
            return False
        return status >= 400

    bad = {}    # full_url -> status
    try:
        with ThreadPoolExecutor(max_workers=POOL_SIZE) as pool:
            future_to_url = {pool.submit(_check, url): url for url, _ in candidates}
            for fut in as_completed(future_to_url):
                url = future_to_url[fut]
                try:
                    status = fut.result()
                except Exception:
                    status = 0
                if _is_broken(status):
                    bad[url] = status
    except Exception as e:
        logger.warning("broken_links: pool error: %s", e)
        return findings

    # FINAL VERIFICATION — the real test. A plain HTTP client (requests) gets
    # blocked by bot protection, TLS quirks, or HEAD refusal on plenty of perfectly
    # working links, returning 0/4xx → false "broken" reports. Before accusing a
    # link, fetch it through the ACTUAL browser (same cookies/UA/TLS/redirects as a
    # human clicking it). If the browser loads it without a 4xx/5xx, it is NOT broken
    # — drop it. Runs on the crawl thread (page lives here), sequentially, only over
    # the small flagged subset, so it's cheap. This is what kills the false positives.
    if bad:
        verified = 0
        try:
            req = page.context.request
            for url in list(bad.keys()):
                try:
                    resp = req.get(url, timeout=PER_REQUEST_TIMEOUT * 1000)
                    real = resp.status
                    try:
                        resp.dispose()
                    except Exception:
                        pass
                    if not _is_broken(real):
                        del bad[url]        # browser opened it fine → false positive
                        verified += 1
                    else:
                        bad[url] = real     # trust the browser's status over requests'
                except Exception:
                    pass                    # browser also failed → keep it flagged
        except Exception as e:
            logger.warning("broken_links: browser verify error: %s", e)
        if verified:
            logger.info("broken_links: %d link(s) cleared after real-browser verification",
                        verified)

    def _link_severity(status, url):
        """A dead link in our own navigation is a real defect (major); a dead
        external reference is worth fixing but not blocking (minor). Server
        errors are major wherever they live."""
        try:
            same_site = urlparse(url).netloc == urlparse(base_url).netloc
        except Exception:
            same_site = False
        if status >= 500:
            return 'major'
        return 'major' if same_site else 'minor'

    # Build findings (with element metadata) only for the failed URLs.
    for full_url, link in candidates:
        if full_url not in bad:
            continue
        try:
            status = bad[full_url]
            link_text = link.inner_text()[:60].strip() or '(no text)'
            findings.append({
                'url': full_url,
                'status_code': status,
                'severity': _link_severity(status, full_url),
                'link_scope': ('internal'
                               if urlparse(full_url).netloc == urlparse(base_url).netloc
                               else 'external'),
                'element_text': link_text,
                'element_selector': _safe_get_selector(link),
                'parent_context': _safe_get_parent_text(link),
                'bounding_box': _safe_get_bounding_box(link),
                'display': f"{full_url} ({status if status > 0 else 'unreachable'})",
            })
        except Exception:
            continue

    return findings


def check_missing_alt(page):
    """Flag images with NO alt attribute at all.

    WCAG nuance that the old version got wrong: alt="" is the CORRECT way to
    mark a decorative image (screen readers skip it), so flagging it was a
    false positive. Same for aria-hidden / role="presentation". Only an image
    that is missing the attribute entirely is a genuine defect.
    """
    findings = []
    try:
        images = page.query_selector_all('img')
        for img in images:
            try:
                alt = img.get_attribute('alt')
                if alt is not None:
                    continue          # has alt (incl. alt="" = declared decorative)
                if (img.get_attribute('aria-hidden') == 'true'
                        or img.get_attribute('role') == 'presentation'):
                    continue          # explicitly hidden from assistive tech
                src = img.get_attribute('src') or '(no src)'
                box = img.bounding_box()
                if not box or box['width'] < 100 or box['height'] < 100:
                    continue          # icons / pixels / trackers — not content
                findings.append({
                    'src': src[:200],
                    'severity': 'minor',
                    'element_selector': _safe_get_selector(img),
                    'parent_context': _safe_get_parent_text(img),
                    'bounding_box': _safe_get_bounding_box(img),
                    'display': src[:200],
                })
            except Exception:
                continue
    except Exception as e:
        logger.warning("missing_alt check error: %s", e)
    return findings


def check_seo(page, page_url):
    """On-page SEO fundamentals (was: title-only, which was almost decorative).

    One evaluate() round-trip gathers everything; findings carry a severity so
    a missing viewport (breaks mobile rendering) outweighs a long title.
    """
    findings = []

    def _add(issue, severity, selector='head', extra=None):
        f = {'issue': issue, 'severity': severity,
             'element_selector': selector, 'display': issue}
        if extra:
            f.update(extra)
        findings.append(f)

    try:
        meta = page.evaluate(
            """() => {
                const m = (n) => { const e = document.querySelector(`meta[name="${n}"]`);
                                   return e ? (e.getAttribute('content') || '') : null; };
                return {
                    title: document.title || '',
                    description: m('description'),
                    robots: (m('robots') || '').toLowerCase(),
                    viewport: m('viewport'),
                    h1Count: document.querySelectorAll('h1').length,
                    canonical: !!document.querySelector('link[rel="canonical"]'),
                };
            }"""
        ) or {}

        title = (meta.get('title') or '').strip()
        if not title:
            _add('Page has no <title>', 'major', 'head > title')
        elif len(title) > 65:
            _add(f'Title is {len(title)} chars — search engines truncate around 60',
                 'info', 'head > title')

        desc = meta.get('description')
        if desc is None or not (desc or '').strip():
            _add('Page has no meta description', 'minor',
                 'head > meta[name="description"]')
        elif len(desc) > 165:
            _add(f'Meta description is {len(desc)} chars — gets truncated around 160',
                 'info', 'head > meta[name="description"]')

        h1 = int(meta.get('h1Count') or 0)
        if h1 == 0:
            _add('Page has no <h1> heading', 'minor', 'h1')
        elif h1 > 1:
            _add(f'Page has {h1} <h1> headings — should have exactly one', 'info', 'h1')

        if not meta.get('viewport'):
            _add('Missing viewport meta tag — page will not scale on mobile devices',
                 'major', 'head > meta[name="viewport"]')

        if 'noindex' in (meta.get('robots') or ''):
            _add('Page is set to noindex — hidden from search engines (verify this is intentional)',
                 'info', 'head > meta[name="robots"]')
    except Exception as e:
        logger.warning("seo check error: %s", e)
        # Last-resort fallback: at least keep the original title check alive.
        try:
            if not (page.title() or '').strip():
                _add('Page has no <title>', 'major', 'head > title')
        except Exception:
            pass
    return findings


def check_security(page_url, response_headers, page=None, console_messages=None):
    """Security hygiene with severity tiers (was: presence-only header check
    that weighed a missing CSP header heavier than a broken link).

    - HTTP instead of HTTPS         -> critical (real exposure)
    - Mixed content on an HTTPS page-> major   (browser console reports it;
                                       the old console filter silently DROPPED these)
    - Session cookies w/o HttpOnly  -> major   (XSS can steal the session)
    - Cookies w/o Secure on HTTPS   -> major
    - Weak CSP (unsafe-inline/eval) -> minor   (present but toothless)
    - Missing hygiene headers       -> minor, grouped into one finding
    """
    findings = []
    is_https = page_url.startswith('https://')
    # response_headers is None when the caller didn't capture the HTTP response
    # (e.g. a manual run where the user navigated by hand). In that case we must
    # NOT report header-presence issues — we'd falsely flag every header as
    # "missing". Protocol, cookie, and mixed-content checks still run.
    headers_known = response_headers is not None
    try:
        headers_lower = {k.lower(): v for k, v in (response_headers or {}).items()}
        csp = headers_lower.get('content-security-policy', '')

        if not is_https:
            findings.append({
                'issue': 'Site is served over insecure HTTP instead of HTTPS',
                'severity': 'critical', 'header': 'protocol',
                'display': 'Site uses insecure HTTP instead of HTTPS',
            })

        # Mixed content: HTTPS page loading HTTP resources. The browser logs it;
        # we surface it here (and keep it OUT of console_errors to avoid double count).
        if is_https and console_messages:
            mixed = [m for m in console_messages
                     if 'mixed content' in (m.get('text') or '').lower()]
            if mixed:
                findings.append({
                    'issue': f'Mixed content: {len(mixed)} insecure HTTP resource(s) loaded on an HTTPS page',
                    'severity': 'major', 'header': 'mixed-content',
                    'count': len(mixed),
                    'examples': [(m.get('text') or '')[:160] for m in mixed[:3]],
                    'display': f'Mixed content — {len(mixed)} insecure resource(s) on HTTPS page',
                })

        # Cookie flags. We only judge the SITE'S OWN login cookies. Third-party
        # cookies (Google Analytics _ga/_gid, Razorpay, Facebook _fbp, etc.) are
        # set by other companies' scripts — the site owner can't add HttpOnly/
        # Secure to them, so flagging those was noise that confused users.
        if page is not None:
            try:
                cookies = page.context.cookies()
            except Exception:
                cookies = []
            page_host = (urlparse(page_url).netloc or '').lower()

            def _first_party(c):
                dom = (c.get('domain') or '').lstrip('.').lower()
                if not dom or not page_host:
                    return False
                return (page_host == dom
                        or page_host.endswith('.' + dom)
                        or dom.endswith('.' + page_host))

            # Names/prefixes of common analytics & ad cookies to ignore outright.
            ANALYTICS_PREFIXES = ('_ga', '_gid', '_gat', '_gcl', '_fbp', '_fbc',
                                  '__utm', '_hj', '_clck', '_clsk', 'amplitude',
                                  'mp_', 'ajs_', 'intercom', 'rzp')

            def _is_third_party_noise(name):
                n = (name or '').lower()
                return n.startswith(ANALYTICS_PREFIXES)

            sessiony = ('sess', 'token', 'auth', 'jwt', 'sid', 'remember', 'login')
            own_cookies = [c for c in cookies
                           if _first_party(c) and not _is_third_party_noise(c.get('name'))]

            def _is_login_cookie(c):
                return any(k in (c.get('name') or '').lower() for k in sessiony)

            no_httponly = [c['name'] for c in own_cookies
                           if _is_login_cookie(c) and not c.get('httpOnly')]
            no_secure = [c['name'] for c in own_cookies
                         if is_https and _is_login_cookie(c) and not c.get('secure')]
            if no_httponly:
                names = ', '.join(sorted(set(no_httponly))[:5])
                findings.append({
                    'issue': f'Login cookie is exposed to scripts: {names}',
                    'severity': 'major', 'header': 'cookies',
                    'display': (f'The login cookie "{names}" can be read by JavaScript on the '
                                'page. If the site ever has a script-injection bug, an attacker '
                                'could steal the login. Fix: mark the cookie "HttpOnly".'),
                })
            if no_secure:
                names = ', '.join(sorted(set(no_secure))[:5])
                findings.append({
                    'issue': f'Login cookie can travel unencrypted: {names}',
                    'severity': 'major', 'header': 'cookies',
                    'display': (f'The login cookie "{names}" is missing the "Secure" flag, so the '
                                'browser may send it over an unencrypted connection where it can '
                                'be intercepted. Fix: mark the cookie "Secure".'),
                })

        # Header-presence checks only make sense when we actually captured the
        # HTTP response. Skip them entirely when headers are unknown (manual run).
        if headers_known:
            # CSP that exists but allows inline/eval gives a false sense of safety.
            csp_l = csp.lower()
            if csp and ('unsafe-inline' in csp_l or 'unsafe-eval' in csp_l
                        or "default-src *" in csp_l or "script-src *" in csp_l):
                findings.append({
                    'issue': 'CSP present but weak (allows unsafe-inline/unsafe-eval or wildcard sources)',
                    'severity': 'minor', 'header': 'Content-Security-Policy',
                    'display': 'Content-Security-Policy present but weak',
                })

            # Missing hygiene headers — grouped as ONE minor finding, not a -15 bomb.
            hygiene = [
                ('content-security-policy', 'Content-Security-Policy', 'Protects against XSS'),
                ('x-content-type-options', 'X-Content-Type-Options', 'Stops MIME sniffing'),
                ('referrer-policy', 'Referrer-Policy', 'Controls referrer leakage'),
            ]
            if is_https:
                hygiene.append(('strict-transport-security', 'HSTS', 'Forces HTTPS'))
            # X-Frame-Options is redundant when CSP declares frame-ancestors.
            if 'frame-ancestors' not in csp_l:
                hygiene.append(('x-frame-options', 'X-Frame-Options', 'Prevents clickjacking'))
            missing = [{'header': name, 'purpose': purpose}
                       for key, name, purpose in hygiene if key not in headers_lower]
            if missing:
                header_names = ', '.join(m['header'] for m in missing)
                findings.append({
                    'issue': f'{len(missing)} recommended security headers are missing',
                    'severity': 'minor', 'header': header_names,
                    'missing_headers': missing,
                    'display': (f'{len(missing)} recommended security headers are missing '
                                f'({header_names}). These are optional server settings that help '
                                'block common attacks like clickjacking and content sniffing.'),
                })
    except Exception as e:
        logger.warning("security check error: %s", e)
    return findings


# Bundled axe-core (the industry-standard WCAG engine). Injected into each page.
AXE_PATH = os.path.join(os.path.dirname(__file__), 'axe.min.js')

# axe-core's own wording is written for accessibility experts ("Elements must
# meet minimum color contrast ratio thresholds"). Most users don't speak that.
# Map the common rule ids to a plain-English sentence that says what's wrong and
# why it matters. Unknown rules fall back to axe's help text.
_A11Y_PLAIN = {
    'color-contrast': "Some text is too light against its background and is hard to read",
    'link-name': "Some links have no readable text, so people using a screen reader can't tell where they lead",
    'button-name': "Some buttons have no label, so screen-reader users don't know what they do",
    'image-alt': "Some images have no text description for screen-reader users",
    'input-image-alt': "An image used as a button has no text description",
    'label': "Some form fields have no label, so it's unclear what to type in them",
    'form-field-multiple-labels': "A form field has conflicting labels",
    'frame-title': "A frame on the page has no title describing what it contains",
    'document-title': "The page has no title shown in the browser tab",
    'html-has-lang': "The page doesn't say what language it's written in",
    'html-lang-valid': "The page's declared language code is not valid",
    'heading-order': "The headings skip levels, which makes the page structure confusing",
    'empty-heading': "There is an empty heading with no text",
    'list': "A list isn't built correctly, so screen readers may misread it",
    'listitem': "A list item is not inside a proper list",
    'duplicate-id': "The same element id is used more than once, which can confuse assistive tech",
    'duplicate-id-active': "The same id is used on more than one interactive element",
    'aria-required-attr': "An interactive element is missing accessibility attributes it needs",
    'aria-roles': "An element uses an invalid accessibility role",
    'aria-valid-attr-value': "An accessibility attribute has an invalid value",
    'aria-hidden-focus': "A hidden element can still be focused with the keyboard",
    'landmark-one-main': "The page has no main landmark, making it harder to navigate by screen reader",
    'region': "Some content sits outside any landmark region",
    'tabindex': "A positive tabindex is used, which makes keyboard navigation jump around unexpectedly",
    'meta-viewport': "The page blocks zooming, which hurts users who need to enlarge text",
    'select-name': "A dropdown has no label",
}


def check_accessibility_axe(page):
    """
    Run a real WCAG audit using axe-core injected into the page.
    Returns findings shaped like the rest of the runner, or None if axe
    could not run (caller falls back to the heuristic check).
    """
    if not os.path.exists(AXE_PATH):
        return None
    try:
        page.add_script_tag(path=AXE_PATH)
        # axe.run returns a Promise; Playwright auto-awaits it in evaluate().
        results = page.evaluate(
            """() => axe.run(document, {
                runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
                resultTypes: ['violations']
            }).then(r => r.violations)"""
        )
    except Exception as e:
        logger.warning("axe-core run failed, using heuristic a11y check: %s", e)
        return None

    findings = []
    # axe impact -> our severity scale.
    _impact_to_sev = {'critical': 'critical', 'serious': 'major',
                      'moderate': 'minor', 'minor': 'minor'}
    for v in results or []:
        # image-alt is already covered by check_missing_alt (which adds cropped
        # evidence) — keeping both double-counted the same defect in the score.
        if v.get('id') == 'image-alt':
            continue
        nodes = v.get('nodes') or []
        first_target = ''
        if nodes and nodes[0].get('target'):
            tgt = nodes[0]['target']
            first_target = tgt[0] if isinstance(tgt, list) and tgt else str(tgt)

        bbox = None
        if first_target:
            try:
                el = page.query_selector(first_target)
                if el:
                    bbox = _safe_get_bounding_box(el)
            except Exception:
                bbox = None

        impact = v.get('impact') or 'minor'
        rule = v.get('id')
        help_text = v.get('help') or rule or 'Accessibility issue'
        # Plain-English summary for users; fall back to axe's expert wording.
        plain = _A11Y_PLAIN.get(rule, help_text)
        count = len(nodes)
        where = f"{count} place{'s' if count != 1 else ''}"
        findings.append({
            'issue': plain,
            'rule': rule,
            'impact': impact,
            'severity': _impact_to_sev.get(impact, 'minor'),
            'description': v.get('description', ''),
            'help_text': help_text,          # original axe wording, kept for reference
            'help_url': v.get('helpUrl', ''),
            'count': count,
            'element_selector': first_target or 'unknown',
            'element_text': (nodes[0].get('html', '')[:120] if nodes else ''),
            'bounding_box': bbox,
            'display': f"{plain} (found in {where})",
        })
    return findings


def check_accessibility(page):
    """WCAG audit via axe-core, with a lightweight heuristic fallback."""
    axe_findings = check_accessibility_axe(page)
    if axe_findings is not None:
        return axe_findings
    return check_accessibility_heuristic(page)


def check_accessibility_heuristic(page):
    findings = []
    try:
        buttons = page.query_selector_all('button')
        for btn in buttons:
            try:
                text = btn.inner_text().strip()
                aria_label = btn.get_attribute('aria-label')
                if not text and not aria_label:
                    box = btn.bounding_box()
                    if not box:
                        continue
                    findings.append({
                        'issue': 'Button has no visible label',
                        'severity': 'minor',
                        'element_selector': _safe_get_selector(btn),
                        'bounding_box': _safe_get_bounding_box(btn),
                        'display': 'Button has no visible label',
                    })
            except Exception:
                continue
        inputs = page.query_selector_all('input[type="text"], input[type="email"], input[type="password"], textarea')
        for inp in inputs:
            try:
                inp_id = inp.get_attribute('id')
                aria_label = inp.get_attribute('aria-label')
                placeholder = inp.get_attribute('placeholder')
                has_label = False
                if inp_id:
                    label = page.query_selector(f'label[for="{inp_id}"]')
                    if label:
                        has_label = True
                if not has_label and not aria_label and not placeholder:
                    input_name = inp.get_attribute('name') or 'unnamed'
                    findings.append({
                        'issue': f'Input field "{input_name}" has no label',
                        'severity': 'minor',
                        'element_selector': _safe_get_selector(inp),
                        'element_text': input_name,
                        'bounding_box': _safe_get_bounding_box(inp),
                        'display': f'Input field "{input_name}" has no label',
                    })
            except Exception:
                continue
    except Exception as e:
        logger.warning("accessibility check error: %s", e)
    return findings


def check_mobile(page):
    findings = []
    try:
        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        client_width = page.evaluate("document.documentElement.clientWidth")
        if scroll_width > client_width + 5:
            findings.append({
                'issue': f'Page has horizontal scrolling on mobile ({scroll_width}px vs {client_width}px)',
                'issue_type': 'horizontal_scroll',
                'severity': 'major',
                'element_selector': 'body',
                'display': 'Page requires horizontal scrolling on mobile',
            })
        clickables = page.query_selector_all('a, button, input[type="submit"]')
        small_targets = []
        for el in clickables[:50]:
            try:
                box = el.bounding_box()
                if box and box['width'] > 0 and box['height'] > 0 and (box['width'] < 44 or box['height'] < 44):
                    text = (el.inner_text() or '').strip()[:30] or el.get_attribute('aria-label') or ''
                    small_targets.append({
                        'text': text or '(unlabeled)',
                        'width': int(box['width']),
                        'height': int(box['height']),
                        'selector': _safe_get_selector(el),
                        'bounding_box': {
                            'x': round(box['x']), 'y': round(box['y']),
                            'width': round(box['width']), 'height': round(box['height'])
                        }
                    })
            except Exception:
                continue
        if small_targets:
            first_target = small_targets[0]
            count = len(small_targets)
            findings.append({
                'issue': f'{count} buttons/links are too small to tap on mobile',
                'issue_type': 'small_tap_targets',
                'severity': 'minor',
                'count': count,
                'examples': small_targets[:10],
                'element_selector': first_target['selector'],
                'element_text': first_target['text'],
                'bounding_box': first_target['bounding_box'],
                'display': f'{count} tap targets too small for mobile users',
            })
    except Exception as e:
        logger.warning("mobile check error: %s", e)
    return findings


# Third-party hosts whose console errors are almost always noise the site owner
# can't act on — ad networks, analytics, tag managers, chat/heatmap widgets. A
# 404 or load failure from any of these says nothing about the site's own pages,
# so we drop them entirely rather than letting them dent the health score.
THIRD_PARTY_NOISE_HOSTS = (
    'google-analytics', 'googletagmanager', 'googlesyndication', 'doubleclick',
    'google.com/ads', 'adservice', 'adsystem', 'gstatic.com/ads',
    'facebook.net', 'connect.facebook', 'facebook.com/tr',
    'hotjar', 'mixpanel', 'segment.io', 'segment.com', 'fullstory',
    'clarity.ms', 'sentry.io', 'intercom', 'intercomcdn', 'zendesk', 'zdassets',
    'hubspot', 'hs-scripts', 'newrelic', 'nr-data', 'cloudflareinsights',
    'optimizely', 'taboola', 'outbrain', 'criteo', 'bing.com/bat',
    'snap.licdn', 'analytics.tiktok', 'tiktok.com', 'amplitude', 'heapanalytics',
)

# Pull the first http(s) URL out of a console message so we can tell whose
# resource actually failed.
_URL_IN_TEXT = re.compile(r'https?://[^\s\'"<>)]+')


def _console_host(text):
    """Best-effort host of the first URL mentioned in a console message ('' if none)."""
    m = _URL_IN_TEXT.search(text or '')
    if not m:
        return ''
    try:
        return (urlparse(m.group(0)).netloc or '').lower()
    except Exception:
        return ''


def filter_console_errors(console_messages, base_url=None):
    """Group + classify console errors instead of dumping the raw stream.

    Old behaviour: every repeat of the same error became its own finding, and a
    failed analytics pixel counted the same as an uncaught TypeError. Now:
    - identical messages are grouped with a count (30 repeats -> 1 finding x30)
    - severity reflects what the error means:
        uncaught JS exception / CORS failure -> major (something is broken)
        failed first-party resource (404 own img/script) -> minor (sloppy)
        failed third-party resource (someone else's CDN) -> info (not actionable)
    - ad/analytics/tracker hosts are dropped outright (pure noise).
    'mixed content' stays skipped here because check_security now surfaces it.
    """
    skip_patterns = ['unrecognized feature', 'deprecated', 'devtools', 'extension', 'favicon',
                     'webkit', 'preload', 'sourcemap', 'mixed content']
    exception_markers = ('uncaught', 'typeerror', 'referenceerror', 'syntaxerror',
                         'rangeerror', 'is not a function', 'cannot read propert',
                         'is not defined', 'unhandled promise rejection')
    cors_markers = ('cors', 'access-control-allow', 'cross-origin')

    base_host = ''
    if base_url:
        try:
            base_host = (urlparse(base_url).netloc or '').lower()
        except Exception:
            base_host = ''

    grouped = {}        # normalized text -> finding
    order = []
    for msg in console_messages:
        if msg.get('type') != 'error':
            continue
        text = (msg.get('text') or '').strip()
        text_lower = text.lower()
        if any(pattern in text_lower for pattern in skip_patterns):
            continue

        host = _console_host(text)
        # Drop ad/analytics/tracker noise no matter what it claims to be — the
        # site owner can't fix someone else's pixel failing to load.
        if any(n in host for n in THIRD_PARTY_NOISE_HOSTS) and host:
            continue
        if any(n in text_lower for n in THIRD_PARTY_NOISE_HOSTS):
            continue

        if any(m in text_lower for m in exception_markers):
            severity, kind = 'major', 'JS exception'
        elif any(m in text_lower for m in cors_markers):
            severity, kind = 'major', 'CORS failure'
        elif 'failed to load resource' in text_lower:
            # A broken resource on the site's OWN host is a real (minor) defect;
            # one on a third-party host is noise we surface only as 'info' so it
            # barely touches the score.
            third_party = bool(host) and bool(base_host) and base_host not in host and host not in base_host
            if third_party:
                severity, kind = 'info', 'Failed resource (third-party)'
            else:
                severity, kind = 'minor', 'Failed resource'
        else:
            severity, kind = 'minor', 'Console error'

        key = text[:300]
        if key in grouped:
            grouped[key]['count'] += 1
        else:
            grouped[key] = {**msg, 'text': text, 'severity': severity,
                            'error_kind': kind, 'count': 1}
            order.append(key)

    filtered = []
    for key in order:
        f = grouped[key]
        suffix = f" (×{f['count']})" if f['count'] > 1 else ''
        f['display'] = f"[{f['error_kind']}] {f['text'][:160]}{suffix}"
        filtered.append(f)
    return filtered


SEVERITY_POINTS = {'critical': 12, 'major': 5, 'minor': 1.5, 'info': 0.3}
CATEGORY_CAP = 25   # one noisy category can dent the score, not zero it alone


def severity_breakdown(findings_by_category):
    """Count findings per severity across all categories (for the run summary
    and Pass/Fail decision). Findings without a severity count as 'minor'."""
    counts = {'critical': 0, 'major': 0, 'minor': 0, 'info': 0}
    for items in findings_by_category.values():
        for it in items:
            sev = it.get('severity', 'minor') if isinstance(it, dict) else 'minor'
            counts[sev if sev in counts else 'minor'] += 1
    return counts


def calculate_health_score(findings_by_category):
    """Severity-weighted score (was: arbitrary per-category weights where one
    missing header (-15) outscored a broken link (-8) and 7 console errors
    zeroed a site). Now a critical finding hurts ~8x a minor one, and each
    category's damage is capped so a flood of one issue type can't single-
    handedly take the score to 0."""
    total_penalty = 0.0
    for cat, items in findings_by_category.items():
        cat_penalty = 0.0
        for it in items:
            sev = it.get('severity', 'minor') if isinstance(it, dict) else 'minor'
            cat_penalty += SEVERITY_POINTS.get(sev, SEVERITY_POINTS['minor'])
        total_penalty += min(cat_penalty, CATEGORY_CAP)
    return max(0, min(100, round(100 - total_penalty)))


# ============================================================
# MULTI-PAGE CRAWLING
# ============================================================

def navigate_spa(page, target_url):
    """
    Move the current page to `target_url` using SPA-internal navigation when
    possible — i.e. clicking an existing <a> on the page so React Router's
    pushState fires and the React tree stays mounted. This preserves the
    authenticated session for SPAs (like DigiELV) that lose their auth context
    on a full page reload.

    Returns (mode, response):
      mode = 'click' if the click navigation succeeded, 'goto' if we had to
             fall back to a hard navigation.
      response = the Response object from page.goto when mode='goto', else None.
    """
    try:
        before = page.url
    except Exception:
        before = ''

    # Look for a link on the current page whose absolute or relative href
    # matches the target. Click it — that goes through React Router instead
    # of forcing a page reload.
    try:
        clicked = page.evaluate(
            """(target) => {
                // Compare with a single trailing slash stripped from both sides.
                // The BFS queue stores URLs rstrip('/')'d, but the DOM's a.href
                // keeps the slash (e.g. https://site.com/ or /dashboard/), so an
                // exact === match misses those links and forces a hard page.goto
                // that can drop the SPA's in-memory auth. Slash-insensitive match
                // keeps more navigations on the auth-preserving click path.
                const norm = (u) => (u || '').split('#')[0].replace(/\\/$/, '');
                const t = norm(target);
                const anchors = document.querySelectorAll('a[href]');
                for (const a of anchors) {
                    if (norm(a.href) === t || norm(a.getAttribute('href')) === t) {
                        try { a.scrollIntoView({block: 'center'}); } catch (e) {}
                        a.click();
                        return true;
                    }
                }
                return false;
            }""",
            target_url,
        )
    except Exception as e:
        logger.warning("SPA navigate evaluate error: %s", e)
        clicked = False

    if clicked:
        # Wait for the route to actually change (pushState updates location.href).
        try:
            page.wait_for_function(
                "(old) => location.href !== old",
                arg=before,
                timeout=6000,
            )
        except Exception:
            pass
        try:
            page.wait_for_load_state('networkidle', timeout=5000)
        except Exception:
            pass
        # Only declare success if the URL actually advanced — otherwise the click
        # was eaten (e.g. JS preventDefault but no router navigation).
        try:
            if page.url != before:
                return 'click', None
        except Exception:
            pass

    # Fall back to a full navigation. Auth state may be lost here if the SPA
    # depends on in-memory React state, but at least we capture *something*.
    try:
        response = page.goto(target_url, timeout=30000, wait_until='domcontentloaded')
        return 'goto', response
    except Exception as e:
        logger.warning("navigate_spa goto fallback failed for %s: %s", target_url, e)
        return 'goto', None


def discover_internal_links(page, base_url, max_pages):
    discovered = set()
    base_parsed = urlparse(base_url)
    base_domain = base_parsed.netloc
    origin = f"{base_parsed.scheme}://{base_domain}"

    # Gather candidate targets from real <a href> AND from SPA router-style nav
    # (routerLink / data-href / etc.). Authenticated dashboards often render their
    # menu as buttons/divs with a router attribute and NO plain href, so an
    # a[href]-only scan misses every protected page. One evaluate() collects both.
    try:
        candidates = page.evaluate(
            """(origin) => {
                const out = new Set();
                const push = (v) => { if (v) out.add(v); };
                document.querySelectorAll('a[href]').forEach(a => push(a.href));
                const attrs = ['routerlink', 'data-href', 'data-to', 'data-url',
                               'data-link', 'ng-reflect-router-link'];
                document.querySelectorAll(
                    '[routerlink],[data-href],[data-to],[data-url],[data-link],[ng-reflect-router-link]'
                ).forEach(el => {
                    for (const at of attrs) {
                        const v = el.getAttribute && el.getAttribute(at);
                        if (v) { try { push(new URL(v, origin).href); } catch (e) {} }
                    }
                });
                return Array.from(out);
            }""",
            origin,
        ) or []
    except Exception as e:
        logger.warning("discover_internal_links evaluate error: %s", e)
        candidates = []

    for href in candidates:
        try:
            if not href:
                continue
            href = href.strip()
            if href.startswith(('#', 'javascript:', 'mailto:', 'tel:', 'sms:')):
                continue
            if href.startswith('/'):
                full_url = f"{origin}{href}"
            elif href.startswith('http'):
                full_url = href
            else:
                full_url = urljoin(base_url, href)

            link_parsed = urlparse(full_url)
            if link_parsed.netloc != base_domain:
                continue

            lower = full_url.lower()
            if any(lower.endswith(ext) for ext in ['.pdf', '.zip', '.exe', '.dmg', '.tar', '.gz',
                                                    '.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp',
                                                    '.mp4', '.mp3', '.avi', '.mov',
                                                    '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']):
                continue

            if any(keyword in lower for keyword in ['/logout', '/signout', '/sign-out', '/log-out']):
                continue

            clean_url = full_url.split('#')[0].rstrip('/')
            if clean_url:
                discovered.add(clean_url)

            if len(discovered) >= max_pages * 3:
                break

        except Exception:
            continue

    return list(discovered)


# Clickable nav items across the common framework conventions (Bootstrap, Material,
# Ant, Tailwind, Angular/React routers, generic sidebars). Kept GENERIC on purpose —
# this tool tests any site, not one specific app.
_CLICK_NAV_SEL = (
    "nav a, nav button, aside a, aside button, header a, "
    "[role=menuitem], [role=menu] a, [role=menu] button, [role=navigation] a, "
    "[role=navigation] button, [role=tab], "
    "[class*=menu] a, [class*=menu] button, "
    "[class*=sidebar] a, [class*=sidebar] button, "
    "[class*=side-bar] a, [class*=side-bar] button, "
    "[class*=nav] a, [class*=nav] button, "
    "[class*=drawer] a, [class*=drawer] button, "
    "li[class*=menu-item] > a, li[class*=menu-item] > span"
)

# --- Generic collapsible-menu expander (runs IN the browser via page.evaluate) ---
# One pass: detect every collapsible TRIGGER (hamburger, aria-expanded, data-toggle,
# accordion header, dropdown trigger, has-submenu, etc.), click the ones not yet
# handled, verify each actually opened (aria flip / container grew / new elements),
# retry once with hover for stubborn JS menus, and return a per-trigger log. Triggers
# are marked with a data attribute so repeated passes (and re-visits) don't re-click
# them — that's the dedup that stops infinite toggle loops. Site-agnostic: only
# standards (ARIA, data-*) and common framework class names, no hardcoded selectors.
_EXPAND_ONE_PASS_JS = r"""
() => {
  const TRIGGER_SEL = [
    '[aria-expanded="false"]',
    '[aria-controls]',
    '[aria-haspopup="true"]',
    '[data-toggle]', '[data-bs-toggle]', '[data-target]', '[data-bs-target]',
    'button[aria-expanded]', '[role="button"][aria-expanded]',
    '[class*="menu-toggle"]', '[class*="nav-toggle"]', '[class*="navbar-toggle"]',
    '[class*="hamburger"]', '[class*="sidebar-toggle"]', '[class*="drawer-toggle"]',
    '[class*="dropdown-toggle"]', '[class*="dropdown-trigger"]', '[class*="menu-trigger"]',
    '[class*="accordion"]', '[class*="collapsible"]', '[class*="expandable"]',
    '[class*="has-submenu"]', '[class*="has-children"]', '[class*="has-sub"]',
    '[class*="tree-toggle"]', '[class*="caret"]', '[class*="chevron"]'
  ].join(',');
  const HAMBURGER_TXT = /^(☰|⋮|⋯|≡|menu|more|\.\.\.)$/i;
  const LOGOUT = /log\s*out|sign\s*out|logout/i;
  const CLASS_KEYS = ['menu-toggle','nav-toggle','navbar-toggle','hamburger',
    'sidebar-toggle','drawer-toggle','dropdown-toggle','dropdown-trigger','menu-trigger',
    'accordion','collapsible','expandable','has-submenu','has-children','has-sub',
    'tree-toggle','caret','chevron'];

  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
  };
  const whyCollapsible = (el) => {
    const reasons = [];
    if (el.getAttribute('aria-expanded') === 'false') reasons.push('aria-expanded=false');
    if (el.hasAttribute('aria-controls')) reasons.push('aria-controls');
    if (el.getAttribute('aria-haspopup') === 'true') reasons.push('aria-haspopup');
    if (el.hasAttribute('data-toggle') || el.hasAttribute('data-bs-toggle')) reasons.push('data-toggle');
    if (el.hasAttribute('data-target') || el.hasAttribute('data-bs-target')) reasons.push('data-target');
    const c = (el.className || '').toString().toLowerCase();
    for (const k of CLASS_KEYS) if (c.includes(k)) reasons.push('class~' + k);
    if (HAMBURGER_TXT.test((el.innerText || '').trim())) reasons.push('hamburger-text');
    return reasons;
  };
  const targetOf = (el) => {
    let id = el.getAttribute('aria-controls')
      || (el.getAttribute('data-target') || el.getAttribute('data-bs-target') || '').replace(/^#/, '');
    if (id) { try { return document.getElementById(id); } catch (e) {} }
    const sib = el.nextElementSibling;
    if (sib && /^(ul|ol|div|nav|section|aside)$/i.test(sib.tagName)) return sib;
    const p = el.closest('li, div, details');
    if (p) {
      const sub = p.querySelector('ul, ol, [class*="submenu"], [class*="sub-menu"], [class*="collapse"], [class*="dropdown-menu"]');
      if (sub && sub !== el && !sub.contains(el)) return sub;
    }
    return null;
  };
  const snap = (t) => t
    ? { vis: isVisible(t), h: t.scrollHeight || 0,
        n: t.querySelectorAll('a,button,[role="menuitem"]').length }
    : { vis: false, h: 0, n: 0 };
  const countClickable = () => {
    // Count only VISIBLE clickables — a freshly revealed submenu (whether it was
    // display:none, max-height:0, or off-screen) becomes visible, so a rise here
    // reliably signals "opened". Counting hidden DOM (querySelectorAll alone) would
    // miss display:none toggles like hamburger sidebars.
    let n = 0;
    document.querySelectorAll('a[href], button, [role="menuitem"], [role="button"]')
      .forEach(e => { if (isVisible(e)) n++; });
    return n;
  };

  const log = [];
  let opened = 0;
  const all = Array.from(document.querySelectorAll(TRIGGER_SEL));
  const beforeTotal = countClickable();

  for (const el of all) {
    if (el.getAttribute('data-qa-expanded') != null) continue;   // dedup across passes
    if (!isVisible(el)) continue;
    const text = (el.innerText || '').trim();
    if (LOGOUT.test(text)) continue;
    const reasons = whyCollapsible(el);
    if (!reasons.length) continue;                               // not a collapsible trigger
    el.setAttribute('data-qa-expanded', '1');                   // mark handled (dedup)
    if (el.getAttribute('aria-expanded') === 'true') continue;  // already open

    const target = targetOf(el);
    const before = snap(target);
    const beforeCount = countClickable();
    let result = 'no-change';
    const evaluate = () => {
      const aria = el.getAttribute('aria-expanded');
      const after = snap(target);
      const delta = countClickable() - beforeCount;
      if (aria === 'true') return ['opened', Math.max(0, delta)];
      if (after.vis && !before.vis) return ['opened', Math.max(0, delta)];
      if (after.h > before.h + 4) return ['opened', Math.max(0, delta)];
      if (after.n > before.n) return ['opened', Math.max(0, after.n - before.n)];
      if (delta > 0) return ['opened', delta];
      return ['no-change', 0];
    };
    let newEls = 0;
    try {
      el.scrollIntoView({ block: 'center' });
      el.click();
      [result, newEls] = evaluate();
      if (result === 'no-change') {
        // Stubborn JS menu: hover then click again before giving up.
        el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
        el.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
        el.click();
        [result, newEls] = evaluate();
      }
    } catch (e) { result = 'error'; }

    if (result === 'opened') opened++;
    log.push({
      tag: el.tagName.toLowerCase(),
      text: text.slice(0, 40),
      cls: (el.className || '').toString().trim().slice(0, 60),
      why: reasons.slice(0, 4),
      result: result,
      newEls: newEls
    });
  }
  return { log: log, opened: opened, newTotal: countClickable() - beforeTotal };
}
"""


def _expand_collapsible_menus(dp, seed_url, rounds=6):
    """Recursively expand every collapsible/accordion/dropdown menu on the page so
    nested nav items render into the DOM, then return the seed if a trigger happened
    to navigate. Each Playwright pass runs `_EXPAND_ONE_PASS_JS`, waits for async
    reveals, and repeats — newly revealed triggers get opened on the next round. The
    data-qa-expanded marker dedups across passes so toggles never loop. Generic.

    Logs every collapsible click: text, which heuristic matched, open/close/no-change,
    and how many new elements appeared. Returns total collapsibles opened.
    """
    seed_clean = seed_url.split('#')[0].rstrip('/')
    total_opened = 0
    for _ in range(rounds):
        try:
            res = dp.evaluate(_EXPAND_ONE_PASS_JS) or {}
        except Exception:
            break
        entries = res.get('log') or []
        for e in entries:
            logger.info(
                "  collapsible <%s> '%s' [%s] -> %s (+%d new) cls=%s",
                e.get('tag'), e.get('text'), ",".join(e.get('why') or []),
                e.get('result'), e.get('newEls') or 0, e.get('cls'))
        opened = int(res.get('opened') or 0)
        total_opened += opened
        # A trigger may have been a real link — get back to the seed if so.
        if dp.url.split('#')[0].rstrip('/') != seed_clean:
            try:
                dp.goto(seed_url, timeout=15000, wait_until='domcontentloaded')
                time.sleep(0.4)
            except Exception:
                break
        if opened == 0:
            break          # nothing new opened this round — fully expanded
        time.sleep(0.5)    # let async submenu content render before the next pass
    if total_opened:
        logger.info("collapsible expansion: opened %d menu(s) on %s",
                    total_opened, seed_clean)
    return total_opened


def discover_links_by_clicking(context, page_url, base_url, max_links=25):
    """Find authenticated pages whose nav renders as JS router BUTTONS (no <a href>)
    — common in SPAs (Angular/React routers, encrypted/dynamic menus) — by expanding
    any collapsible menus and then actually clicking each nav item on a throwaway page
    (same authed context) and recording where it lands.

    Returns a list of same-origin URLs. Never clicks logout. Best-effort: any
    failure just yields fewer links, never breaks the crawl. Fully generic — works
    on any site's sidebar/menu, not a specific app.
    """
    base_host = (urlparse(base_url).netloc or '').lower()
    seed_clean = page_url.split('#')[0].rstrip('/')
    found = set()
    try:
        dp = context.new_page()
    except Exception:
        return []
    try:
        dp.goto(page_url, timeout=30000, wait_until='domcontentloaded')
        try:
            dp.wait_for_load_state('networkidle', timeout=6000)
        except Exception:
            pass
        time.sleep(1.0)

        # 1) Expand collapsible/accordion menus so nested items enter the DOM.
        _expand_collapsible_menus(dp, page_url)

        # 2) Collect every visible nav label (now including the revealed children).
        try:
            labels = dp.evaluate(
                """(sel) => {
                    const seen = new Set(); const out = [];
                    document.querySelectorAll(sel).forEach(el => {
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 && r.height === 0) return;
                        const t = (el.innerText || '').trim().split('\\n')[0].trim();
                        if (t && t.length <= 40 && !seen.has(t)
                            && !/log\\s*out|sign\\s*out|logout/i.test(t)) {
                            seen.add(t); out.push(t);
                        }
                    });
                    return out.slice(0, 60);
                }""", _CLICK_NAV_SEL) or []
        except Exception:
            labels = []

        logger.info("click discovery: found %d nav label(s) on %s: %s",
                    len(labels), page_url, labels[:30])

        # 3) Click each label and record where it navigates. Re-expand after every
        #    navigation because collapsing resets when we return to the seed.
        for label in labels:
            if len(found) >= max_links:
                break
            try:
                # Make sure we're on the seed with menus expanded before clicking.
                if dp.url.split('#')[0].rstrip('/') != seed_clean:
                    try:
                        dp.goto(page_url, timeout=15000, wait_until='domcontentloaded')
                        time.sleep(0.4)
                    except Exception:
                        break
                _expand_collapsible_menus(dp, page_url, rounds=3)

                before = dp.url
                clicked = dp.evaluate(
                    """(args) => {
                        const [sel, label] = args;
                        for (const el of document.querySelectorAll(sel)) {
                            const t = (el.innerText || '').trim().split('\\n')[0].trim();
                            if (t === label) {
                                try { el.scrollIntoView({block: 'center'}); } catch (e) {}
                                el.click(); return true;
                            }
                        }
                        return false;
                    }""", [_CLICK_NAV_SEL, label])
                if not clicked:
                    continue
                try:
                    dp.wait_for_load_state('networkidle', timeout=4000)
                except Exception:
                    pass
                time.sleep(0.5)
                cur = dp.url.split('#')[0].rstrip('/')
                if ((urlparse(cur).netloc or '').lower() == base_host
                        and cur and cur != before.split('#')[0].rstrip('/')):
                    found.add(cur)
            except Exception:
                continue
    except Exception as e:
        logger.warning("click discovery failed for %s: %s", page_url, e)
    finally:
        try:
            dp.close()
        except Exception:
            pass
    logger.info("click discovery: %d route(s) reached by clicking on %s: %s",
                len(found), page_url, list(found)[:20])
    return list(found)


# Destructive/irreversible actions we must NEVER auto-click while probing buttons —
# they could submit forms, delete data, log out, pay, etc. Generic word list.
_UNSAFE_CLICK_TEXT = re.compile(
    r"log\s*out|sign\s*out|delete|remove|submit|pay|buy|checkout|confirm|"
    r"place\s*order|subscribe|unsubscribe|send|save|update|cancel|deactivate|"
    r"close\s*account|withdraw|transfer|order",
    re.I,
)


def check_interactions(page, base_url):
    """Functional / interaction checks — the 'does it actually work?' layer that the
    static checks miss:
      * broken images (src 404s — renders empty)                    [major]
      * an element whose HOVER handler throws a JavaScript error     [major]
      * a control whose CLICK throws a JavaScript error             [major]
      * a 'dead' button — clicking it changes nothing at all        [minor]

    GENERIC (no site-specific selectors) and SAFE: only clicks buttons that are
    clearly non-navigating and non-destructive (skips submit/delete/logout/pay…),
    skips anything inside a <form>, restores the page if a click navigates, and only
    hovers otherwise. Best-effort — any failure yields fewer findings, never breaks
    the crawl.
    """
    findings = []
    IMG_CAP, HOVER_CAP, CLICK_CAP = 40, 15, 15

    # ---- 1) Broken images (objective, always-on) ----
    try:
        checked = 0
        for img in page.query_selector_all('img'):
            if checked >= IMG_CAP:
                break
            checked += 1
            try:
                broken = page.evaluate(
                    "(im) => im.complete && im.naturalWidth === 0 "
                    "&& !!(im.currentSrc || im.src)", img)
            except Exception:
                broken = False
            if not broken:
                continue
            src = (img.get_attribute('src') or img.get_attribute('data-src')
                   or '(no src)')
            try:
                box = img.bounding_box()
            except Exception:
                box = None
            findings.append({
                'issue_type': 'broken_image',
                'issue': 'Image fails to load (broken source)',
                'severity': 'major',
                'element_selector': _safe_get_selector(img),
                'parent_context': _safe_get_parent_text(img),
                'bounding_box': box,
                'src': src,
                'display': f"Broken image — does not load: {src[:120]}",
            })
    except Exception as e:
        logger.warning("interactions: broken-image scan error: %s", e)

    # ---- 1b) Broken CSS background images (verified through the real browser) ----
    try:
        bg = page.evaluate(
            """() => {
                const out = []; const seen = new Set();
                const els = document.querySelectorAll('*');
                for (const el of els) {
                    if (out.length >= 12) break;
                    const b = getComputedStyle(el).backgroundImage;
                    if (!b || b === 'none' || !b.includes('url(')) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) continue;       // ignore icons/spacers
                    const m = b.match(/url\\(["']?([^"')]+)["']?\\)/);
                    if (!m) continue;
                    const url = m[1];
                    if (!/^https?:/i.test(url) || seen.has(url)) continue;
                    seen.add(url);
                    out.push({url, sel: el.id ? ('#'+el.id) : el.tagName.toLowerCase(),
                              box: {x: r.x + scrollX, y: r.y + scrollY, width: r.width, height: r.height}});
                }
                return out;
            }""") or []
        req = page.context.request
        for item in bg:
            url = item.get('url')
            try:
                resp = req.get(url, timeout=8000)
                status = resp.status
                try:
                    resp.dispose()
                except Exception:
                    pass
            except Exception:
                status = 0
            if status == 0 or status >= 400:
                findings.append({
                    'issue_type': 'broken_image',
                    'issue': 'Background image fails to load (broken source)',
                    'severity': 'major',
                    'element_selector': item.get('sel'),
                    'bounding_box': item.get('box'),
                    'src': url,
                    'display': f"Broken background image ({status or 'unreachable'}): {url[:110]}",
                })
    except Exception as e:
        logger.warning("interactions: background-image scan error: %s", e)

    # ---- 1c) Empty / unlabeled links & buttons (broken nav + a11y) ----
    try:
        empties = page.evaluate(
            """() => {
                const out = []; let n = 0;
                const els = document.querySelectorAll('a[href], button, [role=button]');
                for (const el of els) {
                    if (n >= 12) break;
                    const r = el.getBoundingClientRect();
                    const cs = getComputedStyle(el);
                    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                    if (r.width < 4 || r.height < 4) continue;     // not visible/clickable
                    const label = ((el.innerText || '').trim()
                        || (el.getAttribute('aria-label') || '').trim()
                        || (el.getAttribute('title') || '').trim());
                    const hasImg = el.querySelector('img[alt]:not([alt=""]), svg [aria-label], svg title, [role=img][aria-label]');
                    const hasIconLabel = el.querySelector('[aria-label]');
                    if (label || hasImg || hasIconLabel) continue;   // it has SOME label
                    n++;
                    out.push({
                        tag: el.tagName.toLowerCase(),
                        sel: el.id ? ('#'+el.id)
                             : (el.getAttribute('href') ? ('a[href="'+el.getAttribute('href')+'"]') : el.tagName.toLowerCase()),
                        box: {x: r.x + scrollX, y: r.y + scrollY, width: r.width, height: r.height},
                    });
                }
                return out;
            }""") or []
        for e in empties:
            findings.append({
                'issue_type': 'unlabeled_control',
                'issue': 'Clickable element has no visible text or label',
                'severity': 'minor',
                'element_selector': e.get('sel'),
                'bounding_box': e.get('box'),
                'display': f"Empty/unlabeled {e.get('tag')} — no text, screen readers can't name it",
            })
    except Exception as e:
        logger.warning("interactions: empty-control scan error: %s", e)

    # ---- JS-error capture for the hover/click probes ----
    js_errors = []

    def _on_pageerror(err):
        try:
            js_errors.append(str(err))
        except Exception:
            js_errors.append('script error')

    try:
        page.on('pageerror', _on_pageerror)
    except Exception:
        pass

    start_url = page.url
    try:
        # ---- 2) Hover handlers that throw a JS error ----
        try:
            hover_sel = ("img, figure, [class*=hover], [class*=zoom], [class*=card], "
                         "[class*=thumb], [onmouseover], a:has(img)")
            count = 0
            for el in page.query_selector_all(hover_sel):
                if count >= HOVER_CAP:
                    break
                try:
                    if not el.is_visible():
                        continue
                except Exception:
                    continue
                count += 1
                before = len(js_errors)
                # Measure layout BEFORE hovering: the top of a reference element that
                # should NOT move (a good hover effect uses CSS transform, which never
                # reflows siblings; a broken one changes width/height/margin and shoves
                # the rest of the page — that's the bug we want to catch).
                # Reference = the bottom-most content block on the page. A hover that
                # reflows the layout pushes this block down; a good (transform-based)
                # hover leaves it exactly where it was.
                _MEASURE_JS = """(e) => {
                    let ref = document.body.lastElementChild;
                    if (ref === e || (ref && ref.contains(e))) ref = null;
                    const rr = ref ? ref.getBoundingClientRect() : null;
                    const er = e.getBoundingClientRect();
                    const cs = getComputedStyle(e);
                    const brokenImg = (e.tagName === 'IMG')
                        ? (e.complete && e.naturalWidth === 0)
                        : Array.from(e.querySelectorAll('img')).some(
                            i => i.complete && i.naturalWidth === 0);
                    return {
                        refTop: rr ? Math.round(rr.top) : null,
                        sh: document.documentElement.scrollHeight,
                        w: Math.round(er.width), h: Math.round(er.height),
                        vis: cs.display !== 'none' && cs.visibility !== 'hidden'
                             && parseFloat(cs.opacity) > 0.01
                             && er.width > 0 && er.height > 0,
                        brokenImg: brokenImg,
                    };
                }"""
                try:
                    box = el.bounding_box()
                except Exception:
                    box = None
                if not box:
                    continue
                try:
                    pre = el.evaluate(_MEASURE_JS)
                except Exception:
                    pre = None
                # Use a real pointer move (not el.hover, which waits for stability and
                # times out if the element vanishes on hover — which is itself a bug we
                # want to catch).
                try:
                    page.mouse.move(box['x'] + box['width'] / 2,
                                    box['y'] + box['height'] / 2)
                    page.wait_for_timeout(250)
                except Exception:
                    continue

                if len(js_errors) > before:
                    findings.append({
                        'issue_type': 'hover_js_error',
                        'issue': 'Hovering this element triggers a JavaScript error',
                        'severity': 'major',
                        'element_selector': _safe_get_selector(el),
                        'parent_context': _safe_get_parent_text(el),
                        'bounding_box': box,
                        'display': f"Hover error: {js_errors[-1][:120]}",
                    })

                # Objective hover-behaviour anomalies (no AI, no user input needed).
                try:
                    post = el.evaluate(_MEASURE_JS)
                except Exception:
                    post = None
                if pre and post:
                    label = _safe_get_selector(el)
                    # a) element (or its image) vanishes on hover
                    if pre.get('vis') and not post.get('vis'):
                        findings.append({
                            'issue_type': 'hover_disappears',
                            'issue': 'Element disappears when hovered',
                            'severity': 'major',
                            'element_selector': label, 'bounding_box': box,
                            'display': f"Element disappears on hover: {label}",
                        })
                    elif not pre.get('brokenImg') and post.get('brokenImg'):
                        findings.append({
                            'issue_type': 'hover_image_break',
                            'issue': 'Image fails to load when hovered',
                            'severity': 'major',
                            'element_selector': label, 'bounding_box': box,
                            'display': f"Image breaks on hover: {label}",
                        })
                    else:
                        # b) hover reflows the page (good hover effects don't move siblings)
                        rt0, rt1 = pre.get('refTop'), post.get('refTop')
                        moved = (rt0 is not None and rt1 is not None
                                 and abs(rt1 - rt0) > 12)
                        grew = abs((post.get('sh') or 0) - (pre.get('sh') or 0)) > 24
                        if moved or grew:
                            findings.append({
                                'issue_type': 'hover_layout_shift',
                                'issue': 'Hovering shifts the page layout (content jumps)',
                                'severity': 'minor',
                                'element_selector': label, 'bounding_box': box,
                                'display': f"Hover causes layout to jump: {label}",
                            })
                # Move the mouse away so this hover's state doesn't taint the next probe.
                try:
                    page.mouse.move(2, 2)
                    page.wait_for_timeout(60)
                except Exception:
                    pass
        except Exception as e:
            logger.warning("interactions: hover probe error: %s", e)

        # ---- 3) Dead / error-throwing buttons (non-navigating, non-destructive) ----
        try:
            count = 0
            for el in page.query_selector_all("button, [role=button]"):
                if count >= CLICK_CAP:
                    break
                try:
                    if not el.is_visible() or not el.is_enabled():
                        continue
                    txt = (el.inner_text() or '').strip()
                except Exception:
                    continue
                if _UNSAFE_CLICK_TEXT.search(txt):
                    continue          # never auto-trigger destructive/submit actions
                try:
                    if el.evaluate("(e) => !!e.closest('form')"):
                        continue      # inside a form — could submit; skip
                except Exception:
                    continue
                count += 1
                before_err = len(js_errors)
                try:
                    el.evaluate(
                        """(e) => {
                            window.__qa_changed = false;
                            const o = new MutationObserver(() => { window.__qa_changed = true; });
                            o.observe(document.documentElement,
                                      {subtree: true, childList: true, attributes: true});
                            e.__qa_obs = o;
                        }""")
                except Exception:
                    pass
                try:
                    el.click(timeout=1500)
                    page.wait_for_timeout(200)
                except Exception:
                    try:
                        el.evaluate("(e) => { if (e.__qa_obs) e.__qa_obs.disconnect(); }")
                    except Exception:
                        pass
                    continue
                try:
                    navigated = page.url.split('#')[0] != start_url.split('#')[0]
                except Exception:
                    navigated = False
                dom_changed = False
                if not navigated:
                    try:
                        dom_changed = bool(page.evaluate("() => !!window.__qa_changed"))
                    except Exception:
                        dom_changed = True   # unknown → don't flag as dead
                try:
                    el.evaluate("(e) => { if (e.__qa_obs) { e.__qa_obs.disconnect(); delete e.__qa_obs; } }")
                except Exception:
                    pass

                if len(js_errors) > before_err:
                    findings.append({
                        'issue_type': 'click_js_error',
                        'issue': 'Clicking this control triggers a JavaScript error',
                        'severity': 'major',
                        'element_selector': _safe_get_selector(el),
                        'display': f"Click error on '{txt[:40] or 'button'}': {js_errors[-1][:100]}",
                    })
                elif navigated:
                    # It worked (navigated). Restore the page for the rest of the crawl.
                    try:
                        page.go_back(timeout=8000)
                        page.wait_for_timeout(200)
                    except Exception:
                        try:
                            page.goto(start_url, timeout=15000,
                                      wait_until='domcontentloaded')
                        except Exception:
                            pass
                elif not dom_changed:
                    try:
                        box = el.bounding_box()
                    except Exception:
                        box = None
                    findings.append({
                        'issue_type': 'dead_button',
                        'issue': 'Button appears dead — clicking it does nothing',
                        'severity': 'minor',
                        'element_selector': _safe_get_selector(el),
                        'bounding_box': box,
                        'display': f"Dead button — no response on click: '{txt[:40] or 'button'}'",
                    })
        except Exception as e:
            logger.warning("interactions: click probe error: %s", e)
    finally:
        # Always end up where we started so downstream link discovery runs on the
        # correct page, and detach the error listener.
        try:
            if page.url.split('#')[0] != start_url.split('#')[0]:
                page.goto(start_url, timeout=15000, wait_until='domcontentloaded')
        except Exception:
            pass
        try:
            page.remove_listener('pageerror', _on_pageerror)
        except Exception:
            pass

    return findings


# Inspect each <form> field and decide whether it declares validation matching its
# intent. SAFE: pure read-only DOM analysis — never sets values, submits, or sends
# anything. Heuristic intent detection (name/id/placeholder/type), then checks for a
# declared format constraint (input type / pattern). Generic, no site-specific rules.
_FORM_CHECK_JS = r"""
() => {
  const out = [];
  const forms = Array.from(document.querySelectorAll('form')).slice(0, 6);
  const intentOf = (el) => {
    const hay = ((el.name || '') + ' ' + (el.id || '') + ' ' +
                 (el.placeholder || '') + ' ' + (el.getAttribute('aria-label') || '') +
                 ' ' + (el.autocomplete || '')).toLowerCase();
    if (el.type === 'email' || /e-?mail/.test(hay)) return 'email';
    if (el.type === 'url' || /\b(url|website)\b/.test(hay)) return 'url';
    if (el.type === 'password' || /pass\s*word|passwd|pwd/.test(hay)) return 'password';
    if (el.type === 'tel' || /phone|mobile|\btel\b|contact.*(no|number)/.test(hay)) return 'tel';
    if (el.type === 'number' ||
        /\b(age|amount|price|qty|quantity|pin\s*code|zip|postal)\b/.test(hay)) return 'number';
    return null;
  };
  const declaresFormat = (el, intent) => {
    if (el.getAttribute('pattern')) return true;          // explicit regex constraint
    if (intent === 'email')  return el.type === 'email';  // type=email enforces format
    if (intent === 'url')    return el.type === 'url';
    if (intent === 'number') return el.type === 'number';
    if (intent === 'tel')    return false;                // type=tel does NOT enforce
    return true;
  };
  const SKIP = ['hidden','submit','button','reset','search','file','checkbox','radio','range','color'];
  forms.forEach((form, fi) => {
    if (form.getAttribute('role') === 'search') return;
    Array.from(form.querySelectorAll('input, textarea, select')).forEach(el => {
      const t = (el.type || '').toLowerCase();
      if (SKIP.includes(t) || el.disabled) return;
      const intent = intentOf(el);
      if (!intent) return;
      const r = el.getBoundingClientRect();
      const bbox = (r.width || r.height) ? {x: r.x, y: r.y, width: r.width, height: r.height} : null;
      const sel = el.name ? ('[name="' + el.name + '"]') : (el.id ? ('#' + el.id) : el.tagName.toLowerCase());
      const label = el.name || el.id || el.placeholder || el.getAttribute('aria-label') || t || 'input';
      if (intent === 'password') {
        const ml = el.getAttribute('minlength');
        if ((!ml || parseInt(ml, 10) <= 0) && !el.getAttribute('pattern'))
          out.push({form_index: fi, kind: 'password_no_minlength', selector: sel, bbox, label});
        return;
      }
      if (!declaresFormat(el, intent))
        out.push({form_index: fi, kind: 'no_format_validation', selector: sel, bbox, label, intent});
    });
  });
  return out.slice(0, 30);
}
"""


def check_forms(page, base_url):
    """Form-validation checks. SAFE: read-only — never fills, submits, or sends the
    form (so it can't create accounts/orders or trigger anything). Flags fields whose
    purpose (email/phone/url/number/password) has NO declared input validation, so the
    form would accept clearly-invalid data. Generic; severity kept 'minor' because a
    site may still validate via custom JS/server — these are 'verify this' findings.
    """
    findings = []
    try:
        raw = page.evaluate(_FORM_CHECK_JS) or []
    except Exception as e:
        logger.warning("forms: evaluate error: %s", e)
        return findings

    TEMPL = {
        'no_format_validation': "The '{label}' field has no input validation — it "
                                "would accept invalid {intent} values",
        'password_no_minlength': "The password field '{label}' has no minimum-length "
                                 "requirement",
    }
    for r in raw:
        kind = r.get('kind')
        label = (r.get('label') or 'input')[:40]
        intent = r.get('intent') or 'input'
        text = TEMPL.get(kind, "Form field '{label}' may lack validation").format(
            label=label, intent=intent)
        findings.append({
            'issue_type': kind,
            'issue': text,
            'severity': 'minor',
            'element_selector': r.get('selector'),
            'bounding_box': r.get('bbox'),
            'form_index': r.get('form_index'),
            'display': text,
        })
    return findings


# Rule-based VISUAL/layout defect detector (runs in the browser). All measurable —
# pixel positions and sizes — so NO AI and no API credits are needed. Conservative on
# purpose to avoid false positives. Generic; works on any site.
_VISUAL_LAYOUT_JS = r"""
() => {
  const out = [];
  const vw = window.innerWidth, vh = window.innerHeight, tol = 4;
  const sx = window.scrollX, sy = window.scrollY;
  const vis = (el) => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const txt = (el) => (el.innerText || '').trim();
  const sel = (el) => el.id ? ('#' + el.id)
    : (typeof el.className === 'string' && el.className.trim()
        ? el.tagName.toLowerCase() + '.' + el.className.trim().split(/\s+/)[0]
        : el.tagName.toLowerCase());
  const box = (r) => ({x: r.x + sx, y: r.y + sy, width: r.width, height: r.height});

  // 1) Page-level horizontal scroll (content wider than the screen)
  const docW = document.documentElement.scrollWidth;
  if (docW > vw + tol) {
    out.push({kind: 'horizontal_scroll', selector: 'page', sev: 'major',
      text: 'Page is ' + (docW - vw) + 'px wider than the screen — a horizontal scrollbar appears'});
    let n = 0;
    for (const el of document.querySelectorAll('body *')) {
      if (n >= 3) break;
      if (!vis(el)) continue;
      const s = getComputedStyle(el);
      if (s.position === 'fixed' || s.position === 'absolute') continue;
      const r = el.getBoundingClientRect();
      if (r.right > vw + 12 && r.width > 20 && r.width <= vw + 300) {
        out.push({kind: 'overflow_right', selector: sel(el), sev: 'minor', bbox: box(r),
          text: 'Element runs ' + Math.round(r.right - vw) + 'px past the right edge of the screen: <' + el.tagName.toLowerCase() + '>'});
        n++;
      }
    }
  }

  // 2) Distorted images (rendered aspect ratio far from natural, no object-fit)
  let imgN = 0;
  for (const img of document.querySelectorAll('img')) {
    if (imgN >= 15) break;
    if (!vis(img)) continue;
    const nw = img.naturalWidth, nh = img.naturalHeight;
    if (!nw || !nh) continue;
    const of = getComputedStyle(img).objectFit;
    if (of === 'cover' || of === 'contain' || of === 'scale-down') continue;
    const r = img.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) continue;
    const natR = nw / nh, dispR = r.width / r.height;
    if (Math.abs(natR - dispR) / natR > 0.25) {
      imgN++;
      out.push({kind: 'distorted_image', selector: sel(img), sev: 'minor', bbox: box(r),
        text: 'Image looks stretched/distorted (shown ' + Math.round(r.width) + '×' +
              Math.round(r.height) + ' vs original ' + nw + '×' + nh + ')'});
    }
  }

  // 3) Text cut off / clipped (overflow hidden, no ellipsis)
  let clipN = 0;
  for (const el of document.querySelectorAll('h1,h2,h3,h4,p,span,a,button,li,td,div')) {
    if (clipN >= 10) break;
    if (!vis(el)) continue;
    if (el.childElementCount > 1) continue;             // text leaf only — skip layout
                                                        // wrappers / carousels / scrollers
    const s = getComputedStyle(el);
    if (!/hidden|clip/.test(s.overflowX) && !/hidden|clip/.test(s.overflowY)) continue;
    if (s.textOverflow === 'ellipsis') continue;        // intentional truncation
    const t = txt(el);
    if (!t || t.length < 4) continue;
    if ((el.scrollWidth > el.clientWidth + tol || el.scrollHeight > el.clientHeight + tol)
        && el.clientWidth > 0 && el.clientHeight > 0) {
      const r = el.getBoundingClientRect();
      clipN++;
      out.push({kind: 'text_clipped', selector: sel(el), sev: 'minor', bbox: box(r),
        text: 'Text appears cut off / clipped: "' + t.slice(0, 40) + '"'});
    }
  }

  // 4) Sibling overlap (same parent, both have text, normal flow) — kept narrow to
  //    avoid the false positives a global overlap scan produces.
  let ovN = 0;
  for (const p of document.querySelectorAll('body *')) {
    if (ovN >= 6) break;
    const kids = Array.from(p.children).filter(c => vis(c) && txt(c));
    for (let i = 0; i < kids.length && ovN < 6; i++) {
      for (let j = i + 1; j < kids.length && ovN < 6; j++) {
        const a = kids[i], b = kids[j];
        const sa = getComputedStyle(a), sb = getComputedStyle(b);
        if (!/^(static|relative)$/.test(sa.position) || !/^(static|relative)$/.test(sb.position)) continue;
        const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
        const ix = Math.max(0, Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left));
        const iy = Math.max(0, Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top));
        const inter = ix * iy;
        if (inter <= 0) continue;
        const minA = Math.min(ra.width * ra.height, rb.width * rb.height);
        if (minA > 0 && inter / minA > 0.4) {
          ovN++;
          out.push({kind: 'overlap', selector: sel(a) + ' / ' + sel(b), sev: 'minor', bbox: box(ra),
            text: 'Two elements overlap: "' + txt(a).slice(0, 25) + '" and "' + txt(b).slice(0, 25) + '"'});
        }
      }
    }
  }

  return out.slice(0, 40);
}
"""


def check_visual_layout(page, base_url):
    """Rule-based VISUAL/layout defects — no AI, no API credits. Catches the things
    the user can SEE and that are measurable: horizontal scroll, elements running off
    the screen, distorted (wrong aspect-ratio) images, clipped/cut-off text, and
    overlapping sibling elements. Conservative to keep false positives low. Generic.
    """
    findings = []
    try:
        raw = page.evaluate(_VISUAL_LAYOUT_JS) or []
    except Exception as e:
        logger.warning("visual layout scan error: %s", e)
        return findings
    for r in raw:
        if not isinstance(r, dict):
            continue
        text = (r.get('text') or '').strip()
        if not text:
            continue
        sev = r.get('sev')
        if sev not in ('critical', 'major', 'minor', 'info'):
            sev = 'minor'
        findings.append({
            'issue_type': r.get('kind') or 'visual',
            'issue': text,
            'severity': sev,
            'element_selector': r.get('selector'),
            'bounding_box': r.get('bbox'),
            'display': text,
        })
    return findings


# Scan VISIBLE page text for broken values / leaked code / error messages — the kind
# of bug a user immediately sees but the structural checks miss. Runs in the browser
# on document.body.innerText (already excludes hidden text). Generic, conservative.
_CONTENT_SCAN_JS = r"""
() => {
  const text = (document.body ? document.body.innerText : '') || '';
  const out = [];
  const checks = [
    ['broken_data', 'major', 'A broken/placeholder value is shown to users',
      /\[object Object\]|(^|\s)undefined(\s|$|[.,!?)])|(^|\s)NaN(\s|$|[.,!?)])/],
    ['template_leftover', 'major', 'Unrendered template code is visible on the page',
      /\{\{\s*[\w.$\[\]]+\s*\}\}|\$\{\s*[\w.$\[\]]+\s*\}/],
    ['server_error', 'major', 'A server / programming error is visible on the page',
      /Fatal error|Parse error|Uncaught \w*Error|Traceback \(most recent|Stack trace:|SQLSTATE|Warning:\s|Notice:\s|\bon line \d+/],
    ['error_ui', 'major', 'An error message is visible on the page',
      /something went wrong|internal server error|500 internal|503 service unavailable/i],
    ['placeholder_text', 'minor', 'Placeholder / dummy content was left in',
      /lorem ipsum|dolor sit amet|\bTODO\b|\bFIXME\b|\bLOREM\b/i],
  ];
  for (const [kind, sev, label, re] of checks) {
    const m = text.match(re);
    if (m) out.push({kind, sev, label, snippet: (m[0] || '').toString().trim().slice(0, 80)});
  }
  return out;
}
"""


# Plain-language "why this matters" for each finding, so every issue reads as a real,
# logical problem (user impact) instead of a cryptic technical flag. Keyed first by the
# specific issue_type, then a per-category fallback. No AI — just a lookup.
_IMPACT_BY_TYPE = {
    # interaction / functional
    'broken_image': "Visitors see a blank/broken image where a picture should be.",
    'hover_js_error': "The feature errors out when used, so it stops working for the visitor.",
    'click_js_error': "Clicking this throws an error — the action fails for the visitor.",
    'dead_button': "The visitor clicks and nothing happens — the feature looks broken.",
    'hover_layout_shift': "Content jumps when hovered — jarring, and can cause mis-clicks.",
    'hover_disappears': "The element vanishes on hover, so the visitor can't see or use it.",
    'hover_image_break': "The image breaks the moment it's hovered.",
    'unlabeled_control': "Screen readers announce nothing here, and its purpose is unclear.",
    # forms
    'no_format_validation': "Users can submit wrong data (e.g. a fake email), creating bad records.",
    'password_no_minlength': "Weak passwords are allowed, which hurts account security.",
    # visual / layout
    'horizontal_scroll': "The page is wider than the screen, forcing awkward sideways scrolling.",
    'overflow_right': "This element pushes past the screen edge and breaks the layout.",
    'distorted_image': "The image looks stretched/squished and unprofessional.",
    'text_clipped': "Part of the text is cut off, so visitors can't read it fully.",
    'overlap': "Two elements sit on top of each other, looking broken.",
    # content / data
    'broken_data': "A raw/placeholder value shows instead of real content — looks broken.",
    'template_leftover': "Page code is leaking onto the screen instead of real content.",
    'server_error': "An internal error message is exposed to visitors.",
    'error_ui': "Visitors see an error message instead of the content they wanted.",
    'placeholder_text': "Dummy sample text was left in instead of the real content.",
}
_IMPACT_BY_CATEGORY = {
    'broken_links': "Visitors who click this hit a dead page — looks broken and loses trust.",
    'console_errors': "A script crashed in the browser — page features may silently stop working.",
    'missing_alt_images': "Screen-reader users and Google can't tell what this image is.",
    'seo_issues': "Affects how this page shows up in Google search results.",
    'security_issues': "Leaves the site more exposed to common web attacks.",
    'accessibility_issues': "Some visitors (low vision / screen readers) can't use this part.",
    'mobile_issues': "Phone users struggle to tap or read this.",
    'interaction_issues': "A feature on the page doesn't work as a visitor expects.",
    'form_issues': "The form can accept bad input or weak data.",
    'visual_issues': "The page looks broken or hard to read here.",
    'content_issues': "Visitors see broken or placeholder content instead of the real thing.",
}


def _attach_impacts(findings_dict):
    """Add a plain 'why' (why it matters) to every finding. Uses its own key — NOT
    'impact', which axe-core already uses for its severity level (serious/critical…)."""
    for cat, items in findings_dict.items():
        for it in items:
            if isinstance(it, dict) and not it.get('why'):
                why = _IMPACT_BY_TYPE.get(it.get('issue_type')) or _IMPACT_BY_CATEGORY.get(cat)
                if why:
                    it['why'] = why
    return findings_dict


def check_content_quality(page, base_url):
    """Visible-text quality checks — catches what a user sees instantly but the
    structural checks miss: 'undefined'/'NaN'/'[object Object]' (broken data binding),
    unrendered '{{ template }}' code, leaked server/PHP/stack errors, generic error
    messages, and leftover placeholder text. Generic and conservative."""
    findings = []
    try:
        raw = page.evaluate(_CONTENT_SCAN_JS) or []
    except Exception as e:
        logger.warning("content scan error: %s", e)
        return findings
    for r in raw:
        if not isinstance(r, dict):
            continue
        label = (r.get('label') or '').strip()
        snip = (r.get('snippet') or '').strip()
        sev = r.get('sev') if r.get('sev') in ('critical', 'major', 'minor', 'info') else 'minor'
        if not label:
            continue
        findings.append({
            'issue_type': r.get('kind') or 'content',
            'issue': label,
            'severity': sev,
            'snippet': snip,
            'display': f"{label}" + (f': "{snip}"' if snip else ''),
        })
    return findings


def test_single_page(page, page_url, response_headers, console_messages, is_mobile=True):
    broken_links = check_broken_links(page, page_url)
    missing_alt = check_missing_alt(page)
    seo_issues = check_seo(page, page_url)
    security_issues = check_security(page_url, response_headers,
                                     page=page, console_messages=console_messages)
    accessibility_issues = check_accessibility(page)
    # Mobile checks (tap-target size, horizontal scroll) only make sense in the
    # mobile viewport. Authenticated crawls run in a DESKTOP context (to keep the
    # session valid), where these would flag every normal desktop control as a
    # "too small tap target" — pure noise. Skip them off mobile.
    mobile_issues = check_mobile(page) if is_mobile else []
    interaction_issues = check_interactions(page, page_url)
    form_issues = check_forms(page, page_url)
    visual_issues = check_visual_layout(page, page_url)
    content_issues = check_content_quality(page, page_url)
    real_errors = filter_console_errors(console_messages, base_url=page_url)
    console_errors_rich = [
        {**msg, 'element_selector': 'console', 'parent_context': ''}
        for msg in real_errors
    ]
    return _attach_impacts({
        'broken_links': broken_links,
        'console_errors': console_errors_rich,
        'missing_alt_images': missing_alt,
        'seo_issues': seo_issues,
        'security_issues': security_issues,
        'accessibility_issues': accessibility_issues,
        'mobile_issues': mobile_issues,
        'interaction_issues': interaction_issues,
        'form_issues': form_issues,
        'visual_issues': visual_issues,
        'content_issues': content_issues,
    })


def tag_findings_with_page(findings_dict, page_url):
    for category, items in findings_dict.items():
        for item in items:
            if isinstance(item, dict):
                item['page_url'] = page_url
    return findings_dict


def merge_findings(all_pages_findings):
    """Combine per-page findings into one summary, de-duplicating issues that
    repeat across pages. The SAME footer broken-link, missing security header, or
    a11y rule appears on every page of a site; counting it once per page inflated
    the issue total and wrongly tanked the health score. Per-page detail is still
    preserved in the crawled_pages records — this only affects the summary.
    """
    merged = {
        'broken_links': [],
        'console_errors': [],
        'missing_alt_images': [],
        'seo_issues': [],
        'security_issues': [],
        'accessibility_issues': [],
        'mobile_issues': [],
        'interaction_issues': [],
        'form_issues': [],
        'visual_issues': [],
        'content_issues': [],
    }
    # A stable identity per category so the same defect isn't recounted per page.
    keyers = {
        'broken_links': lambda i: i.get('url'),
        'console_errors': lambda i: i.get('text'),
        'missing_alt_images': lambda i: i.get('src'),
        'seo_issues': lambda i: i.get('issue'),
        'security_issues': lambda i: i.get('issue'),
        'accessibility_issues': lambda i: (i.get('rule'), i.get('element_selector')),
        # issue text embeds per-page counts ("3 tap targets…" vs "1 tap target…"),
        # so key on the stable issue_type instead — same defect, one finding.
        'mobile_issues': lambda i: i.get('issue_type') or i.get('issue'),
        # A broken image / dead button is identified by its element + type.
        'interaction_issues': lambda i: (i.get('issue_type'),
                                         i.get('src') or i.get('element_selector')),
        'form_issues': lambda i: (i.get('issue_type'), i.get('element_selector'),
                                  i.get('form_index')),
        # Visual defects: same type + element (or same AI description) → count once.
        'visual_issues': lambda i: (i.get('issue_type'),
                                    i.get('element_selector')
                                    or (i.get('issue') or '')[:80].lower()),
        # Same broken-text kind + snippet across pages → count once.
        'content_issues': lambda i: (i.get('issue_type'),
                                     (i.get('snippet') or i.get('issue') or '')[:60].lower()),
    }
    # Categories where a repeat across pages should ADD to the first finding's
    # count (and remember which pages were affected) instead of being dropped.
    aggregate_counts = {'mobile_issues', 'console_errors'}
    first_by_key = {cat: {} for cat in merged}
    seen = {cat: set() for cat in merged}
    for page_findings in all_pages_findings:
        for category, items in page_findings.items():
            if category not in merged:
                continue
            keyer = keyers.get(category)
            for item in items:
                key = None
                if keyer and isinstance(item, dict):
                    try:
                        key = keyer(item)
                    except Exception:
                        key = None
                if key is not None:
                    if key in seen[category]:
                        if category in aggregate_counts:
                            existing = first_by_key[category].get(key)
                            if existing is not None:
                                existing['count'] = (existing.get('count') or 1) + (item.get('count') or 1)
                                pages = existing.setdefault('pages_affected', [existing.get('page_url')])
                                if item.get('page_url') and item['page_url'] not in pages:
                                    pages.append(item['page_url'])
                                base = (existing.get('display') or existing.get('issue') or '').split(' — across')[0]
                                existing['display'] = f"{base} — across {len(pages)} page(s), {existing['count']} total"
                        continue
                    seen[category].add(key)
                    if isinstance(item, dict):
                        first_by_key[category][key] = item
                merged[category].append(item)
    return merged


# ============================================================
# MAIN ENDPOINT
# ============================================================

@runner_bp.route('/run/<int:testcase_id>', methods=['POST'])
@jwt_required()
def run_test(testcase_id):
    """Synchronous run: blocks until the crawl finishes (kept for compatibility)."""
    user_id = int(get_jwt_identity())
    tc = user_owns_testcase(user_id, testcase_id)
    if not tc:
        return jsonify({'error': 'Test case not found'}), 404
    if tc['test_type'] != 'automated':
        return jsonify({'error': 'Only automated test cases can be run'}), 400

    result, code = _perform_run(testcase_id, dict(tc))
    return jsonify(result), code


@runner_bp.route('/run/<int:testcase_id>/async', methods=['POST'])
@jwt_required()
def run_test_async(testcase_id):
    """Queue the run on a background worker and return a job id immediately.

    If the project has no valid captured login session, the crawl would run as a
    logged-out (public) visitor — meaningless for login-gated sites. We block the
    first attempt with 409 + needs_login_confirm so the UI can warn; the client
    re-sends with ?force=true once the user has confirmed (or after logging in)."""
    user_id = int(get_jwt_identity())
    tc = user_owns_testcase(user_id, testcase_id)
    if not tc:
        return jsonify({'error': 'Test case not found'}), 404
    if tc['test_type'] != 'automated':
        return jsonify({'error': 'Only automated test cases can be run'}), 400

    force = (request.args.get('force') == 'true'
             or bool((request.get_json(silent=True) or {}).get('force')))
    logger.info("run_test_async: tc=%s requires_login=%s force=%s",
                testcase_id, tc.get('requires_login'), force)
    # Only warn for sites the user marked as login-required. Public sites are
    # meant to be tested logged-out, so they run straight through.
    if not force and tc.get('requires_login'):
        session_path = _session_state_path(tc.get('project_id'))
        valid = False
        if session_path:
            valid, _warn = _session_expiry_info(session_path)
        if not valid:
            logger.info("run_test_async: blocking tc=%s with needs_login_confirm "
                        "(no valid session)", testcase_id)
            return jsonify({
                'needs_login_confirm': True,
                'error': ('No saved login session for this project. The test will run '
                          'as a logged-out (public) visitor, so any pages behind a login '
                          'will not be tested. Use "Open & Login" on the Projects page '
                          'first if this site needs login.'),
            }), 409

    job_id = job_manager.submit(user_id, testcase_id, dict(tc))
    return jsonify({'job_id': job_id, 'status': 'queued'}), 202


@runner_bp.route('/job/<job_id>', methods=['GET'])
@jwt_required()
def get_job(job_id):
    """Poll the status/result of a queued run."""
    user_id = int(get_jwt_identity())
    job = job_manager.get(job_id)
    if not job or job.get('user_id') != user_id:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job), 200


@runner_bp.route('/job/<job_id>/cancel', methods=['POST'])
@jwt_required()
def cancel_job(job_id):
    """Ask the worker to stop the crawl. The current page finishes, then the
    worker exits the BFS loop and persists what it has so far."""
    user_id = int(get_jwt_identity())
    ok, message = job_manager.cancel(job_id, user_id)
    if not ok:
        return jsonify({'error': message}), 404 if 'not found' in message else 409
    return jsonify({'message': message}), 202


# ============================================================
# MANUAL RUN  (tracked: headed browser + auto screenshots)
# ============================================================

@runner_bp.route('/manual/<int:testcase_id>/start', methods=['POST'])
@jwt_required()
def manual_start(testcase_id):
    """Open a headed browser at the project URL so the user can walk through
    the test manually. Each page navigation is screenshotted automatically."""
    user_id = int(get_jwt_identity())
    tc = user_owns_testcase(user_id, testcase_id)
    if not tc:
        return jsonify({'error': 'Test case not found'}), 404
    ok, message, snap = manual_run.start(
        testcase_id, tc.get('project_id'), tc.get('base_url'),
        max_pages=tc.get('max_pages') or 25,
    )
    if not ok:
        return jsonify({'error': message}), 500
    return jsonify({'message': message, 'run': snap}), 200


@runner_bp.route('/manual/<int:testcase_id>/autocrawl', methods=['POST'])
@jwt_required()
def manual_autocrawl(testcase_id):
    """User has logged in manually — now let the system BFS-walk the site,
    screenshotting every reachable page from the authenticated state."""
    user_id = int(get_jwt_identity())
    tc = user_owns_testcase(user_id, testcase_id)
    if not tc:
        return jsonify({'error': 'Test case not found'}), 404
    ok, message = manual_run.autocrawl(testcase_id)
    if not ok:
        return jsonify({'error': message}), 409
    return jsonify({'message': message}), 200


@runner_bp.route('/manual/<int:testcase_id>/status', methods=['GET'])
@jwt_required()
def manual_status(testcase_id):
    user_id = int(get_jwt_identity())
    tc = user_owns_testcase(user_id, testcase_id)
    if not tc:
        return jsonify({'error': 'Test case not found'}), 404
    return jsonify(manual_run.status(testcase_id)), 200


@runner_bp.route('/manual/<int:testcase_id>/done', methods=['POST'])
@jwt_required()
def manual_done(testcase_id):
    """User clicked Done. Save a test_runs row with screenshots, mark Pass/Fail."""
    user_id = int(get_jwt_identity())
    tc = user_owns_testcase(user_id, testcase_id)
    if not tc:
        return jsonify({'error': 'Test case not found'}), 404

    data = request.get_json(silent=True) or {}
    outcome = data.get('outcome', 'Pass')
    if outcome not in ('Pass', 'Fail'):
        return jsonify({'error': 'outcome must be Pass or Fail'}), 400
    note = data.get('note', '')

    ok, message, snap = manual_run.finish(testcase_id, outcome, note)
    if not ok:
        return jsonify({'error': message}), 409

    duration_ms = 0
    try:
        started = datetime.fromisoformat(snap['started_at'])
        finished = datetime.fromisoformat(snap['finished_at'])
        duration_ms = int((finished - started).total_seconds() * 1000)
    except Exception:
        pass

    pages = snap.get('pages') or []
    first_shot = pages[0]['screenshot'] if pages else ''

    # Aggregate the per-page QA findings the manual worker collected as the user
    # walked the (authenticated) site, so a manual run produces a real report —
    # deduped across pages, with a computed health score — not just screenshots.
    all_pages_findings = []
    for p in pages:
        f = p.get('findings')
        if isinstance(f, dict):
            all_pages_findings.append(tag_findings_with_page(f, p.get('url', '')))
    merged = merge_findings(all_pages_findings) if all_pages_findings else {}
    issues_found = sum(len(v) for v in merged.values()) if merged else 0
    health = calculate_health_score(merged) if merged else (100 if outcome == 'Pass' else 0)

    findings_evidence = json.dumps({
        'manual': True,
        'note': note,
        'summary': merged,
        'pages': pages,
    })

    try:
        cursor = mysql.connection.cursor()
        cursor.execute("SELECT 1 FROM test_cases WHERE id = %s", (testcase_id,))
        if cursor.fetchone() is None:
            cursor.close()
            return jsonify({'error': 'Test case was deleted before the run finished.'}), 410

        cursor.execute(
            """INSERT INTO test_runs (
                test_case_id, status, screenshot, run_at, duration_ms,
                issues_found, total_requests, findings_evidence, health_score
            ) VALUES (%s, %s, %s, NOW(), %s, %s, %s, %s, %s)""",
            (
                testcase_id, outcome,
                first_shot,
                duration_ms,
                issues_found, len(pages),
                findings_evidence,
                health,
            )
        )
        run_id = cursor.lastrowid
        cursor.execute("UPDATE test_cases SET status = %s WHERE id = %s",
                       (outcome, testcase_id))
        mysql.connection.commit()
        cursor.close()
    except Exception as e:
        logger.exception("manual_done insert failed: %s", e)
        return jsonify({'error': 'Saved screenshots but failed to write run record.'}), 500

    return jsonify({
        'message': message,
        'run_id': run_id,
        'status': outcome,
        'pages_visited': len(pages),
        'issues_found': issues_found,
        'health_score': health,
        'duration_ms': duration_ms,
        'screenshots': [p['screenshot'] for p in pages],
    }), 200


@runner_bp.route('/manual/<int:testcase_id>/cancel', methods=['POST'])
@jwt_required()
def manual_cancel(testcase_id):
    user_id = int(get_jwt_identity())
    tc = user_owns_testcase(user_id, testcase_id)
    if not tc:
        return jsonify({'error': 'Test case not found'}), 404
    ok, message = manual_run.cancel(testcase_id)
    return jsonify({'message': message, 'ok': ok}), 200


def _perform_run(testcase_id, tc, progress_cb=None, cancelled_check=None):
    """
    Execute an automated crawl and persist the results.
    Returns (result_dict, status_code).

    Independent of request context: it uses mysql.connection, which is valid
    inside any Flask app context, so it can be called both from the synchronous
    endpoint and from a background worker that wraps it in app.app_context().

    progress_cb: optional callable(dict) invoked after each page so a background
    job can report live progress (tested / total / current url).

    cancelled_check: optional callable() -> bool. Polled between pages; if it
    returns True we stop the BFS, persist whatever's already been collected,
    and return cleanly so the worker can mark the job 'cancelled'.
    """
    def _report(**kw):
        if progress_cb:
            try:
                progress_cb(kw)
            except Exception:
                pass

    def _is_cancelled():
        if cancelled_check is None:
            return False
        try:
            return bool(cancelled_check())
        except Exception:
            return False
    base_url = tc['base_url']
    crawl_pages = bool(tc.get('crawl_pages'))
    max_pages = int(tc.get('max_pages') or 10)
    if max_pages < 1:
        max_pages = 1
    if max_pages > 100:
        max_pages = 100

    # Use a captured manual-login session if one exists for this project.
    session_path = _session_state_path(tc.get('project_id'))
    session_used = bool(session_path)
    session_warning = None
    if session_used:
        valid, warn = _session_expiry_info(session_path)
        if not valid:
            # Auth cookies are dead. Loading the storage_state would silently fall
            # back to the public site and waste a crawl — fail loud instead.
            session_used = False
            session_warning = warn
            logger.warning("session expired for project '%s': %s", tc.get('project_name'), warn)
        elif warn:
            session_warning = warn
            logger.info("session warning for project '%s': %s", tc.get('project_name'), warn)
    session_message = 'Crawled as logged-in user (captured session).' if session_used \
        else (session_warning or 'Crawled anonymously (no captured session).')

    start_time = time.time()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            # When we have a captured session, use a DESKTOP context that matches the
            # session_capture window. Many sites bind auth cookies to the originating
            # UA — using a mobile UA invalidates the session. Without a session we keep
            # the mobile viewport so the mobile-issues checks still mean something.
            if session_used:
                context_kwargs = {
                    'viewport': {'width': 1366, 'height': 768},
                    'user_agent': (
                        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/131.0.0.0 Safari/537.36'
                    ),
                    'storage_state': session_path,
                }
                logger.info("run_test: using captured session for project '%s' (desktop)", tc.get('project_name'))
            else:
                context_kwargs = {
                    'viewport': {'width': 390, 'height': 844},
                    'user_agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)',
                }
                logger.info("run_test: no captured session for project '%s'; crawling anonymously", tc.get('project_name'))
            context = browser.new_context(**context_kwargs)

            # Stealth: hide navigator.webdriver so sites that gate auth on automation
            # detection (very common for SPA + token APIs) still serve the dashboard.
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )

            # Replay sessionStorage captured during manual login. Playwright's
            # storage_state covers cookies + localStorage but NOT sessionStorage —
            # which is exactly where many SPAs (digielv among them) keep the
            # "user is logged in" flag. Without this replay the SPA boots, sees
            # an empty sessionStorage, and redirects to /user-login even though
            # the auth cookies are valid.
            if session_used:
                ss_data = _session_storage_dict(tc.get('project_id'))
                if ss_data:
                    import json as _json
                    # Double-encode: inner dumps -> JSON text of the data; outer
                    # dumps -> a properly-escaped JS string LITERAL of that text
                    # (ensure_ascii escapes every non-ASCII char incl. U+2028/9).
                    # The script then JSON.parse()s the literal, so captured site
                    # data can never break out of the string and execute as code.
                    payload = _json.dumps(_json.dumps(ss_data))
                    context.add_init_script(
                        "(() => { try { const d = JSON.parse(" + payload + "); "
                        "for (const k in d) sessionStorage.setItem(k, d[k]); "
                        "} catch(e) {} })();"
                    )
                    logger.info("injecting %d sessionStorage keys for project '%s'",
                                len(ss_data), tc.get('project_name'))
                else:
                    logger.warning("no sessionStorage captured for project '%s' — "
                                   "if the site keeps auth here, the crawl will be anonymous. "
                                   "Re-capture session with the latest Open & Login.",
                                   tc.get('project_name'))

            screenshot_dir = os.path.join('static', 'uploads', 'runs')
            os.makedirs(screenshot_dir, exist_ok=True)
            evidence_dir = os.path.join('static', 'uploads', 'evidence')
            os.makedirs(evidence_dir, exist_ok=True)

            crop_run_id = int(time.time())

            # Breadth-first crawl. We start at base_url and, when crawl_pages is on,
            # discover internal links from EVERY page we visit (not just the first),
            # enqueuing new ones until we've tested max_pages. This keeps working even
            # when the start URL is a sparse page (e.g. a login page): once a captured
            # session lands us on the actual app, its nav links get discovered and
            # followed instead of dead-ending after one page.
            base_clean = base_url.rstrip('/').split('#')[0]
            to_visit = [base_clean]
            queued = {base_clean}

            # Also seed the site root so we don't dead-end when base_url is a sparse
            # entry page (e.g. /user-login). Once authenticated via storage_state,
            # the root usually serves the dashboard — that's where the nav lives.
            parsed_base = urlparse(base_clean)
            origin_root = None
            if parsed_base.scheme and parsed_base.netloc:
                origin_root = f"{parsed_base.scheme}://{parsed_base.netloc}"
                if origin_root != base_clean and origin_root not in queued:
                    to_visit.append(origin_root)
                    queued.add(origin_root)

            # If the user manually logged in via "Open & Login", session_capture saved
            # the URL they landed on (i.e. the authenticated dashboard). Use THAT as
            # the primary BFS start — many SPAs keep / and /user-login as public pages
            # even for authenticated users, so the dashboard lives on a different path.
            if session_used:
                landing = _session_landing_url(tc.get('project_id'))
                if landing:
                    landing_clean = landing.rstrip('/').split('#')[0]
                    if landing_clean and landing_clean not in queued:
                        to_visit.insert(0, landing_clean)
                        queued.add(landing_clean)
                        logger.info("crawl will start from authenticated landing: %s", landing_clean)

                # Seed authenticated routes the SPA stored in its menu payload
                # (sessionStorage/localStorage). These are pages whose nav links
                # render as JS router buttons, not <a href>, so normal discovery
                # never reaches them (dashboard, account, bank-details, etc.).
                seeded = 0
                for route in _routes_from_captured_storage(tc.get('project_id'), base_clean):
                    if route not in queued:
                        to_visit.append(route)
                        queued.add(route)
                        seeded += 1
                if seeded:
                    logger.info("seeded %d authenticated route(s) from captured menu/storage", seeded)

                # Click-discover authenticated pages whose nav is JS buttons (no
                # <a href>) — the encrypted-menu case. Done on a throwaway page in
                # the same authed context; the URLs found get the full test below.
                click_seed = (landing_clean if landing else None) or origin_root or base_clean
                if click_seed:
                    try:
                        clicked_routes = discover_links_by_clicking(context, click_seed, base_clean)
                    except Exception as e:
                        logger.warning("click discovery seed failed: %s", e)
                        clicked_routes = []
                    cseeded = 0
                    for route in clicked_routes:
                        if route not in queued:
                            to_visit.append(route)
                            queued.add(route)
                            cseeded += 1
                    if cseeded:
                        logger.info("seeded %d authenticated route(s) by clicking the nav menu", cseeded)

            # Auth warmup. SPAs (like digielv) often hit a separate API to validate the
            # session and only THEN redirect from /user-login to the dashboard. We open
            # the root once, let JS hydrate, and capture the resolved URL. If it lands
            # somewhere other than the login page, we treat THAT as the real start of
            # the crawl — that's where the authenticated nav lives.
            if session_used and origin_root:
                try:
                    warm = context.new_page()
                    warm.goto(origin_root, timeout=30000, wait_until='domcontentloaded')
                    try:
                        warm.wait_for_load_state('networkidle', timeout=8000)
                    except Exception:
                        pass
                    time.sleep(1.5)
                    landed = warm.url.rstrip('/').split('#')[0]
                    looks_like_login = any(
                        kw in landed.lower()
                        for kw in ('login', 'signin', 'sign-in', 'sign_in', 'auth')
                    )
                    if looks_like_login:
                        logger.warning("auth warmup: landed on login page '%s' — session may be "
                                       "invalid/expired (re-capture via Open & Login)", landed)
                    elif landed and landed not in queued:
                        # Front of the queue so the crawl starts from the authenticated
                        # landing page, not the configured /user-login URL.
                        to_visit.insert(0, landed)
                        queued.add(landed)
                        logger.info("auth warmup: authenticated landing = %s", landed)
                    else:
                        logger.info("auth warmup: landed on '%s' (already queued)", landed)
                    warm.close()
                except Exception as e:
                    logger.warning("auth warmup failed: %s", e)

            all_pages_findings = []
            per_page_records = []
            urls_to_test = []   # URLs actually visited, in order
            first_page_error = None   # set if even the base URL couldn't load

            # One page object for the entire crawl. We REUSE it across every URL
            # via SPA-internal click navigation (navigate_spa) so React stays
            # mounted and the authenticated session is preserved. Creating a new
            # tab per URL — as we used to — forces a fresh React mount and a
            # fresh sessionStorage on a new tab, which is why DigiELV-style apps
            # kept redirecting the crawler back to /user-login.
            crawl_page = context.new_page()
            console_messages = []
            saved_first_headers = {}

            crawl_page.on('console', lambda msg:
                console_messages.append({
                    'type': msg.type,
                    'text': msg.text,
                    'display': f"[{msg.type}] {msg.text[:200]}"
                }) if msg.type in ('error', 'warning') else None
            )

            page_idx = -1
            while to_visit and len(per_page_records) < max_pages:
                # User clicked Cancel — stop the crawl and persist whatever we
                # already captured. Treated as a graceful exit, not an error.
                if _is_cancelled():
                    logger.info("crawl cancelled by user at page %d/%d",
                                len(per_page_records), max_pages)
                    break
                page_url = to_visit.pop(0)
                page_idx += 1
                urls_to_test.append(page_url)

                console_messages.clear()         # per-URL console capture
                response_headers = {}
                page_status = 0
                page_title = ''
                page_load_ms = 0
                page_screenshot_url = None

                try:
                    page = crawl_page                       # alias: rest of the body uses `page`
                    nav_start = time.time()
                    nav_error = None
                    if page_idx == 0:
                        # First navigation in this run — full HTTP load so we can
                        # capture security headers and verify the page is reachable.
                        try:
                            response = page.goto(page_url, timeout=30000, wait_until='domcontentloaded')
                            if response:
                                page_status = response.status
                                response_headers = dict(response.headers)
                                saved_first_headers = dict(response.headers)
                        except Exception as e:
                            # Initial reach failed (DNS, refused, timeout, ...). Capture
                            # the reason so the result modal can show something useful
                            # instead of a blank screenshot + generic "0 pages".
                            nav_error = str(e)[:300]
                            response = None
                            logger.warning("initial page.goto failed (%s): %s", page_url, e)
                    else:
                        # SPA-internal click navigation: keeps React mounted and
                        # auth state alive. Falls back to page.goto inside
                        # navigate_spa if no matching link exists on the current
                        # page.
                        _, response = navigate_spa(page, page_url)
                        if response is not None:
                            page_status = response.status
                            response_headers = dict(response.headers)
                        else:
                            page_status = 200                      # click nav success
                            response_headers = saved_first_headers  # CSP/HSTS unchanged across SPA routes
                    page_load_ms = int((time.time() - nav_start) * 1000)

                    # If the first navigation failed to even reach the site, there is
                    # no DOM to inspect. Record a friendly per-page error, skip the
                    # screenshot (would be a blank white image) and the checks, and
                    # stop the BFS — without an entry page we cannot discover any
                    # further URLs.
                    if nav_error:
                        first_page_error = nav_error
                        per_page_records.append({
                            'url': page_url,
                            'page_title': '',
                            'status_code': 0,
                            'health_score': 0,
                            'issues_found': 0,
                            'page_load_time_ms': page_load_ms,
                            'screenshot': '',
                            'findings_evidence': json.dumps({'nav_error': nav_error}),
                        })
                        logger.warning("aborting crawl: initial URL unreachable (%s)", nav_error)
                        break

                    # Let client-side rendering and post-login redirects settle so that
                    # JS-injected navigation links become discoverable.
                    try:
                        page.wait_for_load_state('networkidle', timeout=5000)
                    except Exception:
                        pass
                    time.sleep(1.0)

                    try:
                        page_title = (page.title() or '')[:500]
                    except Exception:
                        page_title = ''

                    page_findings = test_single_page(page, page_url, response_headers,
                                                     console_messages, is_mobile=not session_used)
                    page_findings = tag_findings_with_page(page_findings, page_url)

                    screenshot_filename = f"run_{testcase_id}_{crop_run_id}_p{page_idx}.png"
                    screenshot_path = os.path.join(screenshot_dir, screenshot_filename)
                    try:
                        page.screenshot(path=screenshot_path, full_page=True)
                        page_screenshot_url = f"/static/uploads/runs/{screenshot_filename}"
                    except Exception as e:
                        logger.warning("screenshot error page %s: %s", page_idx, e)

                    if page_screenshot_url:
                        page_findings['broken_links'] = _enrich_with_crops(
                            page_findings['broken_links'], 'brokenlinks',
                            screenshot_path, evidence_dir, crop_run_id, page_idx
                        )
                        page_findings['missing_alt_images'] = _enrich_with_crops(
                            page_findings['missing_alt_images'], 'missingalt',
                            screenshot_path, evidence_dir, crop_run_id, page_idx
                        )
                        page_findings['accessibility_issues'] = _enrich_with_crops(
                            page_findings['accessibility_issues'], 'a11y',
                            screenshot_path, evidence_dir, crop_run_id, page_idx
                        )
                        page_findings['mobile_issues'] = _enrich_with_crops(
                            page_findings['mobile_issues'], 'mobile',
                            screenshot_path, evidence_dir, crop_run_id, page_idx
                        )
                        page_findings['interaction_issues'] = _enrich_with_crops(
                            page_findings.get('interaction_issues', []), 'interaction',
                            screenshot_path, evidence_dir, crop_run_id, page_idx
                        )
                        page_findings['form_issues'] = _enrich_with_crops(
                            page_findings.get('form_issues', []), 'form',
                            screenshot_path, evidence_dir, crop_run_id, page_idx
                        )
                        page_findings['visual_issues'] = _enrich_with_crops(
                            page_findings.get('visual_issues', []), 'visual',
                            screenshot_path, evidence_dir, crop_run_id, page_idx
                        )


                    # Discover further internal links from this rendered page and
                    # enqueue any we haven't seen (BFS), until the queue holds enough
                    # to reach max_pages. Done before closing the page.
                    if crawl_pages and len(queued) < max_pages * 4:
                        try:
                            for link in discover_internal_links(page, base_url, max_pages):
                                if link not in queued:
                                    queued.add(link)
                                    to_visit.append(link)
                        except Exception as e:
                            logger.warning("crawl link-discovery error: %s", e)

                    page_issues_found = sum(len(items) for items in page_findings.values())
                    page_health_score = calculate_health_score(page_findings)

                    all_pages_findings.append(page_findings)
                    per_page_records.append({
                        'url': page_url,
                        'page_title': page_title,
                        'status_code': page_status,
                        'health_score': page_health_score,
                        'issues_found': page_issues_found,
                        'page_load_time_ms': page_load_ms,
                        'screenshot': page_screenshot_url or '',
                        'findings_evidence': json.dumps(page_findings),
                    })

                    logger.info("crawl page %s/%s: %s - %s issues, health %s (queued: %s more)",
                                len(per_page_records), max_pages, page_url, page_issues_found,
                                page_health_score, len(to_visit))
                    _report(tested=len(per_page_records), total=max_pages,
                            current=page_url, issues_so_far=page_issues_found,
                            queued=len(to_visit))
                    # NOTE: do NOT page.close() here. The crawl page is reused
                    # across every URL so React stays mounted and the SPA's auth
                    # context survives. It is closed after the BFS loop.

                except Exception as e:
                    logger.warning("page test error %s: %s", page_url, e)
                    per_page_records.append({
                        'url': page_url,
                        'page_title': page_title or '',
                        'status_code': page_status or 0,
                        'health_score': 0,
                        'issues_found': 0,
                        'page_load_time_ms': page_load_ms,
                        'screenshot': '',
                        'findings_evidence': json.dumps({'error': str(e)[:500]}),
                    })
                    continue

            try:
                crawl_page.close()
            except Exception:
                pass
            browser.close()

            merged_findings = merge_findings(all_pages_findings)
            # Each display row carries the page URL it came from so the result
            # modal can label every issue with "found on /some-route" instead of
            # the user staring at a flat list and wondering which page is broken.
            # Severity is preserved too (the UI badge already keys off it).
            display_findings = {
                category: [
                    {
                        'display': item.get('display', str(item)),
                        'page_url': item.get('page_url', ''),
                        'severity': item.get('severity', 'minor'),
                        'screenshot_crop': item.get('screenshot_crop'),
                        'why': item.get('why', ''),
                    }
                    for item in items
                ]
                for category, items in merged_findings.items()
            }

            health_score = calculate_health_score(merged_findings)
            issues_found = sum(len(items) for items in merged_findings.values())
            breakdown = severity_breakdown(merged_findings)
            # Pass/Fail now reflects severity: a site with only minor/info
            # hygiene findings passes (with notes); anything critical/major fails.
            status = 'Fail' if (breakdown['critical'] or breakdown['major']) else 'Pass'

            # Override: if even the entry URL never loaded, we found 0 issues only
            # because we never tested anything. Showing "100 / PASS / Excellent"
            # in that case is a lie — the crawl literally couldn't begin. Mark
            # the run as Fail with a 0 score so the result modal matches the red
            # "Could not reach the site" banner already shown above the score.
            if first_page_error:
                status = 'Fail'
                health_score = 0

            duration_ms = int((time.time() - start_time) * 1000)

            first_page = per_page_records[0] if per_page_records else {}
            page_load_ms = first_page.get('page_load_time_ms', 0)

            cursor = mysql.connection.cursor()
            # Guard: the user may have deleted the test case while the crawl was
            # running. Inserting into test_runs would then fail with a foreign-key
            # violation, which currently dumps a 21-line traceback to the console.
            # Detect it cleanly and return a meaningful error.
            cursor.execute("SELECT 1 FROM test_cases WHERE id = %s", (testcase_id,))
            if cursor.fetchone() is None:
                cursor.close()
                logger.warning("test case %s was deleted mid-crawl; discarding %d page results",
                               testcase_id, len(per_page_records))
                return {
                    'error': 'Test case was deleted while the crawl was running. '
                             'The collected results have been discarded.',
                    'status': 'Fail',
                    'pages_crawled': len(per_page_records),
                }, 410

            cursor.execute(
                """INSERT INTO test_runs (
                    test_case_id, status, screenshot, run_at, duration_ms,
                    console_errors, broken_links, missing_alt_images, page_load_time_ms,
                    issues_found, total_page_size_kb, total_requests,
                    seo_issues, security_issues, accessibility_issues, mobile_issues,
                    findings_evidence, health_score
                ) VALUES (%s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    testcase_id, status,
                    first_page.get('screenshot', ''),
                    duration_ms,
                    json.dumps(display_findings['console_errors']),
                    json.dumps(display_findings['broken_links']),
                    json.dumps(display_findings['missing_alt_images']),
                    page_load_ms,
                    issues_found, 0, len(urls_to_test),
                    json.dumps(display_findings['seo_issues']),
                    json.dumps(display_findings['security_issues']),
                    json.dumps(display_findings['accessibility_issues']),
                    json.dumps(display_findings['mobile_issues']),
                    json.dumps(merged_findings),
                    health_score,
                )
            )
            mysql.connection.commit()
            run_id = cursor.lastrowid

            for rec in per_page_records:
                try:
                    cursor.execute(
                        """INSERT INTO crawled_pages (
                            test_run_id, url, page_title, status_code, health_score,
                            issues_found, page_load_time_ms, screenshot, findings_evidence
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (run_id, rec['url'][:2000], rec['page_title'][:500],
                         rec['status_code'], rec['health_score'], rec['issues_found'],
                         rec['page_load_time_ms'], rec['screenshot'][:500],
                         rec['findings_evidence'])
                    )
                except Exception as e:
                    logger.warning("crawled_pages insert error: %s", e)
                    continue

            mysql.connection.commit()

            cursor.execute(
                "UPDATE test_cases SET status = %s WHERE id = %s",
                (status, testcase_id)
            )
            mysql.connection.commit()
            cursor.close()

            return {
                'run_id': run_id,
                'status': status,
                'health_score': health_score,
                'issues_found': issues_found,
                'severity_breakdown': breakdown,
                'duration_ms': duration_ms,
                'page_load_time_ms': page_load_ms,
                'total_page_size_kb': 0,
                'total_requests': len(urls_to_test),
                'screenshot': first_page.get('screenshot', ''),
                'pages_tested': len(urls_to_test),
                'pages_crawled': len(per_page_records),
                'is_multi_page': crawl_pages,
                'session_used': session_used,
                'session_message': session_message,
                'session_warning': session_warning,
                'first_page_error': first_page_error,
                'broken_links': display_findings['broken_links'],
                'console_errors': display_findings['console_errors'],
                'missing_alt_images': display_findings['missing_alt_images'],
                'seo_issues': display_findings['seo_issues'],
                'security_issues': display_findings['security_issues'],
                'accessibility_issues': display_findings['accessibility_issues'],
                'mobile_issues': display_findings['mobile_issues'],
                'interaction_issues': display_findings.get('interaction_issues', []),
                'form_issues': display_findings.get('form_issues', []),
                'visual_issues': display_findings.get('visual_issues', []),
                'content_issues': display_findings.get('content_issues', []),
            }, 200

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        error_msg = str(e)[:500]
        logger.exception("RUNNER ERROR: %s", error_msg)

        try:
            cursor = mysql.connection.cursor()
            cursor.execute(
                """INSERT INTO test_runs (
                    test_case_id, status, run_at, duration_ms, error_message, health_score
                ) VALUES (%s, 'Fail', NOW(), %s, %s, 0)""",
                (testcase_id, duration_ms, error_msg)
            )
            mysql.connection.commit()
            cursor.execute(
                "UPDATE test_cases SET status = 'Fail' WHERE id = %s",
                (testcase_id,)
            )
            mysql.connection.commit()
            cursor.close()
        except Exception:
            pass

        return {'error': error_msg, 'status': 'Fail'}, 500


@runner_bp.route('/runs/<int:testcase_id>', methods=['GET'])
@jwt_required()
def get_runs(testcase_id):
    user_id = int(get_jwt_identity())
    tc = user_owns_testcase(user_id, testcase_id)
    if not tc:
        return jsonify({'error': 'Test case not found'}), 404

    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """SELECT id, status, screenshot, run_at, duration_ms, page_load_time_ms,
                      issues_found, total_page_size_kb, total_requests, health_score,
                      error_message
               FROM test_runs WHERE test_case_id = %s
               ORDER BY run_at DESC LIMIT 20""",
            (testcase_id,)
        )
        runs = cursor.fetchall()
        cursor.close()
        for run in runs:
            if run.get('run_at'):
                run['run_at'] = run['run_at'].isoformat()
        return jsonify(runs), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@runner_bp.route('/run/<int:run_id>/pages', methods=['GET'])
@jwt_required()
def get_crawled_pages(run_id):
    user_id = int(get_jwt_identity())
    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """SELECT tr.id FROM test_runs tr
               JOIN test_cases tc ON tr.test_case_id = tc.id
               JOIN projects p ON tc.project_id = p.id
               WHERE tr.id = %s AND p.user_id = %s""",
            (run_id, user_id)
        )
        if not cursor.fetchone():
            cursor.close()
            return jsonify({'error': 'Run not found'}), 404

        cursor.execute(
            """SELECT id, url, page_title, status_code, health_score,
                      issues_found, page_load_time_ms, screenshot, crawled_at, findings_evidence
               FROM crawled_pages
               WHERE test_run_id = %s
               ORDER BY id ASC""",
            (run_id,)
        )
        pages = cursor.fetchall()
        cursor.close()
        for pg in pages:
            if pg.get('crawled_at'):
                pg['crawled_at'] = pg['crawled_at'].isoformat()
        return jsonify(pages), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500