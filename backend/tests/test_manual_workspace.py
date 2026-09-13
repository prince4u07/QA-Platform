"""
The manual tester's workspace: the checklist, reported issues and verdict.

A manual tester finds bugs by using the software like a real user, spotting
layout flaws, poor design and behaviour that does not match what the business
asked for. None of that can be judged by a machine, so the job here is to let
them record it against the exact page and screenshot they were looking at.
"""

import pytest

from modules.runner.manual_run import (
    _ManualSession,
    _checklist_from,
    summarise_manual,
    resolve_manual_page_limit,
    ISSUE_CATEGORIES,
)


# ---------------- crawling the whole site by hand ----------------

def test_a_manual_run_can_crawl_past_the_landing_page():
    """
    The bug: the test-case form saved max_pages=1 for every manual test case,
    and the landing page is captured before auto-crawl starts. The crawl loop
    is `len(pages) < max_pages`, so with a limit of 1 it exited immediately
    and a manual run could only ever record the single page it opened on.
    """
    assert resolve_manual_page_limit(1) > 1


def test_an_unset_page_limit_still_crawls():
    for unset in (None, 0, '', 1):
        assert resolve_manual_page_limit(unset) > 1, unset


def test_a_real_chosen_page_limit_is_respected():
    assert resolve_manual_page_limit(5) == 5
    assert resolve_manual_page_limit(60) == 60


def test_the_page_limit_is_capped_so_one_run_cannot_crawl_forever():
    assert resolve_manual_page_limit(5000) == 100


def test_a_junk_page_limit_does_not_crash_the_run():
    assert resolve_manual_page_limit('lots') > 1


def test_a_manual_session_built_from_a_legacy_test_case_can_still_crawl():
    session = _ManualSession(1, 1, 'https://shop.test',
                             max_pages=resolve_manual_page_limit(1))
    session.pages.append({'url': 'https://shop.test/', 'screenshot': '/p1.png'})
    # The crawl loop's condition, which used to be false straight away.
    assert len(session.pages) < session.max_pages


@pytest.fixture()
def session():
    """A session object without starting its browser thread."""
    return _ManualSession(
        testcase_id=1, project_id=1, base_url='https://shop.test',
        steps_text='1. Open the homepage\n2. Add an item to the basket\n3. Check out',
        expected_result='The order confirmation shows the right total',
    )


# ---------------- the checklist ----------------

def test_written_steps_become_a_checklist():
    items = _checklist_from('1. Open the homepage\n2. Click Buy')
    assert [i['text'] for i in items] == ['Open the homepage', 'Click Buy']
    assert all(i['status'] == 'pending' for i in items)


def test_blank_lines_never_become_steps_nobody_can_complete():
    assert len(_checklist_from('Open it\n\n   \n\nClose it')) == 2


def test_a_test_case_with_no_steps_has_an_empty_checklist():
    assert _checklist_from('') == []
    assert _checklist_from(None) == []


def test_tester_can_mark_a_step_passed_or_failed(session):
    assert session.mark_step(0, 'passed') is True
    assert session.mark_step(1, 'failed', 'Basket count never updated') is True

    snap = session.snapshot()
    assert snap['steps'][0]['status'] == 'passed'
    assert snap['steps'][1]['status'] == 'failed'
    assert snap['steps'][1]['note'] == 'Basket count never updated'
    assert snap['steps_done'] == 2 and snap['steps_failed'] == 1


def test_marking_rejects_an_unknown_status_or_step(session):
    assert session.mark_step(0, 'probably fine') is False
    assert session.mark_step(99, 'passed') is False


def test_a_failed_step_pins_the_screen_it_failed_on(session):
    session.pages.append({'url': 'https://shop.test/basket',
                          'screenshot': '/shots/p002.png'})
    session.mark_step(1, 'failed', 'nothing happened')
    assert session.snapshot()['steps'][1]['screenshot'] == '/shots/p002.png'


# ---------------- reporting what a human sees ----------------

def test_tester_reports_a_layout_flaw_against_the_current_page(session):
    session.pages.append({'url': 'https://shop.test/checkout',
                          'screenshot': '/shots/p003.png'})

    entry = session.report_issue('layout', 'serious',
                                 'Pay button overlaps the total',
                                 note='Only below 900px wide')

    assert entry['category'] == 'layout'
    assert entry['severity'] == 'serious'
    # The evidence must be attached automatically; a tester should not have to.
    assert entry['page_url'] == 'https://shop.test/checkout'
    assert entry['screenshot'] == '/shots/p003.png'


def test_the_categories_cover_what_a_manual_tester_actually_looks_for():
    for expected in ('functional', 'ui-ux', 'layout', 'accessibility', 'content',
                     'business-logic', 'performance', 'broken-link', 'other'):
        assert expected in ISSUE_CATEGORIES


def test_an_unknown_category_or_severity_falls_back_safely(session):
    entry = session.report_issue('vibes', 'apocalyptic', 'Something is off')
    assert entry['category'] == 'other'
    assert entry['severity'] == 'moderate'


def test_an_empty_issue_title_is_refused(session):
    assert session.report_issue('layout', 'serious', '   ') is None
    assert session.snapshot()['reported_count'] == 0


def test_an_issue_can_be_tied_to_the_step_it_was_found_on(session):
    entry = session.report_issue('functional', 'critical',
                                 'Checkout throws a 500', step_index=2)
    assert entry['step_index'] == 2


def test_reporting_with_no_page_captured_yet_falls_back_to_the_project_url(session):
    entry = session.report_issue('design', 'minor', 'Font is hard to read')
    assert entry['page_url'] == 'https://shop.test'


# ---------------- the verdict ----------------

def test_summary_reports_steps_issues_and_the_business_verdict(session):
    session.mark_step(0, 'passed')
    session.mark_step(1, 'failed', 'basket did not update')
    session.report_issue('design', 'minor', 'Button colour is unreadable')
    session.set_expected_met(False)

    summary = summarise_manual(session.snapshot())

    assert summary['steps_total'] == 3
    assert summary['steps_failed'] == 1
    assert summary['steps_pending'] == 1
    assert summary['reported_total'] == 1
    assert summary['reported_by_severity']['minor'] == 1
    assert summary['expected_met'] is False
    assert 'expected result NOT met' in summary['summary']


def test_summary_of_an_untouched_run_says_nothing_was_recorded():
    empty = _ManualSession(1, 1, 'https://x.test')
    summary = summarise_manual(empty.snapshot())
    assert summary['steps_total'] == 0
    assert 'Nothing was recorded' in summary['summary']


def test_expected_result_is_carried_through_for_the_tester_to_answer(session):
    snap = session.snapshot()
    assert snap['expected_result'] == 'The order confirmation shows the right total'
    assert snap['expected_met'] is None       # unanswered until they say so

    session.set_expected_met(True)
    assert session.snapshot()['expected_met'] is True


def test_snapshot_exposes_the_category_list_so_the_ui_stays_in_sync(session):
    assert session.snapshot()['issue_categories'] == ISSUE_CATEGORIES
