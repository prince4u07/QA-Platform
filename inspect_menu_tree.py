r"""
DigiELV full menu-tree dumper (final inspector).
We now know: click-based, single-URL (/dashboard), nested menus.
This logs in, then maps the COMPLETE menu structure — parents, children,
which items expand vs navigate, and what content marker changes on click.
Output drives the click-driven crawler so it's built on facts, not guesses.

Usage (PowerShell, from backend):
    .\venv\Scripts\python.exe ..\inspect_menu_tree.py https://digielvsit.mmcm.in/user-login
"""
import sys, time, json
URL = sys.argv[1] if len(sys.argv) > 1 else 'https://digielvsit.mmcm.in/user-login'
from playwright.sync_api import sync_playwright

print("=" * 70)
print("Browser opens. LOG IN BY HAND, reach the dashboard, then press ENTER.")
print("This maps the full menu tree (parents + nested children).")
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
    input("\n>>> On the dashboard? Press ENTER...\n")
    print(f"\nURL: {page.url}\n")

    # 1. Dump the raw structure of anything that looks like a nav container.
    tree = page.evaluate(r"""() => {
        const result = {containers: [], allMenuItems: []};

        // Find likely nav containers
        const navSel = ['nav', '.sidebar', '.side-nav', '.menu', '[class*="sidebar"]',
                        '[class*="side-menu"]', 'ul[class*="menu"]', 'aside'];
        const containers = new Set();
        navSel.forEach(s => document.querySelectorAll(s).forEach(e => containers.add(e)));

        // For each menu-item, record its text, tag, classes, depth, and whether
        // it has a nested <ul> (=> expandable parent) or an icon hinting expand.
        const items = [...document.querySelectorAll('li.menu-item, li[class*="menu"], li[class*="nav"]')];
        items.forEach(li => {
            // direct text only (not nested children text)
            const clone = li.cloneNode(true);
            [...clone.querySelectorAll('ul, li')].forEach(n => n.remove());
            const ownText = (clone.innerText || '').trim().replace(/\s+/g,' ').slice(0,50);
            const hasNestedUl = !!li.querySelector('ul');
            const nestedItems = [...li.querySelectorAll('ul li')].map(
                n => (n.innerText||'').trim().replace(/\s+/g,' ').slice(0,40)).filter(Boolean);
            const cls = (li.className||'').slice(0,60);
            // depth = how many <li> ancestors
            let depth = 0, a = li.parentElement;
            while (a) { if (a.tagName === 'LI') depth++; a = a.parentElement; }
            result.allMenuItems.push({
                ownText, hasNestedUl, nestedCount: nestedItems.length,
                nestedItems: nestedItems.slice(0,10), cls, depth,
                clickable: li.onclick !== null || li.getAttribute('role') === 'button'
                           || getComputedStyle(li).cursor === 'pointer'
            });
        });
        return result;
    }""")

    print("FULL MENU ITEM LIST (own text only, with nesting info):")
    print("-" * 70)
    for it in tree['allMenuItems']:
        indent = "  " * it['depth']
        nested = f"  [PARENT of {it['nestedCount']}: {it['nestedItems']}]" if it['hasNestedUl'] else ""
        cur = "  (pointer)" if it['clickable'] else ""
        print(f"  {indent}depth{it['depth']} \"{it['ownText']}\"{cur}{nested}")
    print("-" * 70)

    # 2. Identify a content marker we can watch to confirm "screen changed".
    marker = page.evaluate(r"""() => {
        // Heuristic: the main content area's first heading or a router-outlet sibling
        const main = document.querySelector('main, [class*="content"], router-outlet');
        if (main && main.parentElement) {
            const h = main.parentElement.querySelector('h1,h2,h3,.page-title,[class*="title"]');
            return h ? (h.innerText||'').trim().slice(0,60) : '(no heading found)';
        }
        const h = document.querySelector('h1,h2,h3');
        return h ? (h.innerText||'').trim().slice(0,60) : '(no heading found)';
    }""")
    print(f"\nCurrent content heading marker: \"{marker}\"")
    print("(The crawler will watch this heading change to confirm each click worked.)")

    # 3. Try clicking ONE leaf item and watch heading + DOM change (proof of concept)
    print("\nClicking 'Reports' as a test to see what changes...")
    before_marker = marker
    try:
        page.evaluate(r"""() => {
            const items = [...document.querySelectorAll('li.menu-item')];
            const el = items.find(e => /reports/i.test(e.innerText||''));
            if (el) { el.scrollIntoView({block:'center'}); el.click(); }
        }""")
        time.sleep(1.5)
        after_marker = page.evaluate(r"""() => {
            const h = document.querySelector('h1,h2,h3,.page-title,[class*="title"]');
            return h ? (h.innerText||'').trim().slice(0,60) : '(none)';
        }""")
        print(f"  Heading before: \"{before_marker}\"")
        print(f"  Heading after : \"{after_marker}\"")
        print(f"  => {'✓ content CHANGED (heading is our signal)' if after_marker != before_marker else '✗ heading did not change — need a different signal'}")
    except Exception as e:
        print(f"  click test error: {e}")

    with open('static/uploads/_menu_tree.json', 'w', encoding='utf-8') as f:
        json.dump(tree, f, indent=2)
    print("\n  Full tree saved to static/uploads/_menu_tree.json")
    print("  Press ENTER to close.")
    input()
    browser.close()