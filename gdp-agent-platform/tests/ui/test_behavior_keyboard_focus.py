"""Keyboard & focus behaviour: tab order, visible focus ring, skip link, Enter/Space activation,
dialog focus trap and focus return, Escape, arrow keys in tablists/menus, no keyboard traps,
form submit with Enter, autofocus in dialogs."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import open_page
from routes import OS_PAGES

PATHS = [p for p, _ in OS_PAGES]

ACTIVE = "() => { const e = document.activeElement; return e ? (e.tagName + '#' + (e.id||'') + '|' + (e.getAttribute('aria-label')||e.innerText||'').slice(0,40)) : null }"


@pytest.mark.parametrize("path", PATHS)
def test_tab_moves_focus_forward_without_trap(page: Page, path):
    open_page(page, path)
    seen = []
    for _ in range(40):
        page.keyboard.press("Tab")
        seen.append(page.evaluate(ACTIVE))
    distinct = len(set(seen))
    assert distinct >= min(10, len(seen) // 2), f"{path}: focus stuck (only {distinct} distinct stops)"


@pytest.mark.parametrize("path", PATHS)
def test_shift_tab_moves_backwards(page: Page, path):
    open_page(page, path)
    for _ in range(6):
        page.keyboard.press("Tab")
    fwd = page.evaluate(ACTIVE)
    page.keyboard.press("Shift+Tab")
    assert page.evaluate(ACTIVE) != fwd


@pytest.mark.parametrize("path", PATHS)
def test_focus_is_visible(page: Page, path):
    open_page(page, path)
    invisible = []
    for _ in range(15):
        page.keyboard.press("Tab")
        ok = page.evaluate("""() => { const e = document.activeElement; if (!e || e === document.body) return true;
          const s = getComputedStyle(e); const r = e.getBoundingClientRect();
          const ring = (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0) || s.boxShadow !== 'none'
             || /focus/.test(e.className?.toString?.() || '');
          return r.width > 0 && r.height > 0 && ring; }""")
        if not ok:
            invisible.append(page.evaluate(ACTIVE))
    assert len(invisible) <= 2, f"{path}: focus not visible on {invisible}"


@pytest.mark.parametrize("path", PATHS)
def test_focusable_elements_have_names(page: Page, path):
    open_page(page, path)
    bad = page.evaluate("""() => [...document.querySelectorAll('a[href], button, [tabindex]:not([tabindex="-1"])')]
      .filter(e => e.offsetParent && !(e.getAttribute('aria-label') || e.getAttribute('aria-labelledby') || e.title
               || (e.innerText||'').trim() || e.querySelector('img[alt]:not([alt=""]), svg title')))
      .map(e => e.outerHTML.slice(0, 100))""")
    assert not bad, f"{path}: unnamed focusable elements:\n" + "\n".join(bad[:10])


@pytest.mark.parametrize("path", PATHS)
def test_no_positive_tabindex(page: Page, path):
    open_page(page, path)
    n = page.locator("[tabindex]:not([tabindex='0']):not([tabindex='-1'])").count()
    assert n == 0, f"{path}: {n} elements with positive tabindex break tab order"


def test_enter_activates_rail_link(page: Page):
    open_page(page, "/os")
    link = page.get_by_role("navigation", name="Agentic Data OS").get_by_role("link", name="Knowledge", exact=True)
    link.focus()
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile("/os/knowledge"))


def test_space_activates_button(page: Page):
    open_page(page, "/os/knowledge")
    b = page.get_by_role("button", name="Learning policy")
    b.focus()
    page.keyboard.press("Space")
    expect(page.get_by_role("dialog", name="Learning policy")).to_be_visible()


@pytest.mark.parametrize("opener,dialog", [
    (("/os/knowledge", "Learning policy"), "Learning policy"),
    (("/os/knowledge?view=items", "Add knowledge"), "Add knowledge"),
])
def test_dialog_focus_trap_and_return(page: Page, opener, dialog):
    path, btn_name = opener
    open_page(page, path)
    btn = page.get_by_role("button", name=btn_name).last
    btn.focus()
    page.keyboard.press("Enter")
    dlg = page.get_by_role("dialog", name=dialog)
    expect(dlg).to_be_visible()
    inside = []
    for _ in range(30):
        page.keyboard.press("Tab")
        inside.append(page.evaluate("d => d.contains(document.activeElement)", dlg.element_handle()))
    assert all(inside), "focus escaped the open dialog"
    page.keyboard.press("Escape")
    expect(dlg).to_be_hidden()
    returned = page.evaluate("b => document.activeElement === b", btn.element_handle())
    assert returned, "focus did not return to the button that opened the dialog"


def test_dialog_receives_initial_focus(page: Page):
    open_page(page, "/os/knowledge?view=items")
    page.get_by_role("button", name="Add knowledge").last.click()
    dlg = page.get_by_role("dialog", name="Add knowledge")
    expect(dlg).to_be_visible()
    assert page.evaluate("d => d.contains(document.activeElement)", dlg.element_handle())


def test_sign_in_dialog_keyboard(anon_page: Page):
    anon_page.goto("/")
    anon_page.get_by_test_id("open-sign-in").focus()
    anon_page.keyboard.press("Enter")
    dlg = anon_page.get_by_test_id("sign-in-dialog")
    expect(dlg).to_be_visible()
    for _ in range(10):
        anon_page.keyboard.press("Tab")
        assert anon_page.evaluate("d => d.contains(document.activeElement)", dlg.element_handle())
    anon_page.keyboard.press("Escape")
    expect(dlg).to_be_hidden()


def test_tablist_arrow_keys(page: Page):
    open_page(page, "/os/knowledge")
    tabs = page.get_by_role("tablist", name="Knowledge views").get_by_role("tab")
    tabs.first.focus()
    page.keyboard.press("ArrowRight")
    focused = page.evaluate("() => document.activeElement.getAttribute('role')")
    if focused != "tab":
        pytest.skip("tabs are links without roving focus")
    assert page.evaluate("() => document.activeElement.textContent") != tabs.first.inner_text()


def test_menu_keyboard_navigation(page: Page):
    open_page(page, "/os/build/runs")
    from helpers import first_link_matching
    href = first_link_matching(page, r"/os/build/runs/[0-9a-fA-F-]{8,}")
    if not href:
        pytest.skip("no runs")
    open_page(page, re.search(r"/os/build/runs/[^/?#]+", href).group(0))
    page.get_by_test_id("run-menu").focus()
    page.keyboard.press("Enter")
    menu = page.get_by_role("menu")
    expect(menu).to_be_visible()
    page.keyboard.press("ArrowDown")
    assert page.evaluate("() => document.activeElement.getAttribute('role')") in ("menuitem", None)
    page.keyboard.press("Escape")
    expect(menu).to_be_hidden()


def test_enter_submits_search_form(page: Page):
    open_page(page, "/os/knowledge")
    box = page.get_by_placeholder(re.compile("Search memory"))
    box.fill("customer")
    box.press("Enter")
    expect(page).to_have_url(re.compile("q=customer"))


def test_escape_closes_menus_everywhere(page: Page):
    for path in ["/os", "/os/knowledge", "/os/run/tickets"]:
        open_page(page, path)
        trig = page.locator("[aria-haspopup]:visible").first
        if trig.count() == 0:
            continue
        trig.click()
        page.keyboard.press("Escape")
        expect(page.locator("[role=menu]:visible, [role=listbox]:visible")).to_have_count(0)


@pytest.mark.parametrize("path", ["/os/intake/new?type=domain", "/os/run/ops/fixes/new",
                                  "/os/enable/onboarding?type=source&branch=external&kind=database"])
def test_form_labels_click_focuses_field(page: Page, path):
    open_page(page, path)
    labels = page.locator("label[for]:visible")
    for i in range(min(labels.count(), 10)):
        lab = labels.nth(i)
        target = lab.get_attribute("for")
        field = page.locator(f"#{target}")
        if field.count() == 0 or not field.is_visible() or field.get_attribute("type") in ("checkbox", "radio", "file"):
            continue
        lab.click()
        assert page.evaluate(f"() => document.activeElement?.id === {target!r}"), f"label for #{target} does not focus it"
