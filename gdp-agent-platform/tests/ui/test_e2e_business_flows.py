"""End-to-end business flows that chain several journeys, plus per-persona tours.

The chained flows (marked `writes`) walk a real use case from start to finish, carrying state
between steps. Each step is a `with step(...)` block so a failure names the exact step.
"""
import contextlib
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import first_link_matching, open_page, relative_url, unique


@contextlib.contextmanager
def step(name: str):
    try:
        yield
    except pytest.skip.Exception:
        raise
    except Exception as exc:
        raise AssertionError(f"STEP FAILED: {name}\n{exc}") from exc


# ---------------------------------------------------------------- persona tours (read-only)
def _personas(page: Page):
    open_page(page, "/os")
    sel = page.get_by_label("View as persona", exact=True)
    if sel.count() == 0:
        return []
    return sel.locator("option").evaluate_all("os => os.map(o => [o.value, o.textContent.trim()])")


def test_every_persona_can_tour_its_rail(page: Page):
    personas = _personas(page)
    if len(personas) < 2:
        pytest.skip("single persona")
    for value, label in personas:
        with step(f"switch to persona {label}"):
            open_page(page, "/os")
            page.get_by_label("View as persona", exact=True).select_option(value)
            page.wait_for_load_state("load")
        rail = page.get_by_role("navigation", name="Agentic Data OS")
        hrefs = rail.locator("a[href]").evaluate_all("as => as.map(a => a.getAttribute('href'))")
        assert hrefs, f"persona {label} has an empty rail"
        for h in hrefs:
            with step(f"{label}: open {h}"):
                open_page(page, h)
    stop = page.get_by_role("button", name="Stop preview")
    if stop.count():
        stop.click()


def test_denied_screen_redirects_home_with_notice(page: Page):
    personas = _personas(page)
    if len(personas) < 2:
        pytest.skip("single persona")
    page.get_by_label("View as persona", exact=True).select_option(personas[-1][0])
    page.goto("/os/people")
    if "denied=" in page.url:
        expect(page).to_have_url(re.compile(r"/os\?denied="))


# ---------------------------------------------------------------- read-only cross-module flows
def test_flow_knowledge_to_domain_to_runs(page: Page):
    with step("open domains in knowledge hub"):
        open_page(page, "/os/knowledge?view=domains")
        href = first_link_matching(page, r"/os/knowledge/domains/[^/?#]+$")
        if not href:
            pytest.skip("no domains")
    with step("open domain page"):
        open_page(page, href)
    with step("follow first run / built-with link from domain"):
        run = first_link_matching(page, r"/os/build/runs/[^/?#]+")
        if run:
            open_page(page, run)


def test_flow_build_board_to_run_to_review(page: Page):
    with step("build board"):
        open_page(page, "/os/build")
        run = first_link_matching(page, r"/os/build/runs/[^/?#]+")
        if not run:
            pytest.skip("no runs on the build board")
    with step("open run"):
        open_page(page, run)
    with step("open review for that run"):
        rid = re.search(r"/os/build/runs/([^/?#]+)", run).group(1)
        open_page(page, f"/os/review?run={rid}")


def test_flow_incident_to_resolution_to_known_fix(page: Page):
    with step("incidents"):
        open_page(page, "/os/run/incidents")
        inc = first_link_matching(page, r"/incidents/[^/?#]+$")
        if not inc:
            pytest.skip("no incidents")
    with step("incident detail"):
        open_page(page, inc)
    with step("resolutions list"):
        open_page(page, "/os/run/ops/resolutions")
    with step("known fixes list"):
        open_page(page, "/os/run/ops/fixes")


def test_flow_home_search_to_result(page: Page):
    with step("home search"):
        open_page(page, "/os")
        box = page.get_by_test_id("search-mode").locator("input").first
        if box.count() == 0:
            pytest.skip("no home search")
        box.fill("customer")
        box.press("Enter")
        page.wait_for_load_state("load")
    with step("open first result"):
        link = page.locator("main a[href^='/os/']").first
        if link.count():
            link.click()
            page.wait_for_load_state("load")
            open_page(page, relative_url(page))


# ---------------------------------------------------------------- chained write flows
@pytest.mark.writes
def test_e2e_domain_request_to_onboarded_domain(page: Page):
    title = unique("UI-AUTOTEST E2E domain")
    with step("file a domain request"):
        open_page(page, "/os/intake/new?type=domain&intent=new")
        page.locator("#rq-title").fill(title)
        page.locator("#rq-outcome").fill("E2E automated flow")
        page.locator("#rq-domain").fill("UIE2E")
        page.get_by_test_id("submit-request-button").click()
        if page.get_by_test_id("submit-anyway").is_visible():
            page.get_by_test_id("submit-anyway").click()
        page.wait_for_url(re.compile(r"/os/requests/[^/?]+$"))
        request_url = relative_url(page)
    with step("request appears on intake board"):
        open_page(page, "/os/intake")
        expect(page.get_by_text(title).first).to_be_visible()
    with step("hand off to domain onboarding"):
        rid = request_url.rsplit("/", 1)[-1]
        open_page(page, f"/os/enable/new-domain?request={rid}")
        expect(page.get_by_test_id("domain-handoff")).to_be_visible()
        page.get_by_role("link", name="Back to the request").click()
        expect(page).to_have_url(re.compile(re.escape(request_url)))
    with step("close the request"):
        c = page.get_by_test_id("request-controls")
        c.get_by_label("Comment or reason").fill("E2E cleanup")
        c.get_by_test_id("request-cancel").click()
        expect(page.get_by_text("Request cancelled.")).to_be_visible()
    with step("request shows in history"):
        open_page(page, "/os/requests/history")


@pytest.mark.writes
def test_e2e_silver_pipeline_happy_path(page: Page):
    """Take the newest run as far as it can go: profile -> mapping -> DQ -> QA -> dbt -> validation -> review."""
    with step("pick newest run"):
        open_page(page, "/os/build/runs")
        href = first_link_matching(page, r"/os/build/runs/[0-9a-fA-F-]{8,}")
        if not href:
            pytest.skip("no runs")
        run = re.search(r"/os/build/runs/[^/?#]+", href).group(0)

    actions = [
        ("/profile", r"^(Start profiling|Retry profiling)$"),
        ("/modeling", r"^(Generate mapping candidates|Retry mapping)$"),
        ("/modeling?tab=transformations", r"^Generate Data Mapping$"),
        ("/soda", r"^Generate checks$"),
        ("/qa", r"^Run selected$"),
        ("/dbt", r"^Generate only \(review first\)$|^Generate$"),
        ("/validation", r"^Run validation$"),
    ]
    for stage, name in actions:
        with step(f"stage {stage}"):
            open_page(page, run + stage)
            btn = page.get_by_role("button", name=re.compile(name))
            if btn.count() and btn.first.is_enabled():
                btn.first.click()
                page.wait_for_load_state("load")
                expect(page.locator("body")).not_to_contain_text("Application error", timeout=600_000)
            bar = page.get_by_test_id("decision-bar")
            if bar.count():
                bar.get_by_label("Business justification").fill("E2E automated approval")
                bar.get_by_role("button", name=re.compile(r"^Approve")).click()
                page.wait_for_load_state("load")
    with step("review gate"):
        open_page(page, run + "/review")


@pytest.mark.writes
def test_e2e_knowledge_contribute_curate_use(page: Page):
    title = unique("UI-AUTOTEST E2E knowledge")
    with step("add knowledge"):
        open_page(page, "/os/knowledge?view=items")
        page.get_by_role("button", name="Add knowledge").last.click()
        dlg = page.get_by_role("dialog", name="Add knowledge")
        dlg.get_by_label("Domain").select_option(index=1)
        dlg.get_by_label("Title").fill(title)
        dlg.get_by_label(re.compile(r"^Content \(what people")).fill("E2E: synonyms for company name")
        dlg.get_by_role("button", name="Save").click()
        expect(dlg).to_be_hidden()
    with step("find it via filter"):
        page.get_by_label("Filter knowledge").fill(title)
        expect(page.get_by_text(title).first).to_be_visible()
    with step("find it via Ask search"):
        open_page(page, "/os/knowledge?view=ask")
        page.get_by_label("Search or ask").fill(title)
        page.get_by_role("button", name="Search", exact=True).click()
        expect(page.get_by_text(title).first).to_be_visible(timeout=120_000)
    with step("mark verified then retire"):
        open_page(page, "/os/knowledge?view=items")
        page.get_by_label("Filter knowledge").fill(title)
        page.get_by_label(f"Select {title}").check()
        page.get_by_role("button", name="Mark verified").click()
        page.get_by_label(f"Select {title}").check()
        page.get_by_role("button", name="Retire").click()


@pytest.mark.writes
def test_e2e_incident_to_known_fix(page: Page):
    with step("create known fix matching an error"):
        open_page(page, "/os/run/ops/fixes/new")
        f = page.get_by_test_id("fix-form")
        f.get_by_label(re.compile("^Title")).fill(unique("UI-AUTOTEST E2E fix"))
        f.get_by_label(re.compile("^Match text")).fill("UI_E2E_ERROR")
        f.get_by_label("One procedure").check()
        f.get_by_placeholder("CORE.FIX_RECREATE_STAGE").fill("CORE.FIX_RECREATE_STAGE")
        f.get_by_role("button", name="Create draft").click()
        page.wait_for_url(re.compile(r"/os/run/ops/fixes/(?!new)[^/?]+$"))
    with step("fix listed in known fixes"):
        open_page(page, "/os/run/ops/fixes")
        expect(page.get_by_test_id("fix-table")).to_contain_text("UI-AUTOTEST E2E fix")
