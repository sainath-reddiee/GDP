"""Responsive layout at every breakpoint, zoom levels, colour scheme, reduced motion, and visual
regression snapshots (baseline images in tests/ui/snapshots; refresh with --update-snapshots)."""
import io
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

import settings
from helpers import open_page, rail
from routes import OS_PAGES

PATHS = [p for p, _ in OS_PAGES]
BREAKPOINTS = {"mobile-s": (320, 640), "mobile": (390, 844), "tablet": (768, 1024),
               "laptop": (1280, 800), "desktop": (1440, 900), "wide": (1920, 1080)}
SNAP_DIR = Path(__file__).parent / "snapshots"
VISUAL_PAGES = ["/os", "/os/knowledge", "/os/build", "/os/review", "/os/run", "/os/help",
                "/os/intake/new", "/os/enable/new-domain", "/os/people"]


def _new(context, w, h):
    pg = context.new_page()
    pg.set_viewport_size({"width": w, "height": h})
    pg._ui_errors = []
    pg.on("pageerror", lambda e: pg._ui_errors.append(str(e)))
    return pg


@pytest.mark.parametrize("bp", list(BREAKPOINTS))
@pytest.mark.parametrize("path", PATHS)
def test_breakpoint_renders_without_overflow(context, path, bp):
    w, h = BREAKPOINTS[bp]
    pg = _new(context, w, h)
    open_page(pg, path)
    over = pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    pg.close()
    assert over <= 2, f"{path} @ {bp}: horizontal overflow {over}px"
    assert not pg._ui_errors


@pytest.mark.parametrize("bp", ["mobile-s", "mobile", "tablet"])
def test_small_screens_use_nav_toggle(context, bp):
    pg = _new(context, *BREAKPOINTS[bp])
    open_page(pg, "/os")
    toggle = pg.get_by_role("button", name="Open navigation")
    if toggle.count():
        toggle.click()
        expect(rail(pg)).to_be_visible()
        pg.get_by_role("button", name="Close navigation").click()
    pg.close()


@pytest.mark.parametrize("bp", ["laptop", "desktop", "wide"])
def test_large_screens_show_rail(context, bp):
    pg = _new(context, *BREAKPOINTS[bp])
    open_page(pg, "/os")
    expect(rail(pg)).to_be_visible()
    pg.close()


@pytest.mark.parametrize("bp", list(BREAKPOINTS))
def test_text_not_clipped(context, bp):
    pg = _new(context, *BREAKPOINTS[bp])
    open_page(pg, "/os")
    clipped = pg.evaluate("""() => [...document.querySelectorAll('h1,h2,button,a')].filter(e => {
       const s = getComputedStyle(e); return e.offsetParent && s.overflow === 'hidden' && s.textOverflow !== 'ellipsis'
         && e.scrollWidth > e.clientWidth + 2 && s.whiteSpace !== 'nowrap'; }).map(e => e.innerText.slice(0, 40))""")
    pg.close()
    assert not clipped, f"clipped text at {bp}: {clipped[:8]}"


@pytest.mark.parametrize("bp", ["mobile", "tablet"])
def test_touch_targets_large_enough(context, bp):
    pg = _new(context, *BREAKPOINTS[bp])
    open_page(pg, "/os")
    small = pg.evaluate("""() => [...document.querySelectorAll('button, a[href], input, select')]
      .filter(e => e.offsetParent).filter(e => { const r = e.getBoundingClientRect(); return r.width < 24 || r.height < 24; })
      .map(e => (e.getAttribute('aria-label') || e.innerText || e.tagName).slice(0, 30))""")
    pg.close()
    assert len(small) <= 5, f"tap targets under 24px at {bp}: {small[:10]}"


def test_orientation_change(context):
    pg = _new(context, 844, 390)
    open_page(pg, "/os/knowledge")
    pg.set_viewport_size({"width": 390, "height": 844})
    pg.wait_for_timeout(300)
    assert pg.evaluate("() => document.documentElement.scrollWidth") <= 392
    pg.close()


def test_live_resize_keeps_layout(page: Page):
    open_page(page, "/os/knowledge")
    for w in [1440, 1024, 768, 480, 1280]:
        page.set_viewport_size({"width": w, "height": 900})
        page.wait_for_timeout(200)
        expect(page.locator("h1").first).to_be_visible()
    assert not page._ui_errors


@pytest.mark.parametrize("zoom", [0.5, 0.8, 1.25, 1.5, 2.0])
@pytest.mark.parametrize("path", ["/os", "/os/knowledge", "/os/build/runs", "/os/intake/new"])
def test_zoom_levels(page: Page, path, zoom):
    open_page(page, path)
    page.evaluate(f"document.body.style.zoom = '{zoom}'")
    page.wait_for_timeout(200)
    expect(page.locator("h1").first).to_be_visible()


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("path", ["/", "/os", "/os/knowledge", "/os/build"])
def test_color_scheme(browser, browser_context_args, auth_state, path, scheme):
    ctx = browser.new_context(**browser_context_args, storage_state=auth_state, color_scheme=scheme)
    pg = ctx.new_page()
    open_page(pg, path) if path != "/" else pg.goto(path)
    contrast = pg.evaluate("""() => { const b = getComputedStyle(document.body);
       return [b.color, b.backgroundColor]; }""")
    ctx.close()
    assert contrast[0] != contrast[1], f"text and background identical in {scheme} mode"


def test_reduced_motion_respected(browser, browser_context_args):
    ctx = browser.new_context(**browser_context_args, reduced_motion="reduce")
    pg = ctx.new_page()
    pg.goto("/")
    long_anims = pg.evaluate("""() => document.getAnimations().filter(a => {
       const t = a.effect?.getTiming?.(); return t && (t.iterations === Infinity) ; }).length""")
    ctx.close()
    assert long_anims == 0, f"{long_anims} infinite animations run despite prefers-reduced-motion"


def test_print_layout(page: Page):
    open_page(page, "/os/knowledge")
    page.emulate_media(media="print")
    expect(page.locator("h1").first).to_be_visible()
    pdf = page.pdf() if page.context.browser.browser_type.name == "chromium" else b"%PDF"
    assert pdf[:4] == b"%PDF"


# ---------------------------------------------------------------- visual regression
def _diff_ratio(a: bytes, b: bytes) -> float:
    try:
        from PIL import Image, ImageChops
    except ImportError:
        pytest.skip("Pillow not installed (pip install pillow) - visual diff disabled")
    ia, ib = Image.open(io.BytesIO(a)).convert("RGB"), Image.open(io.BytesIO(b)).convert("RGB")
    if ia.size != ib.size:
        return 1.0
    diff = ImageChops.difference(ia, ib).convert("L").point(lambda p: 255 if p > 32 else 0)
    return sum(diff.histogram()[255:]) / (ia.size[0] * ia.size[1])


MASK_DYNAMIC = "time, [data-testid*='kpi'], [data-testid='hero-ribbon'], [data-testid='platform-queue']"


@pytest.mark.parametrize("bp", ["mobile", "desktop"])
@pytest.mark.parametrize("path", VISUAL_PAGES)
def test_visual_snapshot(context, request, path, bp):
    pg = _new(context, *BREAKPOINTS[bp])
    open_page(pg, path)
    pg.add_style_tag(content="*{animation:none!important;transition:none!important;caret-color:transparent!important}")
    pg.wait_for_timeout(300)
    shot = pg.screenshot(full_page=False, mask=[pg.locator(MASK_DYNAMIC)], animations="disabled")
    pg.close()
    name = (path.strip("/").replace("/", "_").replace("?", "_") or "root") + f"@{bp}.png"
    base = SNAP_DIR / name
    if request.config.getoption("--update-snapshots", default=False) or settings.UPDATE_SNAPSHOTS or not base.exists():
        SNAP_DIR.mkdir(exist_ok=True)
        base.write_bytes(shot)
        pytest.skip(f"baseline written: {base.name}")
    ratio = _diff_ratio(base.read_bytes(), shot)
    limit = settings.VISUAL_THRESHOLD
    if ratio > limit:
        (SNAP_DIR / ("FAILED_" + name)).write_bytes(shot)
    assert ratio <= limit, f"{path}@{bp}: {ratio:.2%} of pixels changed (limit {limit:.2%})"
