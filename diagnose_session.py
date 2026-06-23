r"""
DigiELV (or any project) session diagnostic.
Run this on YOUR machine from the backend folder, with the project's id.

It answers ONE question: when the crawler replays your captured session,
does DigiELV treat you as logged-in or does it bounce you to /user-login?
And if it bounces — WHICH of the three causes is it?

Usage (PowerShell, from C:\Users\PRINCE\qa-platform\backend):
    .\venv\Scripts\python.exe ..\diagnose_session.py <project_id>

e.g.  .\venv\Scripts\python.exe ..\diagnose_session.py 1
"""
import sys, os, json, time

if len(sys.argv) < 2:
    print("Usage: python diagnose_session.py <project_id>")
    sys.exit(1)

PROJECT_ID = sys.argv[1]
SESSIONS_DIR = os.path.join('static', 'uploads', 'sessions')
state_path   = os.path.join(SESSIONS_DIR, f'{PROJECT_ID}.json')
ss_path      = os.path.join(SESSIONS_DIR, f'{PROJECT_ID}.session_storage.json')
landing_path = os.path.join(SESSIONS_DIR, f'{PROJECT_ID}.landing')

def line(): print("-" * 70)

# ---------- STEP 1: what's actually in the saved session? ----------
line(); print("STEP 1  —  What did 'Open & Login' actually capture?"); line()
if not os.path.exists(state_path):
    print(f"  ✗ No session file at {state_path}")
    print("    => You haven't captured a session for this project id, or the id is wrong.")
    sys.exit(1)

with open(state_path, encoding='utf-8') as f:
    state = json.load(f)

cookies = state.get('cookies', [])
origins = state.get('origins', [])
ls_items = []
for o in origins:
    ls_items += o.get('localStorage', [])

print(f"  Cookies saved        : {len(cookies)}")
print(f"  localStorage keys    : {len(ls_items)}")
ss_data = {}
if os.path.exists(ss_path):
    with open(ss_path, encoding='utf-8') as f:
        ss_data = json.load(f)
print(f"  sessionStorage keys  : {len(ss_data)}")
landing = ''
if os.path.exists(landing_path):
    with open(landing_path, encoding='utf-8') as f:
        landing = f.read().strip()
print(f"  Landing URL          : {landing or '(none saved)'}")

# Classify cookies: session-only (no expiry) vs persistent, and which look auth-ish
AUTH = ('token','session','auth','jwt','sid','access','refresh')
now = time.time()
auth_cookies, session_only, expired = [], [], []
for c in cookies:
    name = (c.get('name') or '')
    nl = name.lower()
    exp = c.get('expires', -1)
    is_auth = any(k in nl for k in AUTH)
    if is_auth: auth_cookies.append(name)
    if not exp or exp <= 0:
        session_only.append(name)
    elif exp < now:
        expired.append(name)

auth_ls = [kv.get('name') for kv in ls_items if any(k in (kv.get('name') or '').lower() for k in AUTH)]
auth_ss = [k for k in ss_data if any(x in k.lower() for x in AUTH)]

print()
print(f"  Auth-looking cookies      : {auth_cookies or '(none)'}")
print(f"  Auth-looking localStorage : {auth_ls or '(none)'}")
print(f"  Auth-looking sessionStorage: {auth_ss or '(none)'}")
print(f"  Session-only cookies (die on browser close): {session_only or '(none)'}")
print(f"  Already-EXPIRED cookies   : {expired or '(none)'}")

print()
print("  READING THIS:")
if not auth_cookies and not auth_ls and not auth_ss:
    print("  ⚠  No obvious auth token found ANYWHERE. DigiELV may name its token")
    print("     something non-obvious — but if truly nothing was captured, the")
    print("     crawler has nothing to replay. (Cause leans toward #2/#3.)")
if expired:
    print(f"  ⚠  {len(expired)} auth cookie(s) already expired in the saved file.")
    print("     The run SHOULD already be telling you the session expired.")
if session_only and auth_cookies:
    print("  ⚠  Some auth cookies are SESSION-ONLY (no expiry). These are exactly")
    print("     the kind that 'die when the browser closes' — matches your theory.")
    print("     BUT: Playwright's storage_state re-creates them on replay, so they")
    print("     CAN survive into the crawl. Whether DigiELV's SERVER still honours")
    print("     them is what Step 2 tests.")

# ---------- STEP 2: replay exactly like the crawler, see if we stay logged in ----------
line(); print("STEP 2  —  Replay the session like the crawler and check the result"); line()

try:
    from playwright.sync_api import sync_playwright
except Exception as e:
    print(f"  ✗ Playwright not importable here: {e}")
    print("    Run this from the backend venv so Playwright + Chromium are available.")
    sys.exit(1)

test_url = landing or None
if not test_url:
    print("  No landing URL saved; please pass the authenticated dashboard URL as arg 2.")
    if len(sys.argv) >= 3:
        test_url = sys.argv[2]
    else:
        print("  e.g. python diagnose_session.py 1 https://digielvsit.mmcm.in/dashboard")
        sys.exit(1)

print(f"  Replaying session and navigating to: {test_url}")
print("  (headless — exactly how the automated crawl runs)")
print()

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        storage_state=state_path,
        viewport={'width': 1280, 'height': 900},
    )
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    if ss_data:
        payload = json.dumps(ss_data)
        context.add_init_script(
            f"(() => {{ try {{ const d = {payload}; "
            "for (const k in d) sessionStorage.setItem(k, d[k]); } catch(e) {} }})();"
        )
        print(f"  Injected {len(ss_data)} sessionStorage keys (mirrors the crawler).")

    page = context.new_page()
    page.goto(test_url, wait_until='domcontentloaded', timeout=60000)
    time.sleep(3)  # let the SPA boot + run its auth check + redirect if it wants to

    final_url = page.url
    title = ''
    try: title = page.title()
    except Exception: pass

    # Heuristics for "did we land on a login page?"
    login_markers = ['login', 'signin', 'sign-in', 'user-login', 'auth']
    on_login = any(m in final_url.lower() for m in login_markers)
    # Also look for a password field as a strong "this is a login page" signal
    has_pwd = False
    try:
        has_pwd = page.query_selector('input[type="password"]') is not None
    except Exception:
        pass
    # And look for any logout control as a strong "we ARE logged in" signal
    has_logout = False
    try:
        has_logout = page.evaluate(
            "() => /log\\s*out|sign\\s*out/i.test(document.body.innerText)"
        )
    except Exception:
        pass

    os.makedirs('static/uploads', exist_ok=True)
    shot = 'static/uploads/_diagnostic_session.png'
    try: page.screenshot(path=shot); 
    except Exception: shot = None

    print()
    print(f"  Navigated to : {test_url}")
    print(f"  Ended up at  : {final_url}")
    print(f"  Page title   : {title}")
    print(f"  Password field on page? : {has_pwd}")
    print(f"  Logout link on page?    : {has_logout}")
    if shot: print(f"  Screenshot saved        : {shot}  (open it to see with your own eyes)")
    browser.close()

# ---------- VERDICT ----------
line(); print("VERDICT"); line()
redirected = (final_url.rstrip('/') != test_url.rstrip('/'))
looks_logged_out = on_login or has_pwd

if looks_logged_out:
    print("  ✗ The replayed session is NOT logged in. DigiELV bounced the crawler")
    print(f"    to a login page ({final_url}).")
    print("    => THIS is why only the login page gets 'tested' and no inner pages.")
    print()
    print("  WHICH CAUSE?")
    if expired:
        print("  → CAUSE: expired cookies. Re-capture and run immediately.")
    elif session_only and not auth_cookies:
        print("  → CAUSE likely #3: auth lives in a session-only cookie that the")
        print("    SERVER ties to the original browser. 'Close window = logout' is real.")
        print("    Captured cookies replay, but the server no longer honours them.")
    elif auth_ls or auth_ss:
        print("  → Token WAS captured (in localStorage/sessionStorage) yet you're still")
        print("    logged out. CAUSE likely #1: DigiELV validates the token server-side")
        print("    and has invalidated it (single-session, or bound to the login browser).")
    else:
        print("  → No auth token found in the capture at all. CAUSE likely #2: DigiELV")
        print("    stores its token somewhere we're not capturing, OR sets it via an")
        print("    httpOnly cookie that didn't survive. Needs a token-location hunt.")
    print()
    print("  None of these are fixable purely from the crawler if the SERVER refuses")
    print("  the replayed session. The realistic fix is MANUAL MODE: log in by hand in")
    print("  the headed window, THEN crawl from that same live browser (no replay) —")
    print("  which your platform already supports via /manual + autocrawl.")
else:
    print("  ✓ The replayed session IS logged in (no login redirect, no password")
    print("    field). The crawler SHOULD be able to reach inner pages.")
    print("    => If pages still aren't crawled, the problem is link discovery /")
    print("       SPA navigation, NOT the session. We debug the crawler frontier next.")
print()