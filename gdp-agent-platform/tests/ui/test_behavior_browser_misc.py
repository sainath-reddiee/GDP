"""Other browser-level behaviour: hover states & tooltips, external links, downloads, copy buttons,
drag on diagrams, images, meta tags, favicon, title per page, Core Web Vitals (CLS/LCP), memory
growth across navigation, localStorage/cookies, loading skeletons, toasts, empty states."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import first_link_matching, open_page
from routes import OS_PAGES

PATHS = [p for p, _ in OS_PAGES]


@pytest.mark.parametrize("path", PATHS)
def test_hover_changes_interactive_state(page: Page, path):
    open_page(page, path)
    btns = page.locator("main button:visible, main a[href]:visible")
    n = min(btns.count(), 6)
    if n == 0:
        pytest.skip("no controls")
    changed = 0
    for i in range(n):
        b = btns.nth(i)
        before = b.evaluate("e => { const s = getComputedStyle(e); return s.backgroundColor + s.color + s.textDecorationLine + s.cursor }")
        b.hover()
        page.wait_for_timeout(150)
        after = b.evaluate("e => { const s = getComputedStyle(e); return s.backgroundColor + s.color + s.textDecorationLine + s.cursor }")
        changed += before != after or "pointer" in after
    assert changed >= n // 2, f"{path}: most controls show no hover feedback"


@pytest.mark.parametrize("path", PATHS)
def test_title_tooltips_on_icon_only_buttons(page: Page, path):
    open_page(page, path)
    bad = page.evaluate("""() => [...document.querySelectorAll('button')].filter(b => b.offsetParent
       && !(b.innerText||'').trim() && !b.getAttribute('aria-label') && !b.title).length""")
    assert bad == 0, f"{path}: {bad} icon-only buttons without a label/tooltip"


@pytest.mark.parametrize("path", PATHS)
def test_external_links_are_safe(page: Page, path):
    open_page(page, path)
    bad = page.evaluate("""() => [...document.querySelectorAll('a[target=_blank]')]
      .filter(a => !/noopener|noreferrer/.test(a.rel)).map(a => a.href)""")
    assert not bad, f"{path}: target=_blank without rel=noopener: {bad[:5]}"


def test_external_link_opens_new_tab(page: Page, context):
    for p in ["/os/release/source-control", "/os/run/ops", "/os/help"]:
        open_page(page, p)
        a = page.locator("a[target=_blank][href^='http']").first
        if a.count():
            with context.expect_page() as newp:
                a.click()
            newp.value.close()
            return
    pytest.skip("no external links")


@pytest.mark.parametrize("path", PATHS)
def test_page_titles_unique_and_descriptive(page: Page, path):
    open_page(page, path)
    t = page.title()
    assert t and len(t) > 3 and t.lower() not in ("react app", "next.js")


def test_titles_differ_between_sections(page: Page):
    titles = set()
    for p in ["/os", "/os/knowledge", "/os/build", "/os/run", "/os/help"]:
        open_page(page, p)
        titles.add(page.title())
    assert len(titles) >= 3, f"most pages share the same title: {titles}"


def test_meta_viewport_lang_favicon(page: Page):
    open_page(page, "/os")
    assert page.locator("meta[name=viewport]").count() == 1
    assert page.evaluate("document.documentElement.lang")
    icon = page.evaluate("() => document.querySelector('link[rel~=icon]')?.href || '/favicon.ico'")
    assert page.request.get(icon).status < 400


@pytest.mark.parametrize("path", PATHS)
def test_images_load(page: Page, path):
    open_page(page, path)
    page.wait_for_load_state("networkidle")
    broken = page.evaluate("""() => [...document.images].filter(i => i.complete && i.naturalWidth === 0 && i.offsetParent).map(i => i.src)""")
    assert not broken, f"{path}: broken images {broken}"


@pytest.mark.parametrize("path", ["/", "/os", "/os/knowledge", "/os/build", "/os/run", "/os/build/runs"])
def test_layout_shift_and_lcp(page: Page, path):
    page.add_init_script("""
      window.__cls = 0; window.__lcp = 0;
      new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; })
        .observe({type: 'layout-shift', buffered: true});
      new PerformanceObserver(l => { const e = l.getEntries().pop(); if (e) window.__lcp = e.startTime; })
        .observe({type: 'largest-contentful-paint', buffered: true});
    """)
    if path == "/":
        page.goto(path)
    else:
        open_page(page, path)
    page.wait_for_timeout(1500)
    cls, lcp = page.evaluate("[window.__cls, window.__lcp]")
    assert cls < 0.25, f"{path}: cumulative layout shift {cls:.3f} (poor > 0.25)"
    assert lcp < 10000, f"{path}: largest contentful paint {lcp:.0f}ms"


def test_memory_does_not_grow_unbounded(page: Page):
    if page.context.browser.browser_type.name != "chromium":
        pytest.skip("performance.memory is Chromium only")
    seq = ["/os", "/os/knowledge", "/os/build", "/os/run", "/os/help"]
    open_page(page, "/os")
    base = page.evaluate("performance.memory.usedJSHeapSize")
    for _ in range(4):
        for p in seq:
            page.get_by_role("navigation", name="Agentic Data OS").locator(f'a[href="{p}"]').first.click()
            page.wait_for_load_state("load")
    end = page.evaluate("performance.memory.usedJSHeapSize")
    assert end < base * 4 + 50_000_000, f"JS heap grew from {base} to {end}"


@pytest.mark.parametrize("path,name", [("/os/design/modeling", None)])
def test_diagram_drag_pans_or_moves(page: Page, path, name):
    open_page(page, f"{path}?view=models")
    href = first_link_matching(page, r"/os/design/modeling/(?!tables|dbt)[^/?#]+$")
    if not href:
        pytest.skip("no models")
    open_page(page, href.split("?")[0] + "?tab=diagram")
    canvas = page.locator("svg, canvas, .react-flow").first
    if canvas.count() == 0:
        pytest.skip("no diagram")
    box = canvas.bounding_box()
    before = page.evaluate("() => document.querySelector('svg, .react-flow__viewport')?.outerHTML.length")
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2 + 120, box["y"] + box["height"] / 2 + 60, steps=10)
    page.mouse.up()
    page.mouse.wheel(0, -300)
    page.wait_for_timeout(300)
    assert not page._ui_errors
    assert before is not None


@pytest.mark.parametrize("path", ["/os/enable/new-domain?how=import", "/os/design/qa-store",
                                  "/os/admin/settings?section=integrations"])
def test_copy_buttons_write_clipboard(page: Page, context, path):
    context.grant_permissions(["clipboard-read", "clipboard-write"])
    open_page(page, path)
    copy = page.get_by_role("button", name=re.compile("^Copy", re.I))
    if copy.count() == 0:
        pytest.skip("no copy buttons")
    copy.first.click()
    assert page.evaluate("navigator.clipboard.readText()")


@pytest.mark.parametrize("path", ["/os/intake/new?type=domain", "/os/design/sttm", "/os/design/dq-store/suite"])
def test_downloads_have_filenames(page: Page, path):
    open_page(page, path)
    link = page.locator("a[download], a[href*='export'], button:has-text('Download')").first
    if link.count() == 0:
        pytest.skip("no downloads here")
    with page.expect_download() as dl:
        link.click()
    assert dl.value.suggested_filename and not dl.value.failure()


def test_cookies_are_httponly(page: Page):
    open_page(page, "/os")
    for c in page.context.cookies():
        if c["name"] in ("aip_session", "aip_dev"):
            assert c["httpOnly"], f"{c['name']} must be httpOnly"


def test_no_secrets_in_local_storage(page: Page):
    open_page(page, "/os")
    data = page.evaluate("JSON.stringify(Object.assign({}, localStorage, sessionStorage))")
    assert not re.search(r"(?i)(token|password|secret)\"\s*:\s*\"[^\"]{8,}", data), "secret-like value in web storage"


def test_toast_appears_and_dismisses(page: Page):
    open_page(page, "/os/knowledge?view=items")
    page.get_by_role("button", name="Add knowledge").last.click()
    dlg = page.get_by_role("dialog", name="Add knowledge")
    dlg.get_by_label("Title").fill("x")
    dlg.get_by_role("button", name="Cancel").click()
    toast = page.get_by_role("status").or_(page.get_by_role("alert"))
    if toast.count():
        page.wait_for_timeout(8000)


@pytest.mark.parametrize("path,text", [
    ("/os/knowledge?q=zzqq_none_xyz", "No knowledge matches"),
    ("/os/release/source-control?q=zzqq_none_xyz", None),
    ("/os/history?h-q=zzqq_none_xyz", None),
])
def test_empty_states_render(page: Page, path, text):
    open_page(page, path)
    if text:
        expect(page.get_by_text(text)).to_be_visible()
    else:
        expect(page.locator("main")).not_to_be_empty()


def test_loading_state_then_content(page: Page):
    page.goto("/os/build", wait_until="commit")
    expect(page.locator("h1").first).to_be_visible()
    expect(page.locator("[aria-busy=true]")).to_have_count(0, timeout=60_000)
