"""
Builds the QA Platform presentation PDF.

Renders an HTML slide deck (16:9, one slide per page) and prints it to PDF with
headless Chromium via Playwright -- the same browser engine the project itself
uses for automated testing.

Usage:  python presentation/build_deck.py
Output: presentation/QA-Platform-Manual-and-Automated-Testing.pdf
"""

import html
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PDF = os.path.join(HERE, "QA-Platform-Manual-and-Automated-Testing.pdf")
OUT_HTML = os.path.join(HERE, "_deck.html")

# --------------------------------------------------------------------------
# Minimal syntax highlighter (Python / JS). Good enough to make code readable
# on a projected slide; deliberately dependency-free.
# --------------------------------------------------------------------------

PY_KW = {
    "False", "None", "True", "and", "as", "assert", "async", "await", "break",
    "class", "continue", "def", "del", "elif", "else", "except", "finally",
    "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal",
    "not", "or", "pass", "raise", "return", "try", "while", "with", "yield",
    "self", "cls",
}
JS_KW = {
    "const", "let", "var", "function", "return", "if", "else", "for", "while",
    "async", "await", "new", "class", "extends", "import", "export", "from",
    "try", "catch", "finally", "throw", "typeof", "instanceof", "of", "in",
    "default", "switch", "case", "break", "continue", "this", "null",
    "undefined", "true", "false", "=>",
}
BUILTINS = {
    "len", "range", "print", "int", "str", "dict", "list", "set", "tuple",
    "bool", "float", "sorted", "sum", "min", "max", "enumerate", "zip",
    "isinstance", "getattr", "setattr", "hasattr", "round", "abs", "any",
    "all", "map", "filter", "open", "type", "super", "repr", "json", "os",
    "time", "re", "math", "log", "cursor", "conn",
}

TOKEN_RE = re.compile(
    r"""(?P<tstr>\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?''')
      | (?P<dstr>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)
      | (?P<comment>\#[^\n]*|//[^\n]*)
      | (?P<deco>@[A-Za-z_][A-Za-z0-9_.]*)
      | (?P<num>\b\d+(?:\.\d+)?\b)
      | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
      | (?P<ws>\s+)
      | (?P<punc>[^\sA-Za-z0-9_])""",
    re.VERBOSE,
)


def highlight(code, lang="py"):
    """Escape then colourise source code into HTML spans."""
    keywords = JS_KW if lang in ("js", "jsx") else PY_KW
    out = []
    prev_significant = ""
    for m in TOKEN_RE.finditer(code):
        kind = m.lastgroup
        text = m.group(0)
        if kind == "ws":
            out.append(html.escape(text))
            continue
        if kind == "comment":
            out.append('<span class="c">%s</span>' % html.escape(text))
        elif kind in ("tstr", "dstr"):
            out.append('<span class="s">%s</span>' % html.escape(text))
        elif kind == "deco":
            out.append('<span class="d">%s</span>' % html.escape(text))
        elif kind == "num":
            out.append('<span class="n">%s</span>' % html.escape(text))
        elif kind == "name":
            nxt = code[m.end():m.end() + 1]
            if text in keywords:
                out.append('<span class="k">%s</span>' % html.escape(text))
                prev_significant = text
                continue
            if prev_significant in ("def", "class", "function"):
                out.append('<span class="f">%s</span>' % html.escape(text))
            elif nxt == "(":
                out.append('<span class="f">%s</span>' % html.escape(text))
            elif text in BUILTINS or text[:1].isupper():
                out.append('<span class="b">%s</span>' % html.escape(text))
            else:
                out.append(html.escape(text))
        else:
            out.append('<span class="p">%s</span>' % html.escape(text))
        if kind not in ("ws",):
            prev_significant = text if kind == "name" else ""
    return "".join(out)


def code(code_text, lang="py", caption=None, max_lines=None):
    """Render a code block, optionally annotated with a file:line caption."""
    text = code_text.strip("\n")
    if max_lines:
        kept = text.split("\n")
        if len(kept) > max_lines:
            text = "\n".join(kept[:max_lines])
    cap = ""
    if caption:
        cap = '<div class="cap">%s</div>' % html.escape(caption)
    return '<div class="codewrap">%s<pre class="code">%s</pre></div>' % (
        cap, highlight(text, lang))


# --------------------------------------------------------------------------
# Slide model
# --------------------------------------------------------------------------

SLIDES = []


def add(html_body, cls="content"):
    SLIDES.append((cls, html_body))


def title_slide(kicker, main, sub, meta):
    add(
        '<div class="t-wrap">'
        '<div class="t-kicker">%s</div>'
        '<h1 class="t-main">%s</h1>'
        '<div class="t-rule"></div>'
        '<div class="t-sub">%s</div>'
        '<div class="t-meta">%s</div>'
        '</div>' % (kicker, main, sub, meta),
        cls="title",
    )


def section(num, name, blurb, items):
    lis = "".join("<li>%s</li>" % i for i in items)
    add(
        '<div class="s-wrap">'
        '<div class="s-num">%s</div>'
        '<h2 class="s-name">%s</h2>'
        '<div class="s-rule"></div>'
        '<p class="s-blurb">%s</p>'
        '<ul class="s-list">%s</ul>'
        '</div>' % (num, name, blurb, lis),
        cls="section",
    )


def slide(kicker, title, body, notes=None, cls="content"):
    n = ""
    if notes:
        n = '<div class="notes"><b>Speaker note:</b> %s</div>' % notes
    add(
        '<div class="hd"><div class="kicker">%s</div><h2>%s</h2></div>'
        '<div class="bd">%s</div>%s' % (kicker, title, body, n)
    )


def bullets(items, cls="ul"):
    out = []
    for it in items:
        if isinstance(it, tuple):
            out.append('<li><b>%s</b> %s</li>' % (it[0], it[1]))
        else:
            out.append("<li>%s</li>" % it)
    return '<ul class="%s">%s</ul>' % (cls, "".join(out))


def grid(cards, cols=3):
    cells = []
    for c in cards:
        if len(c) == 3:
            cells.append(
                '<div class="card"><div class="ci %s">%s</div>'
                '<div class="ct">%s</div><div class="cd">%s</div></div>'
                % (c[0], c[0], c[1], c[2])
            )
        else:
            cells.append(
                '<div class="card"><div class="ct">%s</div>'
                '<div class="cd">%s</div></div>' % (c[0], c[1])
            )
    return '<div class="grid g%d">%s</div>' % (cols, "".join(cells))


def table(headers, rows, cls=""):
    th = "".join("<th>%s</th>" % h for h in headers)
    trs = []
    for r in rows:
        tds = "".join("<td>%s</td>" % c for c in r)
        trs.append("<tr>%s</tr>" % tds)
    return ('<table class="tbl %s"><thead><tr>%s</tr></thead>'
            "<tbody>%s</tbody></table>" % (cls, th, "".join(trs)))


def flow(steps, accent="blue"):
    """A horizontal numbered flow diagram."""
    cells = []
    for i, s in enumerate(steps, 1):
        cells.append(
            '<div class="fl"><div class="fln">%d</div>'
            '<div class="flt">%s</div></div>' % (i, s)
        )
    return '<div class="flow %s">%s</div>' % (accent, "".join(cells))


def callout(text, kind="info", title=None):
    t = '<div class="co-t">%s</div>' % title if title else ""
    return '<div class="callout %s">%s<div class="co-b">%s</div></div>' % (
        kind, t, text)


# ==========================================================================
#  DECK CONTENT
# ==========================================================================

title_slide(
    "QA PLATFORM &nbsp;&bull;&nbsp; PROJECT PRESENTATION",
    "Manual Testing &amp; Automated<br>Testing, End to End",
    "How both testing modes work inside a full-stack QA management platform "
    "&mdash; the workflows, the real code, and the technology behind them.",
    "React 19 &nbsp;/&nbsp; Vite &nbsp;&bull;&nbsp; Flask 3 &nbsp;/&nbsp; MySQL "
    "&nbsp;&bull;&nbsp; Playwright &nbsp;/&nbsp; axe-core &nbsp;&bull;&nbsp; "
    "Gemini AI<br>Backend suite: 312 pytest tests passing &nbsp;&bull;&nbsp; "
    "Frontend: 14 Vitest tests, ESLint clean, production build green",
)

# ---- 1. Agenda ----------------------------------------------------------
slide(
    "AGENDA",
    "What this deck covers",
    '<div class="two">'
    '<div>'
    + bullets([
        ("01 &nbsp;The problem &mdash;", "why a project needs both manual and automated testing"),
        ("02 &nbsp;Project overview &mdash;", "what QA Platform is and how it is built"),
        ("03 &nbsp;Manual testing &mdash;", "the human-driven workflow, step by step"),
        ("04 &nbsp;Automated testing &mdash;", "the Playwright crawl engine, and exactly how it detects issues"),
        ("05 &nbsp;What each mode detects &mdash;", "the 8 categories both share, and what only one can see"),
        ("06 &nbsp;Technology deep dive &mdash;", "every tool, what it does, and the fact that no API key is needed"),
        ("07 &nbsp;Quality of the platform &mdash;", "our own test automation and results"),
        ("08 &nbsp;Engineering challenges &mdash;", "real bugs we found and how we fixed them"),
        ("09 &nbsp;Limitations &amp; roadmap &mdash;", "what is not solved yet"),
        ("10 &nbsp;Key learnings &mdash;", "transferable practices"),
    ])
    + '</div></div>',
    notes="Keep this to 30 seconds. The shape of the talk is: the problem, the "
          "product, then the two testing modes in detail, then proof.",
)

# ---- 2. Section: The problem -------------------------------------------
section(
    "01",
    "The Problem",
    "Manual testing and automated testing are not competitors. They fail in "
    "opposite directions, which is exactly why a real QA platform needs both.",
    [
        "Manual testing: high judgement, terrible coverage, does not scale",
        "Automated testing: perfect repeatability, zero judgement",
        "Most teams pick one and pay for it in escaped bugs",
        "QA Platform runs both, and keeps their results strictly separate",
    ],
)

slide(
    "THE PROBLEM",
    "Why one testing mode is never enough",
    '<div class="two">'
    '<div class="panel warn"><div class="ph">Manual only</div>'
    + bullets([
        "A tester eyeballs maybe 5&ndash;10 screens per hour",
        "Nobody re-runs the same 60 checks after every hotfix",
        "Exploratory bugs hide in the gaps between scripted paths",
        "One person off sick means zero coverage",
        "Consistency depends entirely on who is holding the mouse",
    ])
    + '</div>'
    '<div class="panel good"><div class="ph">Automation only</div>'
    + bullets([
        "A crawler cannot tell if a button <i>feels</i> broken",
        "Scripts encode yesterday&rsquo;s understanding of the product",
        "Visual and UX regressions slip past DOM assertions",
        "A green build gets reported as &ldquo;quality is fine&rdquo;",
        "Nobody remembers the checkout flow was rewritten",
    ])
    + '</div></div>'
    + callout(
        "<b>The gap this project fills:</b> run human judgement and machine "
        "coverage <i>in the same platform</i>, on the same test cases, and "
        "report both &mdash; without either one overwriting the other.",
        kind="info", title="Design principle"),
    notes="The key line: automation scales verification, manual testing "
          "supplies judgement. Losing either one loses a whole class of bugs.",
)

# ---- 3. Section: Project overview ---------------------------------------
section(
    "02",
    "Project Overview",
    "A full-stack application for managing the entire software quality "
    "workflow &mdash; test cases, executions, evidence, defects, reports and AI.",
    [
        "Projects, test cases, runs, bugs, reports, admin",
        "React 19 + Vite frontend, Flask REST API, MySQL persistence",
        "Playwright drives a real Chromium for every automated run",
        "JWT auth, role-based admin, AI assistance via Google Gemini",
    ],
)

slide(
    "PROJECT OVERVIEW",
    "QA Platform at a glance",
    grid([
        ("01", "Test case management",
         "Manual and automated test cases with steps, expected results, "
         "priority and status. AI can propose the cases for you."),
        ("02", "Manual execution",
         "A real browser window opens, the tester logs in by hand and works "
         "through a checklist. Every step is screenshotted."),
        ("03", "Automated execution",
         "A Playwright crawler audits up to 100 pages for links, console "
         "errors, accessibility, security, performance and more."),
        ("04", "Evidence capture",
         "Full-page screenshots plus pixel-cropped images around each finding "
         "at its exact bounding box."),
        ("05", "Bug tracking",
         "Findings convert into tracked bugs in one click, with reproduction "
         "steps written per defect category."),
        ("06", "Reports &amp; AI",
         "Health trends, pass rates and PDF export, plus Gemini-powered bug "
         "analysis and test-case suggestions."),
    ], cols=3),
    notes="Six capabilities, one platform. Every screen you will see in the "
          "demo maps to one of these cards.",
)

slide(
    "TECH STACK",
    "The technology behind the platform",
    table(
        ["Layer", "Technology", "Version", "Role in testing"],
        [
            ["Frontend", "React + Vite", "19.2 / 8.0", "Test-case UI, run modal, live progress, reports"],
            ["Styling / UI", "Tailwind CSS, Framer Motion, Lucide", "3.4 / 12.4", "Interface, modals, checklist interactions"],
            ["Charts / 3D", "Recharts, Three.js, drei", "3.8 / 0.184", "Health trends, pass-rate graphs"],
            ["HTTP", "Axios", "1.16", "Typed API client with JWT injection"],
            ["Backend API", "Python + Flask", "3.1.3", "9 blueprints, all under <code>/api</code>"],
            ["Auth", "Flask-JWT-Extended + bcrypt", "4.7.3 / 5.0", "JWT access tokens, password hashing, role checks"],
            ["Database", "MySQL via Flask-MySQLdb", "8.x / 2.0", "9 tables, DictCursor, utf8mb4"],
            ["<b>Browser engine</b>", "<b>Playwright</b>", "<b>1.59.0</b>", "<b>Drives real Chromium for all automated runs</b>"],
            ["Accessibility", "axe-core (vendored)", "4.10.2", "WCAG 2.1 A/AA audit injected into each page"],
            ["Imaging", "Pillow", "12.2.0", "Crops screenshots around each finding"],
            ["Reports", "ReportLab", "4.5.0", "Server-side PDF generation"],
            ["AI", "Google Gemini (REST)", "flash", "Bug analysis, test suggestions, chat assistant"],
        ],
        cls="tight",
    ),
    notes="Playwright and axe-core are the two that matter for the testing "
          "story. Everything else is the platform that hosts them.",
)

slide(
    "ARCHITECTURE",
    "System architecture",
    '<div class="arch">'
    '<div class="arow"><div class="abox hi"><b>Browser</b>'
    "<span>React 19 SPA &mdash; Vite dev server :5173</span></div></div>"
    '<div class="aarrow">Axios &nbsp;JSON over&nbsp; <code>/api/*</code>'
    "&nbsp;&mdash;&nbsp; <code>Authorization: Bearer &lt;JWT&gt;</code></div>"
    '<div class="arow"><div class="abox">'
    '<b>Flask API &nbsp;:5000</b>'
    "<span>auth &nbsp;|&nbsp; projects &nbsp;|&nbsp; testcases &nbsp;|&nbsp; "
    "runner &nbsp;|&nbsp; otp &nbsp;|&nbsp; bugs &nbsp;|&nbsp; reports "
    "&nbsp;|&nbsp; ai &nbsp;|&nbsp; admin</span></div></div>"
    '<div class="afan">'
    '<div class="abox sm"><b>Playwright runner</b>'
    "<span>Headless Chromium<br>crawl + 16 checks</span></div>"
    '<div class="abox sm"><b>Manual run worker</b>'
    "<span>Headed Chrome<br>human + evidence</span></div>"
    '<div class="abox sm"><b>JobManager</b>'
    "<span>ThreadPoolExecutor<br>2 workers, polling</span></div>"
    '<div class="abox sm ac2"><b>Google Gemini</b>'
    "<span>Bug analysis<br>test suggestions</span></div>"
    "</div>"
    '<div class="arow"><div class="abox hi2"><b>MySQL &mdash; qa_platform</b>'
    "<span>users &bull; projects &bull; test_cases &bull; test_runs &bull; "
    "crawled_pages &bull; manual_step_evidence &bull; manual_issues &bull; "
    "bugs &bull; otp_sessions &bull; login_history</span></div></div>"
    '<div class="arow"><div class="abox lt"><b>Evidence store &mdash; '
    "static/uploads</b><span>Screenshots, cropped findings, saved browser "
    "sessions</span></div></div>"
    "</div>",
    notes="Note the two separate worker paths on the left of the fan-out: one "
          "headless and machine-driven, one headed and human-driven. They share "
          "the same database but never share a verdict.",
)

# ---- 4. Section: Manual testing -----------------------------------------
section(
    "03",
    "Manual Testing",
    "A human tester drives a real, visible browser. The platform supplies the "
    "checklist, captures the evidence, and quietly automates everything around "
    "the tester.",
    [
        "Headed Chrome opened by the backend, not a new tab",
        "Written steps become a live pass / fail checklist",
        "Every navigation screenshotted automatically",
        "Automatic checks run alongside &mdash; but never decide the verdict",
    ],
)

slide(
    "MANUAL TESTING",
    "How manual testing works in QA Platform",
    flow([
        "Create a <b>manual</b> test case: title, steps, expected result",
        "Click <b>Run Manual Test</b> &rarr; backend opens a headed Chrome",
        "Tester logs in by hand (SSO, OTP, captcha &mdash; all fine)",
        "Tester walks the checklist, ticking pass / fail / skip per step",
        "Platform screenshots every page and runs automatic checks",
        "Optional: report a visual issue with expected vs actual",
        "Answer the expected-result question: <b>Met</b> or <b>Not met</b>",
        "Submit &rarr; run row, step evidence and issues written to MySQL",
    ], accent="orange")
    + callout(
        "The tester supplies <b>judgement</b>. The platform supplies "
        "<b>consistency</b> &mdash; the checklist, the evidence trail, the "
        "storage, and the checks a human would forget on page 12.",
        kind="warn", title="Division of labour"),
    notes="The single most important property: the human's verdict is "
          "authoritative. Automatic findings are shown to inform, never to "
          "override.",
)

slide(
    "MANUAL TESTING &mdash; CODE",
    "Step 1&ndash;2: the test case contract and the browser launch",
    '<div class="two-58">'
    '<div>'
    + code(
        '''# backend/modules/testcases/routes.py : 120
steps = (data.get('steps') or '').strip()
if test_type == 'manual' and not steps:
    return jsonify({'error': 'Steps are required for manual tests'}), 400

expected_result = (data.get('expected_result') or '').strip()
if test_type == 'manual' and not expected_result:
    return jsonify({'error': 'Expected result is required'}), 400

# Automated runs crawl the site by contract. The UI never exposes
# crawling as a separate yes/no -- an automated test always crawls
# up to max_pages -- so trusting the client here would let a request
# ask for an automated test that silently audits a single page.
crawl_pages = (test_type == 'automated')''',
        caption="Manual cases must declare steps + expected result; "
                "crawl_pages is derived server-side, never trusted from the client.",
    )
    + '</div><div>'
    + code(
        '''# backend/modules/runner/manual_run.py : 347
with sync_playwright() as p:
    # Prefer the user's real installed Chrome so the fingerprint is
    # indistinguishable from a normal browser; fall back to bundled
    # Chromium if Chrome isn't installed.
    stealth_args = ['--start-maximized',
                    '--disable-blink-features=AutomationControlled']
    browser = p.chromium.launch(
        channel='chrome', headless=False, args=stealth_args,
    )
    context = browser.new_context(no_viewport=True)
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', "
        "{get: () => undefined});"
    )
    page = context.new_page()''',
        caption="Headed real Chrome, driven by the backend on its own thread.",
    )
    + '</div></div>',
    notes="Two details worth calling out: the server refuses to accept a "
          "manual case with no steps, and it derives crawl_pages itself so a "
          "crafted request cannot fake an automated test.",
)

slide(
    "MANUAL TESTING &mdash; CODE",
    "The auto-crawl trigger: never crawl before the tester has logged in",
    code(
        '''# backend/modules/runner/manual_run.py : 158
def should_auto_trigger(current_url, entry_url, stable_secs,
                        navigated, threshold=None):
    """Decide whether to start the automatic crawl.

    The crawl fires only once the tester has actually gone somewhere.
    The old rule -- "off the login page and stable for 5s" -- also
    fired when the run *started* on a page that merely looks public:
    a login form served at '/' or a custom auth path the login
    detector does not recognise. That crawled before the tester had
    logged in. Sitting still on the entry page is never evidence of
    being logged in; navigating is.
    """
    if threshold is None:
        threshold = AUTO_TRIGGER_STABLE_SECS
    if _looks_like_login(current_url):
        return False
    if stable_secs < threshold:
        return False
    if not navigated and _same_url(current_url, entry_url):
        return False
    return True''',
        caption="This function was rewritten to fix a real bug we hit in "
                "testing. See the Challenges section.",
    )
    + grid([
        ("orange", "The bug", "A run started on a page that <i>looked</i> "
         "public &mdash; a login form served at <code>/</code> &mdash; passed "
         "the detector, so the crawler started before the tester had "
         "authenticated."),
        ("ok", "The fix", "Navigation is now required, not just a "
         "non-login-looking URL. Sitting still never triggers; only actually "
         "going somewhere does."),
        ("blue", "The crawl itself", "Phase 2 is a BFS crawl using "
         "<b>in-app link clicks</b> rather than <code>page.goto</code>, so "
         "React stays mounted and the auth session survives the whole crawl."),
    ], cols=3),
    notes="Good example of a bug that only appears when you actually run the "
          "product. The rule is now 'navigated', not 'looks logged in'.",
)

slide(
    "MANUAL TESTING &mdash; CODE",
    "The verdict contract: human judgement is never overwritten",
    code(
        '''# backend/modules/runner/routes.py : 2477
issues_found = len(functional) + len(reported) + \\
               len(snap.get('auto_findings') or [])

# Manual verdict depends ONLY on the tester's steps, expected-result
# decision and reported issues. Automated evidence is stored alongside
# but never overrides it (see manual_run.summarise_manual).
if snap.get('expected_met') is False:
    verdict_reason = 'Tester marked the expected result as not met.'
elif any(s['status'] == 'failed'
         for s in (snap.get('steps') or [])):
    n = sum(1 for s in (snap.get('steps') or [])
            if s['status'] == 'failed')
    verdict_reason = f'{n} manual step(s) failed.'
elif reported:
    verdict_reason = f'{len(reported)} issue(s) reported by the tester.'

# Persisted with an explicit provenance stamp:
#   run_type       = 'MANUAL'
#   execution_mode = 'HEADED'
#   source         = 'HUMAN'  (or 'HYBRID' if auto-findings exist)''',
        caption="The UI labels the human column "
                "&ldquo;HUMAN OBSERVATION (decides the verdict)&rdquo; to make "
                "the precedence visible to the tester.",
    )
    + callout(
        "<b>Why this matters:</b> if automatic checks could flip a manual "
        "verdict, testers would stop trusting the report. A tool that "
        "overrides the human is a tool the team routes around.",
        kind="ok", title="Trust"),
    notes="This is a design decision, not an accident. It is enforced in the "
          "backend, in the UI label, and in the database row itself.",
)

slide(
    "MANUAL TESTING",
    "What the platform automates <i>around</i> the human",
    table(
        ["Automated by the platform", "Judged by the tester"],
        [
            ["Opens and manages the real Chrome window", "Is the flow usable and understandable?"],
            ["Turns the written steps into a live checklist", "Does each step actually do what it claims?"],
            ["Screenshots every page navigation, automatically", "Does the page look and feel correct?"],
            ["Runs alt-text, SEO, axe-core a11y, console, URL, UI and code checks",
             "Is the content right, the copy correct, the tone appropriate?"],
            ["Crawls deeper pages with BFS once login is done",
             "Is anything confusing, misleading or unexpectedly missing?"],
            ["Captures console output and timings as supporting evidence",
             "Is this a defect worth tracking, and how severe is it?"],
            ["Stores steps, notes, screenshots and issues in MySQL",
             "Met or not met &mdash; the one input that sets Pass / Fail"],
        ],
        cls="tight",
    )
    + callout(
        "Security-header and mobile checks are deliberately <b>skipped</b> in "
        "manual mode. A headed desktop window would report every one of them "
        "falsely &mdash; a desktop viewport cannot fail a 44&nbsp;px tap-target "
        "test. Running them would be noise, and noise trains people to ignore "
        "the report.",
        kind="warn", title="A deliberate omission"),
    notes="This slide answers the question 'isn't this automated anyway?' Yes "
          "for everything mechanical, no for everything requiring judgement.",
)

slide(
    "SEPARATION",
    "Keeping manual and automated results apart &mdash; four layers",
    '<div class="four">'
    '<div class="fq"><div class="fqn">1</div><div class="fqt">Entry guard</div>'
    "<div class=\"fqd\">The synchronous automated endpoint rejects any case "
    "whose <code>test_type != 'automated'</code>, and the manual start "
    "endpoint rejects unknown cases. Neither can be tricked by the other."
    "</div></div>"
    '<div class="fq"><div class="fqn">2</div><div class="fqt">Type enum</div>'
    '<div class="fqd">A validator accepts only <code>manual</code> or '
    "<code>automated</code>. Manual runs have no automation framework field "
    "to fill in at all.</div></div>"
    '<div class="fq"><div class="fqn">3</div><div class="fqt">Storage</div>'
    '<div class="fqd">Manual evidence lands in dedicated tables &mdash; '
    "<code>manual_step_evidence</code> and <code>manual_issues</code> &mdash; "
    "and every finding is stamped <code>source='MANUAL'</code>. Automated "
    "regression logic explicitly refuses to compare against it.</div></div>"
    '<div class="fq"><div class="fqn">4</div><div class="fqt">Reporting</div>'
    '<div class="fqd">Every finding renders with a MANUAL / AUTOMATED badge, '
    "and the reports API filters on <code>run_type</code> and "
    "<code>source</code>.</div></div>"
    "</div>"
    + callout(
        "A dedicated test file, <code>tests/test_run_separation.py</code>, "
        "asserts this contract so a future refactor cannot quietly merge the "
        "two streams.",
        kind="info"),
    notes="Four independent layers. Any one of them alone would be "
          "convention; four together make it a guarantee.",
)

# ---- 5. Section: Automation ---------------------------------------------
section(
    "04",
    "Automated Testing",
    "A Playwright crawler drives headless Chromium through a site and applies "
    "16 independent checks per page, then scores and persists the result.",
    [
        "Asynchronous jobs, polled live by the React UI",
        "Authenticated crawling by replaying a saved browser session",
        "axe-core WCAG 2.1 A/AA audit injected into every page",
        "De-duplicated findings and a weighted health score",
    ],
)

slide(
    "AUTOMATED TESTING",
    "The engine pipeline",
    flow([
        "Queue a job &rarr; return <b>202</b> + job id immediately",
        "Worker thread opens headless Chromium",
        "Replay saved session, or crawl anonymously",
        "BFS the site up to <code>max_pages</code> (1&ndash;100)",
        "Run 16 checks per page, capture evidence",
        "De-duplicate, score, compare to last run",
        "Write run + per-page rows to MySQL",
        "UI polls until <b>done</b>, then shows the result",
    ], accent="blue")
    + grid([
        ("blue", "Async by design", "A 75-page crawl can take 40 minutes. "
         "Holding an HTTP request open for that is not viable, so the API "
         "returns <code>202</code> and the browser polls."),
        ("ok", "Cancellable", "<code>POST /job/&lt;id&gt;/cancel</code> sets "
         "a flag. The worker finishes the current page, persists partial "
         "results, and exits cleanly."),
        ("warn", "Same page object", "The crawl deliberately reuses one page "
         "for the whole run instead of closing it, so React stays mounted and "
         "<code>sessionStorage</code> survives between routes."),
    ], cols=3),
    notes="Emphasise the reuse of a single page object -- that is what makes "
          "authenticated SPA crawling work at all.",
)

slide(
    "AUTOMATED TESTING &mdash; CODE",
    "Asynchronous execution: submit, then poll",
    '<div class="two-45">'
    '<div>'
    + code(
        '''# backend/modules/runner/routes.py : 2201
@runner_bp.route('/run/<int:testcase_id>/async',
                 methods=['POST'])
@jwt_required()
def run_test_async(testcase_id):
    """Queue the run on a background worker and return a job id."""
    user_id = int(get_jwt_identity())
    tc = user_owns_testcase(user_id, testcase_id)
    if not tc:
        return jsonify({'error': 'Test case not found'}), 404
    if tc['test_type'] != 'automated':
        return jsonify({'error':
            'Only automated test cases can be run'}), 400
    ok, reason = _is_allowed_test_url(tc.get('base_url'))
    if not ok:
        return jsonify({'error': reason,
                        'code': 'invalid_url'}), 400

    job_id = job_manager.submit(user_id, testcase_id, dict(tc))
    return jsonify({'job_id': job_id, 'status': 'queued'}), 202''',
        caption="Ownership, type and SSRF checks all happen before a job is "
                "ever queued.",
    )
    + '</div><div>'
    + code(
        '''# frontend/src/pages/TestCases.jsx : 731
const pollRunJob = (tcId, jobId) =>
  new Promise((resolve, reject) => {
    const tick = async () => {
      const res = await getRunJob(jobId);
      const job = res.data;
      if (job.progress) setRunProgress(tcId, job.progress);
      if (job.status === 'done' || job.status === 'failed' ||
          job.status === 'cancelled') {
        resolve(job);
      } else {
        setTimeout(tick, 2000);
      }''',
        lang="js", caption="Live progress every 2 seconds; NaN-safe timing.",
    )
    + callout(
        "A <code>404</code> mid-poll now reports <i>&ldquo;the run was "
        "interrupted &mdash; the backend restarted&rdquo;</i> instead of "
        "<i>&ldquo;Job not found&rdquo;</i>. The in-memory job registry is a "
        "known limitation; a <code>run_jobs</code> table is the planned fix.",
        kind="warn", title="Honest error handling")
    + '</div></div>',
    notes="Mention that ownership and SSRF validation happen at submission, not "
          "in the worker, so a bad request never consumes a worker slot.",
)

slide(
    "AUTOMATED TESTING &mdash; CODE",
    "The background worker",
    code(
        '''# backend/modules/runner/jobs.py : 32
class JobManager:
    """Thread-backed job registry. In-memory: fine for a single
    process. For multiple processes move job state to Redis/DB."""
    def init(self, app, max_workers=2):
        self._executor = ThreadPoolExecutor(max_workers,
                                            thread_name_prefix='runner')

    def submit(self, user_id, testcase_id, tc):
        job_id = uuid4().hex
        with self._lock:
            self._jobs[job_id] = {
                'job_id': job_id, 'status': 'queued',
                'cancel_requested': False, ...}
        try:
            self._executor.submit(self._run, job_id, testcase_id, tc)
        except RuntimeError:      # pool already shut down
            self._jobs.pop(job_id)  # never leave a job stuck "queued"
        return job_id

    def _run(self, job_id, testcase_id, tc):
        # Each worker thread needs its own Flask app context, else
        # mysql.connection cannot bind on that thread.
        with self._app.app_context():
            result, code = _perform_run(testcase_id, tc,
                progress_cb=_progress,       # -> job['progress']
                cancelled_check=_cancelled)  # -> cooperative cancel''',
        caption="Two workers, a lock around the registry, and LRU eviction "
                "that never drops a live job.",
    )
    + grid([
        ("blue", "State machine",
         "<code>queued &rarr; running &rarr; done | failed | cancelled</code>. "
         "Cancellation wins over a success return code."),
        ("warn", "Crash handling",
         "Any worker exception becomes <code>status='failed'</code> with "
         "<code>error_code='worker_crash'</code>."),
        ("ok", "Eviction",
         "Only <i>finished</i> jobs are evicted, oldest first, at 200 "
         "retained. A long crawl is never dropped."),
    ], cols=3),
    notes="The app_context detail is the classic Flask multi-threaded gotcha "
          "-- worth mentioning if the audience is technical.",
)

slide(
    "AUTOMATED TESTING &mdash; CODE",
    "Browser context, session replay and anti-detection",
    code(
        '''# backend/modules/runner/routes.py : 2752
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    # When we have a captured session, use a DESKTOP context that
    # matches the session_capture window. Many sites bind auth
    # cookies to the originating UA -- using a mobile UA
    # invalidates the session. Without a session we keep the mobile
    # viewport so the mobile-issues checks still mean something.
    if session_used:
        context_kwargs = {
            'viewport': {'width': 1366, 'height': 768},
            'user_agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                           '...Chrome/131.0.0.0 Safari/537.36'),
            'storage_state': session_path,
        }
    else:
        context_kwargs = {
            'viewport': {'width': 390, 'height': 844},   # iPhone
            'user_agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 '
                          'like Mac OS X)',
        }
    context = browser.new_context(**context_kwargs)
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', "
        "{get: () => undefined});")''',
        caption="The viewport choice is not cosmetic: it decides whether the "
                "mobile tap-target checks are meaningful or pure noise.",
    )
    + callout(
        "<b>Why 1366&times;768 for authenticated runs?</b> Mobile checks "
        "(44&nbsp;px tap targets, horizontal scroll) are meaningless in a "
        "desktop viewport, so they are skipped. An earlier version ran them "
        "anyway and produced a page of false positives on every authenticated "
        "crawl.",
        kind="info", title="Design note"),
    notes="This is the fix from the 'useless mobile findings' problem. Desktop "
          "context for auth, mobile context for anonymous -- and the checks "
          "follow the context.",
)

slide(
    "AUTOMATED TESTING",
    "The check matrix &mdash; 16 checks per page",
    table(
        ["#", "Check function", "Detects", "Severity ceiling"],
        [
            ["1", "<code>check_broken_links</code>", "Dead links, tested with the logged-in user's cookies and real User-Agent, 8-way parallel, HEAD&rarr;GET fallback", "serious"],
            ["2", "<code>check_missing_alt</code>", "Images with no alt text (skips decorative, aria-hidden, &lt;32&nbsp;px)", "minor"],
            ["3", "<code>check_seo</code>", "Missing title, missing/empty meta description, missing h1", "moderate"],
            ["4", "<code>check_security</code>", "Non-HTTPS, missing CSP / X-Frame-Options / HSTS", "critical"],
            ["5", "<code>check_accessibility_axe</code>", "<b>axe-core</b> WCAG 2.1 A/AA violations, with a heuristic fallback", "from axe impact"],
            ["6", "<code>check_mobile</code>", "Horizontal scroll, tap targets under 44&times;44&nbsp;px", "serious"],
            ["7", "<code>check_dead_controls</code>", "Buttons and links that do nothing (opt-in &mdash; can submit forms or log you out)", "serious"],
            ["8", "<code>check_performance</code>", "Slow pages, heavy payloads, heaviest resources", "serious"],
            ["9", "<code>filter_console_errors</code>", "Real console errors, with extension/deprecaton noise filtered out", "serious"],
            ["10", "<code>check_api_responses</code>", "4xx / 5xx API responses with human-readable status meanings", "critical"],
            ["11", "<code>check_form_validation</code>", "Required fields with no <code>checkValidity()</code> enforcement", "serious"],
            ["12", "<code>check_url_health</code>", "Off-site redirects, session IDs in query strings, malformed URLs", "serious"],
            ["13", "<code>check_api_quality</code>", "Slow APIs, mixed content, APIs returning HTML, repeated failures", "serious"],
            ["14", "<code>check_page_resources</code>", "Empty src slots, dead <code>#fragment</code> anchors", "moderate"],
            ["15", "<code>check_ui_consistency</code>", "Duplicate element IDs, empty buttons, missing viewport meta, overflow", "serious"],
            ["16", "<code>check_code_quality</code>", "Inline <code>onclick</code>, <code>eval()</code>, deprecated tags, jQuery &lt; 3", "serious"],
        ],
        cls="tiny",
    ),
    notes="Sixteen checks, all independent, all degrading gracefully: if one "
          "throws, the page still reports the other fifteen.",
)

slide(
    "ISSUE DETECTION",
    "How a page is actually audited: the fan-out",
    '<div class="two-58">'
    '<div>'
    + code(
        '''# backend/modules/runner/routes.py : 1850
def test_single_page(page, page_url, response_headers,
                     console_messages, page_load_ms=0,
                     api_responses=None, mobile_checked=None,
                     page_status=0):
    """Run every check against one loaded page.

    Returns (findings_by_category, coverage). `coverage` records
    what was capped or skipped so the report can say so out loud
    instead of letting a partial check look like a clean bill of
    health."""
    if mobile_checked is None:
        mobile_checked = bool(is_mobile)
    broken_links, link_coverage = check_broken_links(page, page_url)
    missing_alt = check_missing_alt(page)
    seo_issues = check_seo(page, page_url)
    security_issues = check_security(page_url, response_headers)
    accessibility_issues, a11y_engine = check_accessibility(page)

    if mobile_checked:
        mobile_issues, mobile_coverage = check_mobile(page)
        mobile_skipped_reason = None
    else:
        mobile_issues, mobile_coverage = [], {}
        mobile_skipped_reason = ('disabled: page was audited '
                                 'in a desktop viewport')''',
        caption="One loaded page in, 15 categories of findings out. Every "
                "check has the same contract: (page, ...) -&gt; list[finding].",
    )
    + '</div><div>'
    + '<div class="meth"><div class="mt">1 &nbsp;JS executed inside the page</div>'
    '<div class="md">Most checks call <code>page.evaluate("() =&gt; ...")</code>. '
    "Example &mdash; <code>check_form_validation</code> blanks every "
    "<code>required</code> control, calls <code>checkValidity()</code>, then "
    "reports a form that declares required fields yet accepts an empty "
    "submission.</div></div>"
    + '<div class="meth"><div class="mt">2 &nbsp;A third-party engine</div>'
    '<div class="md">axe-core is injected with <code>add_script_tag</code> and '
    "runs <code>axe.run()</code> for the WCAG tags. Its violations come back "
    "as findings, tagged with the runner's own severities.</div></div>"
    + '<div class="meth"><div class="mt">3 &nbsp;Out-of-band observation</div>'
    '<div class="md">Console errors, network responses, HTTP status and timing '
    "<b>cannot be read from the DOM at all</b>. The crawler attaches "
    "<code>page.on('console')</code>, <code>on('request')</code> and "
    "<code>on('response')</code> once, and those collected messages are "
    "<i>passed into</i> this function.</div></div>"
    + callout(
        "The crawler is a <b>passive listener first and an active prober "
        "second</b> &mdash; it watches everything that happens while the page "
        "loads, then asks questions. The one check that can say &ldquo;the "
        "feature is broken&rdquo; is the step executor: a failed step becomes "
        "a <code>functional_issues</code> finding rated <b>critical</b>.",
        kind="info")
    + '</div></div>',
    notes="The passive-listener point is the one people miss. Console and API "
          "detection are impossible without those listeners, and they are "
          "registered before the first navigation, not per page.",
)

slide(
    "SHARED DETECTION",
    "The 8 issue categories detected in <i>both</i> modes",
    '<p class="lead">One detection library, two policies. '
    "<code>_auto_check()</code> in <code>manual_run.py:593</code> imports the "
    "<i>same functions</i> the crawler calls, so these eight categories are "
    "found in a manual run and an automated run alike.</p>"
    + table(
        ["Category", "What it actually finds", "Severity"],
        [
            ["<b>Missing alt images</b>", "<code>&lt;img&gt;</code> with no <code>alt</code> &mdash; excluding decorative (<code>alt=\"\"</code>), <code>role=presentation</code>, <code>aria-hidden</code>, and images under 32&nbsp;px", "minor"],
            ["<b>SEO</b>", "Missing <code>&lt;title&gt;</code>, missing or empty <code>meta[name=description]</code>, missing <code>&lt;h1&gt;</code>", "moderate"],
            ["<b>Accessibility</b>", "axe-core 4.10.2 WCAG 2.1 A/AA violations (<code>wcag2a, wcag2aa, wcag21a, wcag21aa</code>), violations only. <code>image-alt</code> excluded &mdash; owned by missing-alt", "axe's own <code>impact</code>"],
            ["<b>Console errors</b>", "Real JavaScript errors, with extension and deprecation noise filtered out. Mixed content is deliberately <i>not</i> skipped", "serious"],
            ["<b>URL health</b>", "Off-site 301/302/307/308 redirects, <b>session IDs in the query string</b> (<code>jsessionid</code>, <code>phpsessid</code>, <code>sid</code>), unencoded spaces, URLs over 2000 chars, uppercase path", "serious&ndash;minor"],
            ["<b>UI consistency</b>", "Duplicate element IDs, empty buttons and links, missing <code>&lt;meta viewport&gt;</code>, missing <code>html lang</code>, horizontal overflow", "serious&ndash;minor"],
            ["<b>Code quality</b>", "Inline <code>onclick=</code>, <code>eval()</code> / <code>document.write()</code>, deprecated tags, inline scripts over 50&nbsp;KB, <b>jQuery &lt; 3.x</b> (known XSS CVEs)", "serious&ndash;minor"],
            ["<b>Broken links</b><br><span class='tiny-note'>auto-crawl phase only</span>", "Dead links tested with the logged-in user's cookies and real User-Agent, 8-way parallel, HEAD&rarr;GET fallback, and <b>401/403/429/503 are not counted as broken</b>. Plus empty <code>src</code> slots, dead <code>#fragment</code> anchors, malformed <code>mailto:</code>", "serious"],
        ],
        cls="tiny",
    )
    + callout(
        "In manual mode these run as a silent co-pilot: shown beside the "
        "tester's own observations, counted in the report, and "
        "<b>never</b> allowed to change the verdict. Broken links wait for the "
        "auto-crawl phase because they fire up to 30 HTTP requests and would "
        "stall the live tracker.",
        kind="ok", title="Same checks, different authority"),
    notes="Land the framing sentence: it is not two detection systems, it is "
          "one library with two policies about who is allowed to conclude.",
)

slide(
    "SPLIT DETECTION",
    "What only automation can see &mdash; and what only a human can",
    table(
        ["Automated-only category", "Why a manual session cannot run it"],
        [
            ["<b>Security headers</b>", "Needs real response headers. A manual session has none, so the check would report <i>every</i> header missing on <i>every</i> page &mdash; a pure false positive."],
            ["<b>Mobile</b>", "The tester's window is a desktop viewport, where every ordinary control looks like an undersized 44&nbsp;px tap target."],
            ["<b>Performance</b>", "Needs <code>page_load_ms</code> measured by the crawler. Timing sampled mid-manual-browsing is meaningless."],
            ["<b>API issues</b>", "Needs the <code>request</code>/<code>response</code> listener that records <code>elapsed_ms</code>, status and content-type. Manual mode does not attach it."],
            ["<b>Form validation</b>", "The blank-every-field <code>checkValidity()</code> probe is disruptive on a live interactive session."],
            ["<b>Functional (steps)</b>", "Automation <i>executes</i> your written steps. In manual mode a human ticks the same steps instead."],
            ["<b>Dead controls</b>", "Opt-in per test case: clicking controls on a live site can submit a form or log the crawler out."],
        ],
        cls="tiny",
    )
    + grid([
        ("orange", "Human-only &mdash; judgement", "Three channels no machine "
         "has: the <b>step checklist</b> (passed / failed / skipped), the "
         "<b>expected-result decision</b> (met or not met), and <b>reported "
         "issues</b> with a category and severity the tester chooses."),
        ("blue", "Automated-only &mdash; reach", "The crawler reaches 100 pages "
         "in minutes and re-runs the identical audit after every hotfix. No "
         "tester could do that consistently."),
    ], cols=2)
    + '<p class="lead" style="margin-top:12px">Visual and UX quality, copy '
      "accuracy, and &ldquo;does this feel right&rdquo; sit in the second "
      "column and nowhere else. That asymmetry is the entire argument for "
      "running both modes.</p>",
    notes="This is the slide that answers 'why bother with manual testing if "
          "you have automation'. The left column is reach; the right column "
          "is judgement. Neither substitutes for the other.",
)

slide(
    "AUTOMATED TESTING &mdash; CODE",
    "Accessibility: axe-core injected into the live page",
    code(
        '''# backend/modules/runner/routes.py : 642
AXE_PATH = os.path.join(os.path.dirname(__file__), 'axe.min.js')
# image-alt is owned by check_missing_alt already -- running both would
# charge the score twice for one defect.
AXE_RULES_COVERED_ELSEWHERE = {'image-alt'}

def check_accessibility_axe(page):
    if not os.path.exists(AXE_PATH):
        return None
    try:
        page.add_script_tag(path=AXE_PATH)   # inject axe into the page
        # axe.run() returns a Promise; Playwright auto-awaits it
        results = page.evaluate(
            """() => axe.run(document, {
                runOnly: { type: 'tag',
                    values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
                resultTypes: ['violations']
            }).then(r => r.violations)"""
        )
    except Exception as e:
        logger.warning("axe-core failed, using heuristic a11y: %s", e)
        return None            # caller falls back to heuristics''',
        caption="axe-core 4.10.2 is vendored as a file &mdash; no npm install, "
                "no CDN call, works offline.",
    )
    + '<div class="two-58 tight-cols">'
    + bullets([
        ("Tags run: ", "<code>wcag2a, wcag2aa, wcag21a, wcag21aa</code> "
         "&mdash; WCAG 2.1 A and AA criteria, <code>violations</code> only."),
        ("Severity and weight: ", "axe's own <code>impact</code> becomes the "
         "runner severity, and the count of failing elements feeds a "
         "logarithmic weight."),
        ("Fallback: ", "if the script fails to load, a heuristic check runs "
         "&mdash; unlabelled buttons and inputs. Placeholder text is "
         "deliberately <i>not</i> accepted as a label."),
        ("Scoring integrity: ", "if axe never ran, the category is left out of "
         "the weighted average entirely. A check that never ran is not a "
         "check that passed."),
    ])
    + '</div>',
    notes="The scoring-integrity point is subtle and worth landing: a broken "
          "tool must never be able to raise the score.",
)

slide(
    "AUTOMATED TESTING &mdash; CODE",
    "Judgement inside a check: the security header logic",
    code(
        '''# backend/modules/runner/routes.py : 600
def check_security(page_url, response_headers):
    findings = []
    is_https = page_url.startswith('https://')
    if not is_https:
        findings.append({
            'issue': 'Site is using insecure HTTP instead of HTTPS',
            'severity': 'critical', 'header': 'protocol',
        })
    headers_lower = {k.lower(): v for k, v in response_headers.items()}
    csp = (headers_lower.get('content-security-policy') or '').lower()
    missing = []
    if 'content-security-policy' not in headers_lower:
        missing.append({'header': 'Content-Security-Policy',
                        'purpose': 'Protects against XSS'})
    # CSP frame-ancestors is the modern replacement for
    # X-Frame-Options. A site using it IS protected and must not
    # be told otherwise.
    if 'x-frame-options' not in headers_lower \\
       and 'frame-ancestors' not in csp:
        missing.append({'header': 'X-Frame-Options',
                        'purpose': 'Prevents clickjacking'})
    # HSTS only means anything over HTTPS. On an HTTP page the
    # protocol is already reported above, and charging twice for
    # one root cause drags the score down for a single fault.
    if is_https and 'strict-transport-security' not in headers_lower:
        missing.append({'header': 'HSTS', 'purpose': 'Forces HTTPS'})
    return findings''',
        caption="This is what stops a security check from being a naive "
                "header-presence linter.",
    )
    + bullets([
        ("No false positive on modern CSP &mdash;", "a site using "
         "<code>frame-ancestors</code> is not told it is missing "
         "X-Frame-Options."),
        ("No double-charging &mdash;", "HTTP-and-no-HSTS is one root cause, "
         "not two penalties."),
        ("Escalation &mdash;", "plain HTTP is the only security finding rated "
         "critical; the aggregated header gap is serious."),
    ]),    notes="This function is a good example of the whole project's philosophy: "
          "a check that cries wolf is worse than no check.",
)

slide(
    "AUTOMATED TESTING &mdash; CODE",
    "Written steps become real browser actions",
    code(
        '''# backend/modules/runner/steps.py : 13
    Open https://shop.example.com
    Type alice@example.com into Email
    Type hunter2 into Password
    Click Sign in
    Expect text Welcome back
    Expect url contains /dashboard

    Select Extra Large in Size
    Tick the Terms checkbox
    Expect no "Item removed"     (quoting keeps multi-word values intact)

def parse_step(line):
    raw = (line or '').strip()
    stripped = _NUMBERING.sub('', raw).strip()   # drop "1." / "2)"
    for action, pattern in _PATTERNS:      # first match wins
        match = pattern.match(stripped)
        if not match:
            continue
        g = match.groupdict()
        return {'raw': raw, 'action': action,
                'target': _clean(g.get('target')),
                'value':  _clean(g.get('value'))}
    # A bad line is data, not a crash -- and it is NOT a passing step.
    return {'raw': raw, 'action': 'unknown', 'target': '', 'value': ''}''',
        caption="Plain English, one action per line, 15 supported actions. "
                "This catches functional failures a pure crawler never could.",
    )
    + grid([
        ("blue", "Stop at first failure", "Later steps are recorded as "
         "<i>skipped</i>, not re-reported as new defects &mdash; downstream "
         "failures are consequences, not bugs."),
        ("ok", "Unreadable is not failing", "A step the parser cannot read is "
         "<b>not</b> charged against the site. It never reached the "
         "application, so it surfaces in the coverage notes instead."),
        ("warn", "Smart locator resolution", "Targets resolve by role, then "
         "label, then placeholder, then <code>name</code>, then text."),
    ], cols=3),
    notes="The distinction between 'unreadable' and 'failed' is important: it "
          "stops a typo in the test case from being reported as an "
          "application defect.",
)

slide(
    "AUTOMATED TESTING &mdash; CODE",
    "Navigating a modern SPA without losing the session",
    code(
        '''# backend/modules/runner/routes.py : 2993
if page_idx == 0:
    # First navigation: full HTTP load so we can capture security
    # headers and verify the page is reachable.
    response = page.goto(page_url, timeout=30000,
                         wait_until='domcontentloaded')
    page_status = response.status
    response_headers = dict(response.headers)
    saved_first_headers = dict(response.headers)
else:
    # SPA-internal click navigation: keeps React mounted and auth
    # state alive. Falls back to page.goto inside navigate_spa if
    # no matching link exists on the current page.
    _, response = navigate_spa(page, page_url)
    if response is not None:
        page_status = response.status
        response_headers = dict(response.headers)
    else:
        page_status = 200                     # click nav succeeded
        response_headers = saved_first_headers # CSP/HSTS unchanged
page_load_ms = int((time.time() - nav_start) * 1000)

# Wait for the DOM to stop changing rather than guessing a duration.
page.wait_for_load_state('networkidle', timeout=5000)
wait_for_page_settled(page)   # polls href + node count every 50ms''',
        caption="The crawl clicks links instead of reloading, so "
                "sessionStorage and in-memory auth state survive every route.",
    )
    + bullets([
        ("<code>navigate_spa</code>", "finds a matching "
         "<code>&lt;a&gt;</code> and clicks it &mdash; trailing-slash "
         "insensitive, so <code>/about</code> and <code>/about/</code> both "
         "stay on the click path."),
        ("<code>wait_for_page_settled</code>", "polls "
         "<code>location.href + '|' + querySelectorAll('*').length</code> "
         "until it is steady for a quiet window. Fixed sleeps were the old "
         "approach and they were wrong."),
    ]),
    notes="Everything about this is about authenticated SPAs: click instead of "
          "reload, settle instead of sleep.",
)

slide(
    "AUTOMATED TESTING &mdash; CODE",
    "The health score: why it is not a naive count",
    '<div class="two-45">'
    '<div>'
    + code(
        '''# backend/modules/runner/routes.py : 1303
def finding_weight(finding):
    """axe reports one finding per rule together with the number
    of elements breaking it, so a rule broken by 200 elements has
    to cost more than one broken by a single element."""
    count = 1
    if isinstance(finding, dict):
        raw = finding.get('count')
        if isinstance(raw, int) and raw > 1:
            count = raw
    return 1 + math.log10(count)


def category_subscore(category, items):
    """Score out of 100 with diminishing returns:
       100 * SCALE / (penalty + SCALE).
       The old score was 100 - count*weight, which hit zero on
       any real site and made a page with 20 problems look
       identical to one with 200."""
    penalty = category_penalty(category, items)
    return int(round(100 * SCORE_SCALE / (penalty + SCORE_SCALE)))''',
        caption="SEVERITY_PENALTY = critical 10, serious 5, moderate 2, "
                "minor 1. SCORE_SCALE = 20.",
    )
    + '</div><div>'
    + code(
        '''# backend/modules/runner/routes.py : 1393
def calculate_health_score(findings_by_category, measured=None):
    """Weighted average of the per-category subscores, 1..100.

    `measured` names the categories this run actually checked.
    Anything outside it is left out of the average rather than
    counted as perfect, because a check that never ran is not a
    check that passed."""
    total_weight = weighted_sum = 0
    for category, weight in CATEGORY_WEIGHT.items():
        if measured is not None and category not in measured:
            continue
        items = findings_by_category.get(category) or []
        weighted_sum += weight * category_subscore(category, items)
        total_weight += weight
    if not total_weight:
        return 100
    return max(1, min(100, int(round(weighted_sum / total_weight))))''',
        caption="15 categories, weights summing to 100: functional 25, "
                "security 16, accessibility 14, broken links 13, console 11, "
                "API 10, validation 10, performance 9, execution 8, URL 8, "
                "UI 7, code 7, mobile 6, SEO 4, missing-alt 2.",
    )
    + '</div></div>'
    + callout(
        "The old linear formula saturated at zero. Two sites with 20 issues "
        "and 200 issues both scored 0 &mdash; which destroyed the ability to "
        "compare two runs, the only thing a trend chart is for.",
        kind="info", title="The problem this fixed"),
    notes="Three ideas here: logarithmic weighting, a curve that never "
          "reaches zero, and excluding categories that were never measured.",
)

slide(
    "AUTOMATED TESTING &mdash; CODE",
    "De-duplication: the same footer bug is not 40 bugs",
    code(
        '''# backend/modules/runner/routes.py : 2156
def merge_findings(all_pages_findings):
    """Combine per-page findings into one summary, de-duplicating
    issues that repeat across pages. The SAME footer broken-link,
    missing security header, or a11y rule appears on every page of
    a site; counting it once per page inflated the issue total and
    wrongly tanked the health score. Per-page detail is still
    preserved in the crawled_pages records -- this only affects
    the summary."""
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
    return merged''',
        caption="De-dup keys are per-category: broken link by URL, security by "
                "header, accessibility by rule (not element selector), and "
                "never by screenshot or timing.",
    )
    + grid([
        ("orange", "The bug", "A single dead <code>/dead</code> link in the "
         "footer was reported <b>once per page</b>. 8 missing-header findings "
         "became 2. Issue counts were inflated and the health score was wrong."),
        ("ok", "The fix", "Per-category de-duplication keys. The key must "
         "never include volatile data &mdash; screenshot path, timing, page "
         "order &mdash; or nothing would ever merge."),
        ("blue", "The verification", "An end-to-end crawl against a "
         "controlled site with deliberate issues: 4 pages crawled, "
         "<code>/dead</code> collapsed to <b>1</b>, security collapsed to "
         "<b>2</b>, and <b>no false positives</b> on the valid pages."),
    ], cols=3),
    notes="This was found because the tool was run against a real 75-page "
          "site and the numbers were obviously wrong. Use the tool on "
          "something real early.",
)

# ---- 6. Section: Technology deep dive -----------------------------------
section(
    "05",
    "Technology Deep Dive",
    "What each piece of technology actually does in the testing pipeline, and "
    "why it was chosen.",
    [
        "Playwright as the single automation engine",
        "axe-core vendored offline for WCAG auditing",
        "ThreadPoolExecutor for job isolation",
        "MySQL as the single source of truth",
    ],
)

slide(
    "TECHNOLOGY",
    "What plays which role",
    table(
        ["Technology", "Role in manual testing", "Role in automated testing"],
        [
            ["<b>Playwright</b> (Python, sync API)", "Launches and manages the headed Chrome window; screenshots every navigation",
             "Launches headless Chromium; executes steps; clicks links; reads DOM, console and network"],
            ["<b>axe-core</b> 4.10.2 (vendored JS)", "Runs on each page the tester visits, as supporting evidence",
             "Injected via <code>add_script_tag</code>; WCAG 2.1 A/AA <code>violations</code> only"],
            ["<b>Threading</b> (<code>ThreadPoolExecutor</code>, 2 workers)", "One dedicated thread owns the manual session; Flask threads only signal it",
             "JobManager worker per run; 8-way pool inside the broken-link check"],
            ["<b>Pillow</b>", "&mdash;", "Crops each full-page screenshot around a finding's bounding box (25&nbsp;px padding)"],
            ["<b>requests</b>", "&mdash;", "Parallel link validation, forwarding the browser's cookies and real User-Agent"],
            ["<b>MySQL</b> (9 tables)", "Run row, step evidence, tester issues, verdict", "Run row, per-page findings, score breakdown, coverage, regression"],
            ["<b>Flask-JWT-Extended</b>", "Authenticates every manual endpoint", "Authenticates the submit and the poll; ownership checked on both"],
            ["<b>ReportLab</b>", "&mdash;", "Server-side PDF export of the report bundle"],
            ["<b>Gemini</b> (REST)", "Can explain a tester-reported issue", "Suggests test cases, analyses machine findings, proposes fixes"],
        ],
        cls="tiny",
    ),
    notes="The interesting row is requests: it is the only place the crawler "
          "leaves the browser, and it has to carry the session with it.",
)

slide(
    "CREDENTIALS",
    "What each mode needs &mdash; and why no API key is involved",
    '<p class="lead"><b>Neither manual nor automated testing uses an API key.</b> '
    "Issue detection makes no call to any third-party API: both modes run "
    "entirely locally against the target site, and both work offline.</p>"
    + '<div class="two-58">'
    + '<div>'
    + table(
        ["Mode", "What it actually requires"],
        [
            ["<b>Automated</b>", "Playwright's locally installed Chromium, MySQL credentials, JWT secret. Nothing else."],
            ["<b>Manual</b>", "The user's installed Google Chrome (falls back to bundled Chromium), MySQL credentials, JWT secret. Nothing else."],
        ],
        cls="tight",
    )
    + code(
        '''# The ONLY outbound call in the entire project.
# backend/modules/ai/service.py : 40
requests.post(
    f'https://generativelanguage.googleapis.com/v1beta'
    f'/models/{_get_model()}:generateContent',
    params={'key': api_key},          # <- the one and only key
    json=payload, timeout=60)''',
        caption="Reached only from modules/ai/. Grepping the runner and "
                "testcases modules for AI references returns zero hits.",
    )
    + '</div><div>'
    + '<div class="meth"><div class="mt">The project has exactly one key</div>'
    '<div class="md"><code>GEMINI_API_KEY</code>, loaded from '
    "<code>backend/.env</code> and consumed at one line. It serves four "
    "advisory endpoints &mdash; <code>analyze-bug</code>, "
    "<code>suggest-tests</code>, <code>suggest-fix</code> and <code>chat</code> "
    "&mdash; every one of them on-demand, none of them in the run path. "
    "<code>GET /api/ai/health</code> is the only endpoint that reads the key "
    "without calling out; it just reports whether one is configured.</div></div>"
    + '<div class="meth"><div class="mt">So the failure modes are separate</div>'
    '<div class="md">If the key is invalid, rate-limited or the credits are '
    "exhausted, <b>automated runs and manual runs are entirely "
    "unaffected</b> &mdash; they keep working. Only the AI page degrades, "
    "mapping to <code>429</code> for rate limits and <code>503</code> for "
    "exhausted credits.</div></div>"
    + '</div></div>'
    + callout(
        "That separation is deliberate, and it is <b>why the results are "
        "trustworthy</b>. A model cannot hallucinate a passing grade, because "
        "no model is anywhere near the scoring path. The health score, the "
        "Pass/Fail verdict and bug creation are all deterministic code &mdash; "
        "the AI only explains and suggests.",
        kind="ok", title="Why this matters more than it looks"),
    notes="Strong slide for a sceptical audience. If someone asks 'what if the "
          "API is down during a demo', the answer is: nothing in the testing "
          "path notices.",
)

slide(
    "TECHNOLOGY &mdash; CODE",
    "Authenticated testing: capture once, replay on every run",
    '<div class="two-58">'
    '<div>'
    + code(
        '''# backend/modules/projects/session_capture.py : 125
os.makedirs(SESSIONS_DIR, exist_ok=True)
path = os.path.join(SESSIONS_DIR, f'{self.project_id}.json')
context.storage_state(path=path)     # cookies + localStorage

# Capture sessionStorage too. Playwright's storage_state only
# saves cookies + localStorage; many SPAs keep their auth flag
# in sessionStorage, so without this the crawler appears
# anonymous to the SPA.
session_storage = page.evaluate(
    "() => { const o={}; for (let i=0;i<sessionStorage.length;i++)"
    " { const k=sessionStorage.key(i);"
    "   o[k]=sessionStorage.getItem(k); } return o; }"
) or {}''',
        caption="Three files per project: &lt;id&gt;.json, "
                "&lt;id&gt;.session_storage.json, and &lt;id&gt;.landing.",
    )
    + '</div><div>'
    + code(
        '''# backend/modules/runner/routes.py : 2798
if session_used:
    ss_data = _session_storage_dict(tc.get('project_id'))
    if ss_data:
        payload = json.dumps(ss_data)
        context.add_init_script(
            f"(() => {{ try {{ const d = {payload}; "
            "for (const k in d) "
            "  sessionStorage.setItem(k, d[k]); "
            "} catch(e) {} }})();"
        )''',
        caption="Replayed on every page load, because Playwright's "
                "storage_state cannot carry it.",
    )
    + '</div></div>'
    + flow([
        "<b>Open &amp; Login</b> &mdash; a real window opens",
        "Human signs in: SSO, OTP, captcha, OAuth",
        "Click <b>I'm logged in</b> &rarr; 3 files saved",
        "Every later automated run replays them",
        "Crawl starts at the <b>landing URL</b>, not <code>/</code>",
    ], accent="blue")
    + callout(
        "Sessions are checked for expiry before every run "
        "(<code>_session_expiry_info</code> scans for "
        "<code>token | session | auth | jwt | sid</code> cookies). An expired "
        "session falls back to anonymous crawling <i>and</i> surfaces a "
        "warning &mdash; it never silently crawls the public site and calls "
        "it a pass.",
        kind="ok", title="Honest failure"),
    notes="This is what lets the tool test behind a login, which is where most "
          "real bugs live.",
)

slide(
    "QUALITY FLOW",
    "From a finding to a tracked bug",
    flow([
        "Finding raised by a check<br><i>or</i> by the tester",
        "Result modal shows it with<br>cropped screenshot evidence",
        "<b>Auto-Create Bugs</b><br>one button, whole batch",
        "Per-category repro steps<br>auto-written",
        "Bug row created, linked to<br>the run and test case",
    ], accent="purple")
    + code(
        '''# backend/modules/bugs/routes.py : 732
# Manual runs keep their record under these keys: metadata, the
# pages visited, the whole checklist (passed steps included) and
# the run summary. None of that is a defect.
MANUAL_BOOKKEEPING_KEYS = {
    'manual', 'note', 'pages', 'steps', 'summary',
    'expected_result', 'expected_met',
}

# Whitelist of genuine runner categories -- anything else is
# skipped rather than guessed at.
RUN_FINDING_CATEGORIES = {
    'broken_links', 'console_errors', 'security_issues',
    'accessibility_issues', 'performance_issues',
    'api_issues', 'validation_issues', 'seo_issues',
    'ui_issues', 'code_issues', 'url_issues',
    'mobile_issues', 'missing_alt_images', 'functional_issues',
}''',
        caption="A manual run stores its bookkeeping as a <i>string</i> under "
                "'note'. Without this allowlist, naive iteration would have "
                "created one bug per character.",
    )
    + bullets([
        ("Reproduction steps are written per category", "&mdash; a broken "
         "link becomes &ldquo;open the page &rarr; find the link &rarr; click "
         "it&rdquo; expecting HTTP 200."),
        ("Severity respects the finding's own grade", "before falling back to "
         "the category default, so a critical failure cannot be downgraded to "
         "Minor just because its category has no entry."),
        ("Idempotent per test case", "and per-finding failures are caught, so "
         "one malformed finding cannot abort the batch."),
    ]),
    notes="The allowlist is a nice example of a defensive fix: the data shape "
          "differs between manual and automated, so the code whitelists "
          "instead of assuming.",
)

slide(
    "REPORTING &amp; AI",
    "Reports, dashboards and the AI layer",
    '<div class="two-58">'
    '<div>'
    + '<div class="ph">Reporting &mdash; 9 endpoints</div>'
    + bullets([
        "Health-score trend over time, per project",
        "Pass rate, recent runs, run statistics with filters",
        "Bug breakdown by status and severity",
        "Top issue categories across the whole platform",
        "Detected issues, filterable by <code>MANUAL</code> / "
        "<code>AUTOMATED</code>",
        "<b>PDF export</b> &mdash; ReportLab builds the document in memory "
        "and streams it back as "
        "<code>qa-platform-report-YYYY-MM-DD.pdf</code>",
    ])
    + '</div><div>'
    + '<div class="ph">AI &mdash; Google Gemini</div>'
    + bullets([
        "<b>Bug analysis</b> &rarr; explanation, why it matters, how to fix, "
        "and a code example",
        "<b>Test suggestions</b> &rarr; 8 cases across functional, security, "
        "performance, accessibility, mobile and SEO &mdash; each labelled "
        "automated or manual depending on whether a script could judge it",
        "<b>Fix suggestions</b> &rarr; what it is, user impact, the fix, an "
        "example",
        "<b>Chat</b> &rarr; with live platform context: project and test-case "
        "counts, the last 5 bugs",
        "Every prompt demands strict JSON, and every endpoint degrades "
        "gracefully when parsing fails",
        "Rate limits map to <code>429</code>, exhausted credits to "
        "<code>503</code>",
    ])
    + '</div></div>'
    + callout(
        "The AI never decides a verdict. It analyses, suggests and explains. "
        "Scoring, verdicts and bug creation are all deterministic code &mdash; "
        "which is what makes the reports trustworthy enough to act on.",
        kind="info", title="Boundary"),
    notes="Be explicit that AI is advisory here. If a model hallucinated a "
          "pass rate, the whole platform would be worthless.",
)

# ---- 7. Section: Quality ------------------------------------------------
section(
    "06",
    "Quality of the Platform",
    "A QA tool that is not itself tested is a liability. We built a real test "
    "suite for it &mdash; and it passes.",
    [
        "312 backend tests, all passing",
        "14 frontend tests, all passing",
        "ESLint clean, production build green",
        "Live end-to-end crawls against real Chromium",
    ],
)

slide(
    "OUR OWN TEST AUTOMATION",
    "The backend suite &mdash; 312 tests across 18 files",
    table(
        ["Test file", "Tests", "What it protects"],
        [
            ["<code>test_detection_quality.py</code>", "78", "Every false-positive guard: status classification, de-duplication, mobile skipping, noise filtering"],
            ["<code>test_steps.py</code>", "41", "The step DSL &mdash; all 15 actions, parsing order, locator resolution"],
            ["<code>test_runner_helpers.py</code>", "32", "Scoring, coverage roll-up, URL normalisation, crawl patterns"],
            ["<code>test_manual_workspace.py</code>", "22", "Manual session lifecycle, checklist state, verdict summarisation"],
            ["<code>test_extended_checks.py</code>", "21", "URL health, API quality, page resources, UI consistency, code quality"],
            ["<code>test_runner_logic_fixes.py</code>", "17", "Regression tests for each bug we actually fixed"],
            ["<code>test_run_separation.py</code>", "12", "The manual / automated evidence contract"],
            ["<code>test_admin_access.py</code>", "11", "Role checks, deactivated-account blocking, admin-only routes"],
            ["<code>test_crawl_queue.py</code>", "10", "Frontier seeding, login-page pruning, pattern caps"],
            ["<code>test_testcase_workflow.py</code>", "8", "Create, update, delete, ownership"],
            ["<code>test_page_settle.py</code>", "7", "DOM-settle detection"],
            ["<code>test_page_checks_e2e.py</code>", "6", "Real Chromium: every category returns, evidence crops, no invented findings"],
            ["<code>test_steps_e2e.py</code>", "6", "Real login workflow, wrong-password failure, screenshot per step"],
            ["<code>test_app.py</code>", "5", "App factory, blueprint registration, error handlers"],
            ["<code>test_live_run_e2e.py</code>", "3", "Full live automated run and full manual workflow, headless"],
            ["<code>test_jobs.py</code>", "3", "Job submit, poll, cancel, eviction"],
            ["<code>test_manual_run_e2e.py</code>", "3", "Session capture, clean finish, auto-trigger timing"],
            ["<code>test_session_capture.py</code>", "3", "Storage-state and sessionStorage capture"],
        ],
        cls="tiny",
    ),
    notes="78 of 312 tests exist for one reason: to stop false positives from "
          "coming back. That file is the project's real safety net.",
)

slide(
    "TEST RESULTS",
    "Executed results, not claims",
    '<div class="stats">'
    '<div class="stat ok"><div class="sv">312</div><div class="sl">Backend tests '
    "passing</div><div class=\"sd\">pytest, real headless Chromium, 69s</div></div>"
    '<div class="stat ok"><div class="sv">14</div><div class="sl">Frontend '
    'tests passing</div><div class="sd">Vitest + Testing Library, jsdom</div>'
    "</div>"
    '<div class="stat ok"><div class="sv">0</div><div class="sl">Lint errors'
    "</div><div class=\"sd\">ESLint 10 across the whole frontend</div></div>"
    '<div class="stat ok"><div class="sv">0</div><div class="sl">Build errors'
    "</div><div class=\"sd\">Vite 8 production bundle, 4.5s</div></div>"
    "</div>"
    '<div class="two-58" style="margin-top:14px">'
    '<div>'
    + code(
        '''# Terminal 1 -- backend
cd backend
pytest
#   312 passed, 41 warnings in 69.01s (0:01:09)

# Terminal 2 -- frontend
cd frontend
npm test        # Test Files 2 passed (2)
                #      Tests 14 passed (14)
npm run lint    # eslint . -> exit 0
npm run build   # built in 4.51s -> exit 0''',
        lang="bash",
        caption="Reproduce exactly what was run for this deck.",
    )
    + '</div><div>'
    + bullets([
        ("Not mocked &mdash;", "the end-to-end tests launch real Chromium and "
         "audit a controlled local site with deliberate defects planted in it."),
        ("Not just unit &mdash;", "<code>test_live_run_e2e.py</code> runs the "
         "actual <code>_perform_run</code> with the database stubbed, then "
         "asserts on the result."),
        ("The verification that mattered &mdash;", "11 of 11 assertions passed "
         "on that crawl: 4 pages, a real <code>duration_ms</code>, the "
         "<code>/dead</code> link collapsed to 1, security collapsed to 2, "
         "and no false positives on the valid pages."),
        ("Frontend &mdash;", "14 component tests cover the report rendering "
         "and the run modal, including the NaN-safety guards."),
    ])
    + '</div></div>',
    notes="Say the word 'executed'. The distinction between 'we believe it "
          "works' and 'here is the output' matters enormously in a QA "
          "presentation.",
)

# ---- 8. Section: Challenges ---------------------------------------------
section(
    "07",
    "Engineering Challenges",
    "The bugs we found by running the tool on real sites &mdash; and the "
    "reasoning behind each fix.",
    [
        "Four production bugs diagnosed with data, not guesswork",
        "Every fix verified by executing it, never by claiming it",
        "A logging crash traced to two processes and one locked file",
    ],
)

slide(
    "CHALLENGES",
    "Four real bugs, diagnosed with evidence",
    table(
        ["Symptom", "Actual root cause", "Fix"],
        [
            ["<code>--- Logging error ---</code> flooding the terminal during crawls",
             "<b>Two causes.</b> (1) <code>RotatingFileHandler</code> failing on Windows (<code>WinError 32</code>, file locked) &mdash; the Flask debug reloader runs <b>two processes</b>, both holding <code>app.log</code> open. (2) The console handler's <code>cp1252</code> encoding choking on non-ASCII.",
             "Force console to UTF-8 with <code>errors='replace'</code>; a <code>_SafeRotatingFileHandler</code> that swallows rotation failures; attach the file handler <b>only to the serving process</b> via <code>WERKZEUG_RUN_MAIN</code>"],
            ["Every link behind the login reported as broken",
             "<code>check_broken_links</code> used bare <code>requests.head()</code> with <b>no browser cookies</b>, so every login-gated link returned 302/401/403. Many servers also reject <code>HEAD</code> outright.",
             "Forward the browser's cookies and real User-Agent into a per-thread session so links are tested <i>as the logged-in user</i>; fall back to <code>GET</code> when <code>HEAD</code> is rejected; stop treating 401/403/429/503 as broken"],
            ["Mobile findings that were pure noise on authenticated crawls",
             "Authenticated crawls deliberately run a <b>desktop</b> viewport to keep the session valid &mdash; but the 44&nbsp;px tap-target and horizontal-scroll checks still ran.",
             "Skip the mobile check whenever a session is in use: <code>is_mobile = not session_used</code>"],
            ["Issue counts inflated, health score wrong",
             "<code>merge_findings</code> had <b>no de-duplication</b>, so one footer link or one missing header was counted once per page &mdash; 8 header findings instead of 2.",
             "Per-category de-duplication keys: broken link by URL, security by header, accessibility by rule"],
        ],
        cls="tiny",
    ),
    notes="All four were found by running against a real 75-page site. None "
          "would ever have appeared in a unit test.",
)

slide(
    "CHALLENGES",
    "The hard one: &ldquo;Job not found&rdquo; and <code>NaNs</code>",
    code(
        '''# Symptom: an automated crawl of Amazon died partway
# (page 41 of 75), the result modal showed "Job not found",
# and every duration field rendered as "NaNs".

# Chain of causes, found by reading app.log:

# 1. The async job registry is IN-MEMORY (jobs.py). Any backend
#    restart destroys every in-flight and finished job.
#
# 2. The Flask debug reloader restarts the process on every .py
#    save. app.log showed ~130 restarts during the session --
#    several triggered by our own edits while the server ran.
#
# 3. The frontend polled, got a 404, built an error object with
#    no duration_ms, and then evaluated:
#       (undefined / 1000).toFixed(1)   ->  "NaNs"

# Fixes:
#   * fmtSecs() returns an em-dash instead of NaN when timing is
#     missing -- a missing value is displayed, never invented.
#   * A 404 mid-poll now says: "the run was interrupted -- the
#     backend restarted", instead of "Job not found".
#   * Operational: run the backend with the reloader disabled
#     for real crawls (use_reloader=False).
#   * Planned: persist job state to a MySQL run_jobs table, so
#     polls survive a restart at all.''',
        caption="This is the honest state of the project: the user-facing "
                "symptom is fixed and explained, the durability gap is "
                "documented, not hidden.",
    )
    + callout(
        "The general lesson: <b>an error message that lies is worse than a "
        "crash.</b> &ldquo;Job not found&rdquo; sent us looking for a job-"
        "lookup bug. The truthful message &mdash; &ldquo;the backend "
        "restarted&rdquo; &mdash; identified the cause immediately.",
        kind="info", title="Takeaway"),
    notes="This slide is worth telling as a story. It shows honest "
          "engineering: symptom, root cause chain, partial fix, and the gap "
          "stated plainly.",
)

# ---- 9. Section: Security -----------------------------------------------
section(
    "08",
    "Security &amp; Safeguards",
    "A testing tool takes a target URL, stores credentials and writes files. "
    "That is a large attack surface, so it is defended deliberately.",
    [
        "SSRF protection with DNS resolution and private-IP blocking",
        "JWT auth plus ownership checks on every object",
        "Browser sessions and evidence treated as sensitive data",
    ],
)

slide(
    "SECURITY",
    "Safeguards built into the test engine",
    grid([
        ("red", "SSRF protection", "Every test URL is DNS-resolved and the "
         "resolved IP is rejected if it is private, loopback, link-local, "
         "multicast, reserved or unspecified &mdash; which also blocks "
         "<code>169.254.169.254</code> cloud metadata. DNS failure fails "
         "<i>open</i>; a successful resolve to a private IP fails "
         "<i>closed</i>. Escape hatches exist for local dev."),
        ("blue", "Auth on everything", "Every runner, project, bug and report "
         "endpoint sits behind <code>@jwt_required()</code>, and every object "
         "read or write is ownership-checked against the owning project, not "
         "just the user id."),
        ("ok", "Deactivated accounts", "Tokens do not expire in this "
         "configuration, so a blocklist loader re-checks "
         "<code>users.is_active</code> on every single request."),
        ("warn", "Rate limiting", "Flask-Limiter is applied per endpoint "
         "rather than globally, so login and AI endpoints are protected "
         "without throttling a 100-page crawl."),
        ("purple", "Anti-automation detection", "<code>navigator.webdriver</code> "
         "is masked, the manual runner prefers the user's real installed "
         "Chrome, and <code>--disable-blink-features=AutomationControlled</code> "
         "is set."),
        ("red", "Sensitive data", "Saved sessions contain live cookies and "
         "tokens. They are excluded from version control, stored outside the "
         "web root, and treated as private runtime data."),
    ], cols=3),
    notes="SSRF is the one to dwell on: this tool takes a URL from a user and "
          "makes the server fetch it. Without DNS-level checks it would be a "
          "server-side scanner of the private network.",
)

# ---- 10. Roadmap --------------------------------------------------------
section(
    "09",
    "Limitations &amp; Roadmap",
    "What is not solved yet, and what is next &mdash; ordered by impact per "
    "unit of effort.",
    [
        "Job durability: in-memory registry loses runs on restart",
        "Authenticated crawl unverified end-to-end against a live SPA",
        "Crawl frontier has no URL-pattern de-duplication",
    ],
)

slide(
    "LIMITATIONS",
    "Honest gaps, and what we would do next",
    table(
        ["#", "Limitation", "Impact", "Planned fix"],
        [
            ["1", "<b>Job durability.</b> The job registry is in memory, so any backend restart loses in-flight and finished runs. A 75-page crawl (~40 min) is fragile to any restart.",
             "High &mdash; long runs can be lost entirely",
             "<b>P0.</b> Persist job state to a MySQL <code>run_jobs</code> table so polls survive restarts. A Celery/RQ + Redis worker is the heavier alternative. Interim: run with the reloader off"],
            ["2", "<b>Authenticated crawl, unproven live.</b> Every building block is individually verified (cookie forwarding, session-preserving navigation, sessionStorage replay, landing-URL warmup), but a full real login &rarr; automated crawl against a live SPA has not been run.",
             "High &mdash; the flagship path is untested end to end",
             "<b>P1.</b> Capture a real session on a live authenticated app, run the crawl, and confirm <code>session_used=True</code>, sessionStorage injected, landing page past <code>/login</code>, and protected links <i>not</i> flagged broken"],
            ["3", "<b>Crawl frontier control.</b> A large site queued 380+ URLs for a 75-page cap, many near-duplicate product pages.",
             "Medium &mdash; wasted wall-clock, less meaningful <code>max_pages</code>",
             "<b>P2.</b> Collapse ID-like URL segments into patterns (already implemented for capping), add per-section caps, and optionally test pages in parallel"],
            ["4", "<b>Manual auto-trigger.</b> Can still fire early if a project's start URL is not recognised as a login page. Button-only mode was offered, not applied.",
             "Low &mdash; a one-line change away",
             "<b>P4.</b> Remove the timer, keep the explicit &ldquo;Auto-crawl from here&rdquo; button"],
            ["5", "<b>Health-score weights</b> are untuned now that duplicates are gone, so scores will rise across the board.",
             "Low", "<b>P3.</b> Re-tune the 15 category weights against real post-fix data"],
            ["6", "<b>OTP module unwired.</b> The endpoints and the wait loop exist and are tested, but nothing calls them &mdash; OTP happens inside the human's own browser window.",
             "Low &mdash; scaffolding, not a broken path",
             "Wire it into the manual flow, or delete it. Unused code is a liability"],
        ],
        cls="tiny",
    ),
    notes="Presenting limitations is the strongest thing you can do in a QA "
          "presentation. It proves the rest of the claims are measured.",
)

# ---- 11. Learnings ------------------------------------------------------
slide(
    "KEY LEARNINGS",
    "What this project taught me",
    '<div class="two-58">'
    '<div>'
    + bullets([
        ("A check that cries wolf is worse than no check. ",
         "Every false-positive guard in the 78-test "
         "<code>test_detection_quality.py</code> exists because an early "
         "version told users their site was broken when it was not."),
        ("Fix the root cause, not the symptom. ",
         "<code>NaNs</code> was a formatting bug; the real problem was an "
         "in-memory job registry. Patching only the formatter would have "
         "hidden a lost 40-minute run."),
        ("De-duplicate before you score. ",
         "Counting one footer bug once per page made every score meaningless. "
         "Fixing aggregation was worth more than adding five new checks."),
        ("Run your tool on something real, early. ",
         "All four production bugs appeared only against a live 75-page site, "
         "never in a unit test."),
        ("Non-measurement is not success. ",
         "A category that never got checked is excluded from the score "
         "instead of counted as perfect. This applies to test coverage too."),
        ("Verify by executing. ",
         "Every fix in this project was validated with a real harness and a "
         "recorded result &mdash; never shipped on a claim."),
    ])
    + '</div><div>'
    + '<div class="ph">The one-paragraph summary</div>'
    + '<p class="quote">A testing platform earns trust the same way the tests '
    "it runs do: by being honest about what it checked, honest about what it "
    "found, and honest about what it could not check at all. That is why the "
    "manual verdict is protected from automation, why unmeasured categories "
    "are excluded from the score, and why a broken run is reported as "
    "interrupted rather than quietly passing.</p>"
    + callout(
        "Manual testing supplies judgement. Automation supplies coverage. The "
        "engineering is in keeping them honest with each other.",
        kind="ok", title="If you remember one line")
    + '</div></div>',
)

# ---- 12. Close ----------------------------------------------------------
add(
    '<div class="c-wrap">'
    '<div class="c-kicker">THANK YOU</div>'
    '<h2 class="c-main">Questions?</h2>'
    '<div class="c-rule"></div>'
    '<div class="c-grid">'
    '<div class="c-cell"><div class="ccl">Manual testing</div>'
    "<div class=\"ccd\">Headed real Chrome driven by a backend worker. Written "
    "steps become a live checklist, every navigation is screenshotted, and "
    "the tester&rsquo;s verdict is protected from being overridden by the "
    "automatic checks running alongside it.</div></div>"
    '<div class="c-cell"><div class="ccl">Automated testing</div>'
    '<div class="ccd">Headless Chromium via Playwright, 16 checks per page, '
    "BFS crawl up to 100 pages, authenticated by replaying a saved session, "
    "de-duplicated findings and a weighted health score.</div></div>"
    '<div class="c-cell"><div class="ccl">The proof</div>'
    '<div class="ccd">312 backend tests and 14 frontend tests passing, ESLint '
    "clean, production build green &mdash; with live end-to-end crawls "
    "against real Chromium.</div></div>"
    "</div>"
    '<div class="c-foot">QA Platform &nbsp;&bull;&nbsp; React 19 &nbsp;/&nbsp; '
    "Vite &nbsp;&bull;&nbsp; Flask 3 &nbsp;/&nbsp; MySQL &nbsp;&bull;&nbsp; "
    "Playwright &nbsp;/&nbsp; axe-core &nbsp;&bull;&nbsp; Gemini</div>"
    "</div>",
    cls="closing",
)

# ==========================================================================
#  HTML SHELL
# ==========================================================================

CSS = """
@page { size: 13.333in 7.5in; margin: 0; }
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  font-family: 'Segoe UI', system-ui, -apple-system, 'Helvetica Neue', Arial, sans-serif;
  color: #1e293b; background: #fff;
  font-size: 15px; line-height: 1.5;
}
.slide {
  width: 13.333in; height: 7.5in; position: relative; overflow: hidden;
  padding: 0.5in 0.62in 0.46in; page-break-after: always; break-after: page;
  background: #fff;
}
.slide:last-child { page-break-after: auto; }

/* ---- header ---- */
.hd { border-bottom: 2px solid #e2e8f0; padding-bottom: 9px; margin-bottom: 15px; }
.kicker {
  font-size: 10.5px; font-weight: 700; letter-spacing: 2.1px;
  color: #2563eb; text-transform: uppercase; margin-bottom: 3px;
}
.hd h2 { font-size: 29px; font-weight: 700; color: #0f172a; line-height: 1.14; letter-spacing: -0.4px; }
.bd { font-size: 14.2px; }
.notes {
  position: absolute; left: 0.62in; right: 0.62in; bottom: 0.2in;
  font-size: 10.2px; color: #94a3b8; font-style: italic;
  border-top: 1px dashed #e2e8f0; padding-top: 5px; line-height: 1.35;
}
.notes b { color: #64748b; font-style: normal; }
.pgnum { position: absolute; right: 0.62in; bottom: 0.2in; font-size: 9.6px; color: #cbd5e1; }

/* ---- lists ---- */
ul.ul { list-style: none; }
ul.ul > li {
  position: relative; padding-left: 19px; margin-bottom: 8px; line-height: 1.44;
}
ul.ul > li::before {
  content: ''; position: absolute; left: 3px; top: 8px;
  width: 6px; height: 6px; border-radius: 2px; background: #2563eb;
}
ul.ul > li b { color: #0f172a; font-weight: 650; }
code {
  font-family: 'Cascadia Code', Consolas, 'SF Mono', Menlo, monospace;
  background: #eef2f7; color: #be123c; padding: 1px 5px; border-radius: 3px;
  font-size: 0.89em; white-space: nowrap;
}

/* ---- layout helpers ---- */
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.two-58 { display: grid; grid-template-columns: 1.15fr 1fr; gap: 18px; }
.two-45 { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.tight-cols > ul > li { margin-bottom: 6px; }
.four { display: grid; grid-template-columns: 1fr 1fr; gap: 13px; }
.four + .callout { margin-top: 14px; }
.fq { background: #f8fafc; border: 1px solid #e2e8f0; border-left: 3px solid #2563eb; border-radius: 7px; padding: 11px 13px; }
.fqn {
  display: inline-block; background: #2563eb; color: #fff; font-size: 10.5px;
  font-weight: 700; width: 19px; height: 19px; line-height: 19px; text-align: center;
  border-radius: 50%; margin-bottom: 5px;
}
.fqt { font-weight: 700; color: #0f172a; font-size: 14.5px; margin-bottom: 3px; }
.fqd { font-size: 12.6px; color: #475569; line-height: 1.42; }

/* ---- panels ---- */
.panel { border-radius: 9px; padding: 14px 16px; border: 1px solid; }
.panel.warn { background: #fffbeb; border-color: #fcd34d; }
.panel.good { background: #f0fdf4; border-color: #86efac; }
.ph {
  font-weight: 700; font-size: 15px; color: #0f172a; margin-bottom: 8px;
  padding-bottom: 6px; border-bottom: 1px solid rgba(100,116,139,0.22);
}
.ph + .callout { margin-top: 10px; }

/* ---- callout ---- */
.callout {
  margin-top: 13px; border-radius: 8px; padding: 10px 14px; font-size: 13px;
  line-height: 1.45; border: 1px solid;
}
.callout.info { background: #eff6ff; border-color: #bfdbfe; color: #1e40af; }
.callout.warn { background: #fffbeb; border-color: #fcd34d; color: #92400e; }
.callout.ok   { background: #f0fdf4; border-color: #86efac; color: #166534; }
.co-t { font-weight: 700; font-size: 10.5px; letter-spacing: 1.4px; text-transform: uppercase; margin-bottom: 3px; opacity: 0.85; }
.co-b b { font-weight: 700; }

/* ---- cards / grid ---- */
.grid { display: grid; gap: 12px; }
.g2 { grid-template-columns: 1fr 1fr; }
.g3 { grid-template-columns: repeat(3, 1fr); }
.card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 9px; padding: 12px 13px; }
.ci {
  display: inline-block; font-size: 10px; font-weight: 800; letter-spacing: 0.6px;
  padding: 2px 7px; border-radius: 4px; margin-bottom: 6px;
  background: #dbeafe; color: #1d4ed8;
}
.ci.orange { background: #ffedd5; color: #c2410c; }
.ci.ok     { background: #dcfce7; color: #15803d; }
.ci.blue   { background: #dbeafe; color: #1d4ed8; }
.ci.purple { background: #ede9fe; color: #6d28d9; }
.ci.red    { background: #fee2e2; color: #b91c1c; }
.ci.warn   { background: #fef3c7; color: #a16207; }
.ct { font-weight: 700; color: #0f172a; font-size: 14px; margin-bottom: 4px; line-height: 1.24; }
.cd { font-size: 12.5px; color: #475569; line-height: 1.42; }
.card .ci + .ct { margin-top: 0; }

/* ---- flow ---- */
.flow { display: flex; flex-wrap: wrap; gap: 7px; margin-bottom: 4px; }
.fl {
  flex: 1 1 0; min-width: 0; background: #f1f5f9; border: 1px solid #cbd5e1;
  border-radius: 7px; padding: 8px 9px; position: relative;
}
.flow.orange .fl { background: #fff7ed; border-color: #fdba74; }
.flow.blue   .fl { background: #eff6ff; border-color: #93c5fd; }
.flow.purple .fl { background: #faf5ff; border-color: #d8b4fe; }
.fln {
  width: 18px; height: 18px; line-height: 18px; text-align: center; border-radius: 50%;
  background: #334155; color: #fff; font-size: 10px; font-weight: 700; margin-bottom: 4px;
}
.flow.orange .fln { background: #ea580c; }
.flow.blue   .fln { background: #2563eb; }
.flow.purple .fln { background: #7c3aed; }
.flt { font-size: 11.6px; color: #334155; line-height: 1.34; }
.flt b { color: #0f172a; }

/* ---- code ---- */
.codewrap { border-radius: 8px; overflow: hidden; border: 1px solid #1e293b; background: #0f172a; }
.cap {
  background: #1e293b; color: #94a3b8; font-size: 10.4px; padding: 5px 11px;
  font-style: italic; border-bottom: 1px solid #334155; line-height: 1.35;
}
pre.code {
  font-family: 'Cascadia Code', Consolas, 'SF Mono', Menlo, monospace;
  font-size: 10.2px; line-height: 1.40; color: #e2e8f0; padding: 8px 12px;
  white-space: pre; overflow: hidden;
}
pre.code .k { color: #c084fc; font-weight: 600; }
pre.code .s { color: #86efac; }
pre.code .c { color: #64748b; font-style: italic; }
pre.code .n { color: #fbbf24; }
pre.code .f { color: #60a5fa; }
pre.code .b { color: #22d3ee; }
pre.code .d { color: #f472b6; }
pre.code .p { color: #94a3b8; }

/* ---- tables ---- */
.tbl { width: 100%; border-collapse: collapse; font-size: 12.4px; }
.tbl th {
  background: #0f172a; color: #fff; text-align: left; padding: 6px 9px;
  font-weight: 600; font-size: 11.4px; letter-spacing: 0.3px;
}
.tbl td { padding: 5px 9px; border-bottom: 1px solid #e2e8f0; color: #334155; vertical-align: top; line-height: 1.34; }
.tbl tr:nth-child(even) td { background: #f8fafc; }
.tbl.tight { font-size: 11.6px; }
.tbl.tight th { padding: 5px 8px; font-size: 10.8px; }
.tbl.tight td { padding: 4px 8px; line-height: 1.3; }
.tbl code { font-size: 0.9em; }

/* ---- stats ---- */
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 13px; }
.stat { border-radius: 9px; padding: 13px 15px; text-align: center; border: 1px solid; }
.stat.ok { background: #f0fdf4; border-color: #86efac; }
.sv { font-size: 36px; font-weight: 800; color: #15803d; line-height: 1; }
.sl { font-size: 12.6px; font-weight: 600; color: #166534; margin-top: 3px; }
.sd { font-size: 10.6px; color: #4d7c0f; margin-top: 2px; }

/* ---- quote ---- */
.quote {
  font-size: 13.6px; line-height: 1.56; color: #334155; font-style: italic;
  border-left: 3px solid #cbd5e1; padding-left: 14px; margin-bottom: 4px;
}
.lead { font-size: 13.4px; line-height: 1.5; color: #334155; margin-bottom: 11px; }
.tiny-note { font-size: 9.6px; color: #94a3b8; font-weight: 400; }

/* ---- method blocks (detection / credentials) ---- */
.meth {
  background: #f8fafc; border: 1px solid #e2e8f0; border-left: 3px solid #2563eb;
  border-radius: 7px; padding: 9px 12px; margin-bottom: 9px;
}
.mt { font-size: 13.4px; font-weight: 700; color: #0f172a; margin-bottom: 3px; }
.md { font-size: 12.3px; color: #475569; line-height: 1.42; }
.md code { font-size: 0.88em; }

/* ---- architecture ---- */
.arch { display: flex; flex-direction: column; align-items: center; gap: 5px; }
.arow { width: 100%; display: flex; justify-content: center; }
.abox {
  background: #f1f5f9; border: 1.5px solid #94a3b8; border-radius: 8px;
  padding: 8px 16px; text-align: center; width: 100%;
}
.abox.hi  { background: #0f172a; border-color: #0f172a; color: #fff; width: 62%; }
.abox.hi2 { background: #1e3a5f; border-color: #1e3a5f; color: #fff; width: 80%; }
.abox.lt  { background: #fef3c7; border-color: #f59e0b; width: 66%; }
.abox.ac2 { background: #f5f3ff; border-color: #a78bfa; }
.abox.sm  { width: 23%; padding: 7px 9px; }
.abox b { display: block; font-size: 13.4px; margin-bottom: 1px; }
.abox span { font-size: 11.2px; color: #64748b; line-height: 1.3; display: block; }
.abox.hi span, .abox.hi2 span { color: #cbd5e1; }
.aarrow { font-size: 11.6px; color: #64748b; }
.aarrow code { font-size: 10.6px; }
.afan { display: flex; gap: 9px; width: 100%; justify-content: center; margin: 2px 0; }

/* ---- title slide ---- */
.slide.title {
  background: linear-gradient(135deg, #0b1220 0%, #122244 52%, #1a2f5e 100%);
  display: flex; align-items: center; justify-content: center; text-align: center;
}
.t-wrap { max-width: 10.4in; }
.t-kicker {
  font-size: 12px; font-weight: 700; letter-spacing: 3.4px; color: #60a5fa;
  text-transform: uppercase; margin-bottom: 20px;
}
.t-main {
  font-size: 51px; font-weight: 800; color: #fff; line-height: 1.1; letter-spacing: -1.3px;
}
.t-rule { width: 92px; height: 4px; background: #3b82f6; margin: 22px auto; border-radius: 2px; }
.t-sub { font-size: 16.5px; color: #cbd5e1; line-height: 1.5; max-width: 8.6in; margin: 0 auto; }
.t-meta {
  margin-top: 30px; font-size: 11.6px; color: #7e93b8; line-height: 1.75;
  border-top: 1px solid rgba(148,163,184,0.22); padding-top: 17px;
}

/* ---- section divider ---- */
.slide.section {
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 60%, #334155 100%);
  display: flex; flex-direction: column; justify-content: center;
  padding-left: 1.15in;
}
.s-num {
  font-size: 74px; font-weight: 800; color: rgba(96,165,250,0.28); line-height: 1;
  letter-spacing: -2px;
}
.s-name { font-size: 42px; font-weight: 800; color: #fff; margin-top: 6px; letter-spacing: -0.8px; }
.s-rule { width: 74px; height: 4px; background: #3b82f6; margin: 17px 0; border-radius: 2px; }
.s-blurb { font-size: 16px; color: #cbd5e1; line-height: 1.5; max-width: 8.4in; }
.s-list { list-style: none; margin-top: 19px; display: flex; flex-direction: column; gap: 7px; }
.s-list li {
  font-size: 13.4px; color: #94a3b8; padding-left: 17px; position: relative;
}
.s-list li::before {
  content: ''; position: absolute; left: 0; top: 7px; width: 6px; height: 6px;
  border-radius: 50%; background: #3b82f6;
}

/* ---- closing ---- */
.slide.closing {
  background: linear-gradient(135deg, #0b1220 0%, #122244 52%, #1a2f5e 100%);
  display: flex; flex-direction: column; justify-content: center; text-align: center;
}
.c-wrap { max-width: 10.8in; margin: 0 auto; }
.c-kicker { font-size: 12px; font-weight: 700; letter-spacing: 3.4px; color: #60a5fa; margin-bottom: 11px; }
.c-main { font-size: 46px; font-weight: 800; color: #fff; letter-spacing: -1px; }
.c-rule { width: 84px; height: 4px; background: #3b82f6; margin: 17px auto 26px; border-radius: 2px; }
.c-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; text-align: left; }
.c-cell { background: rgba(255,255,255,0.055); border: 1px solid rgba(148,163,184,0.2); border-radius: 9px; padding: 13px 15px; }
.ccl { font-size: 14.4px; font-weight: 700; color: #93c5fd; margin-bottom: 5px; }
.ccd { font-size: 12.2px; color: #cbd5e1; line-height: 1.45; }
.c-foot { margin-top: 26px; font-size: 11.4px; color: #7e93b8; }
"""


def build_html():
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        "<title>QA Platform - Manual &amp; Automated Testing</title>",
        "<style>%s</style></head><body>" % CSS,
    ]
    for i, (cls, body) in enumerate(SLIDES, 1):
        n = ""
        if cls == "content":
            n = ('<div class="pgnum">%d / %d</div>'
                 % (i, len(SLIDES)))
        parts.append('<section class="slide %s">%s%s</section>' % (cls, body, n))
    parts.append("</body></html>")
    return "".join(parts)


def main():
    doc = build_html()
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(doc)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is required: pip install playwright && "
              "playwright install chromium", file=sys.stderr)
        return 2

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.goto("file:///" + OUT_HTML.replace("\\", "/"))
        page.wait_for_timeout(700)
        page.pdf(
            path=OUT_PDF,
            width="13.333in",
            height="7.5in",
            print_background=True,
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            prefer_css_page_size=True,
        )
        browser.close()

    size = os.path.getsize(OUT_PDF)
    print("Slides: %d" % len(SLIDES))
    print("PDF:    %s" % OUT_PDF)
    print("Size:   %.1f KB" % (size / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
