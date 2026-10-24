"""Classic layout top bar: New menu, tickets shortcut, theme toggle, account menu, layout switch."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import open_page


@pytest.fixture
def classic(page: Page, base_url):
    page.context.add_cookies([{"name": "ados_classic", "value": "1", "url": base_url}])
    open_page(page, "/os/work/overview")
    if page.get_by_test_id("account-menu").count() == 0:
        pytest.skip("classic layout not available for this user")
    return page


def test_new_menu_items_are_links(classic: Page):
    classic.get_by_test_id("new-menu").click()
    menu = classic.get_by_role("menu")
    expect(menu).to_be_visible()
    items = menu.get_by_role("menuitem")
    assert items.count() > 0
    for i in range(items.count()):
        href = items.nth(i).get_attribute("href")
        assert href and href.startswith("/"), f"menu item {i} has no href"
    classic.keyboard.press("Escape")
    expect(menu).to_be_hidden()


def test_new_menu_each_item_navigates(classic: Page):
    classic.get_by_test_id("new-menu").click()
    hrefs = [classic.get_by_role("menuitem").nth(i).get_attribute("href")
             for i in range(classic.get_by_role("menuitem").count())]
    classic.keyboard.press("Escape")
    for h in hrefs:
        open_page(classic, h)


def test_tickets_shortcut(classic: Page):
    link = classic.get_by_test_id("tickets-shortcut")
    expect(link).to_have_attribute("aria-label", re.compile("^Tickets"))
    link.click()
    expect(classic).to_have_url(re.compile("/tickets"))


def test_theme_toggle_changes_theme(classic: Page):
    get = "() => document.documentElement.className + '|' + (document.documentElement.dataset.theme || '')"
    before = classic.evaluate(get)
    classic.get_by_test_id("theme-toggle").click()
    classic.wait_for_timeout(300)
    assert classic.evaluate(get) != before
    classic.get_by_test_id("theme-toggle").click()
    classic.wait_for_timeout(300)
    assert classic.evaluate(get) == before


def test_account_menu(classic: Page):
    classic.get_by_test_id("account-menu").click()
    expect(classic.get_by_text("Connected to Snowflake")).to_be_visible()
    expect(classic.get_by_text("Appearance")).to_be_visible()
    expect(classic.get_by_role("button", name="Sign out")).to_be_visible()
    classic.keyboard.press("Escape")


def test_account_sign_out(classic: Page):
    classic.get_by_test_id("account-menu").click()
    classic.get_by_role("button", name="Sign out").click()
    expect(classic).to_have_url(re.compile("/login"))


def test_classic_layout_switch(classic: Page):
    sw = classic.get_by_test_id("classic-layout-switch")
    expect(sw).to_have_attribute("role", "switch")
    before = sw.get_attribute("aria-checked")
    sw.click()
    classic.wait_for_load_state("load")
    assert classic.get_by_test_id("classic-layout-switch").count() == 0 or \
        classic.get_by_test_id("classic-layout-switch").get_attribute("aria-checked") != before
