"""Workflow sections: Home, Intake, Enable, Design, Build/Runs, Review, Skills, Release."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import first_link_matching, open_page, relative_url
from routes import RUN_STAGES


# ---------------------------------------------------------------- Home
@pytest.mark.smoke
def test_home_queue_and_toolbar(page: Page):
    open_page(page, "/os", "Platform actions")
    expect(page.get_by_test_id("home")).to_be_visible()
    expect(page.get_by_test_id("home-toolbar")).to_be_visible()
    expect(page.get_by_text("Start a workflow")).to_be_visible()
    queue = page.get_by_test_id("platform-queue")
    if queue.count():  # only rendered when there are queued actions
        expect(queue).to_be_visible()


def test_home_search_mode(page: Page):
    open_page(page, "/os")
    mode = page.get_by_test_id("search-mode")
    expect(mode).to_be_visible()
    box = mode.locator("input").first
    if box.count():
        box.fill("customer")
        box.press("Enter")
        page.wait_for_load_state("load")
        open_page(page, relative_url(page))


def test_home_denied_param_handled(page: Page):
    open_page(page, "/os?denied=admin")


# ---------------------------------------------------------------- Intake / Requests
def test_intake_board(page: Page):
    open_page(page, "/os/intake", "Requests")
    expect(page.get_by_test_id("intake-board")).to_be_visible()


def test_intake_new_intent_tabs(page: Page):
    open_page(page, "/os/intake/new", "Create an onboarding request")
    tabs = page.get_by_role("tablist", name="New or enhance").get_by_role("tab")
    assert tabs.count() >= 2
    for i in range(tabs.count()):
        tabs.nth(i).click()
        expect(tabs.nth(i)).to_have_attribute("aria-selected", "true")


def test_intake_new_domain_request_form(page: Page):
    open_page(page, "/os/intake/new")
    expect(page.get_by_test_id("domain-request").or_(page.get_by_test_id("domain-enhance")).first).to_be_visible()


def test_requests_list_and_history(page: Page):
    open_page(page, "/os/requests", "Onboarding requests")
    open_page(page, "/os/requests/history", "Request history")
    expect(page.get_by_test_id("request-history")).to_be_visible()


def test_request_detail_from_list(page: Page):
    open_page(page, "/os/requests")
    href = first_link_matching(page, r"^/os/requests/(?!history)[^/?]+$")
    if not href:
        pytest.skip("no requests")
    open_page(page, href)


# ---------------------------------------------------------------- Enable
def test_enable_source_readiness(page: Page):
    open_page(page, "/os/enable", "Source readiness")
    expect(page.get_by_test_id("source-config")).to_be_visible()


def test_enable_onboard_domain(page: Page):
    open_page(page, "/os/enable/new-domain", "Onboard domain")


def test_enable_onboard_source(page: Page):
    open_page(page, "/os/enable/onboarding?type=source", "Onboard source")
    expect(page.locator("form, input, select").first).to_be_visible()


# ---------------------------------------------------------------- Design / Stores
def test_design_board(page: Page):
    open_page(page, "/os/design", "Profile, model and map")
    expect(page.get_by_test_id("design-board")).to_be_visible()


def test_store_hub_tabs(page: Page):
    open_page(page, "/os/design/store")
    nav = page.get_by_test_id("store-nav")
    expect(nav).to_be_visible()
    items = nav.locator('[data-testid^="store-nav-"]')
    for i in range(items.count()):
        items.nth(i).click()
        page.wait_for_load_state("load")
        expect(page.locator("body")).not_to_contain_text("Application error")


@pytest.mark.parametrize("path,base", [
    ("/os/design/modeling", "/os/design/modeling/"),
    ("/os/design/sttm", "/os/design/sttm/"),
])
def test_store_detail_pages(page: Page, path, base):
    open_page(page, path)
    href = first_link_matching(page, "^" + re.escape(base) + r"[^?]+")
    if not href:
        pytest.skip(f"no entries in {path}")
    open_page(page, href)


def test_dq_store_suite(page: Page):
    open_page(page, "/os/design/dq-store/suite")


# ---------------------------------------------------------------- Build / Runs
def test_build_board(page: Page):
    open_page(page, "/os/build")
    expect(page.get_by_test_id("build-board").or_(page.get_by_text("Build workbench")).first).to_be_visible()


def test_ready_to_build(page: Page):
    open_page(page, "/os/build/ready", "Ready to build")
    expect(page.get_by_test_id("ready-board")).to_be_visible()


def test_silver_runs_list(page: Page):
    open_page(page, "/os/build/runs", "Silver Pipeline")
    expect(page.get_by_role("list", name="Silver Pipeline steps")).to_be_visible()
    expect(page.get_by_role("link", name="New Silver run")).to_be_visible()


def test_gold_runs_list(page: Page):
    open_page(page, "/os/build/gold", "Gold Pipeline")
    expect(page.get_by_role("list", name="Gold Pipeline steps")).to_be_visible()
    expect(page.get_by_test_id("new-gold-run")).to_be_visible()


def test_run_bulk_selection_buttons(page: Page):
    open_page(page, "/os/build/runs")
    box = page.get_by_role("checkbox", name=re.compile("^select "))
    if box.count() == 0:
        pytest.skip("no runs")
    box.first.check()
    expect(page.get_by_role("button", name="Archive selected")).to_be_visible()
    expect(page.get_by_role("button", name="Delete selected")).to_be_visible()
    page.get_by_role("button", name="Delete selected").click()
    dlg = page.get_by_role("dialog", name="Confirm delete").or_(page.get_by_role("alertdialog"))
    expect(dlg.first).to_be_visible()
    page.keyboard.press("Escape")
    box.first.uncheck()


def _first_run(page: Page) -> str:
    open_page(page, "/os/build/runs")
    href = first_link_matching(page, r"/os/build/runs/[0-9a-fA-F-]{8,}")
    if not href:
        pytest.skip("no runs available")
    return re.search(r"/os/build/runs/[^/?#]+", href).group(0)


def test_run_workspace(page: Page):
    run = _first_run(page)
    open_page(page, run)
    expect(page.get_by_test_id("run-menu")).to_be_visible()


@pytest.mark.parametrize("stage", RUN_STAGES)
def test_run_stage_pages(page: Page, stage):
    run = _first_run(page)
    open_page(page, f"{run}/{stage}")


@pytest.mark.parametrize("old,frag", [("mapping", "modeling"), ("sttm", "modeling"),
                                      ("landing", "source"), ("access", "source")])
def test_run_stage_redirects(page: Page, old, frag):
    run = _first_run(page)
    page.goto(f"{run}/{old}")
    expect(page).to_have_url(re.compile(frag))


def test_run_menu_cancel_requires_confirm(page: Page):
    run = _first_run(page)
    open_page(page, run)
    page.get_by_test_id("run-menu").click()
    item = page.get_by_role("menuitem", name="Cancel run")
    if item.count() == 0 or not item.is_enabled():
        pytest.skip("run not cancellable")
    item.click()
    expect(page.get_by_role("alertdialog")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("alertdialog")).to_be_hidden()


def test_lineage_page(page: Page):
    open_page(page, "/os/build/lineage", "Lineage")
    expect(page.get_by_label("Find a table")).to_be_visible()
    expect(page.get_by_role("group", name="Direction")).to_be_visible()


def test_code_and_history(page: Page):
    open_page(page, "/os/build/code", "Code")
    open_page(page, "/os/history", "Run history")
    expect(page.get_by_test_id("run-history")).to_be_visible()


# ---------------------------------------------------------------- Review
def test_review_gate(page: Page):
    open_page(page, "/os/review", "Validation gate")


def test_review_gate_send_back_requires_target(page: Page):
    open_page(page, "/os/review")
    gate = page.get_by_test_id("gate-decision")
    if gate.count() == 0:
        pytest.skip("no gate pending")
    expect(gate.get_by_role("button", name=re.compile("^(Approve|Request approval)$"))).to_be_visible()
    expect(gate.get_by_label("Send back to")).to_be_visible()


def test_qa_signoff_reject_requires_note(page: Page):
    open_page(page, "/os/review")
    sign = page.get_by_test_id("qa-signoff")
    if sign.count() == 0:
        pytest.skip("no QA signoff pending")
    expect(sign.get_by_placeholder("Note (required to reject)")).to_be_visible()


def test_approvals(page: Page):
    open_page(page, "/os/review/approvals", "Approvals")


# ---------------------------------------------------------------- Skills
def test_skill_studio_and_registry(page: Page):
    open_page(page, "/os/skills")
    open_page(page, "/os/skills/registry", "Agent Skills")
    expect(page.get_by_test_id("proposed-skills-link")).to_be_visible()


def test_proposed_skills(page: Page):
    open_page(page, "/os/skills/registry")
    page.get_by_test_id("proposed-skills-link").click()
    page.wait_for_load_state("load")
    open_page(page, relative_url(page), "Proposed agent skills")


def test_skill_detail(page: Page):
    open_page(page, "/os/skills/registry")
    href = first_link_matching(page, r"/skills/(?!proposed|registry)[^/?#]+$")
    if not href:
        pytest.skip("no skills")
    open_page(page, href)


# ---------------------------------------------------------------- Release
def test_release_pages(page: Page):
    open_page(page, "/os/release", "Code, test and release")
    open_page(page, "/os/release/source-control", "Source control")
    open_page(page, "/os/release/qa", "QA testing")


def test_qa_case_detail(page: Page):
    open_page(page, "/os/release/qa")
    href = first_link_matching(page, r"/qa/cases/[^/?#]+")
    if not href:
        pytest.skip("no QA cases")
    open_page(page, href)
