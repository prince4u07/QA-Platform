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
import math
import threading
import requests

from modules.runner.jobs import job_manager
from modules.runner import manual_run
from modules.runner.steps import run_steps, summarise_steps

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
                  tc.steps, tc.expected_result,
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

def resolve_link_url(href, base_url):
    """
    Absolute URL for one href, or None when it does not address a page.

    Relative hrefs (about.html, ../contact) used to be dropped before they
    were even counted, so a broken relative link could never be found while
    the coverage note still claimed every link had been checked. The fragment
    is stripped because #section addresses the same document.
    """
    if not href:
        return None
    href = href.strip()
    if not href or href.startswith(('#', 'javascript:', 'mailto:', 'tel:', 'data:')):
        return None
    try:
        resolved = urljoin(base_url, href)
    except Exception:
        return None
    if not resolved.startswith(('http://', 'https://')):
        return None
    return resolved.split('#', 1)[0]


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
    RETRY_TIMEOUT = 8
    LINK_CAP = 30
    POOL_SIZE = 8

    findings = []
    candidates = []     # (full_url, link_handle)
    coverage = {'links_found': 0, 'links_checked': 0, 'truncated': False}

    try:
        links = page.query_selector_all('a[href]')
        seen_urls = set()
        for link in links:
            try:
                full_url = resolve_link_url(link.get_attribute('href'), base_url)
                if not full_url or full_url in seen_urls:
                    continue
                seen_urls.add(full_url)
                candidates.append((full_url, link))
            except Exception:
                continue
    except Exception as e:
        logger.warning("broken_links: link enumeration error: %s", e)
        return findings, coverage

    # Count everything we found, then check only the first LINK_CAP of them.
    # The caller reports the difference so "0 broken links" can never be
    # mistaken for "all links are fine" when we only looked at some.
    coverage['links_found'] = len(candidates)
    if len(candidates) > LINK_CAP:
        candidates = candidates[:LINK_CAP]
        coverage['truncated'] = True
    coverage['links_checked'] = len(candidates)

    # Send the crawl browser's cookies + User-Agent with every check so links
    # behind a login are tested AS THE LOGGED-IN USER. Plain requests.head() has
    # no session, so on authenticated pages every protected link came back
    # 302/401/403 and got wrongly flagged "broken". (cookies()/evaluate() run on
    # this crawl thread where `page` lives; the pool threads below only touch the
    # requests Session, never a Playwright object.)
    cookie_specs = []
    try:
        for c in page.context.cookies():
            cookie_specs.append((
                c.get('name'), c.get('value'),
                (c.get('domain') or '').lstrip('.'), c.get('path') or '/',
            ))
    except Exception:
        pass
    try:
        user_agent = page.evaluate("navigator.userAgent")
    except Exception:
        user_agent = None

    # requests.Session is not documented as thread-safe and this pool runs 8
    # workers, so each thread builds its own session from the same browser
    # cookies rather than all of them sharing one object.
    thread_state = threading.local()

    def session_for_thread():
        existing = getattr(thread_state, 'session', None)
        if existing is not None:
            return existing
        s = requests.Session()
        for name, value, domain, path in cookie_specs:
            try:
                s.cookies.set(name, value, domain=domain, path=path)
            except Exception:
                continue
        if user_agent:
            s.headers['User-Agent'] = user_agent
        thread_state.session = s
        return s

    def _attempt(url, timeout):
        session = session_for_thread()
        r = session.head(url, timeout=timeout, allow_redirects=True)
        # Many servers don't implement HEAD and answer 403/405/501 even when
        # the page is fine, so confirm with a lightweight GET before judging.
        if r.status_code in (403, 405, 501):
            r = session.get(url, timeout=timeout, allow_redirects=True, stream=True)
            r.close()
        return r.status_code

    def _check(url):
        # A 3 second timeout is not proof a link is dead, it is usually just a
        # slow server. One slower retry before calling it unreachable removes
        # that noise while still catching a genuinely dead host.
        try:
            return _attempt(url, PER_REQUEST_TIMEOUT)
        except requests.RequestException:
            pass
        try:
            return _attempt(url, RETRY_TIMEOUT)
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

    def _severity(url, status):
        # A dead link on your own site is worse than a dead link pointing out.
        # A 5xx means the target is erroring, which is worse still.
        if status >= 500:
            return 'critical'
        try:
            internal = urlparse(url).netloc == urlparse(base_url).netloc
        except Exception:
            internal = False
        if status == 0:
            return 'moderate'          # unreachable: often a timeout, not proof of a fault
        return 'serious' if internal else 'moderate'

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
        return findings, coverage

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
                'severity': _severity(full_url, status),
                'element_text': link_text,
                'element_selector': _safe_get_selector(link),
                'parent_context': _safe_get_parent_text(link),
                'bounding_box': _safe_get_bounding_box(link),
                'display': f"{full_url} ({status if status > 0 else 'unreachable'})",
            })
        except Exception:
            continue

    return findings, coverage


# Below this an image is a spacer, tracking pixel or tiny icon, where asking
# for alt text is noise. At or above it the image carries meaning a screen
# reader user would otherwise lose.
ALT_MIN_SIZE_PX = 32


def check_missing_alt(page):
    findings = []
    try:
        images = page.query_selector_all('img')
        for img in images:
            try:
                alt = img.get_attribute('alt')
                src = img.get_attribute('src') or '(no src)'
                # Any alt attribute means the author made a decision, and
                # alt="" is the correct way to mark an image as decorative.
                # Only a missing attribute is a defect.
                if alt is not None:
                    continue
                if (img.get_attribute('role') or '') in ('presentation', 'none'):
                    continue
                if (img.get_attribute('aria-hidden') or '') == 'true':
                    continue
                box = img.bounding_box()
                if (not box or box['width'] < ALT_MIN_SIZE_PX
                        or box['height'] < ALT_MIN_SIZE_PX):
                    continue
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
    findings = []
    try:
        title = page.title()
        if not title or len(title.strip()) == 0:
            findings.append({
                'issue': 'Page has no title',
                'severity': 'moderate',
                'element_selector': 'head > title',
                'display': 'Page has no title',
            })

        meta = page.query_selector('meta[name="description"]')
        description = (meta.get_attribute('content') or '').strip() if meta else ''
        if not description:
            findings.append({
                'issue': 'Page has no meta description',
                'severity': 'moderate',
                'element_selector': 'head > meta[name="description"]',
                'display': 'Page has no meta description',
            })

        # Every page needs one top-level heading: it is how search engines and
        # screen reader users work out what the page is actually about.
        if not page.query_selector('h1'):
            findings.append({
                'issue': 'Page has no H1 heading',
                'severity': 'moderate',
                'element_selector': 'h1',
                'display': 'Page has no H1 heading',
            })
    except Exception as e:
        logger.warning("seo check error: %s", e)
    return findings


def check_security(page_url, response_headers):
    findings = []
    try:
        is_https = page_url.startswith('https://')
        if not is_https:
            findings.append({
                'issue': 'Site is using insecure HTTP instead of HTTPS',
                'severity': 'critical',
                'header': 'protocol',
                'display': 'Site uses insecure HTTP instead of HTTPS',
            })
        headers_lower = {k.lower(): v for k, v in response_headers.items()}
        csp = (headers_lower.get('content-security-policy') or '').lower()
        missing = []
        if 'content-security-policy' not in headers_lower:
            missing.append({'header': 'Content-Security-Policy',
                            'purpose': 'Protects against XSS'})
        # CSP frame-ancestors is the modern replacement for X-Frame-Options.
        # A site using it is protected and must not be told otherwise.
        if 'x-frame-options' not in headers_lower and 'frame-ancestors' not in csp:
            missing.append({'header': 'X-Frame-Options',
                            'purpose': 'Prevents clickjacking'})
        # HSTS only means anything over HTTPS. On an HTTP page the protocol is
        # already reported above, and charging twice for one root cause drags
        # the score down for a single fault.
        if is_https and 'strict-transport-security' not in headers_lower:
            missing.append({'header': 'HSTS', 'purpose': 'Forces HTTPS'})
        if missing:
            header_names = ', '.join(m['header'] for m in missing)
            findings.append({
                'issue': f'Missing security headers ({len(missing)}): {header_names}',
                'severity': 'serious',
                'header': header_names,
                'missing_headers': missing,
                'display': f'Missing {len(missing)} critical security headers',
            })
    except Exception as e:
        logger.warning("security check error: %s", e)
    return findings


# Bundled axe-core (the industry-standard WCAG engine). Injected into each page.
AXE_PATH = os.path.join(os.path.dirname(__file__), 'axe.min.js')

# Rules that already have a dedicated check and a weighted category of their
# own. Reporting them here as well charged one defect twice over.
AXE_RULES_COVERED_ELSEWHERE = {'image-alt'}


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
    for v in results or []:
        if v.get('id') in AXE_RULES_COVERED_ELSEWHERE:
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

        # axe grades every rule as critical/serious/moderate/minor, which is
        # exactly our own scale, so use it directly rather than treating all
        # accessibility problems as equally bad.
        impact = v.get('impact') or 'minor'
        severity = impact if impact in SEVERITY_PENALTY else 'moderate'
        help_text = v.get('help') or v.get('id', 'Accessibility issue')
        count = len(nodes)
        findings.append({
            'issue': f"{help_text} ({impact})",
            'rule': v.get('id'),
            'impact': impact,
            'severity': severity,
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
    """
    WCAG audit via axe-core, with a lightweight heuristic fallback.
    Returns (findings, engine) so the report can say which one ran — the
    fallback catches far less, and the user deserves to know when it was used.
    """
    axe_findings = check_accessibility_axe(page)
    if axe_findings is not None:
        return axe_findings, 'axe-core'
    return check_accessibility_heuristic(page), 'heuristic-fallback'


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
                        'severity': 'serious',
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
                aria_labelledby = inp.get_attribute('aria-labelledby')
                has_label = False
                if inp_id:
                    label = page.query_selector(f'label[for="{inp_id}"]')
                    if label:
                        has_label = True
                if not has_label:
                    # A label wrapping its input needs no for attribute.
                    try:
                        has_label = bool(inp.evaluate("el => !!el.closest('label')"))
                    except Exception:
                        has_label = False
                # A placeholder is deliberately not accepted here: it vanishes
                # as soon as the user types and is not an accessible name, so
                # treating it as a label hid genuinely unlabelled fields.
                if not has_label and not aria_label and not aria_labelledby:
                    input_name = inp.get_attribute('name') or 'unnamed'
                    findings.append({
                        'issue': f'Input field "{input_name}" has no label',
                        'severity': 'serious',
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


TAP_TARGET_CAP = 50


def _is_inline_text_link(element):
    """
    True for a link sitting inside a run of text. WCAG exempts these from
    target-size rules because an inline link cannot be padded to 44px without
    breaking the sentence around it. A link styled as a button reports
    inline-block or block and is still measured.
    """
    try:
        if (element.evaluate("el => el.nodeName") or '').lower() != 'a':
            return False
        return (element.evaluate("el => getComputedStyle(el).display") or '') == 'inline'
    except Exception:
        return False


def check_mobile(page):
    findings = []
    coverage = {'tap_targets_found': 0, 'tap_targets_checked': 0, 'truncated': False}
    try:
        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        client_width = page.evaluate("document.documentElement.clientWidth")
        if scroll_width > client_width + 5:
            findings.append({
                'issue': f'Page has horizontal scrolling on mobile ({scroll_width}px vs {client_width}px)',
                # Stable identity: the sentence carries per-page pixel widths.
                'issue_type': 'horizontal_scroll',
                'severity': 'serious',
                'element_selector': 'body',
                'display': 'Page requires horizontal scrolling on mobile',
            })
        clickables = page.query_selector_all('a, button, input[type="submit"]')
        coverage['tap_targets_found'] = len(clickables)
        if len(clickables) > TAP_TARGET_CAP:
            coverage['truncated'] = True
        clickables = clickables[:TAP_TARGET_CAP]
        coverage['tap_targets_checked'] = len(clickables)
        small_targets = []
        for el in clickables:
            try:
                if _is_inline_text_link(el):
                    continue
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
                # Stable identity: the sentence carries a per-page count.
                'issue_type': 'tap_targets_too_small',
                # A couple of small controls is a nuisance; a page full of them
                # means mobile users cannot reliably use it at all.
                'severity': 'serious' if count > 10 else 'moderate',
                'count': count,
                'examples': small_targets[:10],
                'element_selector': first_target['selector'],
                'element_text': first_target['text'],
                'bounding_box': first_target['bounding_box'],
                'display': f'{count} tap targets too small for mobile users',
            })
    except Exception as e:
        logger.warning("mobile check error: %s", e)
    return findings, coverage


# Page weight and load-time thresholds. Chosen to match what users actually
# feel: about 3s is the point people start abandoning a page, and 3MB is
# roughly 20s of loading on a slow mobile connection.
SLOW_PAGE_MS = 3000
VERY_SLOW_PAGE_MS = 6000
HEAVY_PAGE_KB = 3000
VERY_HEAVY_PAGE_KB = 6000
HEAVY_RESOURCE_KB = 1000


def check_performance(page, page_load_ms):
    """
    Measure what the page actually cost to load and flag the bottlenecks.

    Returns (findings, metrics). `metrics` carries the real page weight, which
    used to be reported as a hardcoded 0 on every single run.
    """
    findings = []
    metrics = {'page_size_kb': 0, 'request_count': 0, 'heaviest': [],
               'unmeasured_resources': 0}

    try:
        data = page.evaluate("""() => {
            const res = performance.getEntriesByType('resource') || [];
            const nav = (performance.getEntriesByType('navigation') || [])[0];
            let bytes = nav && nav.transferSize ? nav.transferSize : 0;
            const items = [];
            let unmeasured = 0;
            for (const r of res) {
                // transferSize is 0 for cross-origin resources without
                // Timing-Allow-Origin and for anything served from cache.
                // decodedBodySize is the next best estimate; when both are
                // zero the resource cannot be measured from here at all.
                let size = r.transferSize || 0;
                if (!size) {
                    size = r.decodedBodySize || 0;
                    if (!size) unmeasured++;
                }
                bytes += size;
                items.push({
                    name: r.name,
                    size_kb: Math.round(size / 1024),
                    duration_ms: Math.round(r.duration || 0),
                    kind: r.initiatorType || 'other',
                });
            }
            items.sort((a, b) => b.size_kb - a.size_kb);
            return { bytes: bytes, count: res.length, top: items.slice(0, 5),
                     unmeasured: unmeasured };
        }""") or {}
    except Exception as e:
        logger.warning("performance check error: %s", e)
        return findings, metrics

    page_size_kb = int((data.get('bytes') or 0) / 1024)
    metrics['page_size_kb'] = page_size_kb
    metrics['request_count'] = data.get('count') or 0
    metrics['heaviest'] = data.get('top') or []
    metrics['unmeasured_resources'] = data.get('unmeasured') or 0

    if page_load_ms and page_load_ms >= SLOW_PAGE_MS:
        severity = 'serious' if page_load_ms >= VERY_SLOW_PAGE_MS else 'moderate'
        secs = round(page_load_ms / 1000, 1)
        findings.append({
            'issue': f'Page takes {secs}s to load',
            'severity': severity,
            'metric': 'load_time_ms',
            'value': page_load_ms,
            'threshold': SLOW_PAGE_MS,
            'element_selector': 'document',
            'display': f'Slow page load: {secs}s (target is under {SLOW_PAGE_MS / 1000:.0f}s)',
        })

    if page_size_kb >= HEAVY_PAGE_KB:
        severity = 'serious' if page_size_kb >= VERY_HEAVY_PAGE_KB else 'moderate'
        findings.append({
            'issue': f'Page weighs {page_size_kb} KB',
            'severity': severity,
            'metric': 'page_size_kb',
            'value': page_size_kb,
            'threshold': HEAVY_PAGE_KB,
            'element_selector': 'document',
            'display': f'Heavy page: {page_size_kb} KB downloaded '
                       f'({metrics["request_count"]} requests)',
        })

    for item in metrics['heaviest']:
        if item.get('size_kb', 0) < HEAVY_RESOURCE_KB:
            break                      # list is sorted, so nothing below is big enough
        name = (item.get('name') or '')
        short = name.rsplit('/', 1)[-1][:80] or name[:80]
        findings.append({
            'issue': f'Single file is {item["size_kb"]} KB: {short}',
            'severity': 'moderate',
            'metric': 'resource_size_kb',
            'value': item['size_kb'],
            'resource': name[:500],
            'element_selector': 'document',
            'display': f'Large {item.get("kind", "file")}: {short} ({item["size_kb"]} KB)',
        })

    return findings, metrics


def filter_console_errors(console_messages):
    filtered = []
    # Deliberately NOT skipped: mixed content is a real security defect, and
    # the console is the one place it shows up. 'extension' is matched only as
    # a browser-extension URL scheme, because as a bare word it also swallowed
    # genuine application errors such as "file extension not supported".
    skip_patterns = ['unrecognized feature', 'deprecated', 'devtools', 'favicon',
                     'chrome-extension://', 'moz-extension://', 'safari-extension://',
                     'webkit', 'preload', 'sourcemap']
    for msg in console_messages:
        if msg.get('type') != 'error':
            continue
        text_lower = (msg.get('text') or '').lower()
        if any(pattern in text_lower for pattern in skip_patterns):
            continue
        filtered.append(msg)
    return filtered


# ============================================================
# SEVERITY AND HEALTH SCORE
# ============================================================

# How much one finding of each severity costs. Every check tags its own
# findings with a severity; these are the only four values used anywhere.
SEVERITY_PENALTY = {'critical': 10, 'serious': 5, 'moderate': 2, 'minor': 1}
SEVERITY_ORDER = ['critical', 'serious', 'moderate', 'minor']

# Used when a finding somehow arrives without its own severity, so an
# untagged finding still costs something rather than being free.
DEFAULT_SEVERITY = {
    'functional_issues': 'critical',
    'security_issues': 'serious',
    'broken_links': 'serious',
    'console_errors': 'serious',
    'performance_issues': 'moderate',
    'accessibility_issues': 'moderate',
    'mobile_issues': 'moderate',
    'seo_issues': 'moderate',
    'missing_alt_images': 'minor',
}

# How much each category contributes to the overall score. Sums to 100.
# Functional failures carry the most weight by a wide margin: a beautiful,
# accessible, fast page that does not actually work is a failed product.
CATEGORY_WEIGHT = {
    'functional_issues': 25,
    'security_issues': 16,
    'accessibility_issues': 14,
    'broken_links': 13,
    'console_errors': 11,
    'performance_issues': 9,
    'mobile_issues': 6,
    'seo_issues': 4,
    'missing_alt_images': 2,
}

# Controls how fast a category's subscore falls as penalties pile up.
# Larger = more forgiving. At penalty == SCALE the subscore is exactly 50.
SCORE_SCALE = 20


def finding_severity(category, finding):
    """The severity a check assigned, falling back to the category default."""
    if isinstance(finding, dict):
        sev = finding.get('severity')
        if sev in SEVERITY_PENALTY:
            return sev
    return DEFAULT_SEVERITY.get(category, 'moderate')


def finding_weight(finding):
    """
    How many times over one finding counts.

    axe reports a single finding per rule together with the number of elements
    breaking it, so a rule broken by 200 elements has to cost more than one
    broken by a single element. Growth is logarithmic, matching the curve used
    everywhere else here, so one noisy rule can never swamp the whole score.
    """
    count = 1
    if isinstance(finding, dict):
        raw = finding.get('count')
        if isinstance(raw, int) and raw > 1:
            count = raw
    return 1 + math.log10(count)


def category_penalty(category, items):
    """Total severity cost of one category's findings."""
    return sum(
        SEVERITY_PENALTY[finding_severity(category, f)] * finding_weight(f)
        for f in items or []
    )


def category_subscore(category, items):
    """
    Score out of 100 for a single category, using diminishing returns:
    100 * SCALE / (penalty + SCALE).

    The old score was 100 - count * weight, which hit zero on any real site
    and made a page with 20 problems look identical to one with 200. This
    curve keeps the first few findings expensive, never reaches zero, and
    stays strictly decreasing, so two runs can always be compared.
    """
    penalty = category_penalty(category, items)
    return int(round(100 * SCORE_SCALE / (penalty + SCORE_SCALE)))


def score_breakdown(findings_by_category):
    """
    Per-category detail behind the overall score, so the UI can explain
    where the marks were lost instead of showing one unexplained number.
    """
    breakdown = {}
    for category, weight in CATEGORY_WEIGHT.items():
        items = findings_by_category.get(category) or []
        counts = {sev: 0 for sev in SEVERITY_ORDER}
        for f in items:
            counts[finding_severity(category, f)] += 1
        breakdown[category] = {
            'score': category_subscore(category, items),
            'weight': weight,
            'issues': len(items),
            'by_severity': counts,
        }
    return breakdown


def calculate_health_score(findings_by_category):
    """Weighted average of the per-category subscores. Always 1..100."""
    total_weight = 0
    weighted_sum = 0
    for category, weight in CATEGORY_WEIGHT.items():
        items = findings_by_category.get(category) or []
        weighted_sum += weight * category_subscore(category, items)
        total_weight += weight
    if not total_weight:
        return 100
    return max(1, min(100, int(round(weighted_sum / total_weight))))


def summarise_coverage(all_pages_coverage):
    """
    Roll the per-page coverage records into plain sentences for the report.

    Without this, a capped check looks identical to a clean one: "0 broken
    links" reads as "every link works" even when only 30 of 210 were tried.
    """
    links_found = links_checked = 0
    taps_found = taps_checked = 0
    links_truncated = taps_truncated = False
    mobile_skipped_reason = None
    fallback_pages = 0
    page_size_total = 0
    page_size_max = 0
    request_total = 0
    unmeasured_total = 0

    for cov in all_pages_coverage or []:
        perf = cov.get('performance') or {}
        size = perf.get('page_size_kb', 0)
        page_size_total += size
        page_size_max = max(page_size_max, size)
        request_total += perf.get('request_count', 0)
        unmeasured_total += perf.get('unmeasured_resources', 0)

        link = cov.get('links') or {}
        links_found += link.get('links_found', 0)
        links_checked += link.get('links_checked', 0)
        links_truncated = links_truncated or link.get('truncated', False)

        mob = cov.get('mobile') or {}
        taps_found += mob.get('tap_targets_found', 0)
        taps_checked += mob.get('tap_targets_checked', 0)
        taps_truncated = taps_truncated or mob.get('truncated', False)

        if cov.get('mobile_skipped_reason'):
            mobile_skipped_reason = cov['mobile_skipped_reason']
        if cov.get('accessibility_engine') == 'heuristic-fallback':
            fallback_pages += 1

    notes = []
    if links_truncated:
        notes.append(
            f'Only {links_checked} of {links_found} links were tested. '
            f'A clean link result does not mean every link on the site works.'
        )
    if taps_truncated:
        notes.append(
            f'Only {taps_checked} of {taps_found} tap targets were measured for mobile size.'
        )
    if mobile_skipped_reason:
        notes.append(f'Mobile checks were skipped: {mobile_skipped_reason}.')
    if fallback_pages:
        notes.append(
            f'axe-core could not run on {fallback_pages} page(s), so a much more '
            f'limited accessibility check was used there. Real accessibility '
            f'problems may have been missed.'
        )
    if unmeasured_total:
        notes.append(
            f'{unmeasured_total} resource(s) would not report their size, which '
            f'is normal for files served from a CDN. The real page weight is '
            f'higher than the figure shown.'
        )

    return {
        'notes': notes,
        'complete': not notes,
        'links_found': links_found,
        'links_checked': links_checked,
        'tap_targets_found': taps_found,
        'tap_targets_checked': taps_checked,
        'mobile_skipped': bool(mobile_skipped_reason),
        'accessibility_fallback_pages': fallback_pages,
        # Real measured page weight. This used to be reported as 0 on every run.
        'page_size_kb_total': page_size_total,
        'page_size_kb_heaviest': page_size_max,
        'request_count': request_total,
        'unmeasured_resources': unmeasured_total,
    }


def worst_severity(findings_by_category):
    """Most serious severity present across all findings, or None if clean."""
    for sev in SEVERITY_ORDER:
        for category, items in (findings_by_category or {}).items():
            for f in items or []:
                if finding_severity(category, f) == sev:
                    return sev
    return None


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


# How long to wait for a page to stop changing, and how long it must hold
# still to count as settled. Configurable because a slow server needs longer
# and a test against a local fixture needs neither.
SETTLE_TIMEOUT_MS = int(os.getenv('QA_SETTLE_TIMEOUT_MS', '3000'))
SETTLE_QUIET_MS = int(os.getenv('QA_SETTLE_QUIET_MS', '250'))
_SETTLE_POLL_SECS = 0.05


def wait_for_page_settled(page, timeout_ms=None, quiet_ms=None):
    """
    Wait until a page stops changing, instead of sleeping a fixed guess.

    Single-page apps keep mutating after 'networkidle' fires: they hydrate,
    resolve a redirect, then inject the navigation we want to discover. The
    old code slept a flat 1.5s and hoped. That is the classic flaky test:
    too short on a loaded machine and it fails for no real reason, too long
    and every page pays for the worst case.

    This samples the URL and the DOM node count. When both hold steady for
    `quiet_ms` it returns immediately, so a fast page costs ~300ms instead of
    1500ms, and a slow one gets the full timeout instead of being cut off.

    Returns True if the page settled, False if it was still moving at the
    deadline. Never raises: a page that cannot be inspected is not an error
    here, it just means we stop waiting.
    """
    timeout_ms = SETTLE_TIMEOUT_MS if timeout_ms is None else timeout_ms
    quiet_ms = SETTLE_QUIET_MS if quiet_ms is None else quiet_ms

    deadline = time.time() + (timeout_ms / 1000.0)
    signature = None
    steady_since = None

    while time.time() < deadline:
        try:
            current = page.evaluate(
                "() => location.href + '|' + document.querySelectorAll('*').length"
            )
        except Exception:
            return False        # page closed or navigating; nothing to wait on

        now = time.time()
        if current == signature:
            if steady_since is not None and (now - steady_since) * 1000 >= quiet_ms:
                return True
        else:
            signature = current
            steady_since = now

        time.sleep(_SETTLE_POLL_SECS)

    return False


def looks_like_login(url):
    """
    Does this URL belong to an unauthenticated login or auth screen?

    Matches on the path only. Checking the whole URL would discard perfectly
    good targets whose domain happens to contain a keyword, such as
    authenticnews.com or loginworks.io.
    """
    try:
        path = (urlparse(url).path or '').lower()
    except Exception:
        return False
    if not path or path == '/':
        return False
    return any(kw in path for kw in (
        'login', 'signin', 'sign-in', 'sign_in',
        '/auth', 'register', 'forgot', 'password',
    ))


def build_crawl_queue(base_url, landing_url=None, session_used=False):
    """
    Decide which URLs a crawl starts from, in order.

    Anonymous crawls start at the configured URL and also seed the site root,
    because a sparse entry page would otherwise dead-end the crawl.

    Authenticated crawls are different. The user captured a session so that
    the pages *behind* the login get tested, and the configured base_url is
    very often the login screen itself. Once we know where the session
    actually lands, login screens are dropped: auditing one spends the page
    budget on a public page nobody asked about and fills the report with its
    findings. The login page is only kept when dropping it would leave the
    crawl with nowhere to start.
    """
    def _clean(url):
        return (url or '').rstrip('/').split('#')[0]

    base_clean = _clean(base_url)
    ordered = []

    def _add(url, front=False):
        if not url or url in ordered:
            return
        if front:
            ordered.insert(0, url)
        else:
            ordered.append(url)

    _add(base_clean)

    # The site root usually serves the dashboard once authenticated, and that
    # is where the navigation lives.
    origin_root = None
    parsed = urlparse(base_clean)
    if parsed.scheme and parsed.netloc:
        origin_root = f"{parsed.scheme}://{parsed.netloc}"
        _add(origin_root)

    if not session_used:
        return ordered

    # Logged in: start from where the session actually landed.
    landing_clean = _clean(landing_url)
    if landing_clean:
        _add(landing_clean, front=True)
        survivors = [u for u in ordered if not looks_like_login(u)]
        # Only drop the login pages if something is left to crawl.
        if survivors:
            ordered = survivors

    return ordered


def test_single_page(page, page_url, response_headers, console_messages,
                     is_mobile=True, page_load_ms=0):
    """
    Run every check against one loaded page.

    Returns (findings_by_category, coverage). `coverage` records what was
    capped or skipped so the report can say so out loud instead of letting
    a partial check look like a clean bill of health, and carries the real
    performance metrics for this page.
    """
    broken_links, link_coverage = check_broken_links(page, page_url)
    missing_alt = check_missing_alt(page)
    seo_issues = check_seo(page, page_url)
    security_issues = check_security(page_url, response_headers)
    accessibility_issues, a11y_engine = check_accessibility(page)

    # Mobile checks (tap-target size, horizontal scroll) only make sense in the
    # mobile viewport. Authenticated crawls run in a DESKTOP context (to keep the
    # session valid), where these would flag every normal desktop control as a
    # "too small tap target" — pure noise. Skip them off mobile.
    if is_mobile:
        mobile_issues, mobile_coverage = check_mobile(page)
        mobile_skipped_reason = None
    else:
        mobile_issues, mobile_coverage = [], {}
        mobile_skipped_reason = ('logged-in crawls run on a desktop viewport, '
                                 'so mobile checks did not run for this page')

    performance_issues, perf_metrics = check_performance(page, page_load_ms)

    real_errors = filter_console_errors(console_messages)
    console_errors_rich = [
        # A JavaScript error means something on the page is actually broken.
        {**msg, 'severity': 'serious', 'element_selector': 'console', 'parent_context': ''}
        for msg in real_errors
    ]

    findings = {
        # Populated by the caller from the test case's executed steps; a page
        # audit on its own cannot tell whether the feature actually works.
        'functional_issues': [],
        'broken_links': broken_links,
        'console_errors': console_errors_rich,
        'missing_alt_images': missing_alt,
        'seo_issues': seo_issues,
        'security_issues': security_issues,
        'accessibility_issues': accessibility_issues,
        'mobile_issues': mobile_issues,
        'performance_issues': performance_issues,
    }
    coverage = {
        'links': link_coverage,
        'mobile': mobile_coverage,
        'mobile_skipped_reason': mobile_skipped_reason,
        'accessibility_engine': a11y_engine,
        'performance': perf_metrics,
    }
    return findings, coverage


def tag_findings_with_page(findings_dict, page_url):
    for category, items in findings_dict.items():
        for item in items:
            if isinstance(item, dict):
                item['page_url'] = page_url
    return findings_dict


# A stable identity per finding, used both to de-duplicate the same defect
# across pages and to tell one run's findings from the previous run's.
# It must not include anything volatile (screenshots, timings, page order)
# or the same defect would look new on every run.
FINDING_KEYERS = {
    'functional_issues': lambda i: i.get('step'),
    'broken_links': lambda i: i.get('url'),
    'console_errors': lambda i: i.get('text'),
    'missing_alt_images': lambda i: i.get('src'),
    'seo_issues': lambda i: i.get('issue'),
    'security_issues': lambda i: i.get('issue'),
    # The rule is the defect. axe names whichever element happened to be first
    # on that page, which differs page to page and split one site-wide rule
    # breach into one finding per page.
    'accessibility_issues': lambda i: i.get('rule') or i.get('element_selector'),
    # issue_type is stable; the issue sentence embeds per-page counts and pixel
    # widths, so keying on it made every page a brand new finding.
    'mobile_issues': lambda i: i.get('issue_type') or i.get('issue'),
    'performance_issues': lambda i: i.get('issue'),
}


def finding_key(category, finding):
    """Stable identity for one finding, or None if it cannot be identified."""
    keyer = FINDING_KEYERS.get(category)
    if not keyer or not isinstance(finding, dict):
        return None
    try:
        return keyer(finding)
    except Exception:
        return None


def diff_findings(previous, current):
    """
    Compare this run's findings against the previous run of the same test case.

    Returns what a regression report needs: which defects are newly introduced,
    which have been fixed since last time, and how many are still outstanding.
    `previous` of None means this is the first run, so nothing is 'new' — a
    first run is a baseline, not a regression.
    """
    if previous is None:
        return {
            'has_baseline': False,
            'new': {}, 'fixed': {},
            'new_count': 0, 'fixed_count': 0, 'still_open_count': 0,
        }

    new_by_cat, fixed_by_cat = {}, {}
    new_count = fixed_count = still_open = 0

    categories = set(current or {}) | set(previous or {})
    for category in categories:
        cur_items = (current or {}).get(category) or []
        prev_items = (previous or {}).get(category) or []

        prev_keys = {finding_key(category, f) for f in prev_items}
        prev_keys.discard(None)
        cur_keys = {finding_key(category, f) for f in cur_items}
        cur_keys.discard(None)

        fresh = [f for f in cur_items
                 if finding_key(category, f) is not None
                 and finding_key(category, f) not in prev_keys]
        gone = [f for f in prev_items
                if finding_key(category, f) is not None
                and finding_key(category, f) not in cur_keys]

        if fresh:
            new_by_cat[category] = fresh
            new_count += len(fresh)
        if gone:
            fixed_by_cat[category] = gone
            fixed_count += len(gone)
        still_open += len(cur_keys & prev_keys)

    return {
        'has_baseline': True,
        'new': new_by_cat,
        'fixed': fixed_by_cat,
        'new_count': new_count,
        'fixed_count': fixed_count,
        'still_open_count': still_open,
    }


def build_regression(previous_findings, current_findings, previous_score, current_score):
    """
    The full regression summary for one run, including a plain-English verdict
    so the user does not have to interpret the numbers themselves.
    """
    diff = diff_findings(previous_findings, current_findings)
    diff['previous_score'] = previous_score
    diff['current_score'] = current_score
    diff['score_change'] = (
        None if previous_score is None else current_score - previous_score
    )

    if not diff['has_baseline']:
        diff['verdict'] = 'baseline'
        diff['summary'] = 'First run for this test case. Future runs will be compared against it.'
        return diff

    if diff['new_count'] and diff['fixed_count']:
        diff['verdict'] = 'mixed'
        diff['summary'] = (f"{diff['fixed_count']} issue(s) fixed, but "
                           f"{diff['new_count']} new one(s) appeared since the last run.")
    elif diff['new_count']:
        diff['verdict'] = 'regressed'
        diff['summary'] = (f"{diff['new_count']} new issue(s) since the last run. "
                           f"Nothing was fixed.")
    elif diff['fixed_count']:
        diff['verdict'] = 'improved'
        diff['summary'] = (f"{diff['fixed_count']} issue(s) fixed and no new ones. ")
    else:
        diff['verdict'] = 'unchanged'
        diff['summary'] = 'No change since the last run.'
    return diff


def merge_findings(all_pages_findings):
    """Combine per-page findings into one summary, de-duplicating issues that
    repeat across pages. The SAME footer broken-link, missing security header, or
    a11y rule appears on every page of a site; counting it once per page inflated
    the issue total and wrongly tanked the health score. Per-page detail is still
    preserved in the crawled_pages records — this only affects the summary.
    """
    merged = {category: [] for category in FINDING_KEYERS}
    seen = {cat: set() for cat in merged}
    for page_findings in all_pages_findings:
        for category, items in page_findings.items():
            if category not in merged:
                continue
            for item in items:
                key = finding_key(category, item)
                if key is not None:
                    if key in seen[category]:
                        continue
                    seen[category].add(key)
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
        # The written steps become the tester's checklist, and the expected
        # result becomes the question they answer at the end.
        steps_text=tc.get('steps') or '',
        expected_result=tc.get('expected_result') or '',
    )
    if not ok:
        return jsonify({'error': message}), 500
    return jsonify({'message': message, 'run': snap}), 200


@runner_bp.route('/manual/<int:testcase_id>/step/<int:index>', methods=['POST'])
@jwt_required()
def manual_mark_step(testcase_id, index):
    """Tick one checklist step as passed, failed or skipped while testing."""
    user_id = int(get_jwt_identity())
    if not user_owns_testcase(user_id, testcase_id):
        return jsonify({'error': 'Test case not found'}), 404

    data = request.json or {}
    status = (data.get('status') or '').strip()
    session = manual_run.get(testcase_id)
    if not session:
        return jsonify({'error': 'No manual run is open for this test case'}), 404
    if not session.mark_step(index, status, data.get('note', '')):
        return jsonify({'error': 'Unknown step or status'}), 400
    return jsonify({'run': session.snapshot()}), 200


@runner_bp.route('/manual/<int:testcase_id>/issue', methods=['POST'])
@jwt_required()
def manual_report_issue(testcase_id):
    """
    Record something the tester spotted on the page they are looking at.

    This is the half of QA a crawler cannot do: judging that a layout is
    broken, a design is confusing, or the behaviour does not match what the
    business actually asked for.
    """
    user_id = int(get_jwt_identity())
    if not user_owns_testcase(user_id, testcase_id):
        return jsonify({'error': 'Test case not found'}), 404

    data = request.json or {}
    title = (data.get('title') or '').strip()
    if len(title) < 3:
        return jsonify({'error': 'Describe the issue in at least 3 characters'}), 400

    session = manual_run.get(testcase_id)
    if not session:
        return jsonify({'error': 'No manual run is open for this test case'}), 404

    entry = session.report_issue(
        category=data.get('category'),
        severity=data.get('severity'),
        title=title,
        note=data.get('note', ''),
        step_index=data.get('step_index'),
    )
    if entry is None:
        return jsonify({'error': 'Could not record the issue'}), 400
    return jsonify({'issue': entry, 'run': session.snapshot()}), 201


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
    expected_met = data.get('expected_met')

    ok, message, snap = manual_run.finish(testcase_id, outcome, note,
                                          expected_met=expected_met)
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
    manual_summary = manual_run.summarise_manual(snap)

    # A failed checklist step is a functional failure, exactly as it is for an
    # automated run, so it scores and becomes a bug the same way.
    functional = [
        {
            'issue': f'Step {s["index"] + 1} failed: {s["text"]}',
            'severity': 'critical',
            'step': s['text'],
            'step_index': s['index'],
            'reason': s.get('note') or 'Marked as failed by the tester',
            'screenshot': s.get('screenshot', ''),
            'source': 'tester',
            'display': f'Step {s["index"] + 1} failed: {s["text"]}',
        }
        for s in (snap.get('steps') or []) if s['status'] == 'failed'
    ]

    # Everything the tester spotted by eye, in the same shape as any finding.
    reported = [
        {
            'issue': r['title'],
            'severity': r['severity'],
            'category': r['category'],
            'note': r.get('note', ''),
            'page_url': r.get('page_url', ''),
            'screenshot': r.get('screenshot', ''),
            'source': 'tester',
            'display': f'[{r["category"]}] {r["title"]}',
        }
        for r in (snap.get('reported') or [])
    ]

    issues_found = len(functional) + len(reported) + snap.get('auto_findings_count', 0)

    findings_evidence = json.dumps({
        'manual': True,
        'note': note,
        'pages': pages,
        'steps': snap.get('steps') or [],
        'reported': reported,
        'expected_result': snap.get('expected_result', ''),
        'expected_met': snap.get('expected_met'),
        'summary': manual_summary,
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
                issues_found, total_requests, findings_evidence,
                functional_issues, steps_result, health_score
            ) VALUES (%s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s)""",
            (
                testcase_id, outcome,
                first_shot,
                duration_ms,
                issues_found, len(pages),
                findings_evidence,
                json.dumps([f['display'] for f in functional]),
                json.dumps({'results': snap.get('steps') or [],
                            'summary': manual_summary}),
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
        'issues_found': issues_found,
        'steps': snap.get('steps') or [],
        'reported': reported,
        'functional_issues': [f['display'] for f in functional],
        'manual_summary': manual_summary,
        'expected_result': snap.get('expected_result', ''),
        'expected_met': snap.get('expected_met'),
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


def _previous_run_findings(testcase_id):
    """
    The findings and score of the most recent completed run of this test case,
    used as the baseline for regression comparison.

    Returns (findings_dict, health_score), or (None, None) when there is no
    usable previous run — a first run is a baseline, not a regression.
    """
    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """SELECT findings_evidence, health_score
               FROM test_runs
               WHERE test_case_id = %s AND findings_evidence IS NOT NULL
               ORDER BY id DESC LIMIT 1""",
            (testcase_id,)
        )
        row = cursor.fetchone()
        cursor.close()
    except Exception as e:
        logger.warning("could not load previous run for regression: %s", e)
        return None, None

    if not row or not row.get('findings_evidence'):
        return None, None
    try:
        findings = json.loads(row['findings_evidence'])
    except (ValueError, TypeError):
        return None, None
    if not isinstance(findings, dict):
        return None, None
    # Manual runs store a different shape; they are not comparable.
    if findings.get('manual'):
        return None, None
    return findings, row.get('health_score')


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
            parsed_base = urlparse(base_clean)
            origin_root = None
            if parsed_base.scheme and parsed_base.netloc:
                origin_root = f"{parsed_base.scheme}://{parsed_base.netloc}"

            # If the user manually logged in via "Open & Login", session_capture saved
            # the URL they landed on (i.e. the authenticated dashboard). That becomes
            # the crawl start, and login screens are dropped from the queue — the user
            # captured a session precisely so the pages BEHIND the login get tested.
            landing = _session_landing_url(tc.get('project_id')) if session_used else None
            to_visit = build_crawl_queue(base_url, landing_url=landing,
                                         session_used=session_used)
            queued = set(to_visit)
            if session_used:
                logger.info("authenticated crawl queue: %s", to_visit)

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
                    # SPAs redirect after hydration; wait for it to stop moving.
                    wait_for_page_settled(warm)
                    landed = warm.url.rstrip('/').split('#')[0]
                    if landed and not looks_like_login(landed):
                        if landed not in queued:
                            # Front of the queue so the crawl starts from the
                            # authenticated landing page, not the login URL.
                            to_visit.insert(0, landed)
                            queued.add(landed)
                        # Now that a genuine authenticated page is confirmed, the
                        # login screens in the queue are dead weight.
                        pruned = [u for u in to_visit if not looks_like_login(u)]
                        if pruned:
                            to_visit[:] = pruned
                            queued = set(to_visit)
                        logger.info("auth warmup: authenticated landing = %s; crawl queue = %s",
                                    landed, to_visit)
                    else:
                        logger.warning("auth warmup: landed on '%s' (looks like a login page) — "
                                       "the captured session may have expired", landed)
                    warm.close()
                except Exception as e:
                    logger.warning("auth warmup failed: %s", e)

            all_pages_findings = []
            all_pages_coverage = []
            per_page_records = []
            urls_to_test = []   # URLs actually visited, in order

            # Written steps for this test case, executed once on the first page.
            steps_text = (tc.get('steps') or '').strip()
            step_results, step_findings = [], []
            step_summary = summarise_steps([])

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
                    if page_idx == 0:
                        # First navigation in this run — full HTTP load so we can
                        # capture security headers and verify the page is reachable.
                        response = page.goto(page_url, timeout=30000, wait_until='domcontentloaded')
                        if response:
                            page_status = response.status
                            response_headers = dict(response.headers)
                            saved_first_headers = dict(response.headers)
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

                    # Let client-side rendering and post-login redirects settle so that
                    # JS-injected navigation links become discoverable. Waits for the
                    # DOM to stop changing rather than guessing at a duration.
                    try:
                        page.wait_for_load_state('networkidle', timeout=5000)
                    except Exception:
                        pass
                    wait_for_page_settled(page)

                    # Run the written steps once, on the first page. They define
                    # the workflow under test, so a step that fails is a
                    # functional failure rather than a page-quality issue. The
                    # audit below then runs on wherever the workflow ended up,
                    # which for a login flow means the page behind the login.
                    if page_idx == 0 and steps_text:
                        def _capture_step(idx, _record):
                            name = f"run_{testcase_id}_{crop_run_id}_step{idx}.png"
                            path = os.path.join(screenshot_dir, name)
                            page.screenshot(path=path, full_page=False)
                            return f"/static/uploads/runs/{name}"

                        step_results, step_findings = run_steps(
                            page, steps_text, base_url=base_url, on_step=_capture_step)
                        step_summary = summarise_steps(step_results)
                        logger.info("steps for test case %s: %s",
                                    testcase_id, step_summary['summary'])
                        _report(tested=len(per_page_records), total=max_pages,
                                current=page.url, steps=step_summary)
                        # The workflow may have navigated; audit where it landed.
                        try:
                            page_url = page.url or page_url
                        except Exception:
                            pass

                    try:
                        page_title = (page.title() or '')[:500]
                    except Exception:
                        page_title = ''

                    page_findings, page_coverage = test_single_page(
                        page, page_url, response_headers,
                        console_messages, is_mobile=not session_used,
                        page_load_ms=page_load_ms)
                    if page_idx == 0:
                        page_findings['functional_issues'] = step_findings
                    page_findings = tag_findings_with_page(page_findings, page_url)
                    all_pages_coverage.append(page_coverage)

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
            display_findings = {
                category: [item.get('display', str(item)) for item in items]
                for category, items in merged_findings.items()
            }

            health_score = calculate_health_score(merged_findings)
            breakdown = score_breakdown(merged_findings)
            coverage_summary = summarise_coverage(all_pages_coverage)
            top_severity = worst_severity(merged_findings)
            # Compare against the previous run of this same test case so the
            # user sees what they broke and what they fixed, not just a total.
            prev_findings, prev_score = _previous_run_findings(testcase_id)
            regression = build_regression(prev_findings, merged_findings,
                                          prev_score, health_score)
            issues_found = sum(len(items) for items in merged_findings.values())
            status = 'Pass' if issues_found == 0 else 'Fail'
            # A Pass on a partial audit is not a full all-clear. We keep the
            # Pass/Fail meaning simple and let coverage_summary['notes'] carry
            # the caveat about anything that was capped or skipped.
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
                    performance_issues, functional_issues, steps_result,
                    findings_evidence, score_breakdown,
                    coverage, regression, health_score
                ) VALUES (%s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    testcase_id, status,
                    first_page.get('screenshot', ''),
                    duration_ms,
                    json.dumps(display_findings['console_errors']),
                    json.dumps(display_findings['broken_links']),
                    json.dumps(display_findings['missing_alt_images']),
                    page_load_ms,
                    issues_found,
                    coverage_summary['page_size_kb_total'],
                    len(urls_to_test),
                    json.dumps(display_findings['seo_issues']),
                    json.dumps(display_findings['security_issues']),
                    json.dumps(display_findings['accessibility_issues']),
                    json.dumps(display_findings['mobile_issues']),
                    json.dumps(display_findings['performance_issues']),
                    json.dumps(display_findings['functional_issues']),
                    json.dumps({'results': step_results, 'summary': step_summary}),
                    json.dumps(merged_findings),
                    json.dumps(breakdown),
                    json.dumps(coverage_summary),
                    json.dumps(regression),
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
                'duration_ms': duration_ms,
                'page_load_time_ms': page_load_ms,
                'total_page_size_kb': coverage_summary['page_size_kb_total'],
                'total_requests': len(urls_to_test),
                'screenshot': first_page.get('screenshot', ''),
                'pages_tested': len(urls_to_test),
                'pages_crawled': len(per_page_records),
                'is_multi_page': crawl_pages,
                'session_used': session_used,
                'session_message': session_message,
                'session_warning': session_warning,
                # Where the score was lost, per category, so the number is
                # explainable rather than something the user has to trust.
                'score_breakdown': breakdown,
                'worst_severity': top_severity,
                # What the audit did NOT cover. Empty notes means full coverage.
                'coverage': coverage_summary,
                # What changed since the last run of this test case.
                'regression': regression,
                # Step-by-step result of the written workflow.
                'steps': step_results,
                'step_summary': step_summary,
                'broken_links': display_findings['broken_links'],
                'console_errors': display_findings['console_errors'],
                'missing_alt_images': display_findings['missing_alt_images'],
                'seo_issues': display_findings['seo_issues'],
                'security_issues': display_findings['security_issues'],
                'accessibility_issues': display_findings['accessibility_issues'],
                'mobile_issues': display_findings['mobile_issues'],
                'performance_issues': display_findings['performance_issues'],
                'functional_issues': display_findings['functional_issues'],
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
