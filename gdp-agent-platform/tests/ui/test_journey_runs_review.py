"""Journeys: run lists, new Gold run, run workspace (every stage), decision bar, review & release gates."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import first_link_matching, open_page, relative_url, unique

STAGE_LINKS = [
    ("Overview", ""), ("Source Ingestion", "/source"), ("Profiling", "/profile"),
    ("Mapping", "/modeling"), ("QA tests", "/qa"), ("Data Quality", "/soda"),
    ("Silver Code Generation", "/dbt"), ("Validation", "/validation"), ("Code review", "/review"),
]


@pytest.fixture
def run_url(page: Page) -> str:
    open_page(page, "/os/build/runs")
    href = first_link_matching(page, r"/os/build/runs/[0-9a-fA-F-]{8,}")
    if not href:
        pytest.skip("no runs available")
    return re.search(r"/os/build/runs/[^/?#]+", href).group(0)


# ---------------------------------------------------------------- run list
def test_new_silver_run_goes_to_onboarding_or_intake(page: Page):
    open_page(page, "/os/build/runs")
    link = page.get_by_role("link", name=re.compile("New Silver run|Request a source"))
    link.first.click()
    expect(page).to_have_url(re.compile(r"/os/(enable/onboarding\?type=source|intake/new)"))


def test_run_list_search(page: Page):
    open_page(page, "/os/build/runs")
    page.get_by_label("Search runs").fill("zz_no_run_matches")
    page.keyboard.press("Enter")
    page.wait_for_load_state("load")
    expect(page.locator("body")).not_to_contain_text("Application error")


@pytest.mark.parametrize("label", ["Domain", "Stage", "Tag", "Sort"])
def test_run_list_filters(page: Page, label):
    open_page(page, "/os/build/runs")
    sel = page.get_by_label(label, exact=True).first
    n = sel.locator("option").count()
    for i in range(min(n, 5)):
        page.get_by_label(label, exact=True).first.select_option(index=i)
        page.wait_for_load_state("load")
        expect(page.locator("body")).not_to_contain_text("Application error")


def test_run_list_needs_review_toggle(page: Page):
    open_page(page, "/os/build/runs")
    t = page.get_by_role("button", name="Needs review")
    before = t.get_attribute("aria-pressed")
    t.click()
    page.wait_for_load_state("load")
    expect(page.get_by_role("button", name="Needs review")).not_to_have_attribute("aria-pressed", before or "")


def test_run_list_paging(page: Page):
    open_page(page, "/os/build/runs")
    nxt = page.get_by_role("button", name="Next").or_(page.get_by_role("link", name="Next")).first
    if nxt.count() and nxt.is_enabled():
        nxt.click()
        page.get_by_role("button", name="Previous").or_(page.get_by_role("link", name="Previous")).first.click()


# ---------------------------------------------------------------- Gold run
def test_new_gold_run_modal_validation(page: Page):
    open_page(page, "/os/build/gold", "Gold Pipeline")
    page.get_by_test_id("new-gold-run").click()
    dlg = page.get_by_role("dialog", name="New Gold run")
    expect(dlg).to_be_visible()
    start = dlg.get_by_role("button", name=re.compile(r"^Start Gold run"))
    expect(start).to_be_disabled()
    dom = dlg.get_by_label("Domain")
    if dom.locator("option").count() > 1:
        dom.select_option(index=1)
        silver = dlg.get_by_label("Silver schema")
        if silver.locator("option").count() > 1:
            silver.select_option(index=1)
            boxes = dlg.get_by_role("checkbox", name=re.compile("^use "))
            if boxes.count():
                boxes.first.check()
                expect(start).to_be_enabled()
                expect(start).to_contain_text("(1 table")
    dlg.get_by_role("button", name="Cancel").click()
    expect(dlg).to_be_hidden()


@pytest.mark.writes
def test_journey_start_gold_run(page: Page):
    open_page(page, "/os/build/gold")
    page.get_by_test_id("new-gold-run").click()
    dlg = page.get_by_role("dialog", name="New Gold run")
    if dlg.get_by_label("Domain").locator("option").count() < 2:
        pytest.skip("no domains")
    dlg.get_by_label("Domain").select_option(index=1)
    dlg.get_by_label("Run name (optional)").fill(unique("UI-AUTOTEST gold"))
    dlg.get_by_label("Silver schema").select_option(index=1)
    dlg.get_by_role("checkbox", name=re.compile("^use ")).first.check()
    dlg.get_by_role("button", name=re.compile(r"^Start Gold run")).click()
    page.wait_for_url(re.compile(r"/os/build/runs/[^/]+"), timeout=300_000)


# ---------------------------------------------------------------- run workspace
def test_stage_rail_complete(page: Page, run_url):
    open_page(page, run_url)
    rail = page.get_by_role("navigation", name="Run lineage")
    expect(rail).to_be_visible()
    for label, _ in STAGE_LINKS:
        expect(rail.get_by_text(label, exact=True).first).to_be_visible()


@pytest.mark.parametrize("label,suffix", STAGE_LINKS, ids=[l for l, _ in STAGE_LINKS])
def test_stage_rail_navigates_or_locked(page: Page, run_url, label, suffix):
    open_page(page, run_url)
    rail = page.get_by_role("navigation", name="Run lineage")
    link = rail.get_by_role("link", name=label, exact=True)
    if link.count() == 0:
        expect(rail.get_by_title("Locked until earlier stages are approved").first).to_be_visible()
        return
    link.click()
    page.wait_for_url(re.compile(re.escape(run_url + suffix) + r"(\?|$)"))
    open_page(page, run_url + suffix)
    expect(page.get_by_role("navigation", name="Run lineage").get_by_role(
        "link", name=label, exact=True)).to_have_attribute("aria-current", "page")


def test_run_menu_items(page: Page, run_url):
    open_page(page, run_url)
    page.get_by_test_id("run-menu").click()
    menu = page.get_by_role("menu")
    expect(menu.get_by_role("menuitem", name="Audit trail")).to_be_visible()
    expect(menu.get_by_role("menuitem", name=re.compile("^(Archive|Restore) run"))).to_be_visible()
    menu.get_by_role("menuitem", name="Audit trail").click()
    expect(page).to_have_url(re.compile(r"/audit$"))


def test_cancel_run_confirm_keep_running(page: Page, run_url):
    open_page(page, run_url)
    page.get_by_test_id("run-menu").click()
    item = page.get_by_role("menuitem", name=re.compile(r"^Cancel run"))
    if item.count() == 0:
        pytest.skip("run not cancellable")
    item.click()
    dlg = page.get_by_role("alertdialog", name="Confirm cancelling the run")
    expect(dlg).to_be_visible()
    dlg.get_by_role("button", name="Keep running").click()
    expect(dlg).to_be_hidden()


@pytest.mark.writes
def test_journey_archive_and_restore_run(page: Page, run_url):
    open_page(page, run_url)
    page.get_by_test_id("run-menu").click()
    first = page.get_by_role("menuitem", name=re.compile("^(Archive|Restore) run"))
    label = first.inner_text()
    first.click()
    page.wait_for_load_state("load")
    page.get_by_test_id("run-menu").click()
    other = "Restore run" if label.startswith("Archive") else "Archive run"
    page.get_by_role("menuitem", name=other).click()


def test_overview_console_next_step(page: Page, run_url):
    open_page(page, run_url)
    nxt = page.get_by_role("link", name=re.compile(
        "Start profiling|Mapping:|Run validation|Review generated code|Open Data Quality|Open "))
    if nxt.count():
        nxt.first.click()
        page.wait_for_load_state("load")
        open_page(page, relative_url(page))


def test_autopilot_button_present(page: Page, run_url):
    open_page(page, run_url)
    ap = page.get_by_test_id("autopilot")
    if ap.count():
        expect(ap).to_contain_text(re.compile("Run to next gate|Running"))


STAGE_ACTIONS = {
    "/source": r"Validate|Profile \d+ tables|Retry from the failed step|Change ingestion mode",
    "/profile": r"Start profiling|Retry profiling|Profile all from scratch|Re-profile",
    "/domain": r"Score source against packs|Use this pack",
    "/modeling": r"Generate mapping candidates|Retry mapping|Approve suggested|Ask AI|Use v\d+",
    "/modeling?tab=transformations": r"Generate Data Mapping|Confirm join plan|Apply to contract",
    "/soda": r"Generate checks|Regenerate|Approve|Extract checks",
    "/qa": r"Run selected|Approve QA",
    "/dbt": r"Generate|Regenerate|Push v\d+|Push again",
    "/validation": r"Run validation",
}


@pytest.mark.parametrize("stage", list(STAGE_ACTIONS), ids=list(STAGE_ACTIONS))
def test_stage_primary_action_present(page: Page, run_url, stage):
    resp = page.goto(run_url + stage)
    if "Locked" in (page.title() or "") or (resp and resp.status >= 400):
        pytest.skip("stage locked")
    open_page(page, run_url + stage)
    btn = page.get_by_role("button", name=re.compile(STAGE_ACTIONS[stage]))
    done = page.get_by_test_id("decision-bar").or_(page.get_by_text(re.compile("(?i)approved|complete")))
    expect(btn.first.or_(done.first)).to_be_visible()


def test_modeling_tabs(page: Page, run_url):
    open_page(page, run_url + "/modeling")
    for name, qs in [("1. Field mapping", "tab=mapping"), ("2. Transformations and joins", "tab=transformations")]:
        tab = page.get_by_role("tab", name=name)
        if tab.count() == 0:
            pytest.skip("modeling locked")
        tab.click()
        expect(page).to_have_url(re.compile(qs))


def test_profile_json_download(page: Page, run_url):
    open_page(page, run_url + "/profile")
    btn = page.get_by_role("button", name="Profile JSON").or_(page.get_by_role("link", name="Profile JSON"))
    if btn.count() == 0:
        pytest.skip("no profiled tables")
    with page.expect_download() as dl:
        btn.first.click()
    assert dl.value.suggested_filename


def test_dbt_studio_options(page: Page, run_url):
    open_page(page, run_url + "/dbt")
    for name in ["Open the PR after generating", "Draft PR"]:
        box = page.get_by_label(name)
        if box.count():
            box.first.check()
            box.first.uncheck()


# ---------------------------------------------------------------- decision bar
def test_decision_bar_send_back_mode(page: Page, run_url):
    for stage in ["/modeling", "/modeling?tab=transformations", "/soda", "/review", ""]:
        open_page(page, run_url + stage)
        bar = page.get_by_test_id("decision-bar")
        if bar.count():
            break
    else:
        pytest.skip("no gate awaiting approval on this run")
    expect(bar).to_contain_text("Awaiting human approval")
    expect(bar.get_by_label("Business justification")).to_be_visible()
    bar.get_by_role("button", name="Send back").click()
    expect(bar).to_contain_text("Send back for changes")
    expect(bar.get_by_label("What needs to change")).to_be_visible()
    bar.get_by_role("button", name="Cancel").click()
    expect(bar).to_contain_text("Awaiting human approval")


# ---------------------------------------------------------------- review gate
def test_review_queue_to_gate(page: Page):
    open_page(page, "/os/review", "Validation gate")
    expect(page.get_by_text("Gates waiting on people")).to_be_visible()
    href = first_link_matching(page, r"/os/review\?run=")
    if not href:
        pytest.skip("no gates waiting")
    open_page(page, href)
    gate = page.get_by_test_id("gate-decision")
    expect(gate).to_be_visible()
    expect(gate.get_by_label(re.compile("Business justification"))).to_be_visible()
    expect(gate.get_by_test_id("gate-approve")).to_be_visible()
    expect(gate.get_by_test_id("gate-send-back")).to_be_visible()
    sel = gate.get_by_label("Send back to")
    for i in range(sel.locator("option").count()):
        sel.select_option(index=i)


@pytest.mark.writes
def test_journey_gate_send_back(page: Page):
    open_page(page, "/os/review")
    href = first_link_matching(page, r"/os/review\?run=")
    if not href:
        pytest.skip("no gates")
    open_page(page, href)
    gate = page.get_by_test_id("gate-decision")
    gate.get_by_label(re.compile("Business justification")).fill("UI-AUTOTEST: needs changes")
    gate.get_by_test_id("gate-send-back").click()
    expect(page.get_by_text(re.compile("Sent (back )?to"))).to_be_visible()


@pytest.mark.writes
def test_journey_gate_approve(page: Page):
    open_page(page, "/os/review")
    href = first_link_matching(page, r"/os/review\?run=")
    if not href:
        pytest.skip("no gates")
    open_page(page, href)
    gate = page.get_by_test_id("gate-decision")
    gate.get_by_label(re.compile("Business justification")).fill("UI-AUTOTEST: looks right")
    gate.get_by_test_id("gate-approve").click()
    expect(page.get_by_text(re.compile("approved\\. The workflow continues\\.|Sent to"))).to_be_visible()


@pytest.mark.writes
def test_journey_review_standards_and_validate_again(page: Page):
    open_page(page, "/os/review")
    href = first_link_matching(page, r"/os/review\?run=")
    if not href:
        pytest.skip("no gates")
    open_page(page, href)
    for tid in ["review-standards-button", "validate-again-button"]:
        b = page.get_by_test_id(tid)
        if b.count():
            b.click()
            expect(b).to_be_enabled(timeout=300_000)


# ---------------------------------------------------------------- release
def test_release_signoff_requires_note_to_reject(page: Page):
    open_page(page, "/os/release", "Code, test and release")
    sign = page.get_by_test_id("qa-signoff")
    if sign.count() == 0:
        pytest.skip("no QA signoff pending")
    sign.get_by_label("Signoff note").fill("")
    reject = sign.get_by_test_id("qa-reject")
    reject.click()
    expect(page.get_by_text("QA signed off.")).to_have_count(0)


@pytest.mark.writes
def test_journey_release_signoff_and_publish(page: Page):
    open_page(page, "/os/release")
    sign = page.get_by_test_id("qa-signoff")
    if sign.count() == 0:
        pytest.skip("nothing to release")
    sign.get_by_label("Signoff note").fill("UI-AUTOTEST signoff")
    sign.get_by_test_id("qa-approve").click()
    expect(page.get_by_text(re.compile("QA signed off|Sent to"))).to_be_visible()
    pr = page.get_by_test_id("publish-pr")
    if pr.count():
        pr.get_by_label("Draft PR").check()
        pr.get_by_test_id("publish-pr-button").click()
        expect(page.get_by_text(re.compile(r"Pull request #\d+ is open|Sent to"))).to_be_visible(timeout=300_000)


def test_preview_role_disables_decisions(page: Page):
    open_page(page, "/os")
    sel = page.get_by_label("View as persona", exact=True)
    if sel.count() == 0 or sel.locator("option").count() < 2:
        pytest.skip("no persona preview")
    sel.select_option(index=1)
    open_page(page, "/os/review")
    href = first_link_matching(page, r"/os/review\?run=")
    if not href:
        pytest.skip("no gates")
    open_page(page, href)
    if page.get_by_text(re.compile("Decisions are off while you preview")).count():
        expect(page.get_by_test_id("gate-approve")).to_be_disabled()
