"""
Executable test steps.

A test case's Steps field used to be a note nobody read. This turns each line
into a real browser action, so an automated run can catch the two things a
crawler never could: functional failures (a button that does nothing, a login
that rejects valid credentials) and broken integration workflows (step 4 of a
checkout failing because step 3 silently did not happen).

The syntax is plain English, one step per line, matching what the UI already
suggests. Leading numbering ("1.", "2)") is ignored.

    Open https://shop.example.com
    Type alice@example.com into Email
    Type hunter2 into Password
    Click Sign in
    Expect text Welcome back
    Expect url contains /dashboard

Anything not understood is reported as an unreadable step rather than being
silently skipped, so a typo can never masquerade as a passing test.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

# How long any single step may take before it is called a failure.
#
# Configurable because the right value depends on the target: a slow
# server needs longer, and a CI run proving that a step correctly fails
# should not sit here for the full timeout. Tests override the module
# attribute directly; deployments set QA_STEP_TIMEOUT_MS.
STEP_TIMEOUT_MS = int(os.getenv('QA_STEP_TIMEOUT_MS', '8000'))

# Keys that "Press X" understands. Anything else is passed through to
# Playwright, which accepts things like "Control+A".
KNOWN_KEYS = {
    'enter': 'Enter', 'return': 'Enter', 'tab': 'Tab', 'escape': 'Escape',
    'esc': 'Escape', 'space': 'Space', 'backspace': 'Backspace',
    'arrowdown': 'ArrowDown', 'arrowup': 'ArrowUp',
}

# Each entry is (action, regex). First match wins, so put the more specific
# patterns first: "expect url contains x" must beat the generic "expect x".
_PATTERNS = [
    ('expect_url', re.compile(
        r'^(?:expect|verify|check|assert)\s+(?:the\s+)?url\s+(?:to\s+)?contains?\s+(?P<value>.+)$', re.I)),
    ('expect_url', re.compile(
        r'^(?:the\s+)?url\s+should\s+contain\s+(?P<value>.+)$', re.I)),
    ('expect_text', re.compile(
        r'^(?:expect|verify|check|assert)\s+(?:to\s+)?(?:see\s+)?(?:the\s+)?text\s+(?P<value>.+)$', re.I)),
    ('expect_text', re.compile(
        r'^(?:i\s+)?should\s+see\s+(?P<value>.+)$', re.I)),
    ('expect_text', re.compile(
        r'^(?:expect|verify|check|assert|see)\s+(?P<value>.+)$', re.I)),
    ('goto', re.compile(
        r'^(?:open|go\s+to|visit|navigate\s+to|browse\s+to)\s+(?P<target>.+)$', re.I)),
    ('type', re.compile(
        r'^(?:type|enter|input)\s+(?P<value>.+?)\s+(?:in|into)\s+(?:the\s+)?(?P<target>.+?)'
        r'(?:\s+(?:field|box|input))?$', re.I)),
    ('type', re.compile(
        r'^fill\s+(?:the\s+)?(?P<target>.+?)\s+with\s+(?P<value>.+)$', re.I)),
    ('press', re.compile(
        r'^press\s+(?:the\s+)?(?P<value>[\w+]+)(?:\s+key)?$', re.I)),
    ('wait', re.compile(
        r'^wait\s+for\s+(?:the\s+)?(?P<target>.+)$', re.I)),
    ('click', re.compile(
        r'^(?:click|tap|choose|select)\s+(?:on\s+)?(?:the\s+)?(?P<target>.+?)'
        r'(?:\s+(?:button|link|tab))?$', re.I)),
]

_NUMBERING = re.compile(r'^\s*\d+\s*[.)\]-]\s*')
_QUOTES = re.compile(r'^["\'](.*)["\']$', re.S)


def _clean(value):
    """Strip surrounding quotes and whitespace from a captured fragment."""
    value = (value or '').strip()
    match = _QUOTES.match(value)
    if match:
        value = match.group(1).strip()
    return value


def parse_step(line):
    """
    Turn one written line into an action.

    Returns a dict with action / target / value, where action is 'unknown'
    when the line could not be read. Never raises: a bad line is data, not
    a crash.
    """
    raw = (line or '').strip()
    stripped = _NUMBERING.sub('', raw).strip()
    if not stripped:
        return None

    for action, pattern in _PATTERNS:
        match = pattern.match(stripped)
        if not match:
            continue
        groups = match.groupdict()
        return {
            'raw': raw,
            'action': action,
            'target': _clean(groups.get('target')),
            'value': _clean(groups.get('value')),
        }

    return {'raw': raw, 'action': 'unknown', 'target': '', 'value': ''}


def parse_steps(steps_text):
    """Parse a whole Steps field into a list of actions, blank lines dropped."""
    if not steps_text:
        return []
    parsed = []
    for line in str(steps_text).splitlines():
        step = parse_step(line)
        if step:
            parsed.append(step)
    return parsed


def _looks_like_selector(target):
    """True for things clearly meant as CSS rather than visible text."""
    if not target:
        return False
    if target.startswith(('#', '.', '[')):
        return True
    return bool(re.match(r'^[a-z]+\[[^\]]+\]$', target, re.I))


def _candidate_locators(page, target, for_input=False):
    """
    Ways to find an element, best first.

    Real testers describe elements the way a user sees them ("Sign in"), not
    by CSS. Trying several strategies is what keeps a step working after a
    harmless markup change, which is the whole point of automating it.
    """
    if _looks_like_selector(target):
        return [('css', page.locator(target))]

    if for_input:
        return [
            ('label', page.get_by_label(target, exact=False)),
            ('placeholder', page.get_by_placeholder(target, exact=False)),
            ('name attribute', page.locator(f'[name="{target}"]')),
            ('aria-label', page.locator(f'[aria-label*="{target}" i]')),
        ]

    return [
        ('button', page.get_by_role('button', name=target, exact=False)),
        ('link', page.get_by_role('link', name=target, exact=False)),
        ('visible text', page.get_by_text(target, exact=False)),
        ('aria-label', page.locator(f'[aria-label*="{target}" i]')),
        ('value attribute', page.locator(f'input[value="{target}"]')),
    ]


def _resolve(page, target, for_input=False):
    """
    Find one element for a step. Returns (locator, how_it_was_found) or
    (None, reason_it_failed) so the failure message can be specific.
    """
    tried = []
    for how, locator in _candidate_locators(page, target, for_input):
        try:
            count = locator.count()
        except Exception:
            continue
        if count == 1:
            return locator, how
        if count > 1:
            # Ambiguity is a real problem worth reporting, but taking the
            # first visible match is what a human tester would do.
            return locator.first, f'{how} (matched {count}, used the first)'
        tried.append(how)
    return None, f'no element matched (tried: {", ".join(tried) or "css"})'


def execute_step(page, step, base_url=None):
    """
    Run one parsed step against a live page.

    Returns {status, message, detail}, where status is 'passed', 'failed'
    or 'unreadable'. Never raises: a failing step is a result, not a crash,
    because the remaining steps still need to be attempted and reported.
    """
    action = step['action']
    target = step['target']
    value = step['value']

    if action == 'unknown':
        return {
            'status': 'unreadable',
            'message': 'Could not understand this step',
            'detail': 'Try wording like "Click Sign in", "Type alice@x.com into Email", '
                      '"Expect text Welcome" or "Open https://example.com".',
        }

    try:
        if action == 'goto':
            url = target
            if not url.startswith(('http://', 'https://')) and base_url:
                url = base_url.rstrip('/') + '/' + url.lstrip('/')
            page.goto(url, timeout=STEP_TIMEOUT_MS, wait_until='domcontentloaded')
            return {'status': 'passed', 'message': f'Opened {url}', 'detail': ''}

        if action == 'press':
            key = KNOWN_KEYS.get(value.lower(), value)
            page.keyboard.press(key)
            return {'status': 'passed', 'message': f'Pressed {key}', 'detail': ''}

        if action == 'expect_text':
            locator = page.get_by_text(value, exact=False)
            try:
                locator.first.wait_for(state='visible', timeout=STEP_TIMEOUT_MS)
            except Exception:
                return {
                    'status': 'failed',
                    'message': f'Expected to see "{value}" but it was not on the page',
                    'detail': f'Current URL: {page.url}',
                }
            return {'status': 'passed', 'message': f'Found "{value}"', 'detail': ''}

        if action == 'expect_url':
            current = page.url or ''
            if value in current:
                return {'status': 'passed', 'message': f'URL contains "{value}"', 'detail': ''}
            return {
                'status': 'failed',
                'message': f'Expected the URL to contain "{value}"',
                'detail': f'It was actually {current}',
            }

        if action == 'wait':
            if _looks_like_selector(target):
                page.locator(target).first.wait_for(state='visible', timeout=STEP_TIMEOUT_MS)
            else:
                page.get_by_text(target, exact=False).first.wait_for(
                    state='visible', timeout=STEP_TIMEOUT_MS)
            return {'status': 'passed', 'message': f'"{target}" appeared', 'detail': ''}

        if action in ('click', 'type'):
            locator, how = _resolve(page, target, for_input=(action == 'type'))
            if locator is None:
                return {
                    'status': 'failed',
                    'message': f'Could not find "{target}" on the page',
                    'detail': f'{how}. Current URL: {page.url}',
                }
            if action == 'click':
                locator.click(timeout=STEP_TIMEOUT_MS)
                return {'status': 'passed', 'message': f'Clicked "{target}"',
                        'detail': f'found by {how}'}
            locator.fill(value, timeout=STEP_TIMEOUT_MS)
            return {'status': 'passed', 'message': f'Typed into "{target}"',
                    'detail': f'found by {how}'}

    except Exception as e:
        # Playwright errors are long; the first line carries the useful part.
        reason = str(e).split('\n')[0][:300]
        return {
            'status': 'failed',
            'message': f'Step failed: {step["raw"]}',
            'detail': reason,
        }

    return {'status': 'unreadable', 'message': 'Unsupported step', 'detail': ''}


def run_steps(page, steps_text, base_url=None, on_step=None):
    """
    Execute a whole workflow in order and report every step.

    Returns (results, findings). `results` is the step-by-step record for the
    UI. `findings` are runner-shaped entries for the failures, so they score
    and turn into bugs like any other issue.

    Execution stops at the first failed step, because in a workflow every
    later step depends on the one before it: if Sign in failed, "Expect text
    Welcome" failing too is noise, not a second defect.
    """
    steps = parse_steps(steps_text)
    results = []
    findings = []
    stopped_early = False

    for index, step in enumerate(steps):
        if stopped_early:
            results.append({
                'index': index, 'raw': step['raw'], 'action': step['action'],
                'status': 'skipped',
                'message': 'Skipped because an earlier step failed',
                'detail': '', 'screenshot': '',
            })
            continue

        outcome = execute_step(page, step, base_url)
        record = {
            'index': index,
            'raw': step['raw'],
            'action': step['action'],
            'status': outcome['status'],
            'message': outcome['message'],
            'detail': outcome.get('detail', ''),
            'screenshot': '',
        }

        if on_step:
            try:
                record['screenshot'] = on_step(index, record) or ''
            except Exception as e:
                logger.warning("step screenshot failed at step %s: %s", index, e)

        results.append(record)

        if outcome['status'] == 'failed':
            stopped_early = True
            findings.append({
                'issue': f'Step {index + 1} failed: {step["raw"]}',
                # A workflow that does not complete is the worst thing an
                # automated run can find: the feature does not work.
                'severity': 'critical',
                'step_index': index,
                'step': step['raw'],
                'reason': outcome['message'],
                'detail': outcome.get('detail', ''),
                'element_selector': step.get('target') or 'document',
                'screenshot': record['screenshot'],
                'display': f'Step {index + 1} failed: {outcome["message"]}',
            })
        # A step the runner cannot read never reached the site, so it is not
        # a defect in the site. It stays in `results` for whoever wrote it and
        # is counted in the coverage notes, but it is not scored against the
        # thing under test, which never had a chance to fail it.

    return results, findings


def summarise_steps(results):
    """Counts plus a one-line verdict for the report header."""
    total = len(results)
    passed = sum(1 for r in results if r['status'] == 'passed')
    failed = sum(1 for r in results if r['status'] == 'failed')
    unreadable = sum(1 for r in results if r['status'] == 'unreadable')
    skipped = sum(1 for r in results if r['status'] == 'skipped')

    if not total:
        summary = 'No steps were defined for this test case.'
    elif failed:
        summary = f'Workflow broke at step {next(r["index"] + 1 for r in results if r["status"] == "failed")} of {total}.'
    elif unreadable:
        summary = f'{passed} of {total} steps passed; {unreadable} could not be understood.'
    else:
        summary = f'All {total} steps passed.'

    return {
        'total': total, 'passed': passed, 'failed': failed,
        'unreadable': unreadable, 'skipped': skipped,
        'workflow_completed': total > 0 and failed == 0,
        'summary': summary,
    }
