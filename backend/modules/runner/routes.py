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
import ipaddress
import os
import json
import re
import socket
import time
import uuid
import logging
import math
import threading
import requests

from modules.runner.jobs import job_manager
from modules.runner import manual_run
from modules.runner.steps import run_steps, summarise_steps
from modules.runner.extended_checks import (
    check_url_health,
    check_api_quality,
    check_page_resources,
    check_ui_consistency,
    check_code_quality,
)

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
    # High enough that a real page is covered rather than sampled. Checks
    # run in parallel, so the cost is wall-clock on link-heavy pages only.
    LINK_CAP = 100
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
        # slow server, so every failure gets one slower retry before being
        # called broken. The retry also classifies the failure: a timeout is
        # weak evidence (the host answered, just slowly), while a connection
        # error — DNS says the host does not exist, or it refuses the
        # connection — means the host answered nothing at all.
        failure = 'unreachable'
        for timeout in (PER_REQUEST_TIMEOUT, RETRY_TIMEOUT):
            try:
                return _attempt(url, timeout), None
            except requests.Timeout:
                failure = 'timeout'
            except requests.RequestException:
                failure = 'unreachable'
        return 0, failure

    def _is_broken(url, status, failure=None):
        # 401/403/429/503 are auth / anti-bot / rate-limit / transient responses,
        # NOT broken links. A timeout is only useful evidence for a link on the
        # site currently being tested — an external site being slow is not a
        # defect of ours. A connection error is different: the host answered
        # nothing at all, so the destination is dead on any site.
        if status == 0:
            if failure == 'timeout':
                return urlparse(url).netloc == urlparse(base_url).netloc
            return True
        if status in (401, 403, 429, 503):
            return False
        return status >= 400

    def _severity(url, status, failure=None):
        # A dead link on your own site is worse than a dead link pointing out.
        # A 5xx means the target is erroring, which is worse still — but only
        # critical when it is our own server failing. An external site's 500
        # is not a defect in the thing under test.
        try:
            internal = urlparse(url).netloc == urlparse(base_url).netloc
        except Exception:
            internal = False
        if status >= 500:
            return 'critical' if internal else 'serious'
        if status == 0:
            if failure == 'timeout':
                return 'moderate'      # slow: often not a fault at all
            # Refused / no such host: the destination really is unreachable.
            return 'serious' if internal else 'moderate'
        return 'serious' if internal else 'moderate'

    bad = {}    # full_url -> (status, failure)
    try:
        with ThreadPoolExecutor(max_workers=POOL_SIZE) as pool:
            future_to_url = {pool.submit(_check, url): url for url, _ in candidates}
            for fut in as_completed(future_to_url):
                url = future_to_url[fut]
                try:
                    status, failure = fut.result()
                except Exception:
                    status, failure = 0, 'unreachable'
                if _is_broken(url, status, failure):
                    bad[url] = (status, failure)
    except Exception as e:
        logger.warning("broken_links: pool error: %s", e)
        return findings, coverage

    # Build findings (with element metadata) only for the failed URLs.
    for full_url, link in candidates:
        if full_url not in bad:
            continue
        try:
            status, failure = bad[full_url]
            link_text = link.inner_text()[:60].strip() or '(no text)'
            findings.append({
                'url': full_url,
                'status_code': status,
                'severity': _severity(full_url, status, failure),
                'element_text': link_text,
                'element_selector': _safe_get_selector(link),
                'parent_context': _safe_get_parent_text(link),
                'bounding_box': _safe_get_bounding_box(link),
                'display': f"{full_url} ({status if status > 0 else 'unreachable'})",
            })
        except Exception:
            continue

    return findings, coverage


# ============================================================
# USER-FRIENDLY ISSUE GUIDANCE
# One place that explains every finding in plain language:
# what it means for the user and exactly where to fix it.
# The runner attaches `summary` + `fix` to key findings so the
# UI can show a clear message without guessing per category.
# ============================================================

# Category display names shown in the UI (instead of snake_case keys).
CATEGORY_LABELS = {
    'functional_issues': 'Feature not working',
    'broken_links': 'Broken link',
    'console_errors': 'Page error (JavaScript)',
    'missing_alt_images': 'Image missing description',
    'seo_issues': 'Search / tab appearance',
    'security_issues': 'Security header',
    'accessibility_issues': 'Accessibility',
    'mobile_issues': 'Mobile usability',
    'performance_issues': 'Slow / heavy page',
    'api_issues': 'Failed data request',
    'validation_issues': 'Form validation',
    'execution_errors': 'Audit could not finish',
    'url_issues': 'URL problem',
    'ui_issues': 'Interface defect',
    'code_issues': 'Front-end code problem',
}

# Where to fix each category (shown as "Where to change").
CATEGORY_FIX_GUIDANCE = {
    'broken_links': 'Update the link address in the page, or restore the missing destination page.',
    'console_errors': 'Open browser DevTools > Console on the listed page and fix the file/line shown.',
    'missing_alt_images': 'Add descriptive alt text to the image in the page template or component.',
    'seo_issues': 'Update the <head> section of the page template (title / meta description / headings).',
    'security_issues': 'Update the web server, reverse proxy, or app security-header configuration.',
    'accessibility_issues': 'Update the HTML element shown in "Technical location" to meet the stated rule.',
    'mobile_issues': 'Update the CSS or component styles so tap targets are at least 44x44px.',
    'performance_issues': 'Compress images, split large bundles, and lazy-load below-the-fold resources.',
    'functional_issues': 'Fix the workflow step described, then re-run the test.',
    'api_issues': 'Fix the frontend request URL or the backend endpoint that serves it.',
    'validation_issues': 'Add required-field validation to the form control or its submit handler.',
    'execution_errors': 'Re-run the audit; if it persists, check the page loads without login expiry.',
    'url_issues': 'Fix the URL, redirect, or server route so the address serves the intended page.',
    'ui_issues': 'Update the page template so IDs are unique and every control has a label.',
    'code_issues': 'Move inline code into versioned assets, drop deprecated tags, and update libraries.',
}


def category_label(category):
    """Human-readable name for a finding category (falls back to the key)."""
    return CATEGORY_LABELS.get(category, category.replace('_', ' ').capitalize())


def category_fix(category):
    """Actionable next step for a finding category."""
    return CATEGORY_FIX_GUIDANCE.get(
        category, 'Open the page and update the code at the technical location shown.')


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
                # Single bounding-box read: also filters out spacers/icons.
                box = _safe_get_bounding_box(img)
                if (not box or box['width'] < ALT_MIN_SIZE_PX
                        or box['height'] < ALT_MIN_SIZE_PX):
                    continue
                img_name = (src.rsplit('/', 1)[-1].split('?')[0] or 'image')[:60]
                findings.append({
                    'src': src[:200],
                    'severity': 'minor',
                    'element_selector': _safe_get_selector(img),
                    'parent_context': _safe_get_parent_text(img),
                    'bounding_box': box,
                    'display': src[:200],
                    'summary': f'Image "{img_name}" has no text description for screen readers.',
                    'fix': category_fix('missing_alt_images'),
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


IMPORTANT_PAGE_KEYWORDS = {
    'privacy': ('privacy', 'privacy-policy', 'privacy_policy'),
    'terms': ('terms', 'terms-of-service', 'terms_and_conditions', 'conditions'),
    'security': ('security', 'trust', 'responsible-disclosure'),
    'contact': ('contact', 'support'),
}


def check_important_page_links(page, base_url):
    """Verify important pages that the site exposes through navigation or footer links."""
    findings = []
    candidates = {}
    try:
        links = page.query_selector_all('a[href]')
    except Exception as e:
        logger.warning("important page check: link enumeration error: %s", e)
        return findings

    for link in links:
        try:
            href = resolve_link_url(link.get_attribute('href'), base_url)
            if not href or urlparse(href).netloc != urlparse(base_url).netloc:
                continue
            label = ' '.join([
                link.inner_text() or '',
                link.get_attribute('aria-label') or '',
                href,
            ]).lower()
            for page_type, keywords in IMPORTANT_PAGE_KEYWORDS.items():
                if any(keyword in label for keyword in keywords):
                    candidates.setdefault((page_type, href), link)
        except Exception:
            continue

    for (page_type, href), link in candidates.items():
        try:
            response = requests.get(href, timeout=5, allow_redirects=True,
                                    headers={'User-Agent': 'QA-Platform-audit/1.0'})
            if response.status_code >= 400:
                findings.append({
                    'issue': f'Important {page_type} page is unavailable ({response.status_code})',
                    'severity': 'serious' if page_type in ('privacy', 'terms', 'security') else 'moderate',
                    'url': href,
                    'page_type': page_type,
                    'element_selector': 'a[href]',
                    'display': f'{page_type.title()} page link returned HTTP {response.status_code}',
                })
            elif not response.text.strip():
                findings.append({
                    'issue': f'Important {page_type} page is empty',
                    'severity': 'serious' if page_type in ('privacy', 'terms', 'security') else 'moderate',
                    'url': href,
                    'page_type': page_type,
                    'element_selector': 'a[href]',
                    'display': f'{page_type.title()} page returned no content',
                })
        except requests.RequestException as exc:
            findings.append({
                'issue': f'Important {page_type} page could not be reached',
                'severity': 'moderate',
                'url': href,
                'page_type': page_type,
                'element_selector': 'a[href]',
                'display': f'{page_type.title()} page link is unreachable: {str(exc)[:120]}',
            })
    return findings


def check_important_page_presence(base_url):
    """Check conventional legal/security routes when the site exposes no link."""
    findings = []
    route_candidates = {
        'privacy': ('/privacy', '/privacy-policy', '/privacy_policy'),
        'terms': ('/terms', '/terms-and-conditions', '/terms_of_service'),
        'security': ('/security', '/security-policy', '/trust'),
    }
    for page_type, routes in route_candidates.items():
        reachable = False
        for route in routes:
            candidate = urljoin(base_url.rstrip('/') + '/', route.lstrip('/'))
            try:
                response = requests.get(
                    candidate, timeout=3, allow_redirects=True,
                    headers={'User-Agent': 'QA-Platform-audit/1.0'},
                )
                if response.status_code < 400 and response.text.strip():
                    reachable = True
                    break
            except requests.RequestException:
                continue
        if not reachable:
            findings.append({
                'issue': f'No reachable {page_type} page was found',
                'severity': 'moderate' if page_type == 'security' else 'serious',
                'page_type': page_type,
                'element_selector': 'document',
                'display': f'No conventional {page_type} page route is available',
            })
    return findings


AUTH_ROUTE_KEYWORDS = (
    'account', 'admin', 'dashboard', 'profile', 'settings',
    'billing', 'checkout', 'orders', 'user', 'private',
)


def check_authentication_access(page, page_url, session_used):
    """Check discovered protected-looking routes without submitting anything."""
    findings = []
    if session_used and looks_like_login(page_url):
        findings.append({
            'issue': 'Captured authentication session redirected to a login page',
            'severity': 'serious',
            'url': page_url,
            'element_selector': 'document',
            'display': 'The saved login session may be expired or invalid',
        })
        return findings
    if session_used:
        return findings

    try:
        links = page.query_selector_all('a[href]')
    except Exception as e:
        logger.warning("authentication access check: link enumeration error: %s", e)
        return findings

    candidates = set()
    for link in links:
        try:
            href = resolve_link_url(link.get_attribute('href'), page_url)
            path = (urlparse(href).path or '').lower() if href else ''
            if href and urlparse(href).netloc == urlparse(page_url).netloc:
                if any(keyword in path.split('/') for keyword in AUTH_ROUTE_KEYWORDS):
                    candidates.add(href)
        except Exception:
            continue

    for href in sorted(candidates)[:10]:
        try:
            response = requests.get(
                href, timeout=5, allow_redirects=True,
                headers={'User-Agent': 'QA-Platform-anonymous-audit/1.0'},
            )
            if response.status_code < 400 and not looks_like_login(response.url):
                findings.append({
                    'issue': f'Protected-looking route is accessible without authentication: {urlparse(href).path}',
                    'severity': 'critical',
                    'url': href,
                    'element_selector': 'a[href]',
                    'display': 'Anonymous request reached a protected-looking page',
                    'final_url': response.url,
                    'status_code': response.status_code,
                })
        except requests.RequestException:
            # A network failure is handled by the existing broken-link checks.
            continue
    return findings


def check_security(page_url, response_headers, page=None):
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
        if page is not None:
            try:
                insecure_resources = page.evaluate(
                    """() => Array.from(document.querySelectorAll(
                        'img[src],script[src],link[href],iframe[src],video[src],audio[src]'
                    )).map(el => el.src || el.href || '').filter(u => u.startsWith('http://'))"""
                ) or []
                if is_https and insecure_resources:
                    findings.append({
                        'issue': f'HTTPS page loads {len(insecure_resources)} insecure HTTP resource(s)',
                        'severity': 'serious',
                        'element_selector': '[src], [href]',
                        'display': 'Mixed content can expose users to downgraded resources',
                        'urls': insecure_resources[:10],
                    })
            except Exception as e:
                logger.warning("mixed-content security check error: %s", e)
            try:
                password_fields = page.query_selector_all('input[type="password"]')
                for field in password_fields:
                    autocomplete = (field.get_attribute('autocomplete') or '').lower()
                    if autocomplete == 'off':
                        findings.append({
                            'issue': 'Password field disables browser password-manager support',
                            'severity': 'moderate',
                            'element_selector': 'input[type="password"]',
                            'display': 'Password input uses autocomplete="off"',
                        })
            except Exception as e:
                logger.warning("password security check error: %s", e)
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


TAP_TARGET_CAP = 150


def _is_inline_text_link(element):
    """
    True for a link sitting inside a run of text. WCAG exempts these from
    target-size rules because an inline link cannot be padded to 44px without
    breaking the sentence around it. A link styled as a button reports
    inline-block or block and is still measured.
    """
    try:
        # One round trip, not two: this runs per control and the cap is high.
        info = element.evaluate(
            "el => ({tag: el.nodeName.toLowerCase(),"
            " display: getComputedStyle(el).display})")
    except Exception:
        return False
    if not isinstance(info, dict):
        return False
    return info.get('tag') == 'a' and info.get('display') == 'inline'


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



# Words that mean a control does something you cannot take back. The dead
# control check never clicks these. A crawler that deletes a record, places an
# order or logs itself out is far worse than a dead button it failed to spot.
DESTRUCTIVE_LABELS = (
    'delete', 'remove', 'destroy', 'erase', 'clear', 'reset', 'discard',
    'pay', 'buy', 'purchase', 'order', 'checkout', 'subscribe', 'donate',
    'submit', 'send', 'confirm', 'apply', 'save', 'publish', 'archive',
    'log out', 'logout', 'sign out', 'signout', 'deactivate', 'unsubscribe',
    'cancel',
)

# Only things that are clickable but are not navigation. A real link already
# has its destination checked by check_broken_links.
DEAD_CONTROL_SELECTOR = 'button, [role="button"], a[href="#"], a[href=""], a:not([href])'

DEAD_CONTROL_CAP = 15
DEAD_CONTROL_SETTLE_MS = 400


def _control_label(element):
    """What a person would call this control."""
    text = ''
    try:
        text = (element.inner_text() or '').strip()
    except Exception:
        text = ''
    if not text:
        try:
            text = (element.get_attribute('aria-label') or '').strip()
        except Exception:
            text = ''
    return text[:60]


def _is_destructive(label):
    lowered = (label or '').lower()
    return any(word in lowered for word in DESTRUCTIVE_LABELS)


def _control_state(page):
    """
    A cheap fingerprint of everything a working control could change.

    Focus is deliberately left out: clicking always moves focus, so including
    it would make every control look alive.
    """
    return page.evaluate("""() => ({
        url: location.href,
        html: document.documentElement.innerHTML.length,
        nodes: document.getElementsByTagName('*').length,
        requests: (performance.getEntriesByType('resource') || []).length,
        storage: localStorage.length + sessionStorage.length,
    })""")


def check_dead_controls(page, page_url, cap=DEAD_CONTROL_CAP):
    """
    Find controls that do nothing whatsoever when clicked.

    This is the one defect nothing else here can see: a button wired to
    nothing raises no console error, breaks no link and violates no
    accessibility rule. The only way to know is to click it and watch.

    Clicking a live site is destructive, so this is opt-in per test case and
    refuses to touch anything inside a form, anything disabled, any submit or
    reset control, and anything whose label suggests it cannot be undone.

    Returns (findings, coverage).
    """
    findings = []
    coverage = {'controls_found': 0, 'controls_clicked': 0,
                'skipped': 0, 'truncated': False}
    try:
        controls = page.query_selector_all(DEAD_CONTROL_SELECTOR)
    except Exception as e:
        logger.warning("dead controls: could not enumerate: %s", e)
        return findings, coverage

    coverage['controls_found'] = len(controls)

    for element in controls:
        if coverage['controls_clicked'] >= cap:
            coverage['truncated'] = True
            break

        label = _control_label(element)
        try:
            enabled = element.is_enabled()
        except Exception:
            enabled = False
        # A disabled control doing nothing is correct behaviour, not a defect.
        if not enabled or _is_destructive(label):
            coverage['skipped'] += 1
            continue
        try:
            if (element.get_attribute('type') or '') in ('submit', 'reset'):
                coverage['skipped'] += 1
                continue
            if element.evaluate("el => !!el.closest('form')"):
                coverage['skipped'] += 1
                continue
        except Exception:
            coverage['skipped'] += 1
            continue

        try:
            before = _control_state(page)
        except Exception as e:
            logger.warning("dead controls: could not read page state: %s", e)
            break

        try:
            element.click(timeout=2000)
        except Exception:
            # The click never landed, so nothing was learned about this
            # control and nothing is claimed about it.
            continue

        coverage['controls_clicked'] += 1
        try:
            page.wait_for_timeout(DEAD_CONTROL_SETTLE_MS)
            after = _control_state(page)
        except Exception:
            continue

        if after != before:
            if after.get('url') != before.get('url'):
                # It navigated, which means it works. Go back so the rest of
                # the crawl still happens on the page we were auditing.
                try:
                    page.goto(page_url, wait_until='domcontentloaded')
                except Exception as e:
                    logger.warning("dead controls: could not return to %s: %s",
                                   page_url, e)
            continue

        shown = label or '(unlabelled)'
        findings.append({
            'issue': f'Control does nothing when clicked: {shown}',
            'issue_type': 'dead_control',
            'severity': 'serious',
            'control': label,
            'element_selector': _safe_get_selector(element),
            'element_text': label,
            'bounding_box': _safe_get_bounding_box(element),
            'display': f'"{shown}" does nothing when clicked',
        })

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


API_STATUS_MEANINGS = {
    400: 'the request was malformed (check query params / request body)',
    401: 'sign-in is required or the session expired',
    403: 'the logged-in user is not allowed to access it',
    404: 'the endpoint path does not exist on the server',
    409: 'the request conflicted with current data (e.g. duplicate entry)',
    422: 'the server rejected the data as invalid (check field formats)',
    429: 'the app sent too many requests too quickly (rate limited)',
    500: 'the server crashed while handling it (check backend logs)',
    502: 'a gateway/proxy in front of the app is failing',
    503: 'the backend service is temporarily unavailable',
    504: 'the backend timed out before responding',
}


def check_api_responses(api_responses):
    """Turn failed API/XHR responses observed during the page visit into findings.

    Each finding names the exact endpoint, what the status most likely means,
    and where to look (frontend call vs backend route).
    """
    findings = []
    seen = set()
    for response in api_responses or []:
        status = response.get('status', 0)
        url = response.get('url', '')
        if status < 400 or not url or url in seen:
            continue
        seen.add(url)
        path = urlparse(url).path or url
        short_path = path[:120]
        meaning = API_STATUS_MEANINGS.get(
            status, 'the server returned an error for this request')
        findings.append({
            'issue': f'API request to "{short_path}" failed with HTTP {status} ({meaning})',
            'severity': 'critical' if status >= 500 else 'serious',
            'status': status,
            'url': url,
            'element_selector': 'network',
            'display': f'API request failed ({status}): {url}',
            'summary': f'Data request to "{short_path}" failed — {meaning}.',
            'fix': category_fix('api_issues'),
        })
    return findings


def check_form_validation(page):
    """Find forms whose native required-field validation does not work.

    Each finding names which form (by index + id/name when available) and
    how many required fields were tested, so the user knows exactly where
    to add validation.
    """
    try:
        form_results = page.evaluate("""() => Array.from(document.forms).map((form, index) => {
            const controls = Array.from(form.querySelectorAll('input, select, textarea'))
                .filter(control => !control.disabled);
            const required = controls.filter(control => control.required);
            const invalidRequired = required.filter(control => {
                const originalValue = control.value;
                const originalChecked = control.checked;
                if (control.type === 'checkbox' || control.type === 'radio') {
                    control.checked = false;
                } else {
                    control.value = '';
                }
                const invalid = !control.checkValidity();
                control.value = originalValue;
                control.checked = originalChecked;
                return invalid;
            });
            return { index, id: form.id || null, name: form.name || null,
                     required: required.length, invalidRequired: invalidRequired.length };
        })""")
    except Exception as e:
        logger.warning("form validation check error: %s", e)
        return []

    findings = []
    for result in form_results or []:
        if result.get('required') and not result.get('invalidRequired'):
            form_ref = result.get('id') or result.get('name') or f"form #{result['index'] + 1}"
            count = result['required']
            findings.append({
                'issue': f'"{form_ref}" accepts empty submission ({count} required field(s) not validated)',
                'severity': 'serious',
                'form_index': result['index'],
                'form_id': result.get('id'),
                'required_count': count,
                'element_selector': f"form#{result.get('id')}" if result.get('id') else 'form',
                'display': f'"{form_ref}" does not reject empty required fields ({count} unchecked)',
                'summary': f'Form "{form_ref}" lets users submit with {count} required field(s) empty.',
                'fix': category_fix('validation_issues'),
            })
    return findings


def check_generated_form_validation(page):
    """Fill safe invalid values and verify that native constraints reject them."""
    try:
        results = page.evaluate("""() => {
            const output = [];
            for (const [formIndex, form] of Array.from(document.forms).entries()) {
                for (const control of form.querySelectorAll('input, select, textarea')) {
                    if (control.disabled || !control.willValidate) continue;
                    const type = (control.type || 'text').toLowerCase();
                    const values = type === 'email' ? ['not-an-email'] :
                        type === 'url' ? ['not-a-url'] :
                        type === 'tel' ? ['abc'] :
                        type === 'number' && control.min !== '' ? [String(Number(control.min) - 1)] :
                        type === 'number' && control.max !== '' ? [String(Number(control.max) + 1)] :
                        control.pattern ? ['invalid-pattern-value'] :
                        control.minLength > 0 ? ['x'.repeat(Math.max(0, control.minLength - 1))] :
                        control.maxLength > 0 ? ['x'.repeat(control.maxLength + 1)] : [];
                    for (const value of values) {
                        const original = control.value;
                        control.value = value;
                        const accepted = control.checkValidity();
                        control.value = original;
                        if (accepted) {
                            output.push({
                                form_index: formIndex,
                                name: control.name || control.id || control.type || 'field',
                                type, value
                            });
                        }
                    }
                }
            }
            return output;
        }""") or []
    except Exception as e:
        logger.warning("generated form validation check error: %s", e)
        return []

    return [{
        'issue': f'Form accepts invalid {item["type"]} value in "{item["name"]}"',
        'severity': 'serious',
        'form_index': item['form_index'],
        'field': item['name'],
        'element_selector': 'input, select, textarea',
        'display': f'Invalid value "{item["value"]}" was accepted for {item["name"]}',
        'summary': 'Add client-side and server-side validation for this field.',
    } for item in results]


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
    'seo_issues': 'moderate',
    'missing_alt_images': 'minor',
    'mobile_issues': 'moderate',
    'api_issues': 'serious',
    'validation_issues': 'serious',
    'execution_errors': 'serious',
    'url_issues': 'serious',
    'ui_issues': 'moderate',
    'code_issues': 'moderate',
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
    'seo_issues': 4,
    'missing_alt_images': 2,
    'mobile_issues': 6,
    'api_issues': 10,
    'validation_issues': 10,
    'execution_errors': 8,
    'url_issues': 8,
    'ui_issues': 7,
    'code_issues': 7,
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


def measured_categories(step_summary=None, mobile_checked=True,
                        accessibility_measured=True, controls_checked=False):
    """
    The categories a run actually measured.

    A category that was never checked has no findings, and the weighted
    average read that as a perfect result. That rewarded not looking: an
    authenticated crawl skips the mobile checks and used to collect full
    marks for them anyway, and a run where no step ever executed banked the
    whole functional weight, the largest of the nine.

    Mobile counts as measured only when the crawl ran it. Functional counts
    only once at least one step actually executed, because steps the runner
    could not read never ran and so verify nothing about the site.
    """
    measured = set(CATEGORY_WEIGHT)
    if not mobile_checked:
        measured.discard('mobile_issues')
    # When axe-core could not run, the fallback finds far less, so the run
    # produced fewer findings and the score went UP because a tool broke.
    if not accessibility_measured:
        measured.discard('accessibility_issues')
    summary = step_summary or {}
    executed = summary.get('passed', 0) + summary.get('failed', 0)
    # Clicking controls is a functional check too, so a run that only did
    # that still measured the category and deserves credit for a clean result.
    if not executed and not controls_checked:
        measured.discard('functional_issues')
    return measured


def calculate_health_score(findings_by_category, measured=None):
    """
    Weighted average of the per-category subscores. Always 1..100.

    `measured` names the categories this run actually checked. Anything
    outside it is left out of the average rather than counted as perfect,
    because a check that never ran is not a check that passed. Passing
    nothing keeps the original behaviour of scoring all nine.
    """
    total_weight = 0
    weighted_sum = 0
    for category, weight in CATEGORY_WEIGHT.items():
        if measured is not None and category not in measured:
            continue
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
    links_truncated = False
    fallback_pages = 0
    mobile_skipped_pages = 0
    page_size_total = 0
    page_size_max = 0
    request_total = 0
    unmeasured_total = 0
    steps_total = steps_unreadable = 0
    audit_errors = 0

    for cov in all_pages_coverage or []:
        audit_errors += 1 if cov.get('audit_error') else 0
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

        steps = cov.get('steps') or {}
        steps_total += steps.get('total', 0)
        steps_unreadable += steps.get('unreadable', 0)

        if cov.get('accessibility_engine') == 'heuristic-fallback':
            fallback_pages += 1

        if cov.get('mobile_skipped_reason'):
            mobile_skipped_pages += 1

    notes = []
    if mobile_skipped_pages:
        notes.append(
            f'Mobile checks were skipped on {mobile_skipped_pages} page(s), '
            f'so mobile usability is unmeasured there rather than clean.'
        )
    if links_truncated:
        notes.append(
            f'Only {links_checked} of {links_found} links were tested. '
            f'A clean link result does not mean every link on the site works.'
        )
    if fallback_pages:
        notes.append(
            f'axe-core could not run on {fallback_pages} page(s), so a much more '
            f'limited accessibility check was used there. Real accessibility '
            f'problems may have been missed.'
        )
    if steps_unreadable:
        notes.append(
            f'{steps_unreadable} of {steps_total} written step(s) could not be '
            f'understood, so that part of the workflow was never exercised. '
            f'Rewording them would test more of the site.'
        )
    if audit_errors:
        notes.append(
            f'{audit_errors} page(s) could not complete the audit. Their results '
            f'are incomplete and should not be treated as clean.'
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
        'audit_errors': audit_errors,
        'accessibility_fallback_pages': fallback_pages,
        'mobile_skipped_pages': mobile_skipped_pages,
        'mobile_skipped': mobile_skipped_pages > 0,
        # Real measured page weight. This used to be reported as 0 on every run.
        'page_size_kb_total': page_size_total,
        'page_size_kb_heaviest': page_size_max,
        'request_count': request_total,
        'unmeasured_resources': unmeasured_total,
        'steps_total': steps_total,
        'steps_unreadable': steps_unreadable,
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


def normalize_crawl_url(url):
    """
    Canonical form for crawl-queue membership: lowercase host, no fragment,
    no trailing slash (the site root keeps no slash either, matching the
    seed queue, so https://site and https://site/ are one page).

    Without this, https://site/Page and https://site/page/ queue as two
    different pages and the crawl spends its page budget visiting the same
    content twice.
    """
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or '').lower()
        if not host:
            return url
        path = parsed.path or ''
        if path == '/':
            path = ''
        elif len(path) > 1:
            path = path.rstrip('/')
        clean = f"{parsed.scheme}://{host}{path}"
        if parsed.query:
            clean += f"?{parsed.query}"
        return clean
    except Exception:
        return url


# A path segment that is an ID rather than a named route: pure digits, a
# long hex token, a UUID, or a long alphanumeric token containing a digit
# (product ASINs, database ids, session-ish slugs). Named slugs like
# "user-login" contain no digit and are left alone.
_ID_SEGMENT = re.compile(
    r'^(?:\d+|[0-9a-f]{8,}(?:-[0-9a-f]{4,})*|(?=.*\d)[A-Za-z0-9_-]{8,})$', re.I)

# How many pages sharing one URL pattern a crawl will visit. Listing and
# product sites otherwise spend the entire page budget on near-duplicate
# pages (/dp/ASIN1, /dp/ASIN2, ...) and max_pages stops meaning anything.
PATTERN_PAGES_CAP = int(os.getenv('QA_PATTERN_CAP', '10'))


def crawl_pattern(url):
    """
    The URL with ID-like path segments collapsed to '*', so near-duplicate
    pages (/products/849201, /products/849202) count as one pattern.
    Used to cap how much of the page budget a single listing can consume.
    """
    try:
        parsed = urlparse(url)
        segs = []
        for part in (parsed.path or '/').split('/'):
            if part and _ID_SEGMENT.match(part):
                segs.append('*')
            elif part:
                segs.append(part.lower())
            else:
                segs.append(part)
        return f"{(parsed.netloc or '').lower()}/{'/'.join(segs)}"
    except Exception:
        return url


def discover_internal_links(page, base_url, max_pages):
    discovered = set()
    base_parsed = urlparse(base_url)
    base_domain = (base_parsed.netloc or '').lower()

    try:
        links = page.query_selector_all('a[href]')
        for link in links:
            try:
                href = link.get_attribute('href')
                if not href:
                    continue
                href = href.strip()
                if href.startswith(('#', 'javascript:', 'mailto:', 'tel:', 'sms:',
                                     'data:', 'blob:')):
                    continue
                if href.startswith('/'):
                    full_url = f"{base_parsed.scheme}://{base_parsed.netloc}{href}"
                elif re.match(r'^[a-z][a-z0-9+.-]*:', href, re.I) and \
                        not href.lower().startswith(('http://', 'https://')):
                    continue                    # some other scheme (ftp:, file:, ...)
                elif href.lower().startswith(('http://', 'https://')):
                    full_url = href
                else:
                    full_url = urljoin(base_url, href)

                link_parsed = urlparse(full_url)
                if (link_parsed.netloc or '').lower() != base_domain:
                    continue

                lower = full_url.lower()
                if any(lower.endswith(ext) for ext in ['.pdf', '.zip', '.exe', '.dmg', '.tar', '.gz',
                                                        '.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp',
                                                        '.mp4', '.mp3', '.avi', '.mov',
                                                        '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']):
                    continue

                if any(keyword in lower for keyword in ['/logout', '/signout', '/sign-out', '/log-out']):
                    continue

                clean_url = normalize_crawl_url(full_url)
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
                     page_load_ms=0, api_responses=None, is_mobile=False,
                     mobile_checked=None, page_status=0, session_used=False):
    """
    Run every check against one loaded page.

    Returns (findings_by_category, coverage). `coverage` records what was
    capped or skipped so the report can say so out loud instead of letting
    a partial check look like a clean bill of health, and carries the real
    performance metrics for this page.

    Mobile tap-target auditing only means something in a mobile-width
    viewport, so it runs only when `mobile_checked` is true (anonymous
    crawls use a mobile viewport; authenticated crawls use desktop to keep
    the session valid and skip it with an explicit reason). `is_mobile` is
    the legacy spelling of the same flag.
    """
    if mobile_checked is None:
        mobile_checked = bool(is_mobile)
    broken_links, link_coverage = check_broken_links(page, page_url)
    missing_alt = check_missing_alt(page)
    seo_issues = check_seo(page, page_url)
    security_issues = check_security(page_url, response_headers, page)
    accessibility_issues, a11y_engine = check_accessibility(page)

    if mobile_checked:
        mobile_issues, mobile_coverage = check_mobile(page)
        mobile_skipped_reason = None
    else:
        mobile_issues, mobile_coverage = [], {}
        mobile_skipped_reason = 'disabled: page was audited in a desktop viewport'

    performance_issues, perf_metrics = check_performance(page, page_load_ms)
    api_issues = check_api_responses(api_responses)
    try:
        api_issues = (api_issues or []) + check_api_quality(api_responses, page_url)
    except Exception as e:
        logger.warning("extended api check error: %s", e)
    validation_issues = (
        check_form_validation(page) +
        check_generated_form_validation(page)
    )
    security_issues = (
        security_issues +
        check_authentication_access(page, page_url, session_used=session_used)
    )
    important_page_issues = check_important_page_links(page, page_url)
    try:
        if urlparse(page_url).path.rstrip('/') == '':
            important_page_issues += check_important_page_presence(page_url)
    except Exception as e:
        logger.warning("important page presence check error: %s", e)
    # Important-page availability is part of the SEO/site-completeness report
    # so it is persisted and scored by the existing schema.
    seo_issues = (seo_issues or []) + important_page_issues
    try:
        url_issues = check_url_health(page_url, page_status, response_headers)
    except Exception as e:
        logger.warning("url health check error: %s", e)
        url_issues = []
    try:
        resource_findings, resource_coverage = check_page_resources(page, page_url)
    except Exception as e:
        logger.warning("resource check error: %s", e)
        resource_findings, resource_coverage = [], {}
    # Deep resource problems are still broken links, so they share the
    # category and its weight rather than inventing a new score bucket.
    broken_links = (broken_links or []) + (resource_findings or [])
    link_coverage = {**(link_coverage or {}), 'resources': resource_coverage}
    try:
        ui_issues = check_ui_consistency(page)
    except Exception as e:
        logger.warning("ui consistency check error: %s", e)
        ui_issues = []
    try:
        code_issues = check_code_quality(page)
    except Exception as e:
        logger.warning("code quality check error: %s", e)
        code_issues = []

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
        'api_issues': api_issues,
        'validation_issues': validation_issues,
        'url_issues': url_issues,
        'ui_issues': ui_issues,
        'code_issues': code_issues,
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
                # Every automated finding is labelled at the source so the
                # UI, reports and PDFs can filter MANUAL vs AUTOMATED.
                item.setdefault('source', 'AUTOMATED')
    return findings_dict


def _is_allowed_test_url(url):
    """SSRF/unsafe-navigation guard: only http(s) URLs without credentials.

    DNS-level check: a hostname that resolves to a private, loopback,
    link-local, multicast or otherwise reserved IP is rejected, because a
    crawler that fetches it can reach cloud metadata endpoints
    (169.254.169.254) or the internal network. Hosts listed in
    QA_SSRF_ALLOWLIST (default: localhost, 127.0.0.1, ::1 for local dev)
    are exempt, and QA_ALLOW_PRIVATE_HOSTS=1 disables the DNS check
    entirely (never set that in production).

    A DNS failure fails OPEN (the name cannot be proven private) so offline
    dev and unit tests with fake hosts keep working; a successful resolution
    to a non-public IP fails CLOSED.
    """
    try:
        parsed = urlparse(url or '')
        if parsed.scheme not in ('http', 'https'):
            return False, 'URL must start with http:// or https://'
        if not parsed.netloc:
            return False, 'URL has no host'
        if parsed.username or parsed.password:
            return False, 'URLs with embedded credentials are not allowed'
        host = (parsed.hostname or '').lower()
        if _is_ssrf_exempt_host(host):
            return True, ''
        try:
            resolved = socket.getaddrinfo(host, parsed.port or 80, type=socket.SOCK_STREAM)
        except Exception:
            return True, ''      # cannot prove private: allow, scheme checks held
        for family, _type, _proto, _canon, sockaddr in resolved:
            ip = sockaddr[0]
            try:
                addr = ipaddress.ip_address(ip)
            except ValueError:
                continue
            if (addr.is_private or addr.is_loopback or addr.is_link_local
                    or addr.is_multicast or addr.is_reserved or addr.is_unspecified):
                return False, (f'Host {host} resolves to non-public IP {ip}; '
                               'internal targets are not allowed')
        return True, ''
    except Exception:
        return False, 'URL could not be parsed'


def _is_ssrf_exempt_host(host):
    """Local-dev allowlist for the SSRF DNS guard."""
    extra = os.getenv('QA_SSRF_ALLOWLIST', '')
    allowed = {'localhost', '127.0.0.1', '::1'}
    for item in extra.split(','):
        item = item.strip().lower()
        if item:
            allowed.add(item)
    if os.getenv('QA_ALLOW_PRIVATE_HOSTS') == '1':
        return True
    return host in allowed


def _verdict_reason(status, issues_by_severity, health_score):
    """Human-readable reason behind an automated Pass/Fail."""
    crit = issues_by_severity.get('critical', 0)
    if status == 'Pass':
        return f'No issues found (health {health_score}/100).'
    if crit:
        return f'{crit} critical issue(s) found (health {health_score}/100).'
    total = sum(issues_by_severity.values())
    return f'{total} issue(s) found (health {health_score}/100).'


# A stable identity per finding, used both to de-duplicate the same defect
# across pages and to tell one run's findings from the previous run's.
# It must not include anything volatile (screenshots, timings, page order)
# or the same defect would look new on every run.
FINDING_KEYERS = {
    'execution_errors': lambda i: i.get('issue') or i.get('error'),
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
    'api_issues': lambda i: i.get('url') or i.get('issue'),
    'validation_issues': lambda i: i.get('form_index') or i.get('issue'),
    'url_issues': lambda i: i.get('issue'),
    'ui_issues': lambda i: i.get('element_selector') or i.get('issue'),
    'code_issues': lambda i: i.get('issue'),
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
    ok, reason = _is_allowed_test_url(tc.get('base_url'))
    if not ok:
        return jsonify({'error': reason, 'code': 'invalid_url'}), 400

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
    ok, reason = _is_allowed_test_url(tc.get('base_url'))
    if not ok:
        return jsonify({'error': reason, 'code': 'invalid_url'}), 400

    job_id = job_manager.submit(user_id, testcase_id, dict(tc))
    return jsonify({'job_id': job_id, 'status': 'queued'}), 202


@runner_bp.route('/run/<int:testcase_id>/retry', methods=['POST'])
@jwt_required()
def run_test_retry(testcase_id):
    """Retry a failed run: re-queues the same test case as a fresh job."""
    user_id = int(get_jwt_identity())
    tc = user_owns_testcase(user_id, testcase_id)
    if not tc:
        return jsonify({'error': 'Test case not found'}), 404
    if tc['test_type'] != 'automated':
        return jsonify({'error': 'Only automated test cases can be run'}), 400
    ok, reason = _is_allowed_test_url(tc.get('base_url'))
    if not ok:
        return jsonify({'error': reason, 'code': 'invalid_url'}), 400
    job_id = job_manager.submit(user_id, testcase_id, dict(tc))
    return jsonify({'job_id': job_id, 'status': 'queued', 'retried': True}), 202


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
    ok, reason = _is_allowed_test_url(tc.get('base_url'))
    if not ok:
        return jsonify({'error': reason, 'code': 'invalid_url'}), 400
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
    if not session.mark_step(index, status, data.get('note', ''),
                             data.get('issue_ref')):
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
        description=data.get('description', ''),
        expected_result=data.get('expected_result', ''),
        actual_result=data.get('actual_result', ''),
        browser_device=(request.headers.get('User-Agent') or '')[:255],
    )
    if entry is None:
        return jsonify({'error': 'Could not record the issue'}), 400
    return jsonify({'issue': entry, 'run': session.snapshot()}), 201


@runner_bp.route('/manual/<int:testcase_id>/pause', methods=['POST'])
@jwt_required()
def manual_pause(testcase_id):
    """Pause or resume a live manual session without closing the browser."""
    user_id = int(get_jwt_identity())
    if not user_owns_testcase(user_id, testcase_id):
        return jsonify({'error': 'Test case not found'}), 404
    data = request.json or {}
    ok, message, snap = manual_run.pause(testcase_id, bool(data.get('paused', True)))
    if not ok:
        return jsonify({'error': message}), 404
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
    expected_met = data.get('expected_met')

    if expected_met is False:
        outcome = 'Fail'

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

    if snap.get('expected_met') is False:
        functional.append({
            'issue': 'Expected result was not met',
            'severity': 'critical',
            'reason': snap.get('expected_result', ''),
            'source': 'tester',
            'display': 'Expected result was not met',
        })

    # Everything the tester spotted by eye, in the same shape as any finding.
    # source is always MANUAL here so dashboards/reports can filter it.
    reported = [
        {
            'issue': r['title'],
            'severity': r['severity'],
            'category': r['category'],
            'description': r.get('description', r.get('note', '')),
            'note': r.get('note', ''),
            'expected_result': r.get('expected_result', ''),
            'actual_result': r.get('actual_result', ''),
            'page_url': r.get('page_url', ''),
            'url': r.get('url', r.get('page_url', '')),
            'screenshot': r.get('screenshot', ''),
            'browser_device': r.get('browser_device', ''),
            'step_index': r.get('step_index'),
            'timestamp': r.get('timestamp', r.get('reported_at', '')),
            'source': 'MANUAL',
            'display': f'[{r["category"]}] {r["title"]}',
        }
        for r in (snap.get('reported') or [])
    ]

    automatic = {}
    for finding in (snap.get('auto_findings') or []):
        if not isinstance(finding, dict):
            continue
        finding.setdefault('source', 'AUTOMATED')
        category = finding.get('category', 'other')
        automatic.setdefault(category, []).append(finding)

    issues_found = len(functional) + len(reported) + len(snap.get('auto_findings') or [])
    # Manual verdict depends ONLY on the tester's steps, expected-result
    # decision and reported issues. Automated evidence is stored alongside
    # but never overrides it (see manual_run.summarise_manual).
    if snap.get('expected_met') is False:
        verdict_reason = 'Tester marked the expected result as not met.'
    elif any(s['status'] == 'failed' for s in (snap.get('steps') or [])):
        n = sum(1 for s in (snap.get('steps') or []) if s['status'] == 'failed')
        verdict_reason = f'{n} manual step(s) failed.'
    elif reported:
        verdict_reason = f'{len(reported)} issue(s) reported by the tester.'
    elif outcome == 'Pass':
        verdict_reason = 'All checked steps passed and no issues were reported.'
    else:
        verdict_reason = 'Tester marked the run as failed.'

    findings_evidence = json.dumps({
        'manual': True,
        'run_type': 'MANUAL',
        'execution_mode': 'HEADED',
        'source': 'HYBRID' if automatic else 'HUMAN',
        'note': note,
        'pages': pages,
        'steps': snap.get('steps') or [],
        'functional_issues': functional,
        'reported': reported,
        **automatic,
        'expected_result': snap.get('expected_result', ''),
        'expected_met': snap.get('expected_met'),
        'summary': manual_summary,
        'verdict_reason': verdict_reason,
    })

    manual_findings = {'functional_issues': functional}
    for category, items in automatic.items():
        manual_findings.setdefault(category, []).extend(items)
    manual_score = calculate_health_score(manual_findings)
    run_source = 'HYBRID' if automatic else 'HUMAN'
    manual_worst = worst_severity({
        'functional_issues': functional,
        'reported_issues': reported,
        **automatic,
    })

    try:
        cursor = mysql.connection.cursor()
        cursor.execute("SELECT 1 FROM test_cases WHERE id = %s", (testcase_id,))
        if cursor.fetchone() is None:
            cursor.close()
            return jsonify({'error': 'Test case was deleted before the run finished.'}), 410

        try:
            cursor.execute(
                """INSERT INTO test_runs (
                    test_case_id, status, screenshot, run_at, duration_ms,
                    issues_found, total_requests, findings_evidence,
                    functional_issues, steps_result, health_score,
                    run_type, execution_mode, source, verdict_reason, worst_severity
                ) VALUES (%s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    testcase_id, outcome,
                    first_shot,
                    duration_ms,
                    issues_found, len(pages),
                    findings_evidence,
                    json.dumps([f['display'] for f in functional]),
                    json.dumps({'results': snap.get('steps') or [],
                                'summary': manual_summary}),
                    manual_score,
                    'MANUAL', 'HEADED', run_source, verdict_reason[:500],
                    manual_worst,
                )
            )
        except Exception:
            # Older database without the new columns: fall back to the
            # original insert so manual runs keep working pre-migration.
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
                    manual_score,
                )
            )
        run_id = cursor.lastrowid
        # Per-step evidence rows (best-effort: table may not exist pre-migration).
        try:
            for s in (snap.get('steps') or []):
                cursor.execute(
                    """INSERT INTO manual_step_evidence (
                        test_run_id, test_case_id, step_index, step_text,
                        result, note, screenshot, issue_ref
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (run_id, testcase_id, s.get('index', 0),
                     (s.get('text') or '')[:1000], s.get('status', 'pending'),
                     (s.get('note') or '')[:2000],
                     (s.get('screenshot') or '')[:500],
                     str(s.get('issue_ref') or '')[:100] or None),
                )
        except Exception as e:
            logger.warning("manual step evidence insert skipped: %s", e)
        # Manual issue rows (best-effort pre-migration).
        try:
            for r in reported:
                cursor.execute(
                    """INSERT INTO manual_issues (
                        test_run_id, test_case_id, title, description, category,
                        severity, expected_result, actual_result, url, screenshot,
                        browser_device, step_index, source
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (run_id, testcase_id, r['issue'][:255],
                     r.get('description', '')[:2000], r.get('category', 'other'),
                     r.get('severity', 'moderate'),
                     r.get('expected_result', '')[:2000],
                     r.get('actual_result', '')[:2000],
                     (r.get('url') or '')[:2000],
                     (r.get('screenshot') or '')[:500],
                     (r.get('browser_device') or '')[:255],
                     r.get('step_index'), 'MANUAL'),
                )
        except Exception as e:
            logger.warning("manual issues insert skipped: %s", e)
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
        'run_type': 'MANUAL',
        'execution_mode': 'HEADED',
        'source': run_source,
        'verdict_reason': verdict_reason,
        'worst_severity': manual_worst,
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
                 AND status != 'Cancelled'
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


def build_flaky_summary(statuses):
    """Classify inconsistent recent outcomes without calling deterministic failures flaky."""
    clean = [status for status in statuses if status in ('Pass', 'Fail')]
    if len(clean) < 4 or len(set(clean)) < 2:
        return {
            'is_flaky': False,
            'sample_size': len(clean),
            'pass_count': clean.count('Pass'),
            'fail_count': clean.count('Fail'),
            'pass_rate': round(clean.count('Pass') / len(clean) * 100) if clean else None,
        }
    return {
        'is_flaky': True,
        'sample_size': len(clean),
        'pass_count': clean.count('Pass'),
        'fail_count': clean.count('Fail'),
        'pass_rate': round(clean.count('Pass') / len(clean) * 100),
        'summary': f"Outcome changed across {len(clean)} recent runs "
                   f"({clean.count('Pass')} passed, {clean.count('Fail')} failed).",
    }


def _recent_flaky_summary(testcase_id, current_status):
    """Load a small status history so reports can flag unstable tests."""
    try:
        cursor = mysql.connection.cursor()
        cursor.execute(
            """SELECT status FROM test_runs
               WHERE test_case_id = %s AND status IN ('Pass', 'Fail')
               ORDER BY id DESC LIMIT 9""",
            (testcase_id,),
        )
        statuses = [row.get('status') for row in cursor.fetchall()]
        cursor.close()
        return build_flaky_summary([current_status] + statuses)
    except Exception as e:
        logger.warning("could not calculate flaky status: %s", e)
        return build_flaky_summary([current_status])


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
    # Off unless this test case opted in: the check clicks real controls.
    click_controls = bool(tc.get('check_dead_controls'))
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
    _report(phase='starting', status='starting', tested=0, total=max_pages,
            pages_discovered=0, pages_tested=0, current_page=base_url,
            current_category='launch', duration_ms=0)

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                logger.exception("browser launch failed: %s", e)
                return {
                    'error': f'Browser failed to launch: {str(e)[:300]}',
                    'code': 'browser_launch_failure',
                    'status': 'Fail',
                    'pages_crawled': 0,
                }, 500
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
            to_visit = [normalize_crawl_url(u) for u in
                        build_crawl_queue(base_url, landing_url=landing,
                                          session_used=session_used)]
            queued = set(to_visit)
            # Pages per URL pattern already committed to by the seed queue,
            # so discovery below cannot spend the budget on near-duplicates
            # of a page we already plan to visit (e.g. /dp/ASIN1 ... /dp/ASIN99).
            pattern_counts = {}
            for seed in to_visit:
                pat = crawl_pattern(seed)
                pattern_counts[pat] = pattern_counts.get(pat, 0) + 1
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
                    landed = normalize_crawl_url(
                        warm.url.rstrip('/').split('#')[0])
                    if landed and not looks_like_login(landed):
                        if landed not in queued:
                            # Front of the queue so the crawl starts from the
                            # authenticated landing page, not the login URL.
                            to_visit.insert(0, landed)
                            queued.add(landed)
                            pat = crawl_pattern(landed)
                            pattern_counts[pat] = pattern_counts.get(pat, 0) + 1
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
            api_seen = set()    # every XHR/fetch URL observed during the crawl

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
                    'page_url': crawl_page.url,
                    'source_location': msg.location,
                    'display': f"[{msg.type}] {msg.text[:200]}"
                }) if msg.type in ('error', 'warning') else None
            )
            api_responses = []
            _api_start = {}

            def _on_request(request):
                try:
                    if request.resource_type in ('xhr', 'fetch'):
                        _api_start[request.url] = time.time()
                except Exception:
                    pass

            def _on_response(response):
                try:
                    if response.request.resource_type not in ('xhr', 'fetch'):
                        return
                    started = _api_start.pop(response.url, None)
                    elapsed = int((time.time() - started) * 1000) if started else None
                    try:
                        headers = response.headers or {}
                    except Exception:
                        headers = {}
                    ctype = ''
                    try:
                        for k, v in headers.items():
                            if str(k).lower() == 'content-type':
                                ctype = v
                                break
                    except Exception:
                        pass
                    try:
                        method = response.request.method
                    except Exception:
                        method = ''
                    api_responses.append({
                        'url': response.url,
                        'status': response.status,
                        'method': method,
                        'elapsed_ms': elapsed,
                        'content_type': ctype,
                    })
                except Exception:
                    pass

            crawl_page.on('request', _on_request)
            crawl_page.on('response', _on_response)

            page_idx = -1
            was_cancelled = False
            while to_visit and len(per_page_records) < max_pages:
                # User clicked Cancel — stop the crawl and persist whatever we
                # already captured. Treated as a graceful exit, not an error.
                if _is_cancelled():
                    logger.info("crawl cancelled by user at page %d/%d",
                                len(per_page_records), max_pages)
                    was_cancelled = True
                    break
                page_url = to_visit.pop(0)
                page_idx += 1
                urls_to_test.append(page_url)

                console_messages.clear()         # per-URL console capture
                api_responses.clear()            # per-URL API capture
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

                    page_findings, page_coverage = test_single_page(                        page, page_url, response_headers,
                        console_messages,
                        page_load_ms=page_load_ms,
                        api_responses=api_responses,
                        # Anonymous crawls run in a mobile viewport, so the
                        # mobile checks measure something real there.
                        # Authenticated crawls run desktop (session validity
                        # depends on the desktop UA), where they would be noise.
                        mobile_checked=not session_used,
                        page_status=page_status,
                        session_used=session_used)
                    if page_idx == 0:
                        page_findings['functional_issues'] = step_findings
                        page_coverage['steps'] = step_summary

                    # A control wired to nothing is a functional defect, so it
                    # belongs with the workflow failures rather than in a
                    # category of its own.
                    if click_controls:
                        dead, dead_coverage = check_dead_controls(page, page_url)
                        page_findings['functional_issues'] = (
                            page_findings.get('functional_issues') or []) + dead
                        page_coverage['dead_controls'] = dead_coverage
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

                    # Discover further internal links from this rendered page and
                    # enqueue any we haven't seen (BFS), until the queue holds enough
                    # to reach max_pages. Done before closing the page.
                    # Near-duplicate URL patterns (product listings, paginated
                    # indexes) are capped so one section cannot eat the whole
                    # page budget while the rest of the site goes untested.
                    if crawl_pages and len(queued) < max_pages * 4:
                        try:
                            for link in discover_internal_links(page, base_url, max_pages):
                                norm = normalize_crawl_url(link)
                                if norm in queued:
                                    continue
                                pat = crawl_pattern(norm)
                                if pattern_counts.get(pat, 0) >= PATTERN_PAGES_CAP:
                                    continue
                                pattern_counts[pat] = pattern_counts.get(pat, 0) + 1
                                queued.add(norm)
                                to_visit.append(norm)
                        except Exception as e:
                            logger.warning("crawl link-discovery error: %s", e)

                    page_issues_found = sum(len(items) for items in page_findings.values())
                    try:
                        for r in api_responses or []:
                            if r.get('url'):
                                api_seen.add(r['url'])
                    except Exception:
                        pass
                    # Steps only ever run on the first page, so every later
                    # page would otherwise collect the functional weight in
                    # full for a check that never happened there.
                    page_health_score = calculate_health_score(
                        page_findings,
                        measured=measured_categories(
                            step_summary if page_idx == 0 else None,
                            mobile_checked=not session_used,
                            accessibility_measured=(
                                page_coverage.get('accessibility_engine')
                                == 'axe-core'),
                            controls_checked=click_controls))

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
                    _report(phase='crawling', status='running',
                            tested=len(per_page_records), total=max_pages,
                            pages_discovered=len(queued) + len(per_page_records),
                            pages_tested=len(per_page_records),
                            current=page_url, current_page=page_url,
                            current_category='page-audit',
                            issues_so_far=page_issues_found,
                            queued=len(to_visit),
                            duration_ms=int((time.time() - start_time) * 1000))
                    # NOTE: do NOT page.close() here. The crawl page is reused
                    # across every URL so React stays mounted and the SPA's auth
                    # context survives. It is closed after the BFS loop.

                except Exception as e:
                    logger.warning("page test error %s: %s", page_url, e)
                    audit_error = {
                        'issue': 'Page audit could not complete',
                        'severity': 'serious',
                        'error': str(e)[:500],
                        'element_selector': 'page',
                        'display': 'Page audit could not complete; results are incomplete',
                        'page_url': page_url,
                    }
                    all_pages_findings.append({'execution_errors': [audit_error]})
                    all_pages_coverage.append({'audit_error': True})
                    per_page_records.append({
                        'url': page_url,
                        'page_title': page_title or '',
                        'status_code': page_status or 0,
                        'health_score': 1,
                        'issues_found': 1,
                        'page_load_time_ms': page_load_ms,
                        'screenshot': '',
                        'findings_evidence': json.dumps({'execution_errors': [audit_error]}),
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

            # axe running on even one page means the accessibility findings
            # are real; it is only when it never ran that the number is empty
            # for the wrong reason.
            axe_ran = any((c or {}).get('accessibility_engine') == 'axe-core'
                          for c in all_pages_coverage)
            health_score = calculate_health_score(
                merged_findings,
                measured=measured_categories(step_summary,
                                             mobile_checked=not session_used,
                                             accessibility_measured=axe_ran,
                                             controls_checked=click_controls))
            breakdown = score_breakdown(merged_findings)
            coverage_summary = summarise_coverage(all_pages_coverage)
            top_severity = worst_severity(merged_findings)
            # Compare against the previous run of this same test case so the
            # user sees what they broke and what they fixed, not just a total.
            prev_findings, prev_score = _previous_run_findings(testcase_id)
            regression = build_regression(prev_findings, merged_findings,
                                          prev_score, health_score)
            issues_found = sum(len(items) for items in merged_findings.values())
            # A cancelled crawl is not a verdict on the site: it records what
            # was captured so far as 'Cancelled' and leaves the test case's
            # status alone, instead of stamping Pass/Fail on a partial audit.
            status = 'Cancelled' if was_cancelled else ('Pass' if issues_found == 0 else 'Fail')
            regression['flaky'] = _recent_flaky_summary(testcase_id, status)
            # A Pass on a partial audit is not a full all-clear. We keep the
            # Pass/Fail meaning simple and let coverage_summary['notes'] carry
            # the caveat about anything that was capped or skipped.
            duration_ms = int((time.time() - start_time) * 1000)

            # Severity totals for the verdict and the results panel.
            by_severity = {sev: 0 for sev in SEVERITY_ORDER}
            for category, items in merged_findings.items():
                for f in items or []:
                    by_severity[finding_severity(category, f)] = \
                        by_severity.get(finding_severity(category, f), 0) + 1
            verdict_reason = ('Cancelled by the tester before the crawl finished.'
                              if was_cancelled else
                              _verdict_reason(status, by_severity, health_score))
            links_found = sum((c.get('links') or {}).get('links_found', 0)
                              for c in all_pages_coverage)
            links_checked = sum((c.get('links') or {}).get('links_checked', 0)
                                for c in all_pages_coverage)
            api_tested = len(api_seen)
            _report(phase='done', status='done', tested=len(per_page_records),
                    total=max_pages, pages_discovered=len(queued) + len(per_page_records),
                    pages_tested=len(per_page_records), duration_ms=duration_ms)

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

            try:
                cursor.execute(
                    """INSERT INTO test_runs (
                        test_case_id, status, screenshot, run_at, duration_ms,
                        console_errors, broken_links, missing_alt_images, page_load_time_ms,
                        issues_found, total_page_size_kb, total_requests,
                        seo_issues, security_issues, accessibility_issues, mobile_issues,
                        performance_issues, functional_issues, steps_result,
                        findings_evidence, score_breakdown,
                        coverage, regression, health_score,
                        run_type, execution_mode, source, verdict_reason, worst_severity
                    ) VALUES (%s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        testcase_id, status,
                        first_page.get('screenshot', ''),
                        duration_ms,
                        json.dumps(display_findings.get('console_errors', [])),
                        json.dumps(display_findings.get('broken_links', [])),
                        json.dumps(display_findings.get('missing_alt_images', [])),
                        page_load_ms,
                        issues_found,
                        coverage_summary['page_size_kb_total'],
                        len(urls_to_test),
                        json.dumps(display_findings.get('seo_issues', [])),
                        json.dumps(display_findings.get('security_issues', [])),
                        json.dumps(display_findings.get('accessibility_issues', [])),
                        json.dumps(display_findings.get('mobile_issues', [])),
                        json.dumps(display_findings.get('performance_issues', [])),
                        json.dumps(display_findings.get('functional_issues', [])),
                        json.dumps({'results': step_results, 'summary': step_summary}),
                        json.dumps(merged_findings),
                        json.dumps(breakdown),
                        json.dumps(coverage_summary),
                        json.dumps(regression),
                        health_score,
                        'AUTOMATED', 'HEADLESS', 'MACHINE', verdict_reason[:500],
                        top_severity,
                    )
                )
            except Exception:
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
                        json.dumps(display_findings.get('console_errors', [])),
                        json.dumps(display_findings.get('broken_links', [])),
                        json.dumps(display_findings.get('missing_alt_images', [])),
                        page_load_ms,
                        issues_found,
                        coverage_summary['page_size_kb_total'],
                        len(urls_to_test),
                        json.dumps(display_findings.get('seo_issues', [])),
                        json.dumps(display_findings.get('security_issues', [])),
                        json.dumps(display_findings.get('accessibility_issues', [])),
                        json.dumps(display_findings.get('mobile_issues', [])),
                        json.dumps(display_findings.get('performance_issues', [])),
                        json.dumps(display_findings.get('functional_issues', [])),
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

            if not was_cancelled:
                cursor.execute(
                    "UPDATE test_cases SET status = %s WHERE id = %s",
                    (status, testcase_id)
                )
                mysql.connection.commit()
            cursor.close()

            return {
                'run_id': run_id,
                'run_type': 'AUTOMATED',
                'execution_mode': 'HEADLESS',
                'source': 'MACHINE',
                'verdict_reason': verdict_reason,
                'worst_severity': top_severity,
                'status': status,
                'health_score': health_score,
                'issues_found': issues_found,
                'issues_by_severity': by_severity,
                'critical_issues': by_severity.get('critical', 0),
                'major_issues': by_severity.get('serious', 0),
                'minor_issues': by_severity.get('moderate', 0) + by_severity.get('minor', 0),
                'duration_ms': duration_ms,
                'page_load_time_ms': page_load_ms,
                'total_page_size_kb': coverage_summary['page_size_kb_total'],
                'total_requests': len(urls_to_test),
                'screenshot': first_page.get('screenshot', ''),
                'pages_tested': len(urls_to_test),
                'pages_crawled': len(per_page_records),
                'pages_discovered': len(queued) + len(per_page_records),
                'links_found': links_found,
                'links_checked': links_checked,
                'links_tested': links_checked,
                'api_endpoints_tested': api_tested,
                'console_error_count': len(merged_findings.get('console_errors', [])),
                'accessibility_violations': len(merged_findings.get('accessibility_issues', [])),
                'seo_issue_count': len(merged_findings.get('seo_issues', [])),
                'security_issue_count': len(merged_findings.get('security_issues', [])),
                'performance_issue_count': len(merged_findings.get('performance_issues', [])),
                'ui_issue_count': len(merged_findings.get('ui_issues', [])),
                'code_issue_count': len(merged_findings.get('code_issues', [])),
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
                'api_issues': display_findings['api_issues'],
                'validation_issues': display_findings['validation_issues'],
            }, 200

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        error_msg = str(e)[:500]
        logger.exception("RUNNER ERROR: %s", error_msg)

        try:
            cursor = mysql.connection.cursor()
            try:
                cursor.execute(
                    """INSERT INTO test_runs (
                        test_case_id, status, run_at, duration_ms, error_message,
                        health_score, run_type, execution_mode, source, verdict_reason,
                        worst_severity
                    ) VALUES (%s, 'Fail', NOW(), %s, %s, 0, %s, %s, %s, %s, %s)""",
                    (testcase_id, duration_ms, error_msg, 'AUTOMATED', 'HEADLESS',
                     'MACHINE', f'Crawler failure: {error_msg}'[:500], 'serious')
                )
            except Exception:
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

        return {'error': error_msg, 'code': 'crawler_failure', 'status': 'Fail'}, 500


@runner_bp.route('/runs/<int:testcase_id>', methods=['GET'])
@jwt_required()
def get_runs(testcase_id):
    user_id = int(get_jwt_identity())
    tc = user_owns_testcase(user_id, testcase_id)
    if not tc:
        return jsonify({'error': 'Test case not found'}), 404

    try:
        cursor = mysql.connection.cursor()
        try:
            cursor.execute(
                """SELECT id, status, screenshot, run_at, duration_ms, page_load_time_ms,
                          issues_found, total_page_size_kb, total_requests, health_score,
                          error_message, run_type, execution_mode, source, verdict_reason,
                          worst_severity
                   FROM test_runs WHERE test_case_id = %s
                   ORDER BY run_at DESC LIMIT 20""",
                (testcase_id,)
            )
        except Exception:
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
            run.setdefault('run_type', 'AUTOMATED')
            run.setdefault('execution_mode', 'HEADLESS')
            run.setdefault('source', 'MACHINE')
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
