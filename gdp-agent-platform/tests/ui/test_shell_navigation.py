"""App shell: header, left rail, section tabs, persona switcher, mobile nav."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import open_page, path_of, rail, section_tabs
from routes import RAIL, SECTION_TABS


@pytest.mark.smoke
def test_header_brand_links_home(page: Page):
    open_page(page, "/os/help")
    page.get_by_role("link", name=re.compile("Agentic Data OS")).first.click()
    expect(page).to_have_url(re.compile(r"/os/?$"))


@pytest.mark.parametrize("label,href", RAIL, ids=[l for l, _ in RAIL])
def test_rail_link_navigates(page: Page, label: str, href: str):
    open_page(page, "/os")
    link = rail(page).get_by_role("link", name=label, exact=True)
    expect(link).to_be_visible()
    link.click()
    page.wait_for_url(re.compile(re.escape(href) + r"(\?|$|/)"))
    open_page(page, path_of(page))
    expect(rail(page).get_by_role("link", name=label, exact=True)).to_have_attribute(
        "aria-current", "page"
    )


def test_rail_only_one_active_item(page: Page):
    open_page(page, "/os/knowledge")
    assert rail(page).locator('[aria-current="page"]').count() == 1


@pytest.mark.parametrize("section", list(SECTION_TABS), ids=list(SECTION_TABS))
def test_section_tabs_present(page: Page, section: str):
    open_page(page, section)
    tabs = section_tabs(page)
    expect(tabs).to_be_visible()
    for label, _ in SECTION_TABS[section]:
        expect(tabs.get_by_role("link", name=label, exact=True)).to_be_visible()


@pytest.mark.parametrize(
    "section,label,href",
    [(s, l, h) for s, tabs in SECTION_TABS.items() for l, h in tabs],
    ids=[f"{s}:{l}" for s, tabs in SECTION_TABS.items() for l, _ in tabs],
)
def test_section_tab_navigates(page: Page, section: str, label: str, href: str):
    open_page(page, section)
    section_tabs(page).get_by_role("link", name=label, exact=True).click()
    page.wait_for_url(re.compile(re.escape(href)))
    open_page(page, path_of(page))


def test_home_has_no_section_tabs(page: Page):
    open_page(page, "/os")
    expect(section_tabs(page)).to_have_count(0)


def test_persona_switcher(page: Page):
    open_page(page, "/os")
    select = page.get_by_label("View as persona", exact=True)
    if select.count() == 0:
        expect(page.get_by_label("Persona")).to_be_visible()
        pytest.skip("single persona user")
    options = select.locator("option")
    if options.count() < 2:
        pytest.skip("only one persona available")
    value = options.nth(1).get_attribute("value")
    select.select_option(value)
    page.wait_for_load_state("load")
    open_page(page, path_of(page))
    banner = page.get_by_role("status").filter(has_text="Previewing as")
    if banner.count():
        banner.get_by_role("button", name="Stop preview").click()
        expect(banner).to_be_hidden()


def test_copilot_open_full(page: Page):
    open_page(page, "/os")
    full = page.get_by_test_id("copilot-open-full")
    if not full.is_visible():
        page.get_by_role("button", name=re.compile("Copilot", re.I)).first.click()
    expect(full).to_be_visible()
    full.click()
    expect(page).to_have_url(re.compile(r"/os/work/copilot"))


def test_mobile_navigation_toggle(context):
    page = context.new_page()
    page.set_viewport_size({"width": 390, "height": 844})
    open_page(page, "/os")
    page.get_by_role("button", name="Open navigation").click()
    nav = rail(page)
    expect(nav).to_be_visible()
    nav.get_by_role("link", name="Knowledge", exact=True).click()
    expect(page).to_have_url(re.compile(r"/os/knowledge"))
    page.close()


@pytest.mark.parametrize("path", ["/os", "/os/knowledge", "/os/build", "/os/run"])
def test_mobile_pages_render(context, path):
    page = context.new_page()
    page.set_viewport_size({"width": 390, "height": 844})
    open_page(page, path)
    page.close()


def test_keyboard_tab_reaches_rail(page: Page):
    open_page(page, "/os")
    for _ in range(25):
        page.keyboard.press("Tab")
        inside = page.evaluate(
            "() => !!document.activeElement?.closest('nav[aria-label=\"Agentic Data OS\"]')"
        )
        if inside:
            return
    pytest.fail("rail not reachable via keyboard within 25 tabs")
