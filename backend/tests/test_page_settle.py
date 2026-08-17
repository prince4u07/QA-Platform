"""
Waiting for a page to stop changing, instead of sleeping a fixed guess.

A flat sleep is a bet on how fast the machine is. On a loaded CI runner it
loses and the test fails for reasons unrelated to the code, which is exactly
the false failure a blocking pipeline cannot tolerate. These tests pin the
behaviour that replaced it.
"""

import time

from modules.runner.routes import wait_for_page_settled


class _Page:
    """A page whose signature changes for the first `churn` samples."""

    def __init__(self, churn=0, explode_after=None):
        self.samples = 0
        self.churn = churn
        self.explode_after = explode_after

    def evaluate(self, _script):
        self.samples += 1
        if self.explode_after is not None and self.samples > self.explode_after:
            raise RuntimeError('page closed mid-navigation')
        # Signature keeps changing while the page is still "rendering".
        return f'https://site.test/|{max(0, self.churn - self.samples)}'


def test_returns_true_once_the_page_holds_still():
    page = _Page(churn=0)
    assert wait_for_page_settled(page, timeout_ms=2000, quiet_ms=100) is True


def test_a_settled_page_returns_far_faster_than_a_fixed_sleep():
    """
    The point of the change: the old code slept 1000ms on every page. A page
    that is already still must cost roughly the quiet window, not the guess.
    """
    page = _Page(churn=0)
    started = time.time()
    wait_for_page_settled(page, timeout_ms=3000, quiet_ms=150)
    elapsed_ms = (time.time() - started) * 1000
    assert elapsed_ms < 600, f'settled page took {elapsed_ms:.0f}ms'


def test_it_keeps_waiting_while_the_page_is_still_changing():
    """A page mid-render must not be captured just because time passed."""
    page = _Page(churn=6)
    assert wait_for_page_settled(page, timeout_ms=250, quiet_ms=200) is False


def test_a_page_that_never_settles_gives_up_at_the_deadline():
    page = _Page(churn=10_000)
    started = time.time()
    result = wait_for_page_settled(page, timeout_ms=300, quiet_ms=100)
    elapsed_ms = (time.time() - started) * 1000
    assert result is False
    assert elapsed_ms < 1200, 'must respect its own deadline'


def test_a_slow_page_still_settles_when_it_eventually_stops():
    """
    The other half of the win: a page slower than the old 1000ms guess is now
    waited for properly instead of being captured half-rendered.
    """
    page = _Page(churn=4)
    assert wait_for_page_settled(page, timeout_ms=3000, quiet_ms=100) is True


def test_a_page_that_closes_mid_wait_is_not_an_error():
    """A closed page ends the wait quietly; the crawl has other pages to do."""
    page = _Page(churn=0, explode_after=1)
    assert wait_for_page_settled(page, timeout_ms=2000, quiet_ms=100) is False


def test_defaults_come_from_the_module_so_deployments_can_tune_them():
    from modules.runner import routes
    assert routes.SETTLE_TIMEOUT_MS > 0
    assert routes.SETTLE_QUIET_MS > 0
    assert routes.SETTLE_QUIET_MS < routes.SETTLE_TIMEOUT_MS, \
        'a quiet window longer than the timeout could never be satisfied'
