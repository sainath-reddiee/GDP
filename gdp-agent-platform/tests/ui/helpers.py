"""Reusable page helpers and assertions."""
import re
from urllib.parse import urlparse

from playwright.sync_api import Page, Response, expect

ERROR_MARKERS = [
    "Application error",
    "Unhandled Runtime Error",
    "Internal Server Error",
    "This page could not be found",
    "Something went wrong",
    "This page could not be loaded",
    "Cannot reach the API",
]


def path_of(page: Page) -> str:
    return urlparse(page.url).path


def relative_url(page: Page) -> str:
    u = urlparse(page.url)
    return u.path + (f"?{u.query}" if u.query else "")


def goto(page: Page, path: str) -> Response | None:
    resp = page.goto(path, wait_until="domcontentloaded")
    page.wait_for_load_state("load")
    return resp


def assert_healthy(page: Page, resp: Response | None, expected_h1: str | None = None):
    """Page loaded, is not an error page, not bounced to login, and has an h1."""
    if resp is not None:
        assert resp.status < 500, f"{page.url} returned HTTP {resp.status}"
        assert resp.status != 404, f"{page.url} returned 404"
    assert not path_of(page).startswith("/login"), f"bounced to login from {page.url}"
    assert "denied=" not in page.url, f"access denied redirect: {page.url}"
    body = page.locator("body")
    for marker in ERROR_MARKERS:
        expect(body).not_to_contain_text(marker)
    h1 = page.locator("h1").first
    expect(h1).to_be_visible()
    if expected_h1:
        expect(h1).to_contain_text(expected_h1)


def open_page(page: Page, path: str, expected_h1: str | None = None):
    resp = goto(page, path)
    assert_healthy(page, resp, expected_h1)
    return resp


def rail(page: Page):
    return page.get_by_role("navigation", name="Agentic Data OS")


def section_tabs(page: Page):
    return page.get_by_test_id("section-tabs")


def first_link_matching(page: Page, pattern: str) -> str | None:
    """Return href of the first anchor whose href matches regex pattern."""
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
    rx = re.compile(pattern)
    for h in hrefs:
        if h and rx.search(h):
            return h
    return None


def discover(page: Page, list_path: str, pattern: str) -> str:
    """Open a list page and return the first matching detail href, or skip the test."""
    import pytest

    open_page(page, list_path)
    href = first_link_matching(page, pattern)
    if not href:
        pytest.skip(f"no record matching {pattern} on {list_path}")
    return href


def unique(prefix: str = "UI-AUTOTEST") -> str:
    import time

    return f"{prefix} {int(time.time() * 1000) % 10**10}"


def status_text(page: Page, text):
    """Locator for a transient status / alert message."""
    return page.get_by_text(text).first
