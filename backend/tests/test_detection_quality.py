"""
Detection quality: findings that must NOT be reported, and defects that must.

Every test here comes from a specific false positive or missed defect found
while auditing the nine page checks. The checks are exercised through fake
Playwright objects so the rules can be pinned down without a browser.
"""

import requests

from modules.runner import routes as runner_routes
from modules.runner.routes import (
    check_accessibility_heuristic,
    check_broken_links,
    check_missing_alt,
    check_mobile,
    check_performance,
    check_security,
    filter_console_errors,
    merge_findings,
)


# ---------------------------------------------------------------- fakes

class FakeElement:
    """Minimum of the Playwright ElementHandle surface these checks touch."""

    def __init__(self, tag='a', display='inline', width=60, height=19,
                 text='', attrs=None):
        self.tag = tag
        self.display = display
        self._box = {'x': 0, 'y': 0, 'width': width, 'height': height}
        self._text = text
        self._attrs = attrs or {}

    def bounding_box(self):
        return dict(self._box)

    def inner_text(self):
        return self._text

    def get_attribute(self, name):
        return self._attrs.get(name)

    def evaluate(self, script):
        # The checks ask for computed style; the _safe_* selector helpers ask
        # for other things and tolerate anything falsy.
        if 'getComputedStyle' in script:
            return {'tag': self.tag.lower(), 'display': self.display}
        if 'nodeName' in script:
            return self.tag
        return ''


class FakeMobilePage:
    def __init__(self, scroll_width, client_width, clickables):
        self.scroll_width = scroll_width
        self.client_width = client_width
        self.clickables = clickables

    def evaluate(self, script):
        if 'scrollWidth' in script:
            return self.scroll_width
        if 'clientWidth' in script:
            return self.client_width
        return None

    def query_selector_all(self, _selector):
        return self.clickables


class FakeImagePage:
    def __init__(self, images):
        self.images = images

    def query_selector_all(self, _selector):
        return self.images


def inline_link(text='read the terms'):
    """A link sitting inside a sentence, as on any content page."""
    return FakeElement(tag='a', display='inline', width=90, height=19, text=text)


def small_button(text='x'):
    """A genuinely undersized control: not inline, and below 44px."""
    return FakeElement(tag='button', display='inline-block',
                       width=24, height=24, text=text)


def image(alt=None, src='/img/hero.png', width=300, height=200, **attrs):
    attrs['alt'] = alt
    attrs['src'] = src
    return FakeElement(tag='img', width=width, height=height, attrs=attrs)


# ------------------------------------------------- 1. tap target noise

def test_inline_text_links_are_not_reported_as_small_tap_targets():
    """
    A link inside a sentence is roughly 19px tall, so a blanket 44px rule
    flags every link on a content page. WCAG exempts inline links precisely
    because they cannot be resized without breaking the text they sit in.
    """
    page = FakeMobilePage(390, 390, [inline_link(), inline_link('privacy')])

    findings, _coverage = check_mobile(page)

    assert findings == []


def test_a_genuinely_undersized_control_is_still_reported():
    """The exemption must not swallow the real defect it exists beside."""
    page = FakeMobilePage(390, 390, [small_button(), small_button('y')])

    findings, _coverage = check_mobile(page)

    assert len(findings) == 1
    assert 'too small' in findings[0]['issue']


def test_a_link_styled_as_a_button_is_still_measured():
    """Only text-flow links are exempt, not anything that happens to be an <a>."""
    tappable = FakeElement(tag='a', display='inline-block',
                           width=30, height=30, text='Buy')
    page = FakeMobilePage(390, 390, [tappable])

    findings, _coverage = check_mobile(page)

    assert len(findings) == 1


# ------------------------------------------------------ 2. missing alt

def test_an_explicitly_decorative_image_is_not_missing_alt():
    """alt="" is the correct way to mark a decorative image, not a defect."""
    page = FakeImagePage([image(alt='')])

    assert check_missing_alt(page) == []


def test_an_image_hidden_from_assistive_tech_is_not_missing_alt():
    page = FakeImagePage([
        image(alt=None, **{'aria-hidden': 'true'}),
        image(alt=None, role='presentation'),
    ])

    assert check_missing_alt(page) == []


def test_an_image_with_no_alt_attribute_at_all_is_still_reported():
    page = FakeImagePage([image(alt=None, src='/img/product.png')])

    findings = check_missing_alt(page)

    assert len(findings) == 1
    assert findings[0]['src'] == '/img/product.png'


# --------------------------------------------------- 3. mobile dedup

def test_the_same_tap_target_problem_merges_across_pages():
    """
    The count differs per page, so keying on the sentence made every page a
    unique finding. It is one defect, however many pages show it.
    """
    page_a, _ = check_mobile(FakeMobilePage(390, 390, [small_button() for _ in range(3)]))
    page_b, _ = check_mobile(FakeMobilePage(390, 390, [small_button() for _ in range(7)]))

    merged = merge_findings([{'mobile_issues': page_a}, {'mobile_issues': page_b}])

    assert len(merged['mobile_issues']) == 1


def test_horizontal_scrolling_merges_across_pages_of_different_widths():
    page_a, _ = check_mobile(FakeMobilePage(1280, 390, []))
    page_b, _ = check_mobile(FakeMobilePage(900, 390, []))

    merged = merge_findings([{'mobile_issues': page_a}, {'mobile_issues': page_b}])

    assert len(merged['mobile_issues']) == 1


def test_two_different_mobile_problems_are_still_two_findings():
    page, _ = check_mobile(FakeMobilePage(1280, 390, [small_button()]))

    merged = merge_findings([{'mobile_issues': page}])

    assert len(merged['mobile_issues']) == 2


# ------------------------------------------------ 4. accessibility dedup

def test_the_same_wcag_rule_merges_across_pages():
    """
    axe reports the first offending element, which differs page to page, so
    keying on it counted one site-wide rule breach once per page.
    """
    pages = [
        {'accessibility_issues': [{'rule': 'color-contrast',
                                   'element_selector': 'div.header > a'}]},
        {'accessibility_issues': [{'rule': 'color-contrast',
                                   'element_selector': 'main > button.buy'}]},
    ]

    merged = merge_findings(pages)

    assert len(merged['accessibility_issues']) == 1


def test_different_wcag_rules_stay_separate():
    pages = [
        {'accessibility_issues': [{'rule': 'color-contrast', 'element_selector': 'a'}]},
        {'accessibility_issues': [{'rule': 'image-alt', 'element_selector': 'img'}]},
    ]

    assert len(merge_findings(pages)['accessibility_issues']) == 2


# ------------------------------------------------- 5 & 6. security headers

def test_csp_frame_ancestors_satisfies_the_clickjacking_check():
    """
    frame-ancestors is the modern replacement for X-Frame-Options. A site
    using it correctly must not be told it is missing protection it has.
    """
    findings = check_security('https://site.com', {
        'Content-Security-Policy': "default-src 'self'; frame-ancestors 'none'",
        'Strict-Transport-Security': 'max-age=63072000',
    })

    assert findings == []


def test_a_missing_frame_protection_is_still_reported():
    findings = check_security('https://site.com', {
        'Content-Security-Policy': "default-src 'self'",
        'Strict-Transport-Security': 'max-age=63072000',
    })

    assert len(findings) == 1
    assert 'X-Frame-Options' in findings[0]['issue']


def test_an_http_site_is_not_also_penalised_for_missing_hsts():
    """
    HSTS is meaningless over plain HTTP. Reporting both is charging twice
    for one root cause, which drags the score down for a single fault.
    """
    findings = check_security('http://site.com', {
        'Content-Security-Policy': "frame-ancestors 'none'",
    })

    joined = ' '.join(f['issue'] for f in findings)
    assert 'HTTP' in joined
    assert 'HSTS' not in joined


def test_an_https_site_missing_hsts_is_still_reported():
    findings = check_security('https://site.com', {
        'Content-Security-Policy': "frame-ancestors 'none'",
    })

    joined = ' '.join(f['issue'] for f in findings)
    assert 'HSTS' in joined


# ============================================================
# 7 & 8. Broken links: what gets checked, and what counts as broken
# ============================================================

class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def close(self):
        pass


class FakeCookieJar:
    def set(self, *_args, **_kwargs):
        pass


class RecordingSession:
    """Stands in for requests.Session so link checks never touch the network."""

    def __init__(self, responder=None):
        self.headers = {}
        self.cookies = FakeCookieJar()
        self.requested = []
        self._responder = responder or (lambda _url, _attempt: FakeResponse(200))
        self._attempts = {}

    def _respond(self, url):
        self.requested.append(url)
        self._attempts[url] = self._attempts.get(url, 0) + 1
        return self._responder(url, self._attempts[url])

    def head(self, url, **_kwargs):
        return self._respond(url)

    def get(self, url, **_kwargs):
        return self._respond(url)


class FakeBrowserContext:
    def cookies(self):
        return []


class FakeLinkPage:
    def __init__(self, hrefs):
        self.links = [FakeElement(tag='a', text='link', attrs={'href': h}) for h in hrefs]
        self.context = FakeBrowserContext()

    def query_selector_all(self, _selector):
        return self.links

    def evaluate(self, _script):
        return 'FakeAgent/1.0'


def use_session(monkeypatch, session):
    monkeypatch.setattr(runner_routes.requests, 'Session', lambda: session)


def test_a_relative_link_is_checked_not_silently_skipped(monkeypatch):
    """
    Only hrefs starting with / or http were considered, so about.html and
    ../contact were dropped before they were even counted. That made
    "0 broken links" mean "none among the ones we bothered to look at".
    """
    session = RecordingSession()
    use_session(monkeypatch, session)

    _findings, coverage = check_broken_links(
        FakeLinkPage(['about.html', '../contact', '/pricing']),
        'https://site.com/docs/guide',
    )

    assert coverage['links_found'] == 3
    assert coverage['links_checked'] == 3


def test_a_document_relative_link_resolves_against_its_own_page(monkeypatch):
    """about.html next to /docs/guide is /docs/about.html, not /about.html."""
    session = RecordingSession()
    use_session(monkeypatch, session)

    check_broken_links(FakeLinkPage(['about.html']), 'https://site.com/docs/guide')

    assert session.requested == ['https://site.com/docs/about.html']


def test_links_differing_only_by_fragment_are_checked_once(monkeypatch):
    """#section points at the same document, so it is not a second request."""
    session = RecordingSession()
    use_session(monkeypatch, session)

    _findings, coverage = check_broken_links(
        FakeLinkPage(['/guide#intro', '/guide#setup', '#top']),
        'https://site.com/',
    )

    assert coverage['links_checked'] == 1
    assert session.requested == ['https://site.com/guide']


def test_a_slow_link_is_retried_before_being_called_broken(monkeypatch):
    """
    A 3 second timeout is not proof a link is dead. A slow server that
    answers on the retry must not be reported as a broken link.
    """
    def responder(_url, attempt):
        if attempt == 1:
            raise requests.Timeout('too slow')
        return FakeResponse(200)

    use_session(monkeypatch, RecordingSession(responder))

    findings, _coverage = check_broken_links(
        FakeLinkPage(['https://slow.example/page']), 'https://site.com/')

    assert findings == []


def test_a_link_that_never_responds_is_still_reported(monkeypatch):
    def responder(_url, _attempt):
        raise requests.ConnectionError('no such host')

    use_session(monkeypatch, RecordingSession(responder))

    findings, _coverage = check_broken_links(
        FakeLinkPage(['https://dead.example/page']), 'https://site.com/')

    assert len(findings) == 1


def test_an_auth_wall_is_not_a_broken_link(monkeypatch):
    def responder(_url, _attempt):
        return FakeResponse(403)

    use_session(monkeypatch, RecordingSession(responder))

    findings, _coverage = check_broken_links(
        FakeLinkPage(['https://site.com/members']), 'https://site.com/')

    assert findings == []


def test_a_dead_page_is_reported(monkeypatch):
    def responder(_url, _attempt):
        return FakeResponse(404)

    use_session(monkeypatch, RecordingSession(responder))

    findings, _coverage = check_broken_links(
        FakeLinkPage(['https://site.com/gone']), 'https://site.com/')

    assert len(findings) == 1
    assert findings[0]['status_code'] == 404


# ============================================================
# 9. Console errors: what is noise and what is a defect
# ============================================================

def test_mixed_content_is_not_discarded_as_noise():
    """
    Mixed content is a real security defect. It was in the skip list, so the
    one check most likely to notice it threw it away.
    """
    out = filter_console_errors([
        {'type': 'error',
         'text': 'Mixed Content: page loaded over HTTPS requested an insecure script'},
    ])

    assert len(out) == 1


def test_browser_extension_noise_is_still_filtered():
    out = filter_console_errors([
        {'type': 'error', 'text': 'chrome-extension://abc/inject.js failed'},
    ])

    assert out == []


def test_a_real_error_that_mentions_an_extension_is_kept():
    """'extension' as a bare substring also matched genuine application errors."""
    out = filter_console_errors([
        {'type': 'error', 'text': 'Upload failed: file extension .xyz is not supported'},
    ])

    assert len(out) == 1


# ============================================================
# 10. Performance: weight the browser refused to measure
# ============================================================

class FakePerfPage:
    def __init__(self, payload):
        self.payload = payload

    def evaluate(self, _script):
        return self.payload


def test_resources_the_browser_would_not_measure_are_reported():
    """
    transferSize is 0 for cross-origin resources without Timing-Allow-Origin,
    so a CDN-heavy site can look weightless. The run must say the measurement
    was incomplete rather than quietly report a light page.
    """
    _findings, metrics = check_performance(
        FakePerfPage({'bytes': 120_000, 'count': 40, 'top': [], 'unmeasured': 26}), 900)

    assert metrics['unmeasured_resources'] == 26


def test_a_fully_measured_page_reports_nothing_unmeasured():
    _findings, metrics = check_performance(
        FakePerfPage({'bytes': 120_000, 'count': 40, 'top': [], 'unmeasured': 0}), 900)

    assert metrics['unmeasured_resources'] == 0


# ============================================================
# 11. Accessibility fallback: what counts as a label
# ============================================================

class FakeInput:
    def __init__(self, attrs=None, inside_label=False):
        self._attrs = attrs or {}
        self.inside_label = inside_label

    def get_attribute(self, name):
        return self._attrs.get(name)

    def bounding_box(self):
        return {'x': 0, 'y': 0, 'width': 200, 'height': 30}

    def evaluate(self, script):
        if 'closest' in script:
            return self.inside_label
        return ''


class FakeFormPage:
    def __init__(self, inputs, labels_for=()):
        self.inputs = inputs
        self.labels_for = set(labels_for)

    def query_selector_all(self, selector):
        return [] if selector == 'button' else self.inputs

    def query_selector(self, selector):
        for ident in self.labels_for:
            if selector == 'label[for="%s"]' % ident:
                return object()
        return None


def test_a_placeholder_is_not_an_accessible_label():
    """
    A placeholder disappears as soon as the user types and is not exposed as
    a label. Accepting it meant genuinely unlabelled inputs passed the check.
    """
    page = FakeFormPage([FakeInput({'name': 'email', 'placeholder': 'Email address'})])

    findings = check_accessibility_heuristic(page)

    assert len(findings) == 1
    assert 'email' in findings[0]['issue']


def test_an_input_wrapped_in_its_own_label_is_not_flagged():
    """A label wrapping its input needs no for attribute to be valid."""
    page = FakeFormPage([FakeInput({'name': 'city'}, inside_label=True)])

    assert check_accessibility_heuristic(page) == []


def test_an_input_labelled_by_aria_labelledby_is_not_flagged():
    page = FakeFormPage([FakeInput({'name': 'q', 'aria-labelledby': 'search-heading'})])

    assert check_accessibility_heuristic(page) == []


def test_an_input_with_a_real_label_element_is_not_flagged():
    page = FakeFormPage([FakeInput({'name': 'user', 'id': 'user'})], labels_for=['user'])

    assert check_accessibility_heuristic(page) == []


# ============================================================
# 12 to 15. Scoring: charging once, counting elements, SEO cover
# ============================================================

from modules.runner.routes import (  # noqa: E402
    calculate_health_score,
    check_accessibility_axe,
    check_seo,
)


class FakeAxePage:
    """Stands in for a page with axe-core already injected."""

    def __init__(self, violations):
        self.violations = violations

    def add_script_tag(self, path=None):
        return None

    def evaluate(self, _script):
        return self.violations

    def query_selector(self, _selector):
        return None


def violation(rule, impact='serious', nodes=1):
    return {
        'id': rule,
        'impact': impact,
        'help': rule.replace('-', ' '),
        'description': '',
        'helpUrl': '',
        'nodes': [{'target': ['#el%d' % i], 'html': '<i>'} for i in range(nodes)],
    }


def test_axe_does_not_also_charge_for_missing_image_alt():
    """
    Missing alt has its own category with its own weight. Letting axe report
    it too charged one defect against two weighted categories at once.
    """
    findings = check_accessibility_axe(
        FakeAxePage([violation('image-alt'), violation('color-contrast')]))

    rules = [f['rule'] for f in findings]
    assert 'color-contrast' in rules
    assert 'image-alt' not in rules


def test_axe_still_reports_every_other_rule():
    findings = check_accessibility_axe(
        FakeAxePage([violation('label'), violation('link-name')]))

    assert len(findings) == 2


def test_a_smaller_image_without_alt_is_still_reported():
    """
    The 100px floor meant a 64px image with no alt was reported by nothing
    at all once axe stopped covering the same ground.
    """
    page = FakeImagePage([image(alt=None, width=64, height=64)])

    assert len(check_missing_alt(page)) == 1


def test_a_tracking_pixel_is_not_reported_as_missing_alt():
    """A 1x1 pixel conveys nothing, so demanding alt text on it is noise."""
    page = FakeImagePage([image(alt=None, width=1, height=1)])

    assert check_missing_alt(page) == []


def test_a_rule_broken_by_many_elements_costs_more_than_one():
    """
    axe reports one finding per rule and counts the offending elements.
    Ignoring that count made 200 contrast failures score exactly the same
    as a single one.
    """
    one = calculate_health_score(
        {'accessibility_issues': [{'severity': 'serious', 'count': 1}]})
    many = calculate_health_score(
        {'accessibility_issues': [{'severity': 'serious', 'count': 200}]})

    assert many < one


def test_the_element_count_never_swamps_the_whole_score():
    """Growth stays gentle so one noisy rule cannot flatten everything."""
    many = calculate_health_score(
        {'accessibility_issues': [{'severity': 'serious', 'count': 5000}]})

    assert many > 0


def test_a_finding_with_no_element_count_scores_as_before():
    with_count = calculate_health_score(
        {'broken_links': [{'severity': 'serious', 'count': 1}]})
    without = calculate_health_score({'broken_links': [{'severity': 'serious'}]})

    assert with_count == without


class FakeMeta:
    def __init__(self, content):
        self.content = content

    def get_attribute(self, name):
        return self.content if name == 'content' else None


class FakeSeoPage:
    def __init__(self, title='A page', description='what it is', has_h1=True):
        self._title = title
        self._description = description
        self._has_h1 = has_h1

    def title(self):
        return self._title

    def query_selector(self, selector):
        if selector.startswith('meta'):
            return FakeMeta(self._description) if self._description is not None else None
        if selector == 'h1':
            return object() if self._has_h1 else None
        return None


def test_a_missing_meta_description_is_reported():
    """The SEO category carried real weight while asserting only one thing."""
    findings = check_seo(FakeSeoPage(description=None), 'https://site.com/')

    joined = ' '.join(f['issue'] for f in findings)
    assert 'meta description' in joined


def test_an_empty_meta_description_counts_as_missing():
    findings = check_seo(FakeSeoPage(description='   '), 'https://site.com/')

    joined = ' '.join(f['issue'] for f in findings)
    assert 'meta description' in joined


def test_a_page_with_no_h1_is_reported():
    findings = check_seo(FakeSeoPage(has_h1=False), 'https://site.com/')

    joined = ' '.join(f['issue'] for f in findings)
    assert 'H1' in joined


def test_a_missing_title_is_still_reported():
    findings = check_seo(FakeSeoPage(title=''), 'https://site.com/')

    joined = ' '.join(f['issue'] for f in findings)
    assert 'no title' in joined


def test_a_well_formed_page_reports_no_seo_problems():
    assert check_seo(FakeSeoPage(), 'https://site.com/') == []


class RecordingCookieJar:
    def __init__(self):
        self.set_calls = []

    def set(self, name, value, **kwargs):
        self.set_calls.append((name, value, kwargs.get('domain')))


class CookieRecordingSession(RecordingSession):
    def __init__(self, responder=None):
        super().__init__(responder)
        self.cookies = RecordingCookieJar()


class AuthedContext:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self):
        return self._cookies


class AuthedLinkPage(FakeLinkPage):
    def __init__(self, hrefs, cookies):
        super().__init__(hrefs)
        self.context = AuthedContext(cookies)


def test_browser_cookies_are_forwarded_to_link_checks(monkeypatch):
    """
    Links behind a login must be checked as the logged-in user. Without the
    session cookies every protected link answers 401/403 and looks broken.
    Guards the per-thread session rework against losing them.
    """
    session = CookieRecordingSession()
    use_session(monkeypatch, session)

    check_broken_links(
        AuthedLinkPage(['/members'],
                       [{'name': 'sid', 'value': 'abc123',
                         'domain': '.site.com', 'path': '/'}]),
        'https://site.com/')

    assert ('sid', 'abc123', 'site.com') in session.cookies.set_calls


from modules.runner.routes import summarise_coverage  # noqa: E402


def test_coverage_says_when_page_weight_could_not_be_measured():
    """
    A CDN-heavy page reports almost no weight because the browser refuses to
    measure cross-origin resources. Without a note, "light page" reads as a
    fact rather than as a measurement that never actually happened.
    """
    cov = summarise_coverage([
        {'performance': {'page_size_kb': 0, 'request_count': 40,
                         'unmeasured_resources': 26}},
    ])

    assert cov['complete'] is False
    assert '26' in ' '.join(cov['notes'])


def test_a_fully_measured_run_carries_no_weight_note():
    cov = summarise_coverage([
        {'performance': {'page_size_kb': 900, 'request_count': 40,
                         'unmeasured_resources': 0}},
    ])

    assert cov['complete'] is True


# ============================================================
# 16. A check that never ran must not score as a perfect one
# ============================================================

from modules.runner.routes import (  # noqa: E402
    CATEGORY_WEIGHT,
    calculate_health_score,
    measured_categories,
)
from modules.runner.steps import summarise_steps  # noqa: E402

ALL_CATEGORIES = set(CATEGORY_WEIGHT)


def damaged_site():
    """
    A site with real problems. Excluding a perfect category pulls the average
    toward the measured reality, so the effect is only visible when there is
    a reality to pull toward. On a near-clean site it is under a rounding step.
    """
    return {
        'broken_links': [{'severity': 'serious'} for _ in range(5)],
        'security_issues': [{'severity': 'critical'} for _ in range(2)],
        'console_errors': [{'severity': 'serious'} for _ in range(3)],
    }


def test_a_skipped_mobile_check_does_not_count_as_full_marks():
    """
    Authenticated crawls run on a desktop viewport and skip the mobile
    checks entirely. With no findings that category scored 100, so skipping
    a check quietly raised the score. Not measuring is not a pass.
    """
    findings = damaged_site()

    counted_as_perfect = calculate_health_score(findings)
    honest = calculate_health_score(findings, measured=ALL_CATEGORIES - {'mobile_issues'})

    assert honest < counted_as_perfect


def test_a_run_with_no_executed_steps_does_not_bank_the_functional_weight():
    """
    functional_issues carries 25 of the 100 weight, more than any other
    category. When no step ever executed there is nothing to report, so
    that quarter of the score was awarded for work that never happened.
    """
    findings = damaged_site()

    banked = calculate_health_score(findings)
    honest = calculate_health_score(findings, measured=ALL_CATEGORIES - {'functional_issues'})

    assert honest < banked


def test_a_measured_category_that_is_genuinely_clean_still_scores_full_marks():
    """The fix must not punish a check that ran and honestly found nothing."""
    assert calculate_health_score({}, measured=ALL_CATEGORIES) == 100


def test_scoring_is_unchanged_when_no_category_is_declared_unmeasured():
    """Existing behaviour has to survive: measured is an addition, not a rewrite."""
    findings = {'broken_links': [{'severity': 'serious'}],
                'seo_issues': [{'severity': 'moderate'}]}

    assert calculate_health_score(findings) == calculate_health_score(
        findings, measured=ALL_CATEGORIES)


def test_functional_is_unmeasured_when_no_step_ran_at_all():
    assert 'functional_issues' not in measured_categories(
        summarise_steps([]), mobile_checked=True)


def test_functional_is_measured_once_a_step_actually_executed():
    ran = summarise_steps([{'index': 0, 'status': 'passed'}])

    assert 'functional_issues' in measured_categories(ran, mobile_checked=True)


def test_a_failed_step_still_counts_as_a_functional_measurement():
    """A step that ran and failed is exactly the signal this category exists for."""
    failed = summarise_steps([{'index': 0, 'status': 'failed'}])

    assert 'functional_issues' in measured_categories(failed, mobile_checked=True)


def test_steps_the_runner_could_not_read_are_not_a_measurement():
    """
    Prose steps never execute, so nothing about the site was verified. The
    step is still reported to the author, but the site is not scored on a
    check that never actually ran.
    """
    unreadable = summarise_steps([{'index': 0, 'status': 'unreadable'},
                                  {'index': 1, 'status': 'unreadable'}])

    assert 'functional_issues' not in measured_categories(unreadable, mobile_checked=True)


def test_mobile_is_unmeasured_when_the_check_was_skipped():
    ran = summarise_steps([{'index': 0, 'status': 'passed'}])

    assert 'mobile_issues' not in measured_categories(ran, mobile_checked=False)


def test_every_other_category_is_always_measured():
    """These nine run on every page, so only mobile and functional can be absent."""
    measured = measured_categories(summarise_steps([]), mobile_checked=False)

    assert measured == ALL_CATEGORIES - {'mobile_issues', 'functional_issues'}


def test_coverage_says_when_steps_could_not_be_run():
    """
    A run where most steps were unreadable verified almost nothing, and the
    report has to say so rather than presenting the score as a full result.
    """
    cov = summarise_coverage([
        {'steps': {'total': 6, 'passed': 2, 'failed': 0,
                   'unreadable': 4, 'skipped': 0}},
    ])

    assert cov['complete'] is False
    assert '4' in ' '.join(cov['notes'])


def test_coverage_is_quiet_when_every_step_ran():
    cov = summarise_coverage([
        {'steps': {'total': 3, 'passed': 3, 'failed': 0,
                   'unreadable': 0, 'skipped': 0}},
    ])

    assert cov['complete'] is True


# ============================================================
# 17. A broken tool is not the site improving
# ============================================================

from modules.runner.steps import run_steps  # noqa: E402


def test_accessibility_is_unmeasured_when_axe_never_ran():
    ran = summarise_steps([{'index': 0, 'status': 'passed'}])

    measured = measured_categories(ran, mobile_checked=True,
                                   accessibility_measured=False)

    assert 'accessibility_issues' not in measured


def test_a_failed_accessibility_engine_cannot_raise_the_score():
    """
    The heuristic fallback finds far less than axe, so when the engine broke
    the run produced fewer findings and the score went up. A tool failing is
    not the site getting better.
    """
    ran = summarise_steps([{'index': 0, 'status': 'passed'}])
    site = damaged_site()

    with_axe = calculate_health_score(
        site, measured=measured_categories(ran, True, accessibility_measured=True))
    engine_broke = calculate_health_score(
        site, measured=measured_categories(ran, True, accessibility_measured=False))

    assert engine_broke < with_axe


def test_accessibility_still_counts_when_axe_ran():
    ran = summarise_steps([{'index': 0, 'status': 'passed'}])

    measured = measured_categories(ran, mobile_checked=True,
                                   accessibility_measured=True)

    assert 'accessibility_issues' in measured


# ============================================================
# 18. Coverage caps high enough to mean something
# ============================================================

def test_a_link_heavy_page_is_covered_not_sampled(monkeypatch):
    """
    The old cap stopped at 30 links per page, so a page with 80 links was
    mostly unchecked and "no broken links" meant very little.
    """
    session = RecordingSession()
    use_session(monkeypatch, session)

    _findings, coverage = check_broken_links(
        FakeLinkPage(['/page%d' % i for i in range(80)]), 'https://site.com/')

    assert coverage['links_checked'] == 80
    assert coverage['truncated'] is False


def test_a_page_with_many_tap_targets_is_covered(monkeypatch):
    page = FakeMobilePage(390, 390, [small_button('b%d' % i) for i in range(120)])

    _findings, coverage = check_mobile(page)

    assert coverage['tap_targets_checked'] == 120
    assert coverage['truncated'] is False


def test_a_genuinely_enormous_page_is_still_capped_and_says_so(monkeypatch):
    """The cap has to stay, or one page can hold up the whole crawl."""
    session = RecordingSession()
    use_session(monkeypatch, session)

    _findings, coverage = check_broken_links(
        FakeLinkPage(['/page%d' % i for i in range(500)]), 'https://site.com/')

    assert coverage['truncated'] is True
    assert coverage['links_found'] == 500
    assert coverage['links_checked'] < 500


# ============================================================
# 19. A badly written step is not a defect in the site
# ============================================================

def test_an_unreadable_step_is_not_charged_against_the_site():
    """
    "check all validations on empty field" is a badly worded step, not a bug
    in the site under test. It is still reported to the author through the
    step record and the coverage note, but it no longer scores against the
    site, which never had a chance to fail it.
    """
    results, findings = run_steps(
        None, 'and check all butttons and function should be running properly',
        'https://site.com/')

    assert results[0]['status'] == 'unreadable'
    assert findings == []


def test_the_unreadable_step_is_still_visible_to_whoever_wrote_it():
    results, _findings = run_steps(
        None, 'i lareday login', 'https://site.com/')

    assert 'could not' in results[0]['message'].lower() or results[0]['detail']


# ============================================================
# 20. Dead controls: a button that does nothing at all
# ============================================================

from modules.runner.routes import check_dead_controls  # noqa: E402


class FakeControl:
    """A clickable thing, with whatever it does to the page when clicked."""

    def __init__(self, label='Show more', tag='button', in_form=False,
                 disabled=False, attrs=None, effect=None):
        self.label = label
        self.tag = tag
        self.in_form = in_form
        self.disabled = disabled
        self._attrs = attrs or {}
        self.effect = effect
        self.clicked = False

    def inner_text(self):
        return self.label

    def get_attribute(self, name):
        if name == 'aria-label':
            return self._attrs.get('aria-label')
        return self._attrs.get(name)

    def is_enabled(self):
        return not self.disabled

    def bounding_box(self):
        return {'x': 0, 'y': 0, 'width': 80, 'height': 30}

    def evaluate(self, script):
        if 'closest' in script:
            return self.in_form
        if 'getComputedStyle' in script:
            return {'tag': self.tag, 'display': 'inline-block'}
        if 'nodeName' in script:
            return self.tag
        return ''

    def click(self, **_kwargs):
        self.clicked = True
        if self.effect:
            self.effect()


class FakeControlPage:
    def __init__(self, controls, url='https://site.com/page'):
        self.controls = controls
        self.state = {'url': url, 'html': 5000, 'nodes': 120,
                      'requests': 3, 'storage': 1}
        self.goto_calls = []

    def query_selector_all(self, _selector):
        return self.controls

    def evaluate(self, script):
        if 'location.href' in script:
            return dict(self.state)
        return None

    def wait_for_timeout(self, _ms):
        pass

    def goto(self, url, **_kwargs):
        self.goto_calls.append(url)
        self.state['url'] = url


def alive(page, key='nodes'):
    """An effect that changes the page the way a working control would."""
    def effect():
        page.state[key] = page.state[key] + 1 if key != 'url' else 'https://site.com/next'
    return effect


def test_a_button_that_changes_nothing_at_all_is_reported():
    """
    The blind spot this exists for: a button wired to nothing. No console
    error, no broken link, nothing else in the platform notices it.
    """
    page = FakeControlPage([FakeControl('Show more')])

    findings, _coverage = check_dead_controls(page, 'https://site.com/page')

    assert len(findings) == 1
    assert 'Show more' in findings[0]['display']


def test_a_button_that_changes_the_page_is_not_reported():
    page = FakeControlPage([])
    control = FakeControl('Show more', effect=alive(page))
    page.controls = [control]

    findings, _coverage = check_dead_controls(page, 'https://site.com/page')

    assert findings == []
    assert control.clicked is True


def test_a_button_that_only_makes_a_network_request_is_alive():
    """An async control may not touch the DOM before we look."""
    page = FakeControlPage([])
    page.controls = [FakeControl('Load data', effect=alive(page, 'requests'))]

    findings, _coverage = check_dead_controls(page, 'https://site.com/page')

    assert findings == []


def test_a_button_that_only_writes_to_storage_is_alive():
    page = FakeControlPage([])
    page.controls = [FakeControl('Toggle dark mode', effect=alive(page, 'storage'))]

    findings, _coverage = check_dead_controls(page, 'https://site.com/page')

    assert findings == []


def test_a_control_that_navigates_is_alive_and_the_crawl_returns():
    """Navigating away mid-check must not derail the rest of the crawl."""
    page = FakeControlPage([])
    page.controls = [FakeControl('Next', effect=alive(page, 'url'))]

    findings, _coverage = check_dead_controls(page, 'https://site.com/page')

    assert findings == []
    assert page.goto_calls == ['https://site.com/page']


# ---- safety: what must never be clicked --------------------------------

def test_a_control_inside_a_form_is_never_clicked():
    """Clicking inside a form can submit it. Never worth the risk."""
    control = FakeControl('Go', in_form=True)
    page = FakeControlPage([control])

    findings, coverage = check_dead_controls(page, 'https://site.com/page')

    assert control.clicked is False
    assert findings == []
    assert coverage['skipped'] == 1


def test_destructive_labels_are_never_clicked():
    controls = [FakeControl(label) for label in
                ('Delete account', 'Remove item', 'Pay now', 'Log out',
                 'Submit order', 'Cancel subscription', 'Reset everything')]
    page = FakeControlPage(controls)

    findings, _coverage = check_dead_controls(page, 'https://site.com/page')

    assert all(c.clicked is False for c in controls)
    assert findings == []


def test_a_destructive_label_in_aria_label_is_also_respected():
    control = FakeControl('', attrs={'aria-label': 'Delete this row'})
    page = FakeControlPage([control])

    check_dead_controls(page, 'https://site.com/page')

    assert control.clicked is False


def test_a_disabled_control_is_never_clicked_or_reported():
    """A disabled button doing nothing is correct behaviour, not a defect."""
    control = FakeControl('Continue', disabled=True)
    page = FakeControlPage([control])

    findings, _coverage = check_dead_controls(page, 'https://site.com/page')

    assert control.clicked is False
    assert findings == []


def test_a_submit_button_is_never_clicked():
    control = FakeControl('Go', attrs={'type': 'submit'})
    page = FakeControlPage([control])

    check_dead_controls(page, 'https://site.com/page')

    assert control.clicked is False


def test_only_a_bounded_number_of_controls_are_ever_clicked():
    """One page must not be able to hold up the whole crawl."""
    controls = [FakeControl('Item %d' % i) for i in range(60)]
    page = FakeControlPage(controls)

    _findings, coverage = check_dead_controls(page, 'https://site.com/page', cap=15)

    assert sum(1 for c in controls if c.clicked) == 15
    assert coverage['truncated'] is True


def test_a_control_that_cannot_be_clicked_is_not_called_dead():
    """If the click itself failed we learned nothing, so we claim nothing."""
    class Unclickable(FakeControl):
        def click(self, **_kwargs):
            raise RuntimeError('element is not stable')

    page = FakeControlPage([Unclickable('Flaky')])

    findings, _coverage = check_dead_controls(page, 'https://site.com/page')

    assert findings == []
