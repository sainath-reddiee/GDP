"""Journeys: incidents, resolutions, known fixes, permanent fixes."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import discover, open_page, unique

INC = "/os/run/incidents"
INC_DETAIL = r"/incidents/[^/?#]+$"


# ---------------------------------------------------------------- incident inbox
def test_incident_search_and_reset(page: Page):
    open_page(page, INC, "Incidents")
    page.get_by_label("Search incidents").fill("zz_nothing")
    page.keyboard.press("Enter")
    page.wait_for_load_state("load")
    page.get_by_role("button", name="Reset").click()
    expect(page.get_by_label("Search incidents")).to_have_value("")


@pytest.mark.parametrize("label", ["Severity", "Team", "Environment"])
def test_incident_filters(page: Page, label):
    open_page(page, INC)
    sel = page.get_by_label(label, exact=True)
    for i in range(min(sel.locator("option").count(), 5)):
        page.get_by_label(label, exact=True).select_option(index=i)
        page.wait_for_load_state("load")
        expect(page.locator("body")).not_to_contain_text("Application error")


def test_incident_mine_and_status_group(page: Page):
    open_page(page, INC)
    page.get_by_label("Mine").check()
    page.get_by_label("Mine").uncheck()
    group = page.get_by_role("group", name="Status")
    btns = group.get_by_role("button").or_(group.get_by_role("radio"))
    for i in range(btns.count()):
        btns.nth(i).click()
        page.wait_for_load_state("load")


def test_incident_bulk_bar(page: Page):
    open_page(page, INC)
    boxes = page.get_by_role("checkbox", name=re.compile("^Select (?!all)"))
    if boxes.count() == 0:
        pytest.skip("no incidents")
    page.get_by_label("Select all shown").check()
    for b in ["Acknowledge", "Assign", "Resolve", "Clear selection"]:
        expect(page.get_by_role("button", name=b, exact=True)).to_be_visible()
    page.get_by_role("button", name="Assign", exact=True).click()
    expect(page.get_by_label("Assignee")).to_be_visible()
    page.get_by_role("button", name="Clear selection").click()
    expect(page.get_by_role("button", name="Clear selection")).to_have_count(0)


def test_incident_load_more(page: Page):
    open_page(page, INC)
    more = page.get_by_role("button", name="Load more")
    if more.count():
        n = page.locator("tbody tr").count()
        more.click()
        page.wait_for_timeout(1500)
        assert page.locator("tbody tr").count() >= n


# ---------------------------------------------------------------- incident detail
def test_incident_detail_actions_visible(page: Page):
    open_page(page, discover(page, INC, INC_DETAIL))
    expect(page.get_by_test_id("incident-summary")).to_be_visible()
    for name in ["Acknowledge|Reopen", "Assign", "Resolve|Reopen", "Mute", "Diagnose|Regenerate"]:
        expect(page.get_by_role("button", name=re.compile(f"^({name})$")).first).to_be_visible()


def test_incident_assign_form_opens(page: Page):
    open_page(page, discover(page, INC, INC_DETAIL))
    page.get_by_role("button", name="Assign", exact=True).first.click()
    expect(page.get_by_label("Assignee")).to_have_attribute("placeholder", "User name or email")


def test_incident_mute_form_opens(page: Page):
    open_page(page, discover(page, INC, INC_DETAIL))
    page.get_by_role("button", name="Mute", exact=True).first.click()
    expect(page.get_by_label("Until")).to_be_visible()
    expect(page.get_by_label("Reason")).to_have_attribute("placeholder", "Planned maintenance on the source")


def test_incident_retry_dialog_preview(page: Page):
    open_page(page, discover(page, INC, INC_DETAIL))
    btn = page.get_by_role("button", name="Retry task")
    if btn.count() == 0:
        pytest.skip("not retryable")
    btn.click()
    dlg = page.get_by_role("dialog", name="Retry task")
    expect(dlg).to_be_visible()
    dlg.get_by_role("button", name=re.compile("^Preview")).click()
    expect(dlg.get_by_role("button", name=re.compile(r"Clear and retry \d+ task"))).to_be_visible()
    page.keyboard.press("Escape")


def test_incident_ack_query_param_dialog(page: Page):
    href = discover(page, INC, INC_DETAIL)
    page.goto(href + "?ack=1")
    dlg = page.get_by_role("alertdialog", name="Acknowledge this incident")
    if dlg.count():
        expect(dlg).to_be_visible()
        page.keyboard.press("Escape")


@pytest.mark.writes
def test_journey_incident_full_lifecycle(page: Page):
    open_page(page, discover(page, INC, INC_DETAIL))
    if page.get_by_role("button", name="Acknowledge").count():
        page.get_by_role("button", name="Acknowledge").first.click()
        expect(page.get_by_text("Acknowledged.")).to_be_visible()
    page.get_by_label("Timeline note").fill("UI-AUTOTEST note")
    page.get_by_role("button", name="Add note").click()
    expect(page.get_by_text("Note added.")).to_be_visible()
    page.get_by_role("button", name="Assign", exact=True).first.click()
    page.get_by_label("Assignee").fill("UI_AUTOTEST")
    page.get_by_role("button", name="Assign", exact=True).last.click()
    expect(page.get_by_text("Assigned to UI_AUTOTEST.")).to_be_visible()
    page.get_by_role("button", name="Resolve", exact=True).first.click()
    page.get_by_label("Resolution note").fill("Resolved by automated UI test")
    page.get_by_role("button", name="Resolve", exact=True).last.click()
    expect(page.get_by_text("Resolved.")).to_be_visible()
    page.get_by_role("button", name="Reopen").click()
    expect(page.get_by_text("Reopened.")).to_be_visible()


@pytest.mark.writes
def test_journey_incident_diagnose_and_ask(page: Page):
    open_page(page, discover(page, INC, INC_DETAIL))
    page.get_by_role("button", name=re.compile("^(Diagnose|Regenerate)$")).first.click()
    page.get_by_label("Question about this incident").fill("What failed?")
    page.get_by_role("button", name="Ask", exact=True).click()
    expect(page.locator("body")).not_to_contain_text("Application error", timeout=120_000)


# ---------------------------------------------------------------- resolutions
def test_resolution_detail_decision_requires_note(page: Page):
    href = discover(page, "/os/run/ops/resolutions", r"/ops/resolutions/[^/?#]+$")
    open_page(page, href)
    expect(page.get_by_test_id("rca")).to_be_visible()
    dec = page.get_by_test_id("fix-decision")
    if dec.count() == 0:
        pytest.skip("no pending fix decision")
    dec.get_by_role("button", name="Reject").click()
    expect(dec.get_by_label("Decision note")).to_be_visible()


@pytest.mark.writes
def test_journey_resolution_reject(page: Page):
    open_page(page, "/os/run/ops/resolutions")
    dec = page.get_by_test_id("fix-decision").first
    if dec.count() == 0:
        pytest.skip("no approvals")
    dec.get_by_role("button", name="Reject").click()
    dec.get_by_label("Decision note").fill("UI-AUTOTEST reject")
    dec.get_by_role("button", name="Reject").click()


# ---------------------------------------------------------------- known fixes
def _new_fix(page):
    open_page(page, "/os/run/ops/fixes/new")
    form = page.get_by_test_id("fix-form")
    expect(form).to_be_visible()
    return form


def test_known_fix_form_fields(page: Page):
    form = _new_fix(page)
    for lbl in ["Title", "Match text", "Notes"]:
        expect(form.get_by_label(re.compile(f"^{lbl}"))).to_be_visible()
    expect(form.get_by_role("button", name="Create draft")).to_be_visible()


def test_known_fix_procedure_vs_skill_toggle(page: Page):
    form = _new_fix(page)
    form.get_by_label("One procedure").check()
    expect(form.get_by_placeholder("CORE.FIX_RECREATE_STAGE")).to_be_visible()
    form.get_by_label(re.compile("Several steps")).check()
    expect(form.get_by_text("Choose a multi-step fix").or_(form.get_by_label("Skill")).first).to_be_visible()


def test_known_fix_approval_checkbox(page: Page):
    form = _new_fix(page)
    box = form.get_by_label("Ask for human approval before it runs")
    before = box.is_checked()
    box.click()
    assert box.is_checked() != before


@pytest.mark.writes
def test_journey_known_fix_create_save_retire(page: Page):
    form = _new_fix(page)
    form.get_by_label(re.compile("^Title")).fill(unique("UI-AUTOTEST fix"))
    form.get_by_label(re.compile("^Match text")).fill("UI_AUTOTEST_ERROR")
    form.get_by_label("One procedure").check()
    form.get_by_placeholder("CORE.FIX_RECREATE_STAGE").fill("CORE.FIX_RECREATE_STAGE")
    form.get_by_role("button", name="Create draft").click()
    page.wait_for_url(re.compile(r"/os/run/ops/fixes/(?!new)[^/?]+$"))
    page.get_by_test_id("fix-form").get_by_label(re.compile("^Notes")).fill("edited")
    page.get_by_role("button", name="Save draft").click()
    retire = page.get_by_role("button", name="Retire")
    if retire.count():
        retire.click()


# ---------------------------------------------------------------- permanent fixes
def test_permanent_fix_review_controls(page: Page):
    href = discover(page, "/os/run/ops/permanent-fixes", r"/ops/permanent-fixes/[^/?#]+$")
    open_page(page, href)
    rev = page.get_by_test_id("permanent-fix-review")
    expect(rev).to_be_visible()
    if rev.get_by_role("button", name="Preview the pull request").count():
        rev.get_by_role("button", name="Preview the pull request").click()
        expect(page.get_by_role("button", name="Open draft pull request")).to_be_visible()
        page.get_by_role("button", name="Cancel").click()


@pytest.mark.writes
def test_journey_permanent_fix_reject_needs_note(page: Page):
    href = discover(page, "/os/run/ops/permanent-fixes", r"/ops/permanent-fixes/[^/?#]+$")
    open_page(page, href)
    rev = page.get_by_test_id("permanent-fix-review")
    if rev.get_by_role("button", name="Reject").count() == 0:
        pytest.skip("not reviewable")
    rev.get_by_label("Decision note").fill("UI-AUTOTEST reject")
    rev.get_by_role("button", name="Reject").click()
