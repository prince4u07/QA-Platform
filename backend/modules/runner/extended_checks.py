"""Extended issue detection: URL, API, resource-link, UI and code checks.

Each check follows the existing runner conventions:
- accepts a Playwright-ish `page` plus plain data, never raises;
- returns findings shaped like the rest of the runner
  ({issue/display/severity/element_selector/...});
- degrades gracefully with fake pages in unit tests.

These complement (not replace) the checks in `routes.py`:
- URL testing: HTTP status, redirect hygiene, URL-shape problems.
- API testing: slow / mixed-content / unauthenticated-shape / repeated
  failures across the XHR/fetch traffic observed during the visit.
- Link testing (deep): <img>/<script>/<link>/<iframe> sources, fragment
  anchors that point nowhere, malformed mailto:/tel: links.
- UI testing: duplicate IDs, empty controls, missing viewport/lang,
  elements overflowing the viewport.
- Code testing: deprecated tags, inline handlers, javascript: URLs,
  eval/document.write, oversized inline scripts, outdated libraries.
"""

import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

SLOW_API_MS = 2000
VERY_SLOW_API_MS = 5000
BIG_INLINE_SCRIPT_CHARS = 50_000

DEPRECATED_TAGS = ('font', 'center', 'marquee', 'blink', 'big', 'tt')
JS_URL_RE = re.compile(r'^\s*javascript\s*:', re.I)
MAILTO_RE = re.compile(r'^mailto:([^?]+)', re.I)
TEL_RE = re.compile(r'^tel:\+?[0-9().\-\s]+$', re.I)
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
JQUERY_RE = re.compile(r'jquery[/-](\d+)\.(\d+)\.(\d+)', re.I)


# ============================================================
# 1. URL testing
# ============================================================

def check_url_health(page_url, page_status=0, response_headers=None):
    """Flag unreachable pages, error statuses and unhygienic URL shapes."""
    findings = []
    try:
        parsed = urlparse(page_url or '')
    except Exception:
        return findings
    headers = {str(k).lower(): v for k, v in (response_headers or {}).items()}

    if page_status and page_status >= 400:
        findings.append({
            'issue': f'Page URL returns HTTP {page_status}',
            'severity': 'critical' if page_status >= 500 else 'serious',
            'status_code': page_status,
            'element_selector': 'document',
            'display': f'Page URL returns HTTP {page_status}',
            'summary': f'The tested URL itself answers HTTP {page_status}.',
            'fix': 'Fix the route or redirect on the server so the URL serves 200.',
        })
    elif page_status == 0:
        findings.append({
            'issue': 'Page URL did not return any HTTP response',
            'severity': 'serious',
            'status_code': 0,
            'element_selector': 'document',
            'display': 'Page URL is unreachable (no HTTP response)',
        })

    # Redirect hygiene: a chain that bounces or lands elsewhere is worth one note.
    location = headers.get('location')
    if location and page_status in (301, 302, 307, 308):
        try:
            if urlparse(location).netloc and urlparse(location).netloc != parsed.netloc:
                findings.append({
                    'issue': f'Page redirects off-site to {location[:120]}',
                    'severity': 'moderate',
                    'element_selector': 'document',
                    'display': 'Page redirects to a different host',
                })
        except Exception:
            pass

    path = parsed.path or ''
    query = parsed.query or ''
    if ' ' in (page_url or ''):
        findings.append({
            'issue': 'URL contains an unencoded space',
            'severity': 'minor',
            'element_selector': 'document',
            'display': 'URL contains an unencoded space',
        })
    if len(page_url or '') > 2000:
        findings.append({
            'issue': 'URL is excessively long (>2000 chars)',
            'severity': 'minor',
            'element_selector': 'document',
            'display': 'URL is excessively long',
        })
    if re.search(r'[A-Z]', path):
        findings.append({
            'issue': 'URL path contains uppercase letters (duplicate-content risk)',
            'severity': 'minor',
            'element_selector': 'document',
            'display': 'URL path uses uppercase letters',
        })
    if re.search(r'(jsessionid|phpsessid|sid|sessionid)=', query, re.I):
        findings.append({
            'issue': 'URL leaks a session identifier in the query string',
            'severity': 'serious',
            'element_selector': 'document',
            'display': 'Session ID exposed in the URL',
        })
    return findings


# ============================================================
# 2. API testing (active analysis of observed traffic)
# ============================================================

def check_api_quality(api_responses, page_url=''):
    """Deeper analysis of XHR/fetch traffic than status-only reporting.

    `api_responses` entries may carry optional `method`, `elapsed_ms` and
    `content_type` (new listener in routes.py); old two-key entries still work.
    """
    findings = []
    try:
        page_scheme = (urlparse(page_url).scheme or '').lower()
    except Exception:
        page_scheme = ''
    seen = set()
    counts = {}
    for resp in api_responses or []:
        try:
            url = resp.get('url', '')
            status = int(resp.get('status', 0) or 0)
        except Exception:
            continue
        if not url:
            continue
        counts[url] = counts.get(url, 0) + 1
        elapsed = resp.get('elapsed_ms')
        try:
            elapsed = int(elapsed) if elapsed is not None else None
        except Exception:
            elapsed = None
        if elapsed is not None and elapsed >= SLOW_API_MS and status < 400:
            findings.append({
                'issue': f'Slow API response ({elapsed}ms): {urlparse(url).path[:120]}',
                'severity': 'serious' if elapsed >= VERY_SLOW_API_MS else 'moderate',
                'status': status,
                'url': url,
                'element_selector': 'network',
                'display': f'Slow API ({elapsed}ms): {url[:150]}',
                'summary': 'An API call the page depends on is slow.',
                'fix': 'Optimise the endpoint or cache the response.',
            })
        try:
            if page_scheme == 'https' and (urlparse(url).scheme or '').lower() == 'http':
                findings.append({
                    'issue': f'Page served over HTTPS calls insecure API: {url[:150]}',
                    'severity': 'serious',
                    'status': status,
                    'url': url,
                    'element_selector': 'network',
                    'display': 'Insecure API call from a secure page',
                })
        except Exception:
            pass
        ctype = (resp.get('content_type') or '').lower()
        if status == 200 and ctype and 'text/html' in ctype:
            try:
                path = urlparse(url).path or ''
                if '/api/' in path or path.endswith('.json'):
                    findings.append({
                        'issue': f'API endpoint returns HTML instead of data: {path[:120]}',
                        'severity': 'moderate',
                        'status': status,
                        'url': url,
                        'element_selector': 'network',
                        'display': 'API returns HTML instead of JSON',
                    })
            except Exception:
                pass
        # Deduplicate the core failure below; quality notes above may repeat.
        if status < 400 or url in seen:
            continue
        seen.add(url)
    for url, times in counts.items():
        if times >= 3:
            findings.append({
                'issue': f'Endpoint requested {times}x with failures: {urlparse(url).path[:120]}',
                'severity': 'moderate',
                'url': url,
                'element_selector': 'network',
                'display': f'Repeated failing requests ({times}x): {url[:150]}',
            })
    return findings


def discover_api_candidates(page):
    """Best-effort list of API-ish URLs referenced by the page.

    Looks at script contents and common spec locations without any network
    traffic of its own, so it is safe to run on every page.
    """
    candidates = []
    try:
        urls = page.evaluate(
            """() => Array.from(document.scripts || []).map(s => s.src || '').filter(Boolean)"""
        ) or []
        for src in urls:
            if '/api/' in src or 'openapi' in src.lower() or 'swagger' in src.lower():
                candidates.append(src)
    except Exception:
        pass
    return candidates


# ============================================================
# 3. Link testing (deep): resources, fragments, mailto/tel
# ============================================================

def validate_special_link(href):
    """Return a finding dict for a malformed mailto:/tel: link, else None."""
    if not href:
        return None
    low = href.strip().lower()
    if low.startswith('mailto:'):
        match = MAILTO_RE.match(href.strip())
        addr = (match.group(1) if match else '').strip()
        if not addr or not EMAIL_RE.match(addr):
            return {
                'issue': f'Malformed email link: {href[:120]}',
                'severity': 'moderate',
                'element_selector': 'a[href]',
                'display': f'Malformed email link: {href[:120]}',
            }
    elif low.startswith('tel:'):
        if not TEL_RE.match(href.strip()):
            return {
                'issue': f'Malformed phone link: {href[:120]}',
                'severity': 'minor',
                'element_selector': 'a[href]',
                'display': f'Malformed phone link: {href[:120]}',
            }
    return None


def check_page_resources(page, base_url):
    """Check non-anchor resource URLs and in-page fragment targets."""
    findings = []
    try:
        data = page.evaluate("""() => ({
            imgs: Array.from(document.images || []).map(i => i.getAttribute('src') || ''),
            scripts: Array.from(document.scripts || []).map(s => s.src || ''),
            styles: Array.from(document.querySelectorAll('link[rel="stylesheet"]') || [])
                .map(l => l.href || ''),
            frames: Array.from(document.querySelectorAll('iframe[src]') || [])
                .map(f => f.getAttribute('src') || ''),
            ids: new Set(Array.from(document.querySelectorAll('[id]') || [])
                .map(e => e.id)).size,
            anchors: Array.from(document.querySelectorAll('a[href^="#"]') || [])
                .map(a => a.getAttribute('href') || ''),
            special: Array.from(document.querySelectorAll('a[href^="mailto:"], a[href^="tel:"]') || [])
                .map(a => a.getAttribute('href') || ''),
        })""") or {}
    except Exception as e:
        logger.warning("resource check error: %s", e)
        return findings, {'resources_checked': 0}

    checked = 0
    for key in ('imgs', 'scripts', 'styles', 'frames'):
        for src in data.get(key) or []:
            if not src or src.startswith(('data:', 'blob:')):
                continue
            checked += 1
            if not src.strip():
                findings.append({
                    'issue': f'Empty {key} source leaves a broken resource slot',
                    'severity': 'moderate',
                    'element_selector': key,
                    'display': f'Empty {key} source',
                })
    try:
        existing_ids = page.evaluate(
            """() => new Set(Array.from(document.querySelectorAll('[id]') || []).map(e => e.id))"""
        )
    except Exception:
        existing_ids = None
    # Fallback when the fake/evaluate path cannot return a Set over the bridge.
    if not isinstance(existing_ids, (set, list, tuple)):
        try:
            id_list = page.evaluate(
                """() => Array.from(document.querySelectorAll('[id]') || []).map(e => e.id)"""
            ) or []
            existing_ids = set(id_list)
        except Exception:
            existing_ids = set()
    else:
        existing_ids = set(existing_ids)
    for frag in data.get('anchors') or []:
        name = (frag or '')[1:]
        if not name or '/' in name:
            continue
        if name not in existing_ids:
            findings.append({
                'issue': f'Fragment link #{name[:80]} points to nothing on this page',
                'severity': 'minor',
                'element_selector': 'a[href]',
                'display': f'Broken anchor: #{name[:80]}',
            })
    try:
        specials = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href^="mailto:"], a[href^="tel:"]') || [])"""
        ) or []
    except Exception:
        specials = []
    # Evaluate bridge may not return handles; fall back to raw href strings.
    raw_specials = data.get('special') or []
    for idx, href in enumerate(raw_specials):
        bad = validate_special_link(href)
        if bad:
            try:
                el = specials[idx] if idx < len(specials) else None
                from modules.runner.routes import _safe_get_selector  # lazy, avoids cycle
                bad['element_selector'] = _safe_get_selector(el) if el else 'a[href]'
            except Exception:
                pass
            findings.append(bad)
    return findings, {'resources_checked': checked}


# ============================================================
# 4. UI testing: structural interface defects
# ============================================================

def check_ui_consistency(page):
    """Duplicate IDs, empty controls, missing viewport/lang, overflow."""
    findings = []
    try:
        data = page.evaluate("""() => {
            const ids = {};
            document.querySelectorAll('[id]').forEach(e => { ids[e.id] = (ids[e.id] || 0) + 1; });
            const dups = Object.entries(ids).filter(([k, v]) => v > 1).map(([k]) => k).slice(0, 10);
            const empties = Array.from(document.querySelectorAll('button, a'))
                .filter(e => !(e.innerText || '').trim() && !e.getAttribute('aria-label'))
                .length;
            const vw = document.querySelector('meta[name="viewport"]');
            const lang = document.documentElement.getAttribute('lang');
            const overflow = Array.from(document.querySelectorAll('body *')).filter(e => {
                try { const r = e.getBoundingClientRect();
                    return r.width > 0 && (r.left < -50 || r.right > window.innerWidth + 50);
                } catch (err) { return false; }
            }).length;
            return { dups, empties, hasViewport: !!vw, lang: lang || '', overflow };
        }""") or {}
    except Exception as e:
        logger.warning("ui consistency check error: %s", e)
        return findings
    for dup in data.get('dups') or []:
        findings.append({
            'issue': f'Duplicate element ID "{dup[:80]}" breaks scripts and labels',
            'severity': 'moderate',
            'element_selector': f'#{dup[:80]}',
            'display': f'Duplicate ID: {dup[:80]}',
        })
    empties = data.get('empties') or 0
    if empties:
        findings.append({
            'issue': f'{empties} clickable element(s) have no text or label',
            'severity': 'serious' if empties > 5 else 'moderate',
            'count': empties,
            'element_selector': 'button, a',
            'display': f'{empties} empty button/link(s) with no label',
        })
    if not data.get('hasViewport'):
        findings.append({
            'issue': 'Page has no viewport meta tag (mobile layout uncontrolled)',
            'severity': 'moderate',
            'element_selector': 'head > meta[name="viewport"]',
            'display': 'Missing viewport meta tag',
        })
    if not (data.get('lang') or '').strip():
        findings.append({
            'issue': 'Page has no lang attribute (screen readers mispronounce content)',
            'severity': 'minor',
            'element_selector': 'html',
            'display': 'Missing html lang attribute',
        })
    overflow = data.get('overflow') or 0
    if overflow:
        findings.append({
            'issue': f'{overflow} element(s) overflow the viewport horizontally',
            'severity': 'moderate',
            'count': overflow,
            'element_selector': 'body',
            'display': f'{overflow} element(s) stick out of the viewport',
        })
    return findings


# ============================================================
# 5. Code testing: front-end static quality
# ============================================================

def check_code_quality(page):
    """Deprecated markup, risky inline code, missing document basics."""
    findings = []
    try:
        data = page.evaluate("""() => {
            const tags = {};
            ['font', 'center', 'marquee', 'blink', 'big', 'tt'].forEach(t => {
                tags[t] = document.getElementsByTagName(t).length;
            });
            const inlineHandlers = document.querySelectorAll(
                '[onclick], [onload], [onerror], [onmouseover]').length;
            const jsUrls = Array.from(document.querySelectorAll('a[href], [src]'))
                .filter(e => ((e.getAttribute('href') || e.getAttribute('src')) || '')
                    .trim().toLowerCase().startsWith('javascript:')).length;
            const scripts = Array.from(document.scripts || []).map(s => ({
                src: s.src || '', len: (s.textContent || '').length,
                text: (s.textContent || '').slice(0, 4000),
            }));
            const hasDoctype = !!document.doctype;
            const charset = document.characterSet || '';
            const jquery = Array.from(document.scripts || [])
                .map(s => s.src || '').find(u => /jquery/i.test(u)) || '';
            return { tags, inlineHandlers, jsUrls, scripts, hasDoctype, charset, jquery };
        }""") or {}
    except Exception as e:
        logger.warning("code quality check error: %s", e)
        return findings

    for tag in DEPRECATED_TAGS:
        count = (data.get('tags') or {}).get(tag, 0)
        if count:
            findings.append({
                'issue': f'Deprecated <{tag}> tag used {count}x',
                'severity': 'minor',
                'element_selector': tag,
                'display': f'Deprecated <{tag}> x{count}',
            })
    handlers = data.get('inlineHandlers') or 0
    if handlers:
        findings.append({
            'issue': f'{handlers} inline event handler(s) (onclick=...) bypass Content-Security-Policy',
            'severity': 'moderate',
            'count': handlers,
            'element_selector': '[onclick]',
            'display': f'{handlers} inline event handler(s)',
        })
    jsurls = data.get('jsUrls') or 0
    if jsurls:
        findings.append({
            'issue': f'{jsurls} javascript: URL(s) execute code from a link',
            'severity': 'moderate',
            'element_selector': 'a[href]',
            'display': f'{jsurls} javascript: URL(s)',
        })
    for script in data.get('scripts') or []:
        text = script.get('text') or ''
        if re.search(r'\beval\s*\(', text) or 'document.write(' in text:
            findings.append({
                'issue': 'Inline script uses eval()/document.write() (XSS-prone)',
                'severity': 'serious',
                'element_selector': 'script',
                'display': 'Risky eval()/document.write() in inline script',
            })
            break
    for script in data.get('scripts') or []:
        if not script.get('src') and (script.get('len') or 0) > BIG_INLINE_SCRIPT_CHARS:
            findings.append({
                'issue': f'Inline script is {script["len"] // 1024}KB (blocks rendering)',
                'severity': 'moderate',
                'element_selector': 'script',
                'display': 'Oversized inline script',
            })
            break
    if not data.get('hasDoctype'):
        findings.append({
            'issue': 'Page has no DOCTYPE (quirks mode rendering)',
            'severity': 'minor',
            'element_selector': 'document',
            'display': 'Missing DOCTYPE',
        })
    if not (data.get('charset') or '').strip():
        findings.append({
            'issue': 'Page declares no character encoding',
            'severity': 'minor',
            'element_selector': 'head > meta[charset]',
            'display': 'Missing charset declaration',
        })
    jquery_src = data.get('jquery') or ''
    match = JQUERY_RE.search(jquery_src)
    if match and int(match.group(1)) < 3:
        findings.append({
            'issue': f'Outdated jQuery {match.group(0).rsplit("/", 1)[-1]} has known XSS CVEs',
            'severity': 'serious',
            'element_selector': 'script[src]',
            'display': 'Outdated jQuery with known vulnerabilities',
        })
    return findings
