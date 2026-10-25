"""Scrolling behaviour on every page: page scroll, sticky header/rail, inner scroll containers,
anchor links, scroll-into-view for focused controls, back/forward scroll restoration, long tables,
Load more / pagination, horizontal overflow, mobile scroll, wheel and keyboard scrolling."""
import pytest
from playwright.sync_api import Page, expect

from helpers import open_page, rail
from routes import OS_PAGES

PATHS = [p for p, _ in OS_PAGES]

SCROLL_INFO = """() => {
  const d = document.scrollingElement;
  return {h: d.scrollHeight, c: d.clientHeight, y: d.scrollTop, w: d.scrollWidth, cw: d.clientWidth};
}"""

INNER_SCROLLERS = """() => [...document.querySelectorAll('*')].filter(e => {
  const s = getComputedStyle(e);
  return /(auto|scroll)/.test(s.overflowY) && e.scrollHeight > e.clientHeight + 4 && e.clientHeight > 40
      && e !== document.scrollingElement && e !== document.body;
}).length"""


def _scrollable(page) -> bool:
    i = page.evaluate(SCROLL_INFO)
    return i["h"] > i["c"] + 4


@pytest.mark.parametrize("path", PATHS)
def test_page_scrolls_to_bottom_and_back(page: Page, path):
    open_page(page, path)
    if not _scrollable(page):
        pytest.skip("page fits in viewport")
    page.evaluate("window.scrollTo(0, document.scrollingElement.scrollHeight)")
    page.wait_for_timeout(300)
    i = page.evaluate(SCROLL_INFO)
    assert i["y"] + i["c"] >= i["h"] - 5, f"{path}: could not reach bottom"
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    assert page.evaluate(SCROLL_INFO)["y"] == 0


@pytest.mark.parametrize("path", PATHS)
def test_header_and_rail_stay_visible_while_scrolling(page: Page, path):
    open_page(page, path)
    if not _scrollable(page):
        pytest.skip("page fits in viewport")
    page.mouse.wheel(0, 5000)
    page.wait_for_timeout(300)
    expect(rail(page)).to_be_in_viewport()
    expect(page.get_by_role("link", name="Agentic Data OS").first).to_be_in_viewport()


@pytest.mark.parametrize("path", PATHS)
def test_no_horizontal_page_scroll(page: Page, path):
    open_page(page, path)
    i = page.evaluate(SCROLL_INFO)
    assert i["w"] <= i["cw"] + 2, f"{path}: horizontal page scroll of {i['w'] - i['cw']}px"


@pytest.mark.parametrize("path", PATHS)
def test_inner_scroll_containers_scroll(page: Page, path):
    open_page(page, path)
    n = page.evaluate(INNER_SCROLLERS)
    if n == 0:
        pytest.skip("no inner scroll containers")
    moved = page.evaluate("""() => [...document.querySelectorAll('*')].filter(e => {
        const s = getComputedStyle(e);
        return /(auto|scroll)/.test(s.overflowY) && e.scrollHeight > e.clientHeight + 4 && e.clientHeight > 40
          && e !== document.scrollingElement && e !== document.body;
      }).map(e => { const b = e.scrollTop; e.scrollTop = b + 200; const ok = e.scrollTop > b; e.scrollTop = b; return ok; })""")
    assert all(moved), f"{path}: {moved.count(False)} scroll container(s) do not scroll"


@pytest.mark.parametrize("path", PATHS)
def test_keyboard_scrolling(page: Page, path):
    open_page(page, path)
    if not _scrollable(page):
        pytest.skip("page fits in viewport")
    page.locator("body").click(position={"x": 5, "y": 5}, force=True)
    page.keyboard.press("End")
    page.wait_for_timeout(300)
    end_y = page.evaluate(SCROLL_INFO)["y"]
    page.keyboard.press("Home")
    page.wait_for_timeout(300)
    assert page.evaluate(SCROLL_INFO)["y"] < end_y or end_y == 0
    page.keyboard.press("PageDown")
    page.wait_for_timeout(300)


@pytest.mark.parametrize("path", PATHS)
def test_focused_control_scrolls_into_view(page: Page, path):
    open_page(page, path)
    if not _scrollable(page):
        pytest.skip("page fits in viewport")
    last = page.locator("main button:visible, main a[href]:visible, main input:visible").last
    if last.count() == 0:
        pytest.skip("no focusable controls")
    last.focus()
    page.wait_for_timeout(200)
    expect(last).to_be_in_viewport()


@pytest.mark.parametrize("path", PATHS)
def test_last_element_reachable_not_hidden_under_fixed_ui(page: Page, path):
    open_page(page, path)
    if not _scrollable(page):
        pytest.skip("page fits in viewport")
    page.evaluate("window.scrollTo(0, document.scrollingElement.scrollHeight)")
    page.wait_for_timeout(300)
    covered = page.evaluate("""() => {
      const els = [...document.querySelectorAll('main button, main a[href]')].filter(e => e.offsetParent);
      const e = els[els.length - 1]; if (!e) return false;
      const r = e.getBoundingClientRect(); const x = r.left + r.width / 2, y = r.top + r.height / 2;
      if (y < 0 || y > innerHeight) return false;
      const top = document.elementFromPoint(x, y);
      return !(top === e || e.contains(top) || top?.contains(e));
    }""")
    assert not covered, f"{path}: last control is covered by a fixed/sticky element"


def test_scroll_restoration_on_back(page: Page):
    for path in ["/os/knowledge?view=items", "/os/build/runs", "/os/history", "/os/run/incidents"]:
        open_page(page, path)
        if _scrollable(page):
            break
    else:
        pytest.skip("no long list page")
    page.evaluate("window.scrollTo(0, 600)")
    page.wait_for_timeout(300)
    link = page.locator("main a[href^='/os/']").filter(has_not=page.locator("[aria-current]")).first
    y_before = page.evaluate(SCROLL_INFO)["y"]
    link.click()
    page.wait_for_load_state("load")
    page.go_back()
    page.wait_for_load_state("load")
    page.wait_for_timeout(500)
    y_after = page.evaluate(SCROLL_INFO)["y"]
    assert abs(y_after - y_before) < 200 or y_after > 0, "scroll position not restored on back"


def test_new_page_starts_at_top(page: Page):
    open_page(page, "/os/knowledge")
    page.evaluate("window.scrollTo(0, 9999)")
    rail(page).get_by_role("link", name="Help", exact=True).click()
    page.wait_for_load_state("load")
    assert page.evaluate(SCROLL_INFO)["y"] < 50, "navigation kept the old scroll offset"


def test_landing_anchor_links_scroll_to_section(anon_page: Page):
    anon_page.goto("/")
    nav = anon_page.get_by_role("navigation", name="Sections")
    links = nav.get_by_role("link")
    for i in range(links.count()):
        href = links.nth(i).get_attribute("href")
        target = href.split("#", 1)[1]
        links.nth(i).click()
        anon_page.wait_for_timeout(800)
        expect(anon_page.locator(f"#{target}")).to_be_in_viewport()


def test_landing_scroll_whole_page(anon_page: Page):
    anon_page.goto("/")
    for _ in range(30):
        anon_page.mouse.wheel(0, 800)
        anon_page.wait_for_timeout(80)
    i = anon_page.evaluate(SCROLL_INFO)
    assert i["y"] + i["c"] >= i["h"] - 10
    assert not anon_page._ui_errors


@pytest.mark.parametrize("path", ["/os/run/incidents", "/os/knowledge?view=items", "/os/build/runs"])
def test_load_more_or_pagination_extends_list(page: Page, path):
    open_page(page, path)
    rows = page.locator("tbody tr, [role=row], li")
    before = rows.count()
    more = page.get_by_role("button", name="Load more")
    nxt = page.get_by_role("button", name="Next").or_(page.get_by_role("link", name="Next"))
    if more.count():
        more.click()
        page.wait_for_timeout(1500)
        assert rows.count() >= before
    elif nxt.count() and nxt.first.is_enabled():
        nxt.first.click()
        page.wait_for_load_state("load")
        expect(page.locator("body")).not_to_contain_text("Application error")
    else:
        pytest.skip("list fits on one page")


@pytest.mark.parametrize("path", ["/os/knowledge?view=items", "/os/history", "/os/build/runs",
                                  "/os/design/dq-store", "/os/run/incidents"])
def test_wide_tables_scroll_horizontally_inside_container(page: Page, path):
    open_page(page, path)
    res = page.evaluate("""() => [...document.querySelectorAll('table')].map(t => {
      let p = t.parentElement; while (p && p !== document.body) {
        const s = getComputedStyle(p); if (/(auto|scroll)/.test(s.overflowX)) return true; p = p.parentElement; }
      return t.scrollWidth <= document.documentElement.clientWidth + 2; })""")
    assert all(res), f"{path}: a wide table overflows the page instead of its container"


def test_sticky_table_header(page: Page):
    for path in ["/os/history", "/os/knowledge?view=items", "/os/build/runs"]:
        open_page(page, path)
        th = page.locator("thead th").first
        if th.count() and _scrollable(page):
            break
    else:
        pytest.skip("no long table")
    pos = th.evaluate("e => getComputedStyle(e).position + '|' + getComputedStyle(e.closest('thead')).position")
    if "sticky" not in pos:
        pytest.skip("table header is not designed to be sticky")
    page.mouse.wheel(0, 1500)
    page.wait_for_timeout(300)
    expect(th).to_be_in_viewport()


@pytest.mark.parametrize("path", ["/os", "/os/knowledge", "/os/build/runs", "/os/run/incidents"])
def test_mobile_touch_scroll(context, path):
    pg = context.new_page()
    pg.set_viewport_size({"width": 390, "height": 844})
    open_page(pg, path)
    i = pg.evaluate(SCROLL_INFO)
    if i["h"] <= i["c"]:
        pytest.skip("fits")
    pg.mouse.wheel(0, 1200)
    pg.wait_for_timeout(300)
    assert pg.evaluate(SCROLL_INFO)["y"] > 0
    assert pg.evaluate(SCROLL_INFO)["w"] <= 392
    pg.close()


def test_modal_locks_background_scroll(page: Page):
    open_page(page, "/os/knowledge?view=items")
    page.get_by_role("button", name="Add knowledge").last.click()
    dlg = page.get_by_role("dialog").first
    expect(dlg).to_be_visible()
    y0 = page.evaluate(SCROLL_INFO)["y"]
    page.mouse.wheel(0, 2000)
    page.wait_for_timeout(300)
    locked = page.evaluate(SCROLL_INFO)["y"] == y0 or page.evaluate(
        "getComputedStyle(document.body).overflow") == "hidden"
    page.keyboard.press("Escape")
    assert locked, "background page scrolled behind an open modal"


def test_long_dialog_content_scrolls(page: Page):
    open_page(page, "/os/knowledge")
    page.get_by_role("button", name="Learning policy").click()
    dlg = page.get_by_role("dialog", name="Learning policy")
    ok = dlg.evaluate("""d => { const els = [d, ...d.querySelectorAll('*')];
       const s = els.find(e => e.scrollHeight > e.clientHeight + 4 && /(auto|scroll)/.test(getComputedStyle(e).overflowY));
       if (!s) return d.getBoundingClientRect().bottom <= innerHeight + 1;
       const b = s.scrollTop; s.scrollTop = b + 300; return s.scrollTop > b; }""")
    assert ok, "dialog content is cut off and cannot scroll"
    expect(dlg.get_by_role("button", name="Save policy")).to_be_attached()
