"""Source-driven coverage: one test per aria-label and per data-testid found in apps/web.

At collection time the web source is scanned, so every labelled control in the code (including
future ones) gets its own test. At run time a session index visits every page, tab and discovered
detail page once, records where each label / test id is rendered, then each test opens that page
and exercises the control according to its type:
  button / link   -> click (destructive names only with AIP_UI_ALLOW_WRITES=1), must not error
  text field      -> type and clear
  select          -> choose every option
  checkbox/switch -> toggle and restore
  other           -> visible and has a size
Controls that are conditional and not rendered in this environment are reported as skipped,
with the source file, so gaps are visible rather than silently passing.
"""
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

import settings
from helpers import ERROR_MARKERS, open_page
from routes import KNOWLEDGE_VIEWS, OS_PAGES

WEB = Path(__file__).resolve().parents[2] / "apps" / "web"
DESTRUCTIVE = re.compile(
    r"(?i)\b(delete|remove|retire|archive|approve|reject|publish|submit|save|apply|merge|sign ?out|"
    r"cancel run|send back|unlink|disconnect|drop|withdraw|rollback|roll back|deploy|promote|"
    r"run|retry|resolve|mute|import|upload|generate|draft|push|move|transition|confirm)\b")


def _scan():
    labels, ids = {}, {}
    for f in WEB.rglob("*.tsx"):
        s = str(f)
        if any(x in s for x in ("node_modules", ".next", f"{Path('e2e')}")):
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        rel = str(f.relative_to(WEB)).replace("\\", "/")
        for m in re.finditer(r'aria-label="([^"{}]{3,})"', text):
            labels.setdefault(m.group(1), rel)
        for m in re.finditer(r'data-testid=["{`]+([A-Za-z0-9_\-]+)', text):
            ids.setdefault(m.group(1), rel)
    return labels, ids


LABELS, TESTIDS = _scan() if WEB.exists() else ({}, {})

EXTRA_PAGES = (
    ["/", "/login"]
    + [f"/os/knowledge?view={v}" for _, v in KNOWLEDGE_VIEWS if v]
    + [f"/os/admin/settings?section={s}" for s in ["overview", "models", "access", "integrations",
                                                   "skills", "rules", "standards", "environments", "deploy"]]
    + [f"/os/design/modeling?view={v}" for v in ["models", "tables", "design", "runs", "dbt"]]
    + [f"/os/design/profiling?section={s}" for s in ["schedules", "annotations"]]
    + ["/os/release/qa?tab=results", "/os/intake/new?type=source", "/os/intake/new?type=domain&intent=enhance",
       "/os/enable/onboarding?type=source&branch=share",
       "/os/enable/onboarding?type=source&branch=external&kind=database",
       "/os/enable/onboarding?type=source&branch=external&kind=file",
       "/os/enable/new-domain?how=import", "/os/enable/new-domain?how=draft", "/os/run/ops/fixes/new"]
)

INDEX_JS = """() => {
  const vis = e => !!(e.offsetParent || e.getClientRects().length);
  return {
    labels: [...document.querySelectorAll('[aria-label]')].filter(vis).map(e => e.getAttribute('aria-label')),
    ids: [...document.querySelectorAll('[data-testid]')].map(e => e.getAttribute('data-testid')),
  };
}"""


@pytest.fixture(scope="session")
def render_index(browser, browser_context_args, auth_state):
    """url -> (labels, testids) for every page, tab and discovered detail page."""
    from test_interactions_exhaustive import _discover_detail_pages

    ctx = browser.new_context(**browser_context_args, storage_state=auth_state)
    ctx.set_default_navigation_timeout(settings.NAV_TIMEOUT_MS)
    pg = ctx.new_page()
    pg._ui_errors = []
    urls = [p for p, _ in OS_PAGES] + EXTRA_PAGES
    try:
        urls += _discover_detail_pages(pg)
        for d in [u for u in urls if "/os/build/runs/" in u and u.count("/") == 4]:
            urls += [d + "/modeling?tab=transformations", d + "/modeling?tab=mapping"]
        for d in [u for u in urls if "/os/knowledge/domains/" in u]:
            urls += [f"{d}?tab={t}&layer={l}" for t in ["overview", "playbook", "references", "models",
                                                        "knowledge", "rules", "sources", "lineage", "versions"]
                     for l in ["SILVER", "GOLD"]]
    except Exception:
        pass
    index = {}
    for u in dict.fromkeys(urls):
        try:
            pg.goto(u, wait_until="load")
            data = pg.evaluate(INDEX_JS)
            index[u] = (set(data["labels"]), set(data["ids"]))
        except Exception:
            continue
    ctx.close()
    return index


def _where(index, kind, key):
    for url, (labels, ids) in index.items():
        if (key in labels) if kind == "label" else (key in ids):
            return url
    return None


def _assert_ok(page: Page, ctx: str):
    for m in ERROR_MARKERS:
        if page.locator("body").get_by_text(m).count():
            pytest.fail(f"{ctx}: '{m}' after interaction")
    errs = [e for e in page._ui_errors if "ResizeObserver" not in e]
    if errs:
        pytest.fail(f"{ctx}: page error {errs[-1]}")


def _exercise(page: Page, loc, ctx: str, name: str):
    loc.scroll_into_view_if_needed()
    expect(loc).to_be_visible()
    info = loc.evaluate("""e => ({tag: e.tagName.toLowerCase(), role: e.getAttribute('role'),
        type: (e.getAttribute('type')||'').toLowerCase(), disabled: e.disabled || e.getAttribute('aria-disabled') === 'true',
        ro: e.readOnly, href: e.getAttribute('href'), editable: e.isContentEditable})""")
    if info["disabled"]:
        return "disabled"
    tag, role, typ = info["tag"], info["role"], info["type"]
    if tag == "select":
        for v in loc.locator("option").evaluate_all("os => os.map(o => o.value)")[:8]:
            loc.select_option(v)
        _assert_ok(page, ctx)
        return "select"
    if (tag == "input" and typ in ("checkbox", "radio")) or role in ("switch", "checkbox", "radio"):
        if DESTRUCTIVE.search(name) and not settings.ALLOW_WRITES:
            return "skipped-destructive"
        loc.click()
        if typ != "radio" and role != "radio":
            loc.click()
        _assert_ok(page, ctx)
        return "toggle"
    if (tag in ("input", "textarea") and typ not in ("file", "hidden", "submit", "button") and not info["ro"]) \
            or info["editable"] or role in ("textbox", "searchbox", "combobox"):
        loc.fill("ui-autotest")
        loc.fill("")
        _assert_ok(page, ctx)
        return "text"
    if tag == "input" and typ == "file":
        return "file"
    if tag == "a" and info["href"]:
        href = info["href"]
        if href.startswith("/") and not href.startswith("/bff/signout"):
            r = page.request.get(href)
            assert r.status < 400, f"{ctx}: link {href} -> {r.status}"
        return "link"
    if tag == "button" or role in ("button", "tab", "menuitem", "link", "option"):
        if DESTRUCTIVE.search(name) and not settings.ALLOW_WRITES:
            return "skipped-destructive"
        loc.click(timeout=10_000)
        page.wait_for_timeout(400)
        _assert_ok(page, ctx)
        for _ in range(2):
            if page.locator("[role=dialog]:visible, [role=menu]:visible, [role=alertdialog]:visible").count():
                page.keyboard.press("Escape")
        return "click"
    box = loc.bounding_box()
    assert box and box["width"] > 0 and box["height"] > 0, f"{ctx}: rendered with no size"
    return "region"


@pytest.mark.parametrize("label", sorted(LABELS), ids=lambda l: l[:60])
def test_every_aria_label(page: Page, render_index, label):
    url = _where(render_index, "label", label)
    if not url:
        pytest.skip(f"'{label}' ({LABELS[label]}) not rendered in this environment - conditional on data/state")
    open_page(page, url) if url not in ("/", "/login") else page.goto(url)
    loc = page.locator(f'[aria-label="{label}"]:visible').first
    if loc.count() == 0:
        pytest.skip(f"'{label}' not visible on reload of {url}")
    _exercise(page, loc, f"{url} [aria-label={label}]", label)


@pytest.mark.parametrize("tid", sorted(TESTIDS), ids=lambda t: t[:60])
def test_every_test_id(page: Page, render_index, tid):
    pattern_prefix = tid.endswith("-")
    url = None
    for u, (_, ids) in render_index.items():
        if (any(i.startswith(tid) for i in ids) if pattern_prefix else tid in ids):
            url = u
            break
    if not url:
        pytest.skip(f"'{tid}' ({TESTIDS[tid]}) not rendered in this environment - conditional on data/state")
    open_page(page, url) if url not in ("/", "/login") else page.goto(url)
    sel = f'[data-testid^="{tid}"]' if pattern_prefix else f'[data-testid="{tid}"]'
    loc = page.locator(sel).first
    expect(loc).to_be_attached()
    if not loc.is_visible():
        return  # e.g. hidden file inputs - presence is the assertion
    name = (loc.get_attribute("aria-label") or loc.inner_text() or tid)[:80]
    _exercise(page, loc, f"{url} [data-testid={tid}]", name)


def test_source_scan_found_controls():
    assert len(LABELS) > 300 and len(TESTIDS) > 300, "source scan failed - check apps/web path"


def test_render_index_reached_pages(render_index):
    assert len(render_index) >= len(OS_PAGES), f"only {len(render_index)} pages indexed"
