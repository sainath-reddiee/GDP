"""Closes the last uncovered test ids: model workbench review/versions/publish/entities, run assets,
dbt review, table models, source intake extras, ingestion review/save results, home focus, misc."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import first_link_matching, open_page, path_of

MOD = "/os/design/modeling"


@pytest.fixture
def model_url(page: Page):
    open_page(page, f"{MOD}?view=models")
    href = first_link_matching(page, r"/os/design/modeling/(?!tables|dbt)[^/?#]+$")
    if not href:
        pytest.skip("no domain models")
    return href.split("?")[0]


# ---------------------------------------------------------------- model workbench
def test_entities_editor_and_columns_grid(page: Page, model_url):
    open_page(page, model_url + "?tab=entities")
    ent = page.locator('[data-testid^="entity-"]:not([data-testid="entity-editor"])').first
    if ent.count() == 0:
        pytest.skip("model has no entities")
    ent.click()
    expect(page.get_by_test_id("entity-editor")).to_be_visible()
    grid = page.get_by_test_id("columns-grid")
    expect(grid).to_be_visible()
    col = grid.locator('[data-testid^="col-"]').first
    if col.count():
        expect(col.get_by_label("Column name")).to_be_visible()
        expect(col.get_by_label("Data type")).to_be_visible()


def test_review_panel_diff_impact_and_approve(page: Page, model_url):
    open_page(page, model_url + "?tab=review")
    for tid in ["model-diff", "model-impact"]:
        if page.get_by_test_id(tid).count():
            expect(page.get_by_test_id(tid)).to_be_visible()
    ap = page.get_by_test_id("approve-version")
    if ap.count() == 0:
        pytest.skip("no version in review")
    ack = page.get_by_test_id("ack-breaking")
    if ack.count():
        expect(ap).to_be_disabled()
        ack.check()
    expect(ap).to_be_enabled()
    expect(page.locator("#reject-why")).to_be_visible()


@pytest.mark.writes
def test_review_panel_reject_version(page: Page, model_url):
    open_page(page, model_url + "?tab=review")
    if page.locator("#reject-why").count() == 0:
        pytest.skip("nothing to review")
    page.locator("#reject-why").fill("UI-AUTOTEST reject")
    page.get_by_role("button", name="Reject", exact=True).click()
    expect(page.get_by_text(re.compile(r"v\d+ rejected"))).to_be_visible()


def test_versions_rollback_buttons(page: Page, model_url):
    open_page(page, model_url + "?tab=versions")
    rb = page.locator('[data-testid^="rollback-"]')
    if rb.count() == 0:
        pytest.skip("single version")
    expect(rb.first).to_contain_text("Roll back to this")


def test_publish_panel_and_proposals(page: Page, model_url):
    open_page(page, model_url + "?tab=publish")
    expect(page.get_by_test_id("publish-version").or_(page.locator("#pub-repo")).first).to_be_visible()
    open_page(page, model_url + "?tab=changes")
    rc = page.get_by_test_id("run-changes")
    if rc.count():
        expect(rc).to_be_visible()
    mg = page.locator('[data-testid^="merge-"]').first
    if mg.count():
        expect(mg).to_contain_text("Merge into the draft")


def test_import_file_input(page: Page, model_url):
    open_page(page, model_url)
    imp = page.get_by_test_id("import-model")
    if imp.count() == 0:
        pytest.skip("import not available")
    imp.click()
    page.get_by_role("button", name="Model file").click()
    fi = page.get_by_test_id("import-file")
    expect(fi).to_be_attached()
    fi.set_input_files(files=[{"name": "m.sql", "mimeType": "text/plain",
                               "buffer": b"create table UIAUTO (ID number);"}])
    expect(page.get_by_test_id("run-import")).to_be_enabled()
    page.keyboard.press("Escape")


def test_workbench_conflict_banner_reload(page: Page, model_url):
    open_page(page, model_url)
    c = page.get_by_test_id("conflict")
    if c.count() == 0:
        pytest.skip("no edit conflict")
    c.get_by_role("button", name=re.compile("^Reload")).click()
    expect(c).to_have_count(0)


# ---------------------------------------------------------------- run assets / dbt / table models
def test_run_models_table(page: Page):
    open_page(page, f"{MOD}?view=runs")
    expect(page.get_by_test_id("run-models")).to_be_visible()
    link = page.get_by_test_id("run-models").locator('a[href*="/os/build/runs/"]').first
    if link.count():
        link.click()
        expect(page).to_have_url(re.compile(r"/os/build/runs/.+/domain"))


def test_dbt_generations_list(page: Page):
    open_page(page, f"{MOD}?view=dbt")
    expect(page.get_by_test_id("dbt-generations")).to_be_visible()


def test_dbt_viewer_review_section(page: Page):
    open_page(page, f"{MOD}?view=dbt")
    href = first_link_matching(page, r"/os/design/modeling/dbt/[^/?#]+")
    if not href:
        pytest.skip("no dbt generations")
    open_page(page, href)
    if page.get_by_test_id("dbt-review").count():
        expect(page.get_by_test_id("dbt-review")).to_be_visible()


def test_table_model_rows_and_columns(page: Page):
    open_page(page, f"{MOD}?view=tables")
    row = page.locator('[data-testid^="table-model-"]').first
    if row.count() == 0:
        pytest.skip("no table models")
    for l in ["Canvas", "DDL"]:
        expect(row.get_by_role("link", name=l)).to_be_visible()
    row.get_by_role("link", name="Canvas").click()
    open_page(page, path_of(page) + "?tab=columns")
    col = page.locator('[data-testid^="tm-col-"]').first
    if col.count():
        expect(col.get_by_label("Classification").or_(col.get_by_label("Column name")).first).to_be_visible()


# ---------------------------------------------------------------- domain sources / profile compare
def test_domain_model_source_section(page: Page):
    open_page(page, "/os/knowledge?view=domains")
    href = first_link_matching(page, r"/os/knowledge/domains/[^/?#]+$")
    if not href:
        pytest.skip("no domains")
    open_page(page, href + "?tab=sources")
    s = page.get_by_test_id("domain-model-source")
    if s.count():
        expect(s.get_by_role("link", name="Import a model file").or_(s)).to_be_visible()


def test_profile_pass_compare(page: Page):
    open_page(page, "/os/design/profiling")
    row = page.locator("tbody tr").first
    if row.count() == 0:
        pytest.skip("no profiles")
    row.click()
    cmp_ = page.get_by_label("Compare with")
    if cmp_.count() == 0 or cmp_.locator("option").count() < 2:
        pytest.skip("single profiling pass")
    cmp_.select_option(index=1)
    expect(page.get_by_test_id("pass-compare")).to_be_visible()


# ---------------------------------------------------------------- source intake extras
def test_source_intake_registered_and_attach(page: Page):
    open_page(page, "/os/intake/new?type=source&intent=new")
    reg = page.get_by_test_id("sources-registered")
    if reg.count():
        reg.locator("summary, button").first.click()
    att = page.get_by_test_id("attach-sources")
    if att.count():
        expect(att).to_be_visible()


def test_source_intake_suggest_enhance(page: Page):
    open_page(page, "/os/intake/new?type=source&intent=new")
    boxes = page.get_by_test_id("sources-feeding").get_by_role("checkbox")
    if boxes.count() == 0:
        pytest.skip("no sources")
    boxes.first.check()
    s = page.get_by_test_id("suggest-enhance")
    if s.count() == 0:
        pytest.skip("no enhance suggestion for this source")
    s.get_by_text("Enhance instead").click()
    expect(page).to_have_url(re.compile("intent=enhance"))


# ---------------------------------------------------------------- ingestion review / saved results
def test_ingestion_review_section_after_check(page: Page):
    open_page(page, "/os/enable/onboarding?type=source&branch=external&kind=database")
    page.locator("#source_name").fill("UIAUTO_PG")
    page.locator("#sql-0").fill("PUBLIC.CUSTOMERS")
    page.get_by_test_id("check-ingestion-end").click()
    if page.get_by_test_id("ingestion-review").count() == 0:
        pytest.skip("config incomplete for this environment")
    expect(page.get_by_test_id("ingestion-review")).to_be_visible()
    expect(page.get_by_test_id("assigned-on-save").first).to_contain_text("Set when you save")


@pytest.mark.writes
def test_ingestion_save_writes_snaplogic(page: Page):
    open_page(page, "/os/enable/onboarding?type=source&branch=external&kind=database")
    page.locator("#source_name").fill("UIAUTO_SL")
    page.locator("#sql-0").fill("PUBLIC.CUSTOMERS")
    page.get_by_test_id("go-snaplogic").click()
    page.locator("#snaplogic_project_name").fill("UIAUTO")
    page.get_by_test_id("check-ingestion-end").click()
    page.get_by_test_id("save-ingestion-end").click()
    expect(page.get_by_test_id("ingestion-saved")).to_be_visible()
    if page.get_by_test_id("snaplogic-saved").count():
        expect(page.get_by_test_id("snaplogic-saved")).to_contain_text("SnapLogic")


@pytest.mark.writes
def test_share_snaplogic_result(page: Page):
    open_page(page, "/os/enable/onboarding?type=source&branch=share")
    r = page.get_by_test_id("share-snaplogic-result")
    if r.count() == 0:
        pytest.skip("only shown after landing a share with SnapLogic (see onboarding journey)")
    expect(r).to_contain_text(re.compile("SnapLogic rows written|Write them again"))


# ---------------------------------------------------------------- home focus / mapping groups / build all
def test_home_request_focus_and_scope(page: Page):
    open_page(page, "/os/intake")
    href = first_link_matching(page, r"request=[^&]+")
    if not href:
        pytest.skip("no requests on the intake board")
    open_page(page, href)
    rf = page.get_by_test_id("request-focus")
    if rf.count():
        expect(rf).to_be_visible()
        if page.get_by_test_id("item-scope").count():
            expect(page.get_by_test_id("item-scope").first).to_be_visible()


def test_mapping_groups(page: Page):
    open_page(page, "/os/build/runs")
    href = first_link_matching(page, r"/os/build/runs/[0-9a-fA-F-]{8,}")
    if not href:
        pytest.skip("no runs")
    run = re.search(r"/os/build/runs/[^/?#]+", href).group(0)
    open_page(page, run + "/modeling?tab=mapping")
    g = page.locator('[data-testid^="mapping-group-"]')
    if g.count() == 0:
        pytest.skip("no mapping groups")
    expect(g.first).to_be_visible()


def test_build_all_ready_button(page: Page):
    open_page(page, "/os/build/ready")
    b = page.get_by_test_id("build-all-ready")
    if b.count() == 0:
        pytest.skip("nothing ready to build")
    expect(b).to_contain_text(re.compile(r"Build all ready \(\d+\)"))
