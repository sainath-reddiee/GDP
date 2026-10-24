"""Public landing page, sign-in dialog, login form and route protection."""
import re

import pytest
from playwright.sync_api import Page, expect

import settings
from helpers import path_of
from routes import LANDING_SECTIONS, OS_PAGES


@pytest.mark.smoke
def test_landing_renders_for_anonymous(anon_page: Page):
    resp = anon_page.goto("/")
    assert resp and resp.status < 400
    expect(anon_page.get_by_test_id("open-sign-in")).to_be_visible()
    expect(anon_page.get_by_role("button", name="Sign in with Snowflake").first).to_be_visible()


def test_landing_section_nav_links(anon_page: Page):
    anon_page.goto("/")
    nav = anon_page.get_by_role("navigation", name="Sections")
    for label in LANDING_SECTIONS:
        link = nav.get_by_role("link", name=label, exact=True)
        expect(link).to_be_visible()
        href = link.get_attribute("href") or ""
        assert href.startswith("#") or "#" in href, f"{label} should be an in-page anchor"


def test_landing_section_anchor_scrolls(anon_page: Page):
    anon_page.goto("/")
    nav = anon_page.get_by_role("navigation", name="Sections")
    nav.get_by_role("link", name="Features", exact=True).click()
    expect(anon_page).to_have_url(re.compile(r"#"))


def test_landing_feature_tabs_switch(anon_page: Page):
    anon_page.goto("/")
    tabs = anon_page.get_by_role("tablist", name="Core engine features").get_by_role("tab")
    count = tabs.count()
    assert count >= 2, "expected multiple feature tabs"
    for i in range(count):
        tabs.nth(i).click()
        expect(tabs.nth(i)).to_have_attribute("aria-selected", "true")


def test_landing_run_preview_controls(anon_page: Page):
    anon_page.goto("/")
    preview = anon_page.get_by_test_id("run-preview")
    preview.scroll_into_view_if_needed()
    expect(preview).to_be_visible()
    toggle = preview.get_by_role("button", name=re.compile(r"^(Play|Pause)"))
    before = toggle.inner_text()
    toggle.click()
    expect(preview.get_by_role("button", name=re.compile(r"^(Play|Pause)"))).not_to_have_text(before)
    preview.get_by_role("button", name=re.compile("Restart")).click()


def test_sign_in_dialog_open_and_close(anon_page: Page):
    anon_page.goto("/")
    anon_page.get_by_test_id("open-sign-in").click()
    dialog = anon_page.get_by_test_id("sign-in-dialog")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("heading", name="Sign in")).to_be_visible()
    anon_page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    anon_page.get_by_test_id("open-sign-in").click()
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Close", exact=True).click()
    expect(dialog).to_be_hidden()


def test_login_page_opens_dialog(anon_page: Page):
    anon_page.goto("/login")
    expect(anon_page).to_have_title(re.compile("Sign in"))
    expect(anon_page.get_by_test_id("sign-in-dialog")).to_be_visible()


def test_login_expired_message(anon_page: Page):
    anon_page.goto("/login?expired=1")
    expect(anon_page.get_by_text("Your session ended. Sign in again to continue.")).to_be_visible()


@pytest.mark.parametrize("path", ["/os", "/os/knowledge", "/os/build", "/os/run/ops", "/runs"])
def test_protected_routes_redirect_to_login(anon_page: Page, path: str):
    anon_page.goto(path)
    expect(anon_page).to_have_url(re.compile(r"/login"))


def test_all_os_routes_protected(anon_page: Page):
    for path, _ in OS_PAGES:
        anon_page.goto(path)
        assert path_of(anon_page).startswith("/login"), f"{path} is reachable without auth"


def test_version_endpoint_is_public(anon_page: Page):
    resp = anon_page.request.get("/bff/version")
    assert resp.status < 400


@pytest.mark.smoke
def test_dev_sso_login_flow(anon_page: Page):
    if settings.AUTH_MODE != "dev":
        pytest.skip("dev auth only")
    anon_page.goto("/login")
    anon_page.get_by_role("button", name="Continue with Snowflake SSO").click()
    anon_page.wait_for_url(re.compile(r"/os"))
    expect(anon_page.locator("h1").first).to_contain_text("Platform actions")


@pytest.mark.pat_only
def test_pat_login_token_visibility_toggle(anon_page: Page):
    anon_page.goto("/login")
    token = anon_page.get_by_label("Programmatic access token")
    token.fill("secret")
    expect(token).to_have_attribute("type", "password")
    anon_page.get_by_role("button", name="Show token").click()
    expect(token).to_have_attribute("type", "text")
    anon_page.get_by_role("button", name="Hide token").click()
    expect(token).to_have_attribute("type", "password")


@pytest.mark.pat_only
def test_pat_login_invalid_token_shows_error(anon_page: Page):
    anon_page.goto("/login")
    anon_page.get_by_label("Snowflake user").fill("NOT_A_USER")
    anon_page.get_by_label("Programmatic access token").fill("invalid-token")
    anon_page.get_by_role("button", name="Sign in", exact=True).click()
    expect(anon_page.get_by_role("alert")).to_be_visible()
    assert path_of(anon_page).startswith("/login")


@pytest.mark.pat_only
def test_pat_login_success(anon_page: Page):
    anon_page.goto("/login")
    anon_page.get_by_label("Snowflake user").fill(settings.E2E_USER)
    anon_page.get_by_label("Programmatic access token").fill(settings.E2E_TOKEN)
    anon_page.get_by_role("button", name="Sign in", exact=True).click()
    anon_page.wait_for_url(re.compile(r"/os"))


def test_signed_in_user_landing_redirects_to_os(page: Page):
    page.goto("/")
    expect(page).to_have_url(re.compile(r"/os"))


def test_signout_clears_session(page: Page):
    page.goto("/os")
    page.goto("/bff/signout")
    expect(page).to_have_url(re.compile(r"/login|/$"))
    page.goto("/os")
    expect(page).to_have_url(re.compile(r"/login"))
