"""Journeys: tickets, QA suites/tests, DQ store, people & personas, admin settings, lineage, code."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import open_page, unique

TICKETS = "/os/run/tickets"


# ---------------------------------------------------------------- tickets
@pytest.mark.parametrize("view", ["All open", "Assigned to me"])
def test_ticket_saved_views(page: Page, view):
    open_page(page, TICKETS, "Tickets")
    page.get_by_label("Saved views").get_by_text(view, exact=True).click()
    page.wait_for_load_state("load")
    expect(page.locator("body")).not_to_contain_text("Application error")


@pytest.mark.parametrize("group,options", [
    ("Source", ["All", "Jira", "Cases"]), ("Status", ["Open", "Done", "Any"]),
    ("Assignee", ["Anyone", "Me", "Unassigned"]), ("Priority", ["P1", "P2", "P3", "P4"]),
])
def test_ticket_segmented_filters(page: Page, group, options):
    open_page(page, TICKETS)
    g = page.get_by_role("group", name=group)
    for o in options:
        g.get_by_role("button", name=o, exact=True).or_(g.get_by_role("radio", name=o)).first.click()
        page.wait_for_load_state("load")


def test_ticket_more_filters_and_clear(page: Page):
    open_page(page, TICKETS)
    page.get_by_role("button", name="More filters").click()
    for lbl in ["Sprint", "Domain"]:
        expect(page.get_by_label(lbl).first).to_be_visible()
    for t in ["SLA breached", "Linked to data", "In the Copilot"]:
        tog = page.get_by_role("button", name=t).or_(page.get_by_label(t)).first
        if tog.count():
            tog.click()
    page.get_by_role("button", name="Clear filters").click()


def test_ticket_search(page: Page):
    open_page(page, TICKETS)
    page.get_by_label("Search tickets").fill("zz_no_ticket")
    page.keyboard.press("Enter")
    page.wait_for_load_state("load")


def test_ticket_save_view_form_cancel(page: Page):
    open_page(page, TICKETS)
    page.get_by_role("button", name="Save view").click()
    expect(page.get_by_label("View name")).to_be_visible()
    expect(page.get_by_label("Share")).to_be_visible()
    page.get_by_role("button", name="Cancel").click()
    expect(page.get_by_label("View name")).to_have_count(0)


@pytest.mark.writes
def test_journey_ticket_view_save_and_delete(page: Page):
    name = unique("UIAUTO view")
    open_page(page, TICKETS)
    page.get_by_role("group", name="Status").get_by_role("button", name="Done").click()
    page.get_by_role("button", name="Save view").click()
    page.get_by_label("View name").fill(name)
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_label("Saved views").get_by_text(name)).to_be_visible()
    page.get_by_role("button", name=f"Delete view {name}").click()
    expect(page.get_by_label("Saved views").get_by_text(name)).to_have_count(0)


def test_ticket_sprint_control(page: Page):
    open_page(page, TICKETS)
    ctl = page.get_by_test_id("sprint-control").first
    if ctl.count() == 0:
        pytest.skip("no Jira tickets")
    sel = ctl.get_by_role("combobox")
    expect(sel.locator("option", has_text="Backlog (no sprint)")).to_have_count(1)


# ---------------------------------------------------------------- QA
QA = "/os/release/qa"


def test_qa_tabs(page: Page):
    open_page(page, QA, "QA testing")
    tabs = page.get_by_role("tablist", name="QA views").get_by_role("tab")
    for i in range(tabs.count()):
        tabs.nth(i).click()
        page.wait_for_load_state("load")
        expect(page.locator("body")).not_to_contain_text("Application error")


def test_qa_new_suite_form(page: Page):
    open_page(page, QA)
    page.get_by_role("button", name="New suite").click()
    expect(page.get_by_label("Suite name")).to_be_visible()
    expect(page.get_by_role("button", name="Create")).to_be_visible()


def test_qa_filters(page: Page):
    open_page(page, QA)
    page.get_by_label("Filter tables").first.fill("zz")
    page.get_by_label("Filter tables").first.fill("")


def test_qa_describe_test_assistant(page: Page):
    open_page(page, QA)
    box = page.get_by_label("Describe a test")
    if box.count() == 0:
        link = page.locator('a[href*="/qa/tables/"], a[href*="table="]').first
        if link.count() == 0:
            pytest.skip("no QA tables")
        link.click()
        box = page.get_by_label("Describe a test")
    expect(box).to_have_attribute("placeholder", "Describe what to check in plain English")
    expect(page.get_by_role("button", name="Generate")).to_be_visible()


@pytest.mark.writes
def test_journey_qa_create_and_delete_suite(page: Page):
    name = unique("UIAUTO suite")
    open_page(page, QA)
    page.get_by_role("button", name="New suite").click()
    page.get_by_label("Suite name").fill(name)
    page.get_by_role("button", name="Create").click()
    expect(page.get_by_text(name).first).to_be_visible()
    page.get_by_text(name).first.click()
    page.get_by_role("button", name="Delete").click()
    confirm = page.get_by_role("button", name="Delete").last
    if confirm.is_visible():
        confirm.click()


# ---------------------------------------------------------------- DQ store
DQ = "/os/design/dq-store"


def test_dq_add_check_modal(page: Page):
    open_page(page, DQ, "Attribute checks catalog")
    page.get_by_test_id("add-check").click()
    dlg = page.get_by_role("dialog", name="Add a data quality check")
    expect(dlg).to_be_visible()
    expect(dlg.get_by_test_id("save-check")).to_be_visible()
    page.keyboard.press("Escape")


def test_dq_filter_checks(page: Page):
    open_page(page, DQ)
    page.get_by_label("Filter checks").fill("zz_none")
    page.get_by_label("Filter checks").fill("")


def test_dq_suite_tabs_and_controls(page: Page):
    open_page(page, DQ + "/suite")
    tabs = page.locator('[data-testid^="suite-tab-"]')
    for i in range(tabs.count()):
        tabs.nth(i).click()
    for tid in ["suite-open-draft", "suite-compare", "suite-add-check", "suite-add-first", "suite-submit"]:
        if page.get_by_test_id(tid).count():
            expect(page.get_by_test_id(tid)).to_be_visible()


@pytest.mark.writes
def test_journey_dq_add_check_to_draft_and_submit(page: Page):
    open_page(page, DQ)
    page.get_by_test_id("add-check").click()
    dlg = page.get_by_role("dialog", name="Add a data quality check")
    for sel in dlg.locator("select").all():
        if sel.locator("option").count() > 1:
            sel.select_option(index=1)
    for f in dlg.locator("input[type=text], textarea").all():
        if not f.input_value():
            f.fill("UI_AUTOTEST")
    dlg.get_by_test_id("save-check").click()
    open_page(page, DQ + "/suite")
    if page.get_by_test_id("suite-submit").count():
        page.get_by_label("Change note").fill("UI-AUTOTEST")
        page.get_by_test_id("suite-submit").click()


# ---------------------------------------------------------------- people & admin
def test_people_persona_toggles(page: Page):
    open_page(page, "/os/people", "People and personas")
    row = page.locator('[data-testid^="person-"]').first
    toggles = row.locator("button[aria-pressed]")
    if toggles.count() == 0:
        pytest.skip("no persona toggles")
    t = toggles.first
    before = t.get_attribute("aria-pressed")
    t.click()
    expect(t).not_to_have_attribute("aria-pressed", before)
    t.click()
    expect(t).to_have_attribute("aria-pressed", before)


def test_people_add_person_form(page: Page):
    open_page(page, "/os/people")
    add = page.get_by_test_id("add-person")
    expect(add.locator("#new-user")).to_have_attribute("placeholder", "e.g. JDOE")


def test_people_preview_as_persona(page: Page):
    open_page(page, "/os/people")
    prev = page.locator('[data-testid^="preview-"]').first
    if prev.count() == 0:
        pytest.skip("no preview buttons")
    prev.click()
    expect(page).to_have_url(re.compile(r"/os/?(\?|$)"))
    banner = page.get_by_role("status").filter(has_text="Previewing as")
    expect(banner).to_be_visible()
    banner.get_by_role("button", name="Stop preview").click()


@pytest.mark.writes
def test_journey_people_save_personas(page: Page):
    open_page(page, "/os/people")
    row = page.locator('[data-testid^="person-"]').first
    t = row.locator("button[aria-pressed]").first
    t.click()
    row.get_by_role("button", name="Save personas").click()
    expect(page.get_by_text(re.compile("^Saved: "))).to_be_visible()
    t.click()
    row.get_by_role("button", name="Save personas").click()


ADMIN_SECTIONS = ["overview", "models", "access", "integrations", "skills", "rules", "standards",
                  "environments", "deploy"]


@pytest.mark.parametrize("section", ADMIN_SECTIONS)
def test_admin_sections(page: Page, section):
    open_page(page, f"/os/admin/settings?section={section}", "Admin")
    if section != "overview":
        expect(page.get_by_role("button", name=re.compile(
            "Save|Add|Connect|Test|Deploy|Preview")).first.or_(page.locator("form").first)).to_be_visible()


def test_admin_environment_preview(page: Page):
    open_page(page, "/os/admin/settings?section=environments")
    p = page.get_by_test_id("preview-environment")
    if p.count():
        p.click()
        expect(page.locator("body")).not_to_contain_text("Application error")


# ---------------------------------------------------------------- lineage & code
def test_lineage_trace_journey(page: Page):
    open_page(page, "/os/build/lineage", "Lineage")
    box = page.get_by_label("Find a table")
    expect(box).to_have_attribute("placeholder", re.compile("DATABASE.SCHEMA.TABLE"))
    box.fill("CUSTOMER")
    page.get_by_role("button", name=re.compile("^Trac")).click()
    expect(page.get_by_role("button", name="Trace")).to_be_visible(timeout=120_000)


@pytest.mark.parametrize("direction", ["Where it comes from", "Both", "Where it goes"])
def test_lineage_direction(page: Page, direction):
    open_page(page, "/os/build/lineage")
    b = page.get_by_role("group", name="Direction").get_by_role("button", name=direction)
    b.click()
    expect(b).to_have_attribute("aria-pressed", "true")


def test_code_search_and_kinds(page: Page):
    open_page(page, "/os/build/code", "Code")
    box = page.get_by_label("Search code")
    box.fill("customer")
    box.press("Enter")
    page.wait_for_load_state("load")
    kinds = page.locator("button[aria-pressed]")
    for i in range(min(kinds.count(), 8)):
        kinds.nth(i).click()
    clear = page.get_by_role("button", name="Clear search")
    if clear.count():
        clear.click()
        expect(page.get_by_label("Search code")).to_have_value("")
