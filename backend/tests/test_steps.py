"""
Unit tests for the executable-steps parser and workflow runner.

These cover the reading of a step and the shape of the result. The real
browser behaviour is covered separately in test_steps_e2e.py.
"""

from modules.runner.steps import (
    parse_step,
    parse_steps,
    run_steps,
    summarise_steps,
)


# ---------------- parsing ----------------

def test_parses_the_example_workflow_from_the_ui_placeholder():
    """The placeholder text the UI shows must actually be executable."""
    steps = parse_steps(
        "1. Open the homepage\n"
        "2. Click the search bar\n"
        "3. Type shoes into search\n"
        "4. Press Enter\n"
    )
    assert [s['action'] for s in steps] == ['goto', 'click', 'type', 'press']


def test_numbering_and_punctuation_are_ignored():
    for prefix in ('1. ', '2) ', '3] ', '4 - ', ''):
        step = parse_step(f'{prefix}Click Sign in')
        assert step['action'] == 'click'
        assert step['target'] == 'Sign in'


def test_parses_typing_both_ways_round():
    a = parse_step('Type alice@example.com into Email')
    assert (a['action'], a['value'], a['target']) == ('type', 'alice@example.com', 'Email')

    b = parse_step('Fill Password with hunter2')
    assert (b['action'], b['value'], b['target']) == ('type', 'hunter2', 'Password')


def test_url_assertion_beats_the_generic_text_assertion():
    """'Expect url contains /dashboard' must not be read as expecting that text."""
    step = parse_step('Expect url contains /dashboard')
    assert step['action'] == 'expect_url'
    assert step['value'] == '/dashboard'


def test_parses_the_text_assertion_wordings():
    for line in ('Expect text Welcome back', 'I should see Welcome back',
                 'Verify Welcome back', 'See Welcome back'):
        step = parse_step(line)
        assert step['action'] == 'expect_text', line
        assert 'Welcome back' in step['value'], line


def test_strips_trailing_noise_words_from_a_target():
    assert parse_step('Click the Sign in button')['target'] == 'Sign in'
    assert parse_step('Click on the Checkout link')['target'] == 'Checkout'


def test_quoted_values_keep_their_inner_spacing():
    step = parse_step('Type "  spaced value  " into Search')
    assert step['value'] == 'spaced value'


def test_blank_lines_are_dropped_not_counted_as_steps():
    assert parse_steps('Click A\n\n   \nClick B') and len(parse_steps('Click A\n\n   \nClick B')) == 2


def test_an_unreadable_line_is_reported_not_silently_skipped():
    """A typo must never look like a step that passed."""
    step = parse_step('mash the buttons until it works')
    assert step['action'] == 'unknown'


def test_parse_steps_handles_empty_input():
    assert parse_steps('') == []
    assert parse_steps(None) == []


# ---------------- execution ----------------

class _ScriptedPage:
    """A page whose every action succeeds or fails on command."""

    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.url = 'https://site.test/'
        self.calls = []

    def goto(self, url, **_kw):
        self.calls.append(('goto', url))
        self._maybe_fail('goto')
        self.url = url

    def _maybe_fail(self, what):
        if self.fail_on == what:
            raise RuntimeError('scripted failure')

    class _Keyboard:
        def __init__(self, page):
            self.page = page

        def press(self, key):
            self.page.calls.append(('press', key))
            self.page._maybe_fail('press')

    @property
    def keyboard(self):
        return _ScriptedPage._Keyboard(self)

    def get_by_text(self, text, **_kw):
        return _ScriptedLocator(self, text, 'text')

    def get_by_role(self, _role, name=None, **_kw):
        return _ScriptedLocator(self, name, 'role')

    def get_by_label(self, text, **_kw):
        return _ScriptedLocator(self, text, 'label')

    def get_by_placeholder(self, text, **_kw):
        return _ScriptedLocator(self, text, 'placeholder')

    def locator(self, sel):
        return _ScriptedLocator(self, sel, 'css')

    def screenshot(self, **_kw):
        return b''


class _ScriptedLocator:
    def __init__(self, page, target, how):
        self.page, self.target, self.how = page, target, how

    @property
    def first(self):
        return self

    def count(self):
        # 'role' is tried first for clicks, so resolving there keeps tests simple.
        return 1 if self.how in ('role', 'label') else 0

    def click(self, **_kw):
        self.page.calls.append(('click', self.target))
        self.page._maybe_fail('click')

    def fill(self, value, **_kw):
        self.page.calls.append(('fill', self.target, value))
        self.page._maybe_fail('fill')

    def wait_for(self, **_kw):
        self.page._maybe_fail('wait')


def test_a_passing_workflow_reports_every_step_as_passed():
    page = _ScriptedPage()
    results, findings = run_steps(page, 'Click Sign in\nPress Enter')
    assert [r['status'] for r in results] == ['passed', 'passed']
    assert findings == []
    assert summarise_steps(results)['workflow_completed'] is True


def test_a_failed_step_becomes_a_critical_functional_finding():
    """A feature that does not work is the most serious thing a run can find."""
    page = _ScriptedPage(fail_on='click')
    _results, findings = run_steps(page, 'Click Sign in')
    assert len(findings) == 1
    assert findings[0]['severity'] == 'critical'
    assert 'Step 1' in findings[0]['issue']


def test_later_steps_are_skipped_once_the_workflow_breaks():
    """
    In a workflow every step depends on the one before it. Reporting three
    failures when only the first is a real defect is noise.
    """
    page = _ScriptedPage(fail_on='click')
    results, findings = run_steps(
        page, 'Click Sign in\nExpect text Welcome\nClick Checkout')
    assert [r['status'] for r in results] == ['failed', 'skipped', 'skipped']
    assert len(findings) == 1


def test_unreadable_step_is_flagged_but_does_not_stop_the_run():
    page = _ScriptedPage()
    results, findings = run_steps(page, 'do a barrel roll\nClick Sign in')
    assert results[0]['status'] == 'unreadable'
    assert results[1]['status'] == 'passed'          # the run carried on
    assert findings[0]['severity'] == 'moderate'     # a typo is not a broken feature


def test_relative_goto_is_resolved_against_the_project_url():
    page = _ScriptedPage()
    run_steps(page, 'Open /checkout', base_url='https://shop.test')
    assert ('goto', 'https://shop.test/checkout') in page.calls


def test_url_assertion_fails_with_the_actual_url_in_the_message():
    page = _ScriptedPage()
    page.url = 'https://site.test/login?error=1'
    results, _ = run_steps(page, 'Expect url contains /dashboard')
    assert results[0]['status'] == 'failed'
    assert 'https://site.test/login?error=1' in results[0]['detail']


def test_screenshot_callback_is_attached_to_each_step():
    page = _ScriptedPage()
    results, _ = run_steps(page, 'Click Sign in',
                           on_step=lambda i, _r: f'/shot-{i}.png')
    assert results[0]['screenshot'] == '/shot-0.png'


def test_a_broken_screenshot_callback_does_not_fail_the_step():
    def _explode(_i, _r):
        raise RuntimeError('disk full')

    page = _ScriptedPage()
    results, _ = run_steps(page, 'Click Sign in', on_step=_explode)
    assert results[0]['status'] == 'passed'
    assert results[0]['screenshot'] == ''


def test_summary_names_the_step_the_workflow_broke_on():
    page = _ScriptedPage(fail_on='click')
    results, _ = run_steps(page, 'Press Enter\nClick Sign in\nClick Pay')
    summary = summarise_steps(results)
    assert summary['workflow_completed'] is False
    assert 'step 2 of 3' in summary['summary']


def test_summary_of_no_steps_is_not_a_pass():
    summary = summarise_steps([])
    assert summary['workflow_completed'] is False
    assert summary['total'] == 0
