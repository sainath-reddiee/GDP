"""QA store (packs, import, drawer, environments, git, run tests), DQ store (tables, seed, schedules,
generated), DQ suite (diff, decision, versions, publish, export), source control."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import open_page, unique

QAS = "/os/design/qa-store"
DQ = "/os/design/dq-store"


# ================================================================ QA store
def test_qa_store_search_and_status(page: Page):
    open_page(page, QAS, "Domain test packages")
    page.get_by_label("Search packs").fill("zz_none")
    page.get_by_label("Search packs").fill("")
    for s in ["All", "Approved", "Drafts", "Pending", "Retired"]:
        page.get_by_role("radio", name=s).or_(page.get_by_role("button", name=s, exact=True)).first.click()


def test_qa_store_import_flow_ui(page: Page):
    open_page(page, QAS)
    page.get_by_test_id("qa-import").click()
    expect(page.get_by_test_id("qa-import-pick")).to_be_visible()
    page.locator("input[type=file]").first.set_input_files(
        files=[{"name": "t.sql", "mimeType": "text/plain", "buffer": b"select 1 as ok;"}])
    expect(page.get_by_text("t.sql")).to_be_visible()
    page.keyboard.press("Escape")


def test_qa_store_new_pack_modal(page: Page):
    open_page(page, QAS)
    page.get_by_role("button", name="New pack").click()
    expect(page.locator("#np-name")).to_have_attribute("placeholder", "Referential integrity")
    expect(page.get_by_role("button", name="Create draft")).to_be_visible()
    page.keyboard.press("Escape")


def _pack(page):
    open_page(page, QAS)
    card = page.locator('[data-testid^="pack-"]:not([data-testid^="pack-git"]):not([data-testid^="pack-conflict"])').first
    if card.count() == 0:
        pytest.skip("no QA packs")
    card.click()
    return card


@pytest.mark.parametrize("tab", ["Queries", "Versions", "Runs", "Git"])
def test_pack_drawer_tabs(page: Page, tab):
    _pack(page)
    page.get_by_role("tab", name=re.compile(f"^{tab}")).or_(
        page.get_by_role("button", name=re.compile(f"^{tab}"))).first.click()
    expect(page.locator("body")).not_to_contain_text("Application error")


def test_pack_drawer_controls(page: Page):
    _pack(page)
    expect(page.get_by_test_id("save-pack").or_(page.get_by_test_id("approve-pack")).first).to_be_visible()
    tq = page.get_by_test_id("test-query")
    if tq.count():
        expect(tq.first).to_be_visible()
    page.get_by_role("button", name="Add query").or_(page.get_by_text("Compare")).first.is_visible()


def test_pack_environment_run_and_dry_run(page: Page):
    _pack(page)
    page.get_by_role("tab", name="Runs").or_(page.get_by_role("button", name="Runs")).first.click()
    env = page.get_by_test_id("environment-run")
    if env.count() == 0:
        pytest.skip("no environment run panel")
    for lbl in ["Environment", "Version to run"]:
        s = env.get_by_label(lbl)
        if s.count() and s.locator("option").count():
            s.select_option(index=0)
    env.get_by_test_id("dry-run-pack").click()
    expect(page.get_by_test_id("dry-run")).to_be_visible(timeout=120_000)


def test_pack_git_panel(page: Page):
    _pack(page)
    page.get_by_role("tab", name="Git").or_(page.get_by_role("button", name="Git")).first.click()
    expect(page.get_by_test_id("pack-git").or_(page.get_by_test_id("pack-conflict")).first).to_be_visible()
    if page.get_by_test_id("pack-conflict").count():
        expect(page.get_by_test_id("take-repo")).to_be_visible()
        expect(page.get_by_test_id("keep-app")).to_be_visible()
    else:
        expect(page.get_by_test_id("publish-pack")).to_be_visible()


def test_pack_drawer_close_resets_url(page: Page):
    _pack(page)
    page.keyboard.press("Escape")
    expect(page).to_have_url(re.compile(r"/os/design/qa-store/?$"))


@pytest.mark.writes
def test_journey_qa_pack_create_save_submit_sendback(page: Page):
    name = unique("UIAUTO pack")
    open_page(page, QAS)
    page.get_by_role("button", name="New pack").click()
    page.locator("#np-name").fill(name)
    page.get_by_role("button", name="Create draft").click()
    expect(page.get_by_text(f"Created {name} as a draft")).to_be_visible()
    page.locator(f'[data-testid="pack-{name}"]').click()
    page.get_by_role("button", name="Add query").click()
    page.locator("textarea").last.fill("select 1 as ok")
    page.get_by_label("Change note").fill("UI-AUTOTEST")
    page.get_by_test_id("save-pack").click()
    expect(page.get_by_text(re.compile(r"Saved as draft v\d+"))).to_be_visible()
    page.get_by_test_id("test-query").first.click()
    page.get_by_role("button", name="Submit for approval").click()
    expect(page.get_by_text(re.compile(r"v\d+ submitted for approval"))).to_be_visible()
    page.get_by_role("button", name="Send back").click()
    expect(page.get_by_text("Sent back to draft")).to_be_visible()


@pytest.mark.writes
def test_journey_qa_import_sql(page: Page):
    open_page(page, QAS)
    page.get_by_test_id("qa-import").click()
    page.locator("input[type=file]").first.set_input_files(
        files=[{"name": "uiauto.sql", "mimeType": "text/plain", "buffer": b"-- UI-AUTOTEST\nselect 1 as ok;"}])
    page.get_by_role("button", name="Import", exact=True).click()
    done = page.get_by_test_id("qa-import-done")
    expect(done).to_be_visible()
    done.get_by_role("button", name="Close").click()


# ---------------------------------------------------------------- run tests tab
def _run_tests(page):
    open_page(page, QAS + "?view=run-tests")
    rt = page.get_by_test_id("run-tests")
    if rt.count() == 0:
        tab = page.locator('[data-testid^="store-tab-"]', has_text=re.compile("(?i)run tests"))
        if tab.count():
            tab.first.click()
        rt = page.get_by_test_id("run-tests")
    if rt.count() == 0:
        pytest.skip("run tests tab not reachable")
    return rt


@pytest.mark.parametrize("toggle", ["all", "failing", "passing", "never run"])
def test_run_tests_filters(page: Page, toggle):
    rt = _run_tests(page)
    rt.get_by_placeholder("Filter by test, table or run").fill("")
    rt.get_by_role("button", name=re.compile(f"^{toggle}", re.I)).first.click()


def test_run_tests_promote_modal(page: Page):
    rt = _run_tests(page)
    boxes = rt.get_by_role("checkbox", name=re.compile("^Select "))
    if boxes.count() == 0:
        pytest.skip("no run tests")
    boxes.first.check()
    rt.get_by_test_id("promote-tests").click()
    expect(page.get_by_test_id("confirm-promote")).to_be_visible()
    page.keyboard.press("Escape")


def test_run_test_drawer_and_history(page: Page):
    rt = _run_tests(page)
    t = rt.locator('[data-testid^="test-"]:not([data-testid="test-history"])').first
    if t.count() == 0:
        pytest.skip("no tests")
    t.click()
    h = page.locator('[data-testid^="history-"]').first
    if h.count():
        h.click()
        expect(page.get_by_test_id("test-history")).to_be_visible()
        expect(page.locator('[data-testid^="restore-v"]').first.or_(page.get_by_test_id("test-history"))).to_be_visible()


# ================================================================ DQ store
def test_dq_store_toolbar(page: Page):
    open_page(page, DQ, "Attribute checks catalog")
    expect(page.get_by_label("Filter checks")).to_have_attribute("placeholder", "Filter tables, columns or SodaCL")
    st = page.get_by_label("Status", exact=True)
    for i in range(min(st.locator("option").count(), 4)):
        page.get_by_label("Status", exact=True).select_option(index=i)
        expect(page).to_have_url(re.compile(r"/os/design/dq-store"))


def test_dq_add_check_modal_fields(page: Page):
    open_page(page, DQ)
    page.get_by_test_id("add-check").click()
    dlg = page.get_by_role("dialog", name="Add a data quality check")
    expect(dlg.locator("#ck-dsl")).to_have_attribute("placeholder", "missing_count(EMAIL) = 0")
    sev = dlg.locator("#ck-sev")
    for o in ["Warn (alerts)", "Fail (blocks)"]:
        sev.select_option(label=o)
    for r in ["One line SodaCL", "Guided editor"]:
        dlg.get_by_label(r).check()
    picker = dlg.get_by_test_id("table-picker")
    expect(picker).to_be_visible()
    for m in ["registered", "browse", "type"]:
        b = picker.get_by_test_id(f"table-picker-{m}")
        if b.count():
            b.click()
    page.keyboard.press("Escape")


def test_dq_table_section_controls(page: Page):
    open_page(page, DQ)
    sec = page.locator('[data-testid^="dq-table-"]').first
    if sec.count() == 0:
        pytest.skip("no DQ tables")
    expect(sec.get_by_test_id("run-table")).to_be_visible()
    expect(sec.get_by_role("button", name=re.compile("^Add a check to "))).to_be_visible()
    menu = sec.get_by_role("button", name="Check actions").first
    if menu.count():
        menu.click()
        for item in ["Edit", "Pause|Resume", "Retire"]:
            expect(page.get_by_role("menuitem", name=re.compile(f"^({item})$")).first).to_be_visible()
        page.keyboard.press("Escape")


def test_dq_schedule_modal(page: Page):
    open_page(page, DQ)
    sec = page.locator('[data-testid^="dq-table-"]').first
    if sec.count() == 0:
        pytest.skip("no DQ tables")
    sec.get_by_role("button", name=re.compile("Not scheduled|Daily|Weekly|Every|Hourly")).first.click()
    expect(page.get_by_role("button", name="Save schedule")).to_be_visible()
    page.keyboard.press("Escape")


@pytest.mark.parametrize("mode", ["seed-table", "seed-run", "seed-requirement", "seed-file"])
def test_dq_seed_modes(page: Page, mode):
    open_page(page, DQ)
    page.get_by_role("button", name="Seed checks").first.click()
    page.get_by_test_id(mode).first.click()
    if mode == "seed-run":
        expect(page.locator("#seed-v")).to_be_visible()
    if mode == "seed-requirement":
        expect(page.locator("#seed-req")).to_be_visible()
    expect(page.get_by_role("button", name=re.compile("^(Seed|Generate|Import) checks$")).last).to_be_visible()
    page.keyboard.press("Escape")


def test_dq_suite_strip_links(page: Page):
    open_page(page, DQ)
    strip = page.locator('[data-testid^="suite-"]').first
    if strip.count() == 0:
        pytest.skip("no suites")
    link = strip.get_by_role("link", name=re.compile("^(Review|Open draft|Versions)$")).first
    link.click()
    expect(page).to_have_url(re.compile(r"/os/design/dq-store/suite\?table="))


@pytest.mark.writes
def test_journey_dq_run_table_now(page: Page):
    open_page(page, DQ)
    sec = page.locator('[data-testid^="dq-table-"]').first
    if sec.count() == 0:
        pytest.skip("no DQ tables")
    sec.get_by_test_id("run-table").click()
    expect(page.get_by_text(re.compile(r"\d+ of \d+ passed"))).to_be_visible(timeout=600_000)


@pytest.mark.writes
def test_journey_dq_generate_from_requirement(page: Page):
    open_page(page, DQ)
    page.get_by_role("button", name="Seed checks").first.click()
    page.get_by_test_id("seed-requirement").first.click()
    page.locator("#seed-req").fill("UI-AUTOTEST: EMAIL must never be null")
    page.get_by_role("button", name="Generate checks").last.click()
    summ = page.get_by_test_id("generated-summary")
    expect(summ).to_be_visible(timeout=300_000)
    summ.get_by_role("button", name="Done").click()


# ---------------------------------------------------------------- DQ suite
def _suite(page, tab="review"):
    open_page(page, DQ)
    strip = page.locator('[data-testid^="suite-"]').first
    if strip.count() == 0:
        pytest.skip("no suites")
    href = strip.get_by_role("link").first.get_attribute("href")
    table = re.search(r"table=([^&]+)", href).group(1)
    open_page(page, f"{DQ}/suite?table={table}&tab={tab}")


@pytest.mark.parametrize("tab", ["review", "versions", "publish", "export"])
def test_suite_tabs(page: Page, tab):
    _suite(page)
    page.get_by_test_id(f"suite-tab-{tab}").click()
    expect(page).to_have_url(re.compile(f"tab={tab}"))


def test_suite_review_panels(page: Page):
    _suite(page, "review")
    expect(page.get_by_test_id("suite-decision").or_(page.get_by_test_id("suite-open-draft")).first).to_be_visible()
    if page.get_by_test_id("suite-diff").count():
        expect(page.get_by_test_id("suite-diff")).to_be_visible()
    if page.get_by_test_id("suite-model").count():
        expect(page.get_by_test_id("suite-model")).to_be_visible()
    row = page.locator('[data-testid^="suite-check-"]').first
    if row.count():
        for a in ["Edit check", "Remove check"]:
            b = row.get_by_role("button", name=a)
            if b.count():
                expect(b).to_be_visible()


def test_suite_approve_needs_ack_for_loosened(page: Page):
    _suite(page, "review")
    ack = page.get_by_test_id("suite-ack")
    if ack.count() == 0:
        pytest.skip("nothing loosened / pending")
    expect(page.get_by_test_id("suite-approve")).to_be_disabled()
    ack.check()
    expect(page.get_by_test_id("suite-approve")).to_be_enabled()


def test_suite_versions_table(page: Page):
    _suite(page, "versions")
    expect(page.get_by_test_id("suite-versions")).to_be_visible()
    v = page.get_by_label("Version", exact=True)
    if v.count() and v.locator("option").count() > 1:
        v.select_option(index=1)
        expect(page).to_have_url(re.compile("version="))


def test_suite_publish_panel(page: Page):
    _suite(page, "publish")
    pub = page.get_by_test_id("suite-publish")
    expect(pub).to_be_visible()
    expect(pub.locator("#dq-repo")).to_be_visible()
    expect(pub.get_by_test_id("suite-publish-version")).to_be_visible()
    sc = page.get_by_test_id("soda-cloud")
    if sc.count():
        sc.get_by_text("Publish to Soda Cloud").click()


@pytest.mark.parametrize("fmt", ["SodaCL", "Great Expectations", "Portable (any environment)"])
def test_suite_export_formats(page: Page, fmt):
    _suite(page, "export")
    exp = page.get_by_test_id("suite-export")
    exp.get_by_label(fmt).check()
    dl = exp.get_by_role("link", name="Download")
    r = page.request.get(dl.get_attribute("href"))
    assert r.status < 400


@pytest.mark.writes
def test_journey_suite_open_draft_submit_withdraw(page: Page):
    _suite(page, "review")
    if page.get_by_test_id("suite-open-draft").count():
        page.get_by_test_id("suite-open-draft").click()
        expect(page.get_by_text("Draft opened")).to_be_visible()
    page.get_by_test_id("suite-decision").get_by_label("Change note").fill("UI-AUTOTEST")
    page.get_by_test_id("suite-submit").click()
    expect(page.get_by_text(re.compile(r"v\d+ sent for review"))).to_be_visible()
    page.get_by_role("button", name="Withdraw to keep editing").click()
    expect(page.get_by_text(re.compile(r"v\d+ back to draft"))).to_be_visible()


# ================================================================ source control
SC = "/os/release/source-control"


def test_source_control_list_or_empty(page: Page):
    open_page(page, SC, "Source control")
    expect(page.get_by_test_id("pr-list").or_(page.get_by_test_id("pr-empty")).first).to_be_visible()


@pytest.mark.parametrize("label,param", [("State", "state"), ("Domain", "domain")])
def test_source_control_filters(page: Page, label, param):
    open_page(page, SC)
    s = page.get_by_label(label, exact=True)
    if s.locator("option").count() > 1:
        s.select_option(index=1)
        expect(page).to_have_url(re.compile(f"{param}="))


def test_source_control_kind_tabs_and_search(page: Page):
    open_page(page, SC)
    page.get_by_text("All assets", exact=True).first.click()
    page.get_by_label("Search pull requests").fill("zz_none")
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile("q=zz_none"))
    expect(page.get_by_test_id("pr-empty")).to_be_visible()


@pytest.mark.writes
def test_source_control_check_all(page: Page):
    open_page(page, SC)
    page.get_by_role("button", name="Check all now").click()
    expect(page.get_by_text(re.compile(r"\d+ pull requests? checked"))).to_be_visible(timeout=180_000)
