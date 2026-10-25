"""Resilience: API/BFF failures and slow responses (route interception), offline mode, browser
back/forward/reload, session expiry mid-use, double-submit protection, hostile/long input,
deep links with bad params, concurrent tabs."""
import re

import pytest
from playwright.sync_api import Page, expect

import settings
from helpers import ERROR_MARKERS, goto, open_page, path_of
from routes import OS_PAGES

XSS = "<img src=x onerror=window.__xss=1><script>window.__xss=1</script>\"'`;--"
LONG = "A" * 5000
UNICODE = "名前 Ñandú 🚀 مرحبا \u202e reversed"


# ---------------------------------------------------------------- network failures
@pytest.mark.parametrize("status", [500, 502, 401, 403])
def test_bff_error_responses_handled(page: Page, status):
    page.route(re.compile(r"/bff/(nav|agent|copilot).*"), lambda r: r.fulfill(status=status, body="{}"))
    open_page(page, "/os")
    page.get_by_role("button", name=re.compile("Copilot", re.I)).first.click()
    page.wait_for_timeout(800)
    assert not page._ui_errors, f"uncaught error on BFF {status}"


def test_bff_aborted_requests_handled(page: Page):
    page.route(re.compile(r"/bff/.*"), lambda r: r.abort())
    open_page(page, "/os/knowledge")
    page.wait_for_timeout(500)
    page._ui_errors.clear()  # aborted fetches may log; only crashes matter below
    page.get_by_role("button", name="Learning policy").click()
    expect(page.locator("body")).not_to_contain_text("Application error")


def test_slow_network_shows_pending_state(page: Page):
    def slow(route):
        page.wait_for_timeout(3000)
        route.continue_()
    page.route(re.compile(r"/os/knowledge.*"), slow)
    page.goto("/os/knowledge", wait_until="commit")
    expect(page.locator("h1").first).to_be_visible(timeout=settings.NAV_TIMEOUT_MS)


@pytest.mark.parametrize("path", ["/os/knowledge?view=ask", "/os/build/lineage"])
def test_server_action_failure_shows_message(page: Page, path):
    open_page(page, path)
    page.route("**/*", lambda r: r.fulfill(status=500, body="boom")
               if r.request.method == "POST" else r.continue_())
    box = page.get_by_label("Search or ask").or_(page.get_by_label("Find a table")).first
    box.fill("customer")
    page.get_by_role("button", name=re.compile("^(Search|Trace)$")).first.click()
    page.wait_for_timeout(1500)
    expect(page.locator("h1").first).to_be_visible()


def test_offline_then_online(page: Page, context):
    open_page(page, "/os/knowledge")
    context.set_offline(True)
    try:
        page.get_by_role("navigation", name="Agentic Data OS").get_by_role("link", name="Help", exact=True).click()
        page.wait_for_timeout(1500)
    finally:
        context.set_offline(False)
    open_page(page, "/os/help")


def test_static_assets_load(page: Page):
    failed = []
    page.on("response", lambda r: failed.append(f"{r.status} {r.url}")
            if r.status >= 400 and re.search(r"\.(js|css|woff2?|svg|png|ico)(\?|$)", r.url) else None)
    open_page(page, "/os")
    page.wait_for_load_state("networkidle")
    assert not failed, "failed assets:\n" + "\n".join(failed)


# ---------------------------------------------------------------- history / reload
def test_back_forward_through_sections(page: Page):
    seq = ["/os", "/os/knowledge", "/os/build", "/os/run"]
    for p in seq:
        open_page(page, p)
    for p in reversed(seq[:-1]):
        page.go_back()
        page.wait_for_load_state("load")
        assert path_of(page) == p
    page.go_forward()
    page.wait_for_load_state("load")
    assert path_of(page) == seq[1]


def test_back_after_tab_change_restores_tab(page: Page):
    open_page(page, "/os/knowledge")
    page.get_by_role("tablist", name="Knowledge views").get_by_role("tab", name="All items").click()
    page.wait_for_url(re.compile("view=items"))
    page.get_by_role("tablist", name="Knowledge views").get_by_role("tab", name="Review inbox").click()
    page.wait_for_url(re.compile("view=inbox"))
    page.go_back()
    expect(page).to_have_url(re.compile("view=items"))


@pytest.mark.parametrize("path", ["/os/knowledge?view=items", "/os/history?h-status=failed", "/os/build/runs"])
def test_reload_keeps_query_state(page: Page, path):
    open_page(page, path)
    before = page.url
    page.reload()
    assert page.url == before


def test_reload_with_unsaved_form_does_not_crash(page: Page):
    open_page(page, "/os/intake/new?type=domain")
    page.locator("#rq-title").fill("unsaved")
    page.once("dialog", lambda d: d.accept())
    page.reload()
    expect(page.locator("h1").first).to_be_visible()


# ---------------------------------------------------------------- session
def test_session_expiry_redirects_to_login(page: Page, context):
    open_page(page, "/os/knowledge")
    context.clear_cookies()
    page.goto("/os/build")
    expect(page).to_have_url(re.compile(r"/login"))


def test_session_expiry_during_client_action(page: Page, context):
    open_page(page, "/os/knowledge?view=ask")
    context.clear_cookies()
    page.get_by_label("Search or ask").fill("customer")
    page.get_by_role("button", name="Search", exact=True).click()
    page.wait_for_timeout(2000)
    assert "/login" in page.url or page.locator("h1").first.is_visible()


def test_two_tabs_same_session(context):
    a, b = context.new_page(), context.new_page()
    open_page(a, "/os/knowledge")
    open_page(b, "/os/build")
    a.reload()
    expect(a.locator("h1").first).to_contain_text("Knowledge hub")
    a.close(); b.close()


def test_signout_in_one_tab_affects_other(context):
    a, b = context.new_page(), context.new_page()
    open_page(a, "/os")
    open_page(b, "/os/knowledge")
    a.goto("/bff/signout")
    b.goto("/os/build")
    expect(b).to_have_url(re.compile("/login"))
    a.close(); b.close()


# ---------------------------------------------------------------- inputs
FIELDS = [
    ("/os/knowledge", "placeholder", "Search memory"),
    ("/os/knowledge?view=items", "label", "Filter knowledge"),
    ("/os/knowledge?view=ask", "label", "Search or ask"),
    ("/os/build/lineage", "label", "Find a table"),
    ("/os/run/incidents", "label", "Search incidents"),
    ("/os/run/tickets", "label", "Search tickets"),
    ("/os/build/runs", "label", "Search runs"),
    ("/os/intake/new?type=domain", "css", "#rq-title"),
]


def _field(page, kind, sel):
    if kind == "label":
        return page.get_by_label(sel).first
    if kind == "placeholder":
        return page.get_by_placeholder(re.compile(sel)).first
    return page.locator(sel)


@pytest.mark.parametrize("value", [XSS, LONG, UNICODE, "   ", "%00%27%22", "'; DROP TABLE x; --"],
                         ids=["xss", "long", "unicode", "spaces", "encoded", "sqli"])
@pytest.mark.parametrize("path,kind,sel", FIELDS, ids=[f[0] for f in FIELDS])
def test_hostile_input_is_safe(page: Page, path, kind, sel, value):
    open_page(page, path)
    f = _field(page, kind, sel)
    if f.count() == 0:
        pytest.skip("field not present")
    f.fill(value)
    f.press("Enter")
    page.wait_for_timeout(1000)
    assert not page.evaluate("() => window.__xss === 1"), "XSS payload executed"
    body = page.locator("body")
    for m in ERROR_MARKERS:
        expect(body).not_to_contain_text(m)


@pytest.mark.parametrize("q", ["view=nope", "id=../../etc/passwd", "q=%3Cscript%3E", "family=<x>",
                               "offset=-1&limit=999999", "domain=%00"])
def test_bad_query_params_on_knowledge(page: Page, q):
    resp = goto(page, f"/os/knowledge?{q}")
    assert resp is None or resp.status < 500
    assert not page.evaluate("() => window.__xss === 1")


@pytest.mark.parametrize("path", ["/os/build/runs/not-a-run", "/os/knowledge/domains/not-a-domain",
                                  "/os/run/incidents/0", "/os/skills/registry/nope-skill",
                                  "/os/design/modeling/nope", "/os/design/sttm/nope"])
def test_bad_ids_do_not_500(page: Page, path):
    resp = page.goto(path)
    assert resp is not None and resp.status < 500, f"{path} -> {resp.status if resp else None}"
    expect(page.locator("body")).not_to_contain_text("Unhandled Runtime Error")


def test_double_click_submit_is_safe(page: Page):
    open_page(page, "/os/knowledge?view=ask")
    page.get_by_label("Search or ask").fill("customer")
    btn = page.get_by_role("button", name="Search", exact=True)
    posts = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
    btn.dblclick()
    page.wait_for_timeout(2000)
    assert len(posts) <= 2, f"{len(posts)} POSTs from one double click"


def test_paste_into_fields(page: Page, context):
    context.grant_permissions(["clipboard-read", "clipboard-write"])
    open_page(page, "/os/knowledge?view=items")
    page.evaluate("navigator.clipboard.writeText('pasted-value')")
    f = page.get_by_label("Filter knowledge")
    f.focus()
    page.keyboard.press("Control+V")
    expect(f).to_have_value("pasted-value")


@pytest.mark.parametrize("path", [p for p, _ in OS_PAGES[:20]])
def test_rapid_navigation_no_crash(page: Page, path):
    page.goto(path, wait_until="commit")
    page.goto("/os", wait_until="commit")
    page.goto(path)
    expect(page.locator("h1").first).to_be_visible()
    assert not page._ui_errors
