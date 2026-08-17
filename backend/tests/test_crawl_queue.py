"""
Which pages an authenticated crawl actually starts from.

When a login session has been captured, the pages worth auditing are the ones
behind the login. The configured base_url is usually the login screen itself,
which is public and sparse. Auditing it spends the page budget on a page the
user did not ask about and fills the report with findings from it.
"""

from modules.runner.routes import build_crawl_queue


LOGIN = 'https://app.example.com/user-login'
ROOT = 'https://app.example.com'
DASH = 'https://app.example.com/dashboard'


def test_anonymous_crawl_starts_at_the_configured_url():
    queue = build_crawl_queue(LOGIN, session_used=False)
    assert queue[0] == LOGIN


def test_anonymous_crawl_also_seeds_the_site_root():
    """Without a session the login page may be the only way in, so keep both."""
    queue = build_crawl_queue(LOGIN, session_used=False)
    assert ROOT in queue


def test_authenticated_crawl_starts_behind_the_login():
    queue = build_crawl_queue(LOGIN, landing_url=DASH, session_used=True)
    assert queue[0] == DASH


def test_authenticated_crawl_drops_the_login_page_entirely():
    """
    The bug: the login page stayed in the queue even when logged in, so a
    5-page crawl spent a slot auditing a page the user never asked about.
    """
    queue = build_crawl_queue(LOGIN, landing_url=DASH, session_used=True)
    assert LOGIN not in queue


def test_authenticated_crawl_keeps_the_login_page_if_there_is_nowhere_else():
    """
    Never return an empty queue. If the session gave us no landing page, the
    configured URL is still the only lead we have.
    """
    queue = build_crawl_queue(LOGIN, session_used=True)
    assert queue, 'a crawl with no start URL would test nothing at all'
    assert LOGIN in queue


def test_a_normal_base_url_is_never_dropped():
    queue = build_crawl_queue('https://shop.example.com/products',
                              landing_url=DASH, session_used=True)
    assert 'https://shop.example.com/products' in queue


def test_login_is_matched_on_the_path_not_the_domain():
    """
    'authenticnews.com' and 'loginworks.io' contain auth keywords in the host.
    Matching the whole URL would wrongly discard a perfectly good target.
    """
    for url in ('https://authenticnews.com/articles',
                'https://loginworks.io/pricing',
                'https://mysignin.com/'):
        queue = build_crawl_queue(url, landing_url=DASH, session_used=True)
        assert url.rstrip('/') in [q.rstrip('/') for q in queue], url


def test_queue_has_no_duplicates():
    queue = build_crawl_queue(ROOT, landing_url=ROOT, session_used=True)
    assert len(queue) == len(set(queue))


def test_trailing_slashes_do_not_create_a_second_entry():
    queue = build_crawl_queue('https://app.example.com/dashboard/',
                              landing_url=DASH, session_used=True)
    assert len(queue) == len(set(q.rstrip('/') for q in queue))


def test_a_junk_base_url_does_not_crash_the_crawl():
    assert build_crawl_queue('not a url', session_used=False)
