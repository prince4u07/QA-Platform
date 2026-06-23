r"""
DigiELV menu-click mapper (round 2).
We now KNOW the nav is <li class="menu-item"> click-based. This script logs in,
then CLICKS each menu item one-by-one and records the URL it lands on — giving us
the exact route list the crawler needs, and confirming the click pattern works.

Usage (PowerShell, from backend):
    .\venv\Scripts\python.exe ..\inspect_nav2.py https://digielvsit.mmcm.in/user-login
"""
import sys, time, json

URL = sys.argv[1] if len(sys.argv) > 1 else 'https://digielvsit.mmcm.in/user-login'
from playwright.sync_api import sync_playwright

print("=" * 70)
print("A browser opens. LOG IN BY HAND, reach the dashboard, then press ENTER.")
print("This script will then click each menu item and record its URL.")
print("=" * 70)

with sync_playwright() as p:
    try:
        browser = p.chromium.launch(channel='chrome', headless=False,
                                    args=['--start-maximized', '--disable-blink-features=AutomationControlled'])
    except Exception:
        browser = p.chromium.launch(headless=False,
                                    args=['--start-maximized', '--disable-blink-features=AutomationControlled'])
    ctx = browser.new_context(no_viewport=True)
    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    page = ctx.new_page()
    page.goto(URL, wait_until='commit', timeout=60000)

    input("\n>>> Logged in and on the dashboard? Press ENTER...\n")
    print(f"\nStart URL: {page.url}\n")

    # Grab the menu item texts first (so stale-element clicks don't break the loop).
    labels = page.eval_on_selector_all(
        'li.menu-item',
        "els => els.map(e => (e.innerText||'').trim()).filter(Boolean)"
    )
    print(f"Menu items detected: {labels}\n")
    print("-" * 70)

    routes = {}
    for label in labels:
        try:
            before = page.url
            # Re-find by text each time (Angular re-renders the list on navigation).
            clicked = page.evaluate(r"""(label) => {
                const items = [...document.querySelectorAll('li.menu-item')];
                const el = items.find(e => (e.innerText||'').trim() === label);
                if (el) { el.scrollIntoView({block:'center'}); el.click(); return true; }
                return false;
            }""", label)
            if not clicked:
                print(f"  [skip] '{label}' — couldn't find element")
                continue
            # Wait for URL to change (SPA pushState) or network to settle.
            try:
                page.wait_for_function("(o)=>location.href!==o", arg=before, timeout=5000)
            except Exception:
                pass
            try:
                page.wait_for_load_state('networkidle', timeout=4000)
            except Exception:
                pass
            time.sleep(0.6)
            after = page.url
            routes[label] = after
            changed = "✓ changed" if after != before else "✗ URL did NOT change"
            print(f"  '{label:28}' -> {after}   [{changed}]")
        except Exception as e:
            print(f"  [error] '{label}': {e}")

    print("-" * 70)
    print("\nROUTES FOUND (feed these to the crawler):")
    seen = set()
    for label, url in routes.items():
        from urllib.parse import urlparse
        path = urlparse(url).path
        if path and path not in seen:
            seen.add(path)
            print(f"     {path}")

    with open('static/uploads/_routes.json', 'w', encoding='utf-8') as f:
        json.dump(routes, f, indent=2)
    print("\n  Saved to static/uploads/_routes.json")
    print("  Press ENTER to close.")
    input()
    browser.close()