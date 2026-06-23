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
                  p.has_active_session
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

    PER_REQUEST_TIMEOUT = 3
    LINK_CAP = 30
    POOL_SIZE = 8

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
    session = requests.Session()
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
    try:
        ua = page.evaluate("navigator.userAgent")
        if ua:
            session.headers['User-Agent'] = ua
    except Exception:
        pass

    def _check(url):
        try:
            r = session.head(url, timeout=PER_REQUEST_TIMEOUT, allow_redirects=True)
            # Many servers don't implement HEAD and answer 403/405/501 even when
            # the page is fine — confirm with a lightweight GET before judging.
            if r.status_code in (403, 405, 501):
                r = session.get(url, timeout=PER_REQUEST_TIMEOUT,
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

        # Cookie flags. Session-bearing cookies without HttpOnly are readable by
        # any injected script; non-Secure cookies on HTTPS leak over HTTP.
        if page is not None:
            try:
                cookies = page.context.cookies()
            except Exception:
                cookies = []
            sessiony = ('sess', 'token', 'auth', 'jwt', 'sid', 'remember')
            no_httponly = [c['name'] for c in cookies
                           if not c.get('httpOnly')
                           and any(k in (c.get('name') or '').lower() for k in sessiony)]
            no_secure = [c['name'] for c in cookies
                         if is_https and not c.get('secure')]
            if no_httponly:
                names = ', '.join(sorted(set(no_httponly))[:5])
                findings.append({
                    'issue': f'Session cookie(s) missing HttpOnly flag: {names}',
                    'severity': 'major', 'header': 'cookies',
                    'display': f'Session cookies readable by JavaScript (no HttpOnly): {names}',
                })
            if no_secure:
                names = ', '.join(sorted(set(no_secure))[:5])
                findings.append({
                    'issue': f'Cookie(s) missing Secure flag on HTTPS site: {names}',
                    'severity': 'major', 'header': 'cookies',
                    'display': f'Cookies sent without Secure flag: {names}',
                })

        # CSP that exists but allows inline/eval script gives a false sense of safety.
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
                'issue': f'Missing security headers ({len(missing)}): {header_names}',
                'severity': 'minor', 'header': header_names,
                'missing_headers': missing,
                'display': f'Missing {len(missing)} security hygiene headers',
            })
    except Exception as e:
        logger.warning("security check error: %s", e)
    return findings


# Bundled axe-core (the industry-standard WCAG engine). Injected into each page.
AXE_PATH = os.path.join(os.path.dirname(__file__), 'axe.min.js')


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
        help_text = v.get('help') or v.get('id', 'Accessibility issue')
        count = len(nodes)
        findings.append({
            'issue': f"{help_text} ({impact})",
            'rule': v.get('id'),
            'impact': impact,
            'severity': _impact_to_sev.get(impact, 'minor'),
            'description': v.get('description', ''),
            'help_url': v.get('helpUrl', ''),
            'count': count,
            'element_selector': first_target or 'unknown',
            'element_text': (nodes[0].get('html', '')[:120] if nodes else ''),
            'bounding_box': bbox,
            'display': f"{help_text} — {count} element(s) [{impact}]",
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


def filter_console_errors(console_messages):
    """Group + classify console errors instead of dumping the raw stream.

    Old behaviour: every repeat of the same error became its own finding, and a
    failed analytics pixel counted the same as an uncaught TypeError. Now:
    - identical messages are grouped with a count (30 repeats -> 1 finding x30)
    - severity reflects what the error means:
        uncaught JS exception / CORS failure -> major (something is broken)
        failed resource load (404 img/script) -> minor (sloppy, rarely fatal)
    'mixed content' stays skipped here because check_security now surfaces it.
    """
    skip_patterns = ['unrecognized feature', 'deprecated', 'devtools', 'extension', 'favicon',
                     'webkit', 'preload', 'sourcemap', 'mixed content']
    exception_markers = ('uncaught', 'typeerror', 'referenceerror', 'syntaxerror',
                         'rangeerror', 'is not a function', 'cannot read propert',
                         'is not defined', 'unhandled promise rejection')
    cors_markers = ('cors', 'access-control-allow', 'cross-origin')

    grouped = {}        # normalized text -> finding
    order = []
    for msg in console_messages:
        if msg.get('type') != 'error':
            continue
        text = (msg.get('text') or '').strip()
        text_lower = text.lower()
        if any(pattern in text_lower for pattern in skip_patterns):
            continue

        if any(m in text_lower for m in exception_markers):
            severity, kind = 'major', 'JS exception'
        elif any(m in text_lower for m in cors_markers):
            severity, kind = 'major', 'CORS failure'
        elif 'failed to load resource' in text_lower:
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

    try:
        links = page.query_selector_all('a[href]')
        for link in links:
            try:
                href = link.get_attribute('href')
                if not href:
                    continue
                href = href.strip()
                if href.startswith(('#', 'javascript:', 'mailto:', 'tel:', 'sms:')):
                    continue
                if href.startswith('/'):
                    full_url = f"{base_parsed.scheme}://{base_domain}{href}"
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
    except Exception as e:
        logger.warning("discover_internal_links error: %s", e)

    return list(discovered)


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
    real_errors = filter_console_errors(console_messages)
    console_errors_rich = [
        {**msg, 'element_selector': 'console', 'parent_context': ''}
        for msg in real_errors
    ]
    return {
        'broken_links': broken_links,
        'console_errors': console_errors_rich,
        'missing_alt_images': missing_alt,
        'seo_issues': seo_issues,
        'security_issues': security_issues,
        'accessibility_issues': accessibility_issues,
        'mobile_issues': mobile_issues,
    }


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
    """Queue the run on a background worker and return a job id immediately."""
    user_id = int(get_jwt_identity())
    tc = user_owns_testcase(user_id, testcase_id)
    if not tc:
        return jsonify({'error': 'Test case not found'}), 404
    if tc['test_type'] != 'automated':
        return jsonify({'error': 'Only automated test cases can be run'}), 400

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
    findings_evidence = json.dumps({
        'manual': True,
        'note': note,
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
                0, len(pages),
                findings_evidence,
                100 if outcome == 'Pass' else 0,
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
                    payload = _json.dumps(ss_data)
                    context.add_init_script(
                        f"(() => {{ try {{ const d = {payload}; "
                        "for (const k in d) sessionStorage.setItem(k, d[k]); "
                        "} catch(e) {} }})();"
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
                    if landed and landed not in queued and not looks_like_login:
                        # Front of the queue so the crawl starts from the authenticated
                        # landing page, not the configured /user-login URL.
                        to_visit.insert(0, landed)
                        queued.add(landed)
                        logger.info("auth warmup: authenticated landing = %s", landed)
                    else:
                        logger.warning("auth warmup: landed on '%s' (looks like login) — session may not be valid", landed)
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