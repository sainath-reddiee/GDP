"""Every route renders, every legacy URL redirects correctly."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import assert_healthy, goto, open_page, path_of
from routes import LEGACY_REDIRECTS, OS_PAGES


@pytest.mark.smoke
@pytest.mark.parametrize("path,h1", OS_PAGES, ids=[p for p, _ in OS_PAGES])
def test_page_renders(page: Page, path: str, h1: str | None):
    open_page(page, path, h1)


@pytest.mark.parametrize("path,h1", OS_PAGES, ids=[p for p, _ in OS_PAGES])
def test_page_has_single_h1_and_title(page: Page, path: str, h1):
    open_page(page, path)
    assert page.locator("h1").count() == 1, f"{path} should have exactly one h1"
    assert page.title().strip(), f"{path} has empty <title>"


@pytest.mark.parametrize("path,h1", OS_PAGES, ids=[p for p, _ in OS_PAGES])
def test_page_survives_reload(page: Page, path: str, h1):
    open_page(page, path)
    resp = page.reload(wait_until="domcontentloaded")
    assert_healthy(page, resp, h1)


@pytest.mark.parametrize("legacy,target", LEGACY_REDIRECTS, ids=[l for l, _ in LEGACY_REDIRECTS])
def test_legacy_redirect(page: Page, legacy: str, target: str):
    goto(page, legacy)
    assert path_of(page).startswith(target), f"{legacy} -> {page.url}, expected {target}"
    assert_healthy(page, None)


def test_knowledge_legacy_tab_param_maps_to_view(page: Page):
    goto(page, "/knowledge?tab=overview")
    expect(page).to_have_url(re.compile(r"/os/knowledge\?.*view=ask"))


def test_domains_redirect_to_knowledge_domains_view(page: Page):
    goto(page, "/domains")
    expect(page).to_have_url(re.compile(r"view=domains"))


def test_unknown_route_is_404(page: Page):
    resp = page.goto("/os/this-route-does-not-exist-xyz")
    assert resp is not None and resp.status == 404


def test_missing_request_shows_not_found(page: Page):
    goto(page, "/os/requests/00000000-0000-0000-0000-000000000000")
    expect(page.get_by_text("Request not found")).to_be_visible()


def test_no_horizontal_overflow_desktop(page: Page):
    for path, _ in OS_PAGES[:12]:
        open_page(page, path)
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 2, f"{path} overflows horizontally by {overflow}px"
