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
                 'Verify text Welcome back', 'Expect "Welcome back"'):
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

    def select_option(self, value=None, **_kw):
        chosen = _kw.get('label', value)
        self.page.calls.append(('select_option', self.target, chosen))
        self.page._maybe_fail('select_option')

    def check(self, **_kw):
        self.page.calls.append(('check', self.target))
        self.page._maybe_fail('check')

    def uncheck(self, **_kw):
        self.page.calls.append(('uncheck', self.target))
        self.page._maybe_fail('uncheck')

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
    # A typo never reached the site, so it is reported to its author
    # through `results` rather than scored against the site.
    assert findings == []


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


def test_prose_is_not_silently_read_as_an_assertion():
    """
    These are instructions written for a person. Read as assertions they can
    never pass, and a failed step is reported as a critical defect in the
    site, which is the worst kind of false positive this runner can produce.
    """
    for line in ('check all validations on empty field',
                 'and check all butttons and function should be running properly',
                 'verify the payment flow works end to end',
                 'see if the dashboard loads'):
        assert parse_step(line)['action'] == 'unknown', line


def test_quoting_makes_a_bare_assertion_unambiguous():
    """Quotes say "these exact words", which is not a guess."""
    step = parse_step('Verify "Order confirmed"')

    assert step['action'] == 'expect_text'
    assert step['value'] == 'Order confirmed'


def test_the_documented_assertion_forms_all_still_work():
    """These are the forms the UI actually tells people to use."""
    assert parse_step('Expect text Welcome back')['action'] == 'expect_text'
    assert parse_step('Expect url contains /dashboard')['action'] == 'expect_url'
    assert parse_step('I should see Welcome back')['action'] == 'expect_text'


# ---------------- dropdowns, checkboxes, absence, exact URLs ----------------

def test_parses_dropdown_selection_both_ways_round():
    a = parse_step('Select Extra Large in Size')
    assert (a['action'], a['value'], a['target']) == ('select_option', 'Extra Large', 'Size')

    b = parse_step('Set Country to India')
    assert (b['action'], b['value'], b['target']) == ('select_option', 'India', 'Country')


def test_a_bare_select_is_still_a_click():
    """Navigation menus use the word Select; only "X in Y" names an option."""
    assert parse_step('Select the Photos tab')['action'] == 'click'


def test_parses_checkbox_and_radio_actions():
    assert parse_step('Tick the Terms checkbox')['action'] == 'check'
    assert parse_step('Tick Newsletter')['target'] == 'Newsletter'
    assert parse_step('Check the Newsletter box')['action'] == 'check'
    assert parse_step('Uncheck Newsletter')['action'] == 'uncheck'
    assert parse_step('Untick the Terms checkbox')['target'] == 'Terms'


def test_a_bare_check_is_still_not_guessed_to_be_a_control():
    """Prose safety from the original runner must survive the new actions."""
    assert parse_step('check all validations on empty field')['action'] == 'unknown'
    assert parse_step('Check "Order confirmed"')['action'] == 'expect_text'


def test_clearing_a_field_needs_the_control_word():
    assert parse_step('Clear the Search box')['action'] == 'clear'
    assert parse_step('Clear the Search box')['target'] == 'Search'
    # A button labelled Clear is a click, not a field to empty.
    assert parse_step('Clear cart')['action'] == 'unknown'


def test_parses_absence_assertions():
    for line in ('Expect no "Item removed"', 'I should not see Error badge',
                 'Expect text "Loading" to be gone', 'Expect not to see Cart items'):
        step = parse_step(line)
        assert step['action'] == 'expect_no_text', line


def test_parses_exact_url_assertions():
    a = parse_step('Expect url to be /dashboard')
    assert (a['action'], a['value']) == ('expect_url_exact', '/dashboard')

    b = parse_step('The url should be https://site.test/dashboard')
    assert (b['action'], b['value']) == ('expect_url_exact', 'https://site.test/dashboard')

    # The "contains" wording still reads as contains.
    assert parse_step('Expect url contains /dashboard')['action'] == 'expect_url'


def test_parses_a_wait_step_with_its_cap_in_mind():
    assert parse_step('Wait 2 seconds')['action'] == 'wait_seconds'
    assert parse_step('sleep 3')['action'] == 'wait_seconds'


def test_a_dropdown_selection_uses_the_label_fallback():
    page = _ScriptedPage()
    results, _ = run_steps(page, 'Select Extra Large in Size')

    assert results[0]['status'] == 'passed'
    assert ('select_option', 'Size', 'Extra Large') in page.calls


def test_a_failing_dropdown_selection_is_reported_like_any_step():
    page = _ScriptedPage(fail_on='select_option')
    results, findings = run_steps(page, 'Set Country to India')

    assert results[0]['status'] == 'failed'
    assert len(findings) == 1


def test_tick_and_clear_drive_the_control_they_named():
    page = _ScriptedPage()
    # chr(10) rather than an escape sequence, so the two steps cannot be
    # mistaken for one line no matter how this file is edited later.
    two_steps = 'Tick the Terms checkbox' + chr(10) + 'Clear the Search box'
    results, _ = run_steps(page, two_steps)

    assert [r['status'] for r in results] == ['passed', 'passed']
    assert ('check', 'Terms') in page.calls
    assert ('fill', 'Search', '') in page.calls


def test_expect_no_text_passes_when_nothing_matches():
    page = _ScriptedPage()
    results, findings = run_steps(page, 'Expect no "Item removed"')

    assert results[0]['status'] == 'passed'
    assert findings == []


def test_expect_no_text_fails_while_the_text_is_still_there(monkeypatch):
    from modules.runner import steps as steps_module

    class _StubbornLocator:
        def count(self):
            return 1

    page = _ScriptedPage()
    monkeypatch.setattr(page, 'get_by_text', lambda text, **_kw: _StubbornLocator())
    monkeypatch.setattr(steps_module, 'STEP_TIMEOUT_MS', 300)

    results, findings = run_steps(page, 'Expect no "Item removed"')

    assert results[0]['status'] == 'failed'
    assert len(findings) == 1


def test_exact_url_assertion_checks_the_path(monkeypatch):
    from modules.runner import steps as steps_module

    page = _ScriptedPage()
    page.url = 'https://site.test/dashboard?tab=overview'
    results, _ = run_steps(page, 'Expect url to be /dashboard')
    assert results[0]['status'] == 'passed'

    page.url = 'https://site.test/settings'
    results, _ = run_steps(page, 'Expect url to be /dashboard')
    assert results[0]['status'] == 'failed'
    assert '/settings' in results[0]['detail']


def test_wait_seconds_honours_the_step_and_respects_the_cap(monkeypatch):
    from modules.runner import steps as steps_module

    slept = []
    monkeypatch.setattr(steps_module.time, 'sleep', slept.append)

    page = _ScriptedPage()
    results, _ = run_steps(page, 'Wait 60 seconds')
    assert results[0]['status'] == 'passed'
    assert slept == [steps_module.MAX_WAIT_SECONDS]


def test_uncheck_unticks_the_control_it_names():
    """Unticking is the mirror of ticking — same locator strategy, opposite action."""
    page = _ScriptedPage()
    results, _ = run_steps(page, 'Uncheck the Newsletter checkbox')

    assert results[0]['status'] == 'passed'
    assert ('uncheck', 'Newsletter') in page.calls


def test_press_maps_common_key_aliases_to_playwright_names():
    """A tester writes 'esc' or 'return'; the runner sends the Playwright key name."""
    page = _ScriptedPage()
    results, _ = run_steps(page, 'Press Enter')

    assert results[0]['status'] == 'passed'
    assert ('press', 'Enter') in page.calls

    # 'esc' is a common shorthand that must resolve to 'Escape'.
    page = _ScriptedPage()
    results, _ = run_steps(page, 'Press esc')
    assert results[0]['status'] == 'passed'
    assert ('press', 'Escape') in page.calls


def test_wait_for_element_blocks_until_visible_then_passes():
    """A bare 'Wait for X' step blocks until the element is visible, then passes."""
    page = _ScriptedPage()
    results, _ = run_steps(page, 'Wait for the Save button')

    assert results[0]['status'] == 'passed'
    assert 'Save button' in results[0]['message']

