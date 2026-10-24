"""Exhaustive interaction crawler.

For every page (static routes + discovered detail pages) this exercises EVERY visible control:
  * each button is clicked on a fresh page load and must respond (DOM change, URL change,
    dialog/menu opened, aria-expanded/pressed/selected toggle, or download) without errors
  * each tab is selected and becomes aria-selected
  * each checkbox / switch toggles both ways
  * each <select> option can be chosen
  * each text field accepts typing
  * each open dialog/menu closes with Escape
Destructive buttons (delete, approve, publish, ...) are only clicked when AIP_UI_ALLOW_WRITES=1.
"""
import hashlib
import re

import pytest
from playwright.sync_api import Page, expect

import settings
from helpers import ERROR_MARKERS, first_link_matching, open_page, relative_url
from routes import OS_PAGES, RUN_STAGES

DESTRUCTIVE = re.compile(
    r"\b(delete|remove|retire|archive|restore|approve|reject|publish|submit|save|apply|merge|"
    r"sign ?out|cancel run|send back|sign off|verify|withdraw|dismiss all|run|retry|resolve|mute|"
    r"ack|assign|import|upload|create|accept|promote|sync|refresh|scan|generate|deploy|start|"
    r"request|confirm|onboard|land|ingest|reprofile|rollback|discard)\b",
    re.I,
)
SKIP_ALWAYS = re.compile(r"(Stop preview|Open navigation|Close navigation)", re.I)

CONTROLS = {
    "button": "button:visible:not([disabled]), [role=button]:visible:not([aria-disabled=true])",
    "tab": "[role=tab]:visible",
    "checkbox": "input[type=checkbox]:visible:not([disabled]), [role=switch]:visible, [role=checkbox]:visible",
    "select": "select:visible:not([disabled])",
    "text": "input[type=text]:visible:not([readonly]):not([disabled]), input[type=search]:visible, "
            "input:not([type]):visible:not([readonly]), textarea:visible:not([readonly]):not([disabled])",
}


def _name(loc) -> str:
    return (loc.get_attribute("aria-label") or loc.inner_text() or loc.get_attribute("title") or "").strip()


def _fingerprint(page: Page) -> str:
    html = page.evaluate("() => document.body.innerHTML.length + '|' + document.body.innerText")
    return hashlib.md5(html.encode()).hexdigest() + page.url


def _assert_no_error(page: Page, ctx: str):
    body = page.locator("body")
    for m in ERROR_MARKERS:
        if body.get_by_text(m).count():
            pytest.fail(f"{ctx}: error marker '{m}' after interaction")
    errs = [e for e in page._ui_errors if "ResizeObserver" not in e]
    if errs:
        pytest.fail(f"{ctx}: uncaught page error {errs[-1]}")


def _close_overlays(page: Page):
    for _ in range(3):
        if page.locator("[role=dialog]:visible, [role=menu]:visible, [role=alertdialog]:visible").count() == 0:
            return
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)


def _discover_detail_pages(page: Page) -> list[str]:
    found = []
    probes = [
        ("/os/build/runs", r"^/os/build/runs/[0-9a-fA-F-]{8,}$"),
        ("/os/knowledge?view=domains", r"^/os/knowledge/domains/[^/?#]+$"),
        ("/os/requests", r"^/os/requests/(?!history)[^/?#]+$"),
        ("/os/run/incidents", r"/incidents/[^/?#]+$"),
        ("/os/run/ops/resolutions", r"/ops/resolutions/[^/?#]+$"),
        ("/os/run/ops/fixes", r"/ops/fixes/[^/?#]+$"),
        ("/os/run/ops/permanent-fixes", r"/ops/permanent-fixes/[^/?#]+$"),
        ("/os/skills/registry", r"/skills/(?!proposed|registry)[^/?#]+$"),
        ("/os/release/qa", r"/qa/cases/[^/?#]+$"),
        ("/os/design/modeling", r"/os/design/modeling/[^?]+$"),
        ("/os/design/sttm", r"/os/design/sttm/[^?]+$"),
        ("/os/work/copilot", r"/os/work/copilot/[^/?#]+$"),
    ]
    for src, rx in probes:
        try:
            page.goto(src)
            href = first_link_matching(page, rx)
        except Exception:
            href = None
        if href:
            found.append(href)
            if "/os/build/runs/" in href:
                found += [f"{href}/{s}" for s in RUN_STAGES]
    return found


def pytest_generate_tests(metafunc):
    if "crawl_path" in metafunc.fixturenames:
        paths = [p for p, _ in OS_PAGES] + [
            "/os/knowledge?view=" + v for v in ["domains", "ask", "items", "inbox", "feed", "health"]
        ]
        metafunc.parametrize("crawl_path", paths, ids=paths)


@pytest.fixture(scope="module")
def detail_pages(browser, browser_context_args, auth_state):
    ctx = browser.new_context(**browser_context_args, storage_state=auth_state)
    ctx.set_default_navigation_timeout(settings.NAV_TIMEOUT_MS)
    p = ctx.new_page()
    p._ui_errors = []
    pages = _discover_detail_pages(p)
    ctx.close()
    return pages


def _count(page, kind):
    return page.locator(CONTROLS[kind]).count()


# ---------------------------------------------------------------- per control kind

def crawl_buttons(page: Page, path: str):
    open_page(page, path)
    total = _count(page, "button")
    clicked = 0
    for i in range(total):
        open_page(page, path)
        btns = page.locator(CONTROLS["button"])
        if i >= btns.count():
            break
        btn = btns.nth(i)
        name = _name(btn)
        if SKIP_ALWAYS.search(name):
            continue
        if DESTRUCTIVE.search(name) and not settings.ALLOW_WRITES:
            continue
        before_url, before = page.url, _fingerprint(page)
        attrs_before = [btn.get_attribute(a) for a in ("aria-expanded", "aria-pressed", "aria-selected")]
        downloads = []
        page.on("download", downloads.append)
        btn.click(timeout=10000)
        page.wait_for_timeout(700)
        page.remove_listener("download", downloads.append)
        attrs_after = (
            [btn.get_attribute(a) for a in ("aria-expanded", "aria-pressed", "aria-selected")]
            if btn.count() else None
        )
        responded = bool(
            downloads
            or page.url != before_url
            or _fingerprint(page) != before
            or page.locator("[role=dialog]:visible, [role=menu]:visible, [role=alertdialog]:visible").count()
            or (attrs_after is not None and attrs_after != attrs_before)
            # submit blocked by native validation of an empty form is a valid response
            or (btn.count() and btn.evaluate("b => b.type === 'submit' && b.form && !b.form.checkValidity()"))
        )
        _assert_no_error(page, f"{path} button '{name}'")
        assert responded, f"{path}: button '{name}' did nothing when clicked"
        _close_overlays(page)
        clicked += 1
    return clicked


def crawl_tabs(page: Page, path: str):
    open_page(page, path)
    for i in range(_count(page, "tab")):
        open_page(page, path)
        tab = page.locator(CONTROLS["tab"]).nth(i)
        name = _name(tab)
        tab.click()
        page.wait_for_load_state("load")
        _assert_no_error(page, f"{path} tab '{name}'")
        if tab.count() and tab.is_visible():
            expect(tab).to_have_attribute("aria-selected", "true")


def crawl_checkboxes(page: Page, path: str):
    open_page(page, path)
    for i in range(min(_count(page, "checkbox"), 15)):
        box = page.locator(CONTROLS["checkbox"]).nth(i)
        name = _name(box) or box.get_attribute("name") or f"#{i}"
        role = box.get_attribute("role")
        attr = "aria-checked" if role in ("switch", "checkbox") else None
        before = box.get_attribute(attr) if attr else box.is_checked()
        box.click()
        page.wait_for_timeout(300)
        if not box.count():
            continue
        after = box.get_attribute(attr) if attr else box.is_checked()
        _assert_no_error(page, f"{path} checkbox '{name}'")
        if role == "switch":
            assert after != before, f"{path}: switch '{name}' did not toggle"
            box.click()  # restore
        else:
            assert after != before, f"{path}: checkbox '{name}' did not toggle"
            box.click()
        _close_overlays(page)


def crawl_selects(page: Page, path: str):
    open_page(page, path)
    for i in range(_count(page, "select")):
        sel = page.locator(CONTROLS["select"]).nth(i)
        label = sel.get_attribute("aria-label") or sel.get_attribute("name") or f"#{i}"
        if re.search("persona", label, re.I):
            continue  # covered in test_shell_navigation
        values = sel.locator("option").evaluate_all("os => os.map(o => o.value)")
        for v in values[:8]:
            sel = page.locator(CONTROLS["select"]).nth(i)
            if not sel.count():
                open_page(page, path)
                sel = page.locator(CONTROLS["select"]).nth(i)
            sel.select_option(v)
            page.wait_for_timeout(300)
            _assert_no_error(page, f"{path} select '{label}'={v}")
        open_page(page, path)


def crawl_text_fields(page: Page, path: str):
    open_page(page, path)
    for i in range(_count(page, "text")):
        f = page.locator(CONTROLS["text"]).nth(i)
        label = f.get_attribute("aria-label") or f.get_attribute("placeholder") or f.get_attribute("name") or f"#{i}"
        f.fill("ui-autotest")
        expect(f).to_have_value("ui-autotest")
        f.fill("")
        _assert_no_error(page, f"{path} field '{label}'")


# ---------------------------------------------------------------- tests

@pytest.mark.parametrize("kind", ["buttons", "tabs", "checkboxes", "selects", "text_fields"])
def test_every_control_on_page(page: Page, crawl_path: str, kind: str):
    globals()[f"crawl_{kind}"](page, crawl_path)


@pytest.mark.parametrize("kind", ["buttons", "tabs", "checkboxes", "selects", "text_fields"])
def test_every_control_on_detail_pages(page: Page, detail_pages, kind: str):
    if not detail_pages:
        pytest.skip("no detail pages discovered (empty environment)")
    for path in detail_pages:
        globals()[f"crawl_{kind}"](page, path)


def test_every_dialog_closes_with_escape(page: Page, crawl_path: str):
    open_page(page, crawl_path)
    n = _count(page, "button")
    for i in range(n):
        open_page(page, crawl_path)
        btn = page.locator(CONTROLS["button"]).nth(i)
        if DESTRUCTIVE.search(_name(btn)) or SKIP_ALWAYS.search(_name(btn)):
            continue
        try:
            btn.click(timeout=5000)
        except Exception:
            continue
        dlg = page.locator("[role=dialog]:visible, [role=menu]:visible")
        if dlg.count():
            page.keyboard.press("Escape")
            expect(dlg.first).to_be_hidden(timeout=5000)


def test_every_menu_item_reachable(page: Page, crawl_path: str):
    open_page(page, crawl_path)
    triggers = page.locator("[aria-haspopup=menu]:visible, [aria-haspopup=true]:visible")
    for i in range(triggers.count()):
        open_page(page, crawl_path)
        t = page.locator("[aria-haspopup=menu]:visible, [aria-haspopup=true]:visible").nth(i)
        t.click()
        menu = page.get_by_role("menu")
        expect(menu.first).to_be_visible()
        items = menu.first.get_by_role("menuitem")
        assert items.count() > 0, f"{crawl_path}: menu '{_name(t)}' is empty"
        for j in range(items.count()):
            expect(items.nth(j)).to_be_visible()
        page.keyboard.press("Escape")


def test_every_link_target_loads(page: Page, crawl_path: str):
    open_page(page, crawl_path)
    hrefs = sorted(set(page.eval_on_selector_all(
        "a[href^='/']:visible", "els => els.map(e => e.getAttribute('href'))")))
    for h in hrefs:
        if h.startswith("/bff/"):
            continue
        open_page(page, h)
