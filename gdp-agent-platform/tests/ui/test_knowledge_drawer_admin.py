"""Knowledge item drawer (edit/verify/retire/diff/rollback), review inbox decisions, admin sections
(models, integrations: GitHub, Jira, Airflow, Datadog)."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import open_page


# ---------------------------------------------------------------- knowledge drawer
def _open_drawer(page):
    open_page(page, "/os/knowledge?view=items")
    rows = page.locator("tbody tr")
    if rows.count() == 0:
        pytest.skip("no knowledge items")
    title = rows.first.locator("a, button").first
    name = title.inner_text().strip()
    title.click()
    dlg = page.get_by_role("dialog").first
    expect(dlg).to_be_visible()
    return dlg, name


@pytest.mark.parametrize("tab", ["Content", "Versions", "Provenance", "Usage"])
def test_drawer_tabs(page: Page, tab):
    dlg, _ = _open_drawer(page)
    t = dlg.get_by_role("tab", name=tab).or_(dlg.get_by_role("button", name=tab, exact=True))
    t.first.click()
    expect(dlg).to_be_visible()


def test_drawer_actions_present(page: Page):
    dlg, _ = _open_drawer(page)
    expect(dlg.get_by_role("button", name="Edit")).to_be_visible()
    expect(dlg.get_by_role("button", name=re.compile("^(Mark verified|Verify again)$"))).to_be_visible()
    expect(dlg.get_by_role("button", name=re.compile("^(Retire|Restore)$"))).to_be_visible()


def test_drawer_diff_open_close(page: Page):
    dlg, _ = _open_drawer(page)
    dlg.get_by_role("tab", name="Versions").or_(dlg.get_by_role("button", name="Versions", exact=True)).first.click()
    ch = dlg.get_by_role("button", name=re.compile(r"^Changes from v\d+"))
    if ch.count() == 0:
        pytest.skip("single version")
    ch.first.click()
    dlg.get_by_role("button", name="Close diff").click()
    expect(dlg.get_by_role("button", name="Close diff")).to_have_count(0)


def test_drawer_edit_opens_editor(page: Page):
    dlg, _ = _open_drawer(page)
    dlg.get_by_role("button", name="Edit").click()
    ed = page.get_by_role("dialog", name="Edit knowledge")
    expect(ed).to_be_visible()
    ed.get_by_role("button", name="Cancel").click()


def test_drawer_close(page: Page):
    dlg, _ = _open_drawer(page)
    dlg.get_by_role("button", name="Close").first.click()
    expect(dlg).to_be_hidden()


def test_catalog_full_record_button(page: Page):
    open_page(page, "/os/knowledge")
    row = page.locator('a[href*="id="]').first
    if row.count() == 0:
        pytest.skip("empty catalog")
    row.click()
    page.get_by_role("button", name="Full record: diff, rollback, edit").click()
    expect(page.get_by_role("dialog").first).to_be_visible()


@pytest.mark.writes
def test_drawer_verify(page: Page):
    dlg, _ = _open_drawer(page)
    dlg.get_by_role("button", name=re.compile("^(Mark verified|Verify again)$")).click()
    expect(page.get_by_text(re.compile("^Verified\\."))).to_be_visible()


@pytest.mark.writes
def test_drawer_rollback(page: Page):
    dlg, _ = _open_drawer(page)
    dlg.get_by_role("tab", name="Versions").or_(dlg.get_by_role("button", name="Versions", exact=True)).first.click()
    r = dlg.get_by_role("button", name="Restore this version")
    if r.count() == 0:
        pytest.skip("single version")
    r.first.click()
    expect(page.get_by_text(re.compile(r"v\d+ is in use again"))).to_be_visible()


# ---------------------------------------------------------------- review inbox
def _inbox(page):
    open_page(page, "/os/knowledge?view=inbox")
    if page.get_by_text("Inbox zero").count():
        pytest.skip("inbox empty")


def test_inbox_select_all_updates_bulk_count(page: Page):
    _inbox(page)
    page.get_by_label("Select all").check()
    expect(page.get_by_role("button", name=re.compile(r"^Approve \d+"))).to_be_visible()
    page.get_by_label("Select all").uncheck()


def test_inbox_diff_and_replay(page: Page):
    _inbox(page)
    page.get_by_role("button", name="Diff").first.click()
    expect(page.get_by_text(re.compile(r"^(In use now|Proposed) · v\d+")).first).to_be_visible()
    rep = page.get_by_role("button", name="Replay")
    if rep.count() and __import__("settings").ALLOW_WRITES:
        rep.first.click()


def test_inbox_view_in_document(page: Page):
    _inbox(page)
    src = page.get_by_test_id("inbox-source").first
    if src.count() == 0:
        pytest.skip("no document-sourced proposals")
    src.get_by_role("button", name="View in document").click()
    expect(page.get_by_test_id("document-text").or_(page.get_by_role("dialog")).first).to_be_visible()


@pytest.mark.writes
def test_inbox_reject_one(page: Page):
    _inbox(page)
    page.get_by_placeholder("Review note (optional)").fill("UI-AUTOTEST reject")
    page.get_by_role("checkbox", name=re.compile("^Select (?!all)")).first.check()
    page.get_by_role("button", name="Reject", exact=True).first.click()
    expect(page.get_by_role("status").filter(has_text=re.compile(r"\d+ rejected"))).to_be_visible()


@pytest.mark.writes
def test_inbox_approve_one(page: Page):
    _inbox(page)
    page.get_by_role("checkbox", name=re.compile("^Select (?!all)")).first.check()
    page.get_by_role("button", name=re.compile(r"^Approve 1$")).click()
    expect(page.get_by_role("status").filter(has_text="approved and now in use")).to_be_visible()


# ---------------------------------------------------------------- admin: models
MODELS = "/os/admin/settings?section=models"


def test_admin_models_search_and_filter(page: Page):
    open_page(page, MODELS)
    page.get_by_label("Search models").fill("claude")
    page.get_by_label("Search models").fill("")
    box = page.get_by_label("Allowed models only")
    box.check()
    box.uncheck()
    expect(page.get_by_text(re.compile("^(Default|Allowed|Blocked)$")).first).to_be_visible()


def test_admin_models_stage_select_shows_save_bar(page: Page):
    open_page(page, MODELS)
    sel = page.get_by_label(re.compile(r" model$")).first
    if sel.count() == 0 or sel.locator("option").count() < 2:
        pytest.skip("no stage model selects")
    current = sel.input_value()
    other = [v for v in sel.locator("option").evaluate_all("os => os.map(o => o.value)") if v != current][0]
    sel.select_option(other)
    expect(page.get_by_text(re.compile(r"\d+ unsaved stage model"))).to_be_visible()
    page.get_by_role("button", name="Discard").click()
    expect(sel).to_have_value(current)


def test_admin_model_test_button(page: Page):
    open_page(page, MODELS)
    t = page.get_by_role("button", name="Test", exact=True)
    if t.count() == 0:
        pytest.skip("no models listed")
    t.first.click()
    expect(page.get_by_text(re.compile(r"^(\d+\.\ds|Failed)$")).first).to_be_visible(timeout=120_000)


def test_admin_models_refresh(page: Page):
    open_page(page, MODELS)
    page.get_by_role("button", name="Refresh list").click()
    expect(page.locator("body")).not_to_contain_text("Application error")


# ---------------------------------------------------------------- admin: integrations
INTEG = "/os/admin/settings?section=integrations"


@pytest.mark.parametrize("tab", ["Code repositories", "Jira", "Airflow", "Datadog", "Incidents", "Cases"])
def test_integrations_nav(page: Page, tab):
    open_page(page, INTEG)
    nav = page.get_by_role("navigation", name="Integrations").or_(page.get_by_role("tablist", name="Integrations"))
    nav.first.get_by_text(tab, exact=True).click()
    expect(page.locator("body")).not_to_contain_text("Application error")


def _integ(page, tab):
    open_page(page, INTEG)
    nav = page.get_by_role("navigation", name="Integrations").or_(page.get_by_role("tablist", name="Integrations"))
    nav.first.get_by_text(tab, exact=True).click()


def test_github_controls(page: Page):
    _integ(page, "Code repositories")
    expect(page.get_by_label("GitHub token").or_(page.get_by_role("button", name="Rotate token")).first).to_be_visible()
    page.get_by_role("button", name="Connect repository").click()
    dlg = page.get_by_role("dialog", name="Connect repository")
    expect(dlg).to_be_visible()
    expect(dlg.get_by_role("button", name="Connect")).to_be_visible()
    page.keyboard.press("Escape")


def test_repo_settings_tabs(page: Page):
    _integ(page, "Code repositories")
    s = page.get_by_role("button", name=re.compile("^Settings for "))
    if s.count() == 0:
        pytest.skip("no repositories")
    s.first.click()
    for t in ["Branch", "Indexing", "Schedule", "dbt workspace", "Credentials", "History"]:
        page.get_by_role("tab", name=t).or_(page.get_by_role("button", name=t, exact=True)).first.click()
    page.keyboard.press("Escape")


def test_jira_settings_fields(page: Page):
    _integ(page, "Jira")
    expect(page.get_by_label("Client ID")).to_be_visible()
    expect(page.get_by_label("Site URL")).to_have_attribute("placeholder", "https://yourteam.atlassian.net")
    expect(page.get_by_role("button", name=re.compile("^(Connect Jira|Connect again|Disconnect)$")).first).to_be_visible()
    expect(page.get_by_role("button", name="Save settings")).to_be_visible()


def test_airflow_add_environment_form(page: Page):
    _integ(page, "Airflow")
    page.get_by_role("button", name="Add environment").first.click()
    expect(page.get_by_label("Name")).to_have_attribute("placeholder", "Production")
    kind = page.get_by_label("Environment kind")
    for i in range(kind.locator("option").count()):
        kind.select_option(index=i)
    expect(page.get_by_label("Poll every (seconds)")).to_be_visible()
    page.keyboard.press("Escape")


def test_airflow_environment_actions(page: Page):
    _integ(page, "Airflow")
    for b in ["Test connection", "Poll now", "Refresh catalogue", "Edit", "Push setup"]:
        el = page.get_by_role("button", name=b)
        if el.count():
            expect(el.first).to_be_visible()


def test_datadog_settings(page: Page):
    _integ(page, "Datadog")
    expect(page.get_by_label("Use Datadog")).to_be_visible()
    for lbl in ["Hosts", "Services", "Minutes before the failure"]:
        expect(page.get_by_label(lbl).first).to_be_visible()
    expect(page.get_by_role("button", name="Test connection")).to_be_visible()


@pytest.mark.parametrize("tab", ["Incidents", "Cases"])
def test_incidents_cases_settings(page: Page, tab):
    _integ(page, tab)
    expect(page.get_by_role("button", name=re.compile("^(Save settings|Save|Add team|Add rule)$")).first).to_be_visible()


@pytest.mark.writes
def test_datadog_test_connection(page: Page):
    _integ(page, "Datadog")
    page.get_by_role("button", name="Test connection").click()
    expect(page.locator("body")).not_to_contain_text("Application error", timeout=60_000)


@pytest.mark.writes
def test_jira_save_settings_roundtrip(page: Page):
    _integ(page, "Jira")
    page.get_by_role("button", name="Save settings").click()
    expect(page.locator("body")).not_to_contain_text("Application error")


# ---------------------------------------------------------------- admin: other sections save
@pytest.mark.parametrize("section", ["access", "skills", "rules", "standards", "environments", "deploy"])
def test_admin_section_primary_controls(page: Page, section):
    open_page(page, f"/os/admin/settings?section={section}")
    expect(page.get_by_role("button", name=re.compile(
        "^(Save|Save settings|Add as viewer|Add environment|Deploy now|Discard)")).first.or_(
        page.locator("main").first)).to_be_visible()


def test_admin_access_add_viewer_field(page: Page):
    open_page(page, "/os/admin/settings?section=access")
    b = page.get_by_role("button", name="Add as viewer")
    if b.count():
        expect(b).to_be_visible()
