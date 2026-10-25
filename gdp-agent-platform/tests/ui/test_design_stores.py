"""Design stores: domain models + model workbench, table models, dbt viewer, Data Mapping (STTM),
profiling store (profiles, schedules, annotations), source store."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import first_link_matching, open_page, unique

MOD = "/os/design/modeling"
STTM = "/os/design/sttm"
PROF = "/os/design/profiling"


def _go(page, list_path, rx):
    open_page(page, list_path)
    href = first_link_matching(page, rx)
    if not href:
        pytest.skip(f"nothing matching {rx} in {list_path}")
    return href


# ================================================================ modeling store
@pytest.mark.parametrize("label,view", [("Domain models", "models"), ("Table models", "tables"),
                                        ("Legacy designs", "design"), ("Run models", "runs"),
                                        ("dbt projects", "dbt")])
def test_modeling_store_tabs(page: Page, label, view):
    open_page(page, MOD, "Modeling Store")
    page.get_by_text(label, exact=True).first.click()
    expect(page).to_have_url(re.compile(f"view={view}"))
    open_page(page, f"{MOD}?view={view}")


def test_domain_models_filters(page: Page):
    open_page(page, f"{MOD}?view=models")
    expect(page.get_by_test_id("domain-models")).to_be_visible()
    page.get_by_label("Filter domains").fill("zz_none")
    page.get_by_label("Filter domains").fill("")
    g = page.get_by_role("group", name="Layer")
    if g.count():
        b = g.get_by_role("button")
        for i in range(b.count()):
            b.nth(i).click()


def test_domain_model_fork_modal(page: Page):
    open_page(page, f"{MOD}?view=models")
    fork = page.get_by_role("button", name="Fork")
    if fork.count() == 0:
        pytest.skip("no models to fork")
    fork.first.click()
    expect(page.locator("#fork-name")).to_have_attribute("placeholder", "SALES_EU")
    page.keyboard.press("Escape")


def test_table_models_list(page: Page):
    open_page(page, f"{MOD}?view=tables")
    expect(page.get_by_test_id("table-models")).to_be_visible()
    page.get_by_label("Search table models").fill("zz_none")
    page.get_by_label("Search table models").fill("")
    for lbl in ["Source system", "Layer"]:
        s = page.get_by_label(lbl, exact=True)
        if s.count() and s.first.evaluate("e => e.tagName") == "SELECT":
            for i in range(min(s.first.locator("option").count(), 4)):
                s.first.select_option(index=i)


def test_new_table_model_modal(page: Page):
    open_page(page, f"{MOD}?view=tables")
    page.get_by_test_id("new-table-model").click()
    expect(page.locator("#tm-name")).to_be_visible()
    page.keyboard.press("Escape")


@pytest.mark.writes
def test_journey_create_table_model(page: Page):
    open_page(page, f"{MOD}?view=tables")
    page.get_by_test_id("new-table-model").click()
    page.locator("#tm-name").fill(unique("UIAUTO_TM").replace(" ", "_"))
    page.get_by_role("button", name=re.compile("^Create")).click()
    page.wait_for_url(re.compile(r"/tables/[^/?]+\?tab=columns"))
    expect(page.get_by_test_id("table-workbench")).to_be_visible()


# ---------------------------------------------------------------- model workbench
@pytest.fixture
def model_url(page: Page):
    return _go(page, f"{MOD}?view=models", r"/os/design/modeling/(?!tables|dbt)[^/?#]+$").split("?")[0]


TABS = ["diagram", "entities", "relationships", "standards", "versions", "review", "publish", "changes"]


@pytest.mark.parametrize("tab", TABS)
def test_workbench_tabs(page: Page, model_url, tab):
    open_page(page, model_url)
    page.get_by_test_id(f"model-tab-{tab}").click()
    expect(page.locator("body")).not_to_contain_text("Application error")


def test_workbench_version_selector(page: Page, model_url):
    open_page(page, model_url)
    v = page.get_by_label("Version", exact=True)
    if v.count() and v.locator("option").count() > 1:
        v.select_option(index=1)
        expect(page).to_have_url(re.compile("version="))


@pytest.mark.parametrize("fmt", ["ddl", "pack", "dbt", "csv"])
def test_workbench_exports(page: Page, model_url, fmt):
    open_page(page, model_url)
    link = page.locator(f'a[href*="/bff/model-export/"][href*="format={fmt}"]')
    if link.count() == 0:
        pytest.skip("export not offered")
    r = page.request.get(link.first.get_attribute("href"))
    assert r.status < 400


def test_workbench_import_modal_modes(page: Page, model_url):
    open_page(page, model_url)
    imp = page.get_by_test_id("import-model")
    if imp.count() == 0:
        pytest.skip("import not available")
    imp.click()
    for mode, field in [("Paste DDL", "#imp-text"), ("Live schema", "#imp-db"),
                        ("Model file", None), ("domain_pack.json", None)]:
        page.get_by_role("button", name=mode).click()
        if field:
            expect(page.locator(field)).to_be_visible()
    expect(page.get_by_test_id("run-import")).to_be_visible()
    page.keyboard.press("Escape")


def test_workbench_validate(page: Page, model_url):
    open_page(page, model_url)
    v = page.get_by_test_id("model-bar").get_by_role("button", name="Validate")
    if v.count() == 0:
        pytest.skip("no draft open")
    v.click()
    expect(page.get_by_test_id("model-tab-standards")).to_be_visible()


def test_workbench_entities_editing_discard(page: Page, model_url):
    open_page(page, model_url + "?tab=entities")
    add = page.get_by_test_id("add-entity")
    if add.count() == 0 or not add.is_enabled():
        pytest.skip("read-only (no draft)")
    page.get_by_label("New entity name").fill("UIAUTO_ENTITY")
    add.click()
    expect(page.get_by_test_id("entity-UIAUTO_ENTITY")).to_be_visible()
    page.get_by_test_id("add-column").click()
    expect(page.get_by_test_id("model-bar")).to_contain_text("Unsaved changes")
    page.get_by_test_id("model-bar").get_by_role("button", name="Discard").click()
    expect(page.get_by_test_id("entity-UIAUTO_ENTITY")).to_have_count(0)


def test_workbench_relationship_fields(page: Page, model_url):
    open_page(page, model_url + "?tab=relationships")
    add = page.get_by_test_id("add-relationship")
    if add.count() == 0 or not add.is_enabled():
        pytest.skip("read-only")
    add.click()
    for lbl in ["From table", "From column", "To table", "To column", "Cardinality"]:
        expect(page.get_by_label(lbl).last).to_be_visible()


def test_workbench_review_comments(page: Page, model_url):
    open_page(page, model_url + "?tab=review")
    c = page.get_by_test_id("comments")
    if c.count() == 0:
        pytest.skip("no review comments panel")
    expect(c.get_by_test_id("post-comment")).to_be_visible()
    expect(c.get_by_label("Comment", exact=True)).to_be_visible()


def test_workbench_compare_versions(page: Page, model_url):
    open_page(page, model_url + "?tab=versions")
    cmp_ = page.get_by_test_id("compare-versions")
    if cmp_.count():
        cmp_.click()
        expect(page.locator("body")).not_to_contain_text("Application error")


@pytest.mark.writes
def test_journey_model_draft_save_submit(page: Page, model_url):
    open_page(page, model_url)
    if page.get_by_test_id("open-draft").count():
        page.get_by_test_id("open-draft").click()
        expect(page.get_by_text(re.compile(r"Draft v\d+ opened"))).to_be_visible()
    page.goto(model_url + "?tab=entities")
    page.get_by_label("New entity name").fill("UIAUTO_E")
    page.get_by_test_id("add-entity").click()
    bar = page.get_by_test_id("model-bar")
    bar.get_by_label("Change note").fill("UI-AUTOTEST")
    page.get_by_test_id("save-draft").click()
    expect(page.get_by_text(re.compile("^Draft saved"))).to_be_visible()
    page.get_by_test_id("drop-entity").click()
    page.get_by_test_id("save-draft").click()
    if page.get_by_role("button", name="Withdraw to edit").count() == 0:
        page.get_by_test_id("submit-review").click()
        expect(page.get_by_text(re.compile(r"v\d+ sent for review"))).to_be_visible()
        page.get_by_role("button", name="Withdraw to edit").click()
        expect(page.get_by_text("Taken out of review; keep editing")).to_be_visible()


@pytest.mark.writes
def test_journey_model_comment_and_resolve(page: Page, model_url):
    open_page(page, model_url + "?tab=review")
    c = page.get_by_test_id("comments")
    c.get_by_label("Comment", exact=True).fill("UI-AUTOTEST comment")
    c.get_by_test_id("post-comment").click()
    expect(c.get_by_text("UI-AUTOTEST comment").first).to_be_visible()
    c.get_by_role("button", name="Resolve").first.click()


# ---------------------------------------------------------------- table model workbench
@pytest.fixture
def table_model_url(page: Page):
    return _go(page, f"{MOD}?view=tables", r"/os/design/modeling/tables/[^/?#]+").split("?")[0]


@pytest.mark.parametrize("tab", ["diagram", "columns", "code", "versions", "publish"])
def test_table_model_tabs(page: Page, table_model_url, tab):
    open_page(page, table_model_url)
    page.get_by_test_id(f"tm-tab-{tab}").click()
    expect(page.locator("body")).not_to_contain_text("Application error")


def test_table_model_save_unchanged(page: Page, table_model_url):
    open_page(page, table_model_url + "?tab=columns")
    save = page.get_by_test_id("tm-save")
    if save.count() == 0 or not save.is_enabled():
        pytest.skip("read-only")
    save.click()
    expect(page.get_by_text("Nothing changed")).to_be_visible()


def test_table_model_publish_controls(page: Page, table_model_url):
    open_page(page, table_model_url + "?tab=publish")
    pub = page.get_by_test_id("tm-publish")
    expect(pub).to_be_visible()
    expect(pub.locator("#tm-domain")).to_be_visible()
    expect(pub.get_by_test_id("tm-propose").or_(pub.get_by_test_id("tm-push")).first).to_be_visible()


@pytest.mark.writes
def test_table_model_suggest(page: Page, table_model_url):
    open_page(page, table_model_url + "?tab=columns")
    page.get_by_test_id("tm-suggest").click()
    expect(page.get_by_role("button", name="Accept").first.or_(
        page.get_by_text("No suggestions: the columns look right"))).to_be_visible(timeout=180_000)


# ---------------------------------------------------------------- dbt viewer
def test_dbt_viewer(page: Page):
    href = _go(page, f"{MOD}?view=dbt", r"/os/design/modeling/dbt/[^/?#]+")
    open_page(page, href)
    expect(page.get_by_test_id("dbt-readonly")).to_be_visible()
    page.get_by_label("Find a file").fill("sql")
    files = page.locator('[data-testid^="file-"]')
    if files.count():
        files.first.click()
    ch = page.get_by_test_id("dbt-changes")
    if ch.count():
        ch.click()
        expect(page.get_by_test_id("dbt-diff")).to_be_visible()
    page.get_by_role("button", name="Code").click()


# ================================================================ STTM
def test_sttm_filters(page: Page):
    open_page(page, STTM, "Data Mapping Store")
    page.get_by_label("Search", exact=True).fill("zz")
    for lbl in ["Source entity", "Target entity", "Status"]:
        s = page.get_by_label(lbl, exact=True)
        if s.count() and s.evaluate("e => e.tagName") == "SELECT" and s.locator("option").count() > 1:
            s.select_option(index=1)
    page.get_by_role("button", name="Apply").click()
    page.wait_for_load_state("load")
    expect(page.locator("body")).not_to_contain_text("Application error")


@pytest.fixture
def sttm_url(page: Page):
    return _go(page, STTM, r"/os/design/sttm/[^/?#]+").split("?")[0]


@pytest.mark.parametrize("tab", ["columns", "joins", "sql", "evidence", "versions"])
def test_sttm_tabs(page: Page, sttm_url, tab):
    open_page(page, sttm_url)
    page.get_by_test_id(f"sttm-tab-{tab}").click()
    expect(page.locator("body")).not_to_contain_text("Application error")


@pytest.mark.parametrize("chip", ["all", "with logic", "unmapped", "filtered", "changed"])
def test_sttm_column_chips(page: Page, sttm_url, chip):
    open_page(page, sttm_url)
    page.get_by_role("button", name=re.compile(f"^{chip}", re.I)).first.click()
    page.get_by_label("Filter columns").fill("")


def test_sttm_exports(page: Page, sttm_url):
    open_page(page, sttm_url)
    for name in ["dbt", "CSV"]:
        link = page.get_by_role("link", name=name, exact=True)
        if link.count():
            assert page.request.get(link.first.get_attribute("href")).status < 400


def test_sttm_line_drawer_stage_and_discard(page: Page, sttm_url):
    open_page(page, sttm_url)
    line = page.locator('[data-testid^="line-"]').first
    if line.count() == 0:
        pytest.skip("no lines")
    line.click()
    for fid in ["#ln-target", "#ln-rule"]:
        expect(page.locator(fid)).to_be_visible()
    page.locator("#ln-def").fill("UI-AUTOTEST definition")
    page.get_by_test_id("stage-line").click()
    bar = page.get_by_test_id("change-bar")
    expect(bar).to_contain_text(re.compile(r"\d+ changes? staged"))
    bar.get_by_role("button", name="Discard").click()
    expect(bar).to_have_count(0)


def test_sttm_submit_requires_note(page: Page, sttm_url):
    open_page(page, sttm_url)
    line = page.locator('[data-testid^="line-"]').first
    if line.count() == 0:
        pytest.skip("no lines")
    line.click()
    page.locator("#ln-def").fill("x")
    page.get_by_test_id("stage-line").click()
    bar = page.get_by_test_id("change-bar")
    bar.get_by_label("Change note").fill("ab")
    expect(bar.get_by_test_id("submit-change")).to_be_disabled()
    bar.get_by_role("button", name="Discard").click()


def test_sttm_joins_editor(page: Page, sttm_url):
    open_page(page, sttm_url)
    page.get_by_test_id("sttm-tab-joins").click()
    ed = page.get_by_test_id("filters-editor")
    expect(ed).to_be_visible()
    ed.get_by_role("button", name="Add filter").click()
    expect(ed.get_by_label("Filter condition").last).to_be_visible()
    ed.get_by_label("Remove filter").last.click()
    for f in ["#d-grain", "#d-keys"]:
        expect(page.locator(f)).to_be_visible()


def test_sttm_versions_compare(page: Page, sttm_url):
    open_page(page, sttm_url)
    page.get_by_test_id("sttm-tab-versions").click()
    frm = page.get_by_label("From version")
    if frm.count() and frm.locator("option").count() > 1:
        frm.select_option(index=1)
        page.get_by_role("button", name="Compare").click()


def test_sttm_publish_panel(page: Page, sttm_url):
    open_page(page, sttm_url)
    git = page.get_by_test_id("sttm-git")
    if git.count():
        expect(git.locator("#sttm-repo")).to_be_visible()
        expect(git.get_by_test_id("publish-sttm")).to_be_visible()


@pytest.mark.writes
def test_journey_sttm_edit_and_submit(page: Page, sttm_url):
    open_page(page, sttm_url)
    page.locator('[data-testid^="line-"]').first.click()
    page.locator("#ln-def").fill("UI-AUTOTEST definition")
    page.get_by_test_id("stage-line").click()
    bar = page.get_by_test_id("change-bar")
    bar.get_by_label("Change note").fill("UI-AUTOTEST change")
    bar.get_by_test_id("submit-change").click()
    expect(page.get_by_text(re.compile(r"Saved as v\d+")).or_(page.get_by_test_id("pending-changes"))).to_be_visible()


# ================================================================ profiling store
@pytest.mark.parametrize("label,section", [("Profiles", None), ("Schedules", "schedules"),
                                           ("Annotations", "annotations")])
def test_profiling_sections(page: Page, label, section):
    open_page(page, PROF, "Profiling Store")
    page.get_by_role("navigation", name="Profiling sections").get_by_role("link", name=label).click()
    if section:
        expect(page).to_have_url(re.compile(f"section={section}"))


def test_profile_browser_filters(page: Page):
    open_page(page, PROF)
    page.get_by_label("Search tables").fill("zz")
    page.get_by_label("Search tables").fill("")
    for lbl in ["Domain", "Run", "Tag", "Sort"]:
        s = page.get_by_label(lbl, exact=True)
        if s.count() and s.first.evaluate("e => e.tagName") == "SELECT":
            for i in range(min(s.first.locator("option").count(), 3)):
                s.first.select_option(index=i)
    v = page.get_by_role("group", name="View")
    if v.count():
        for i in range(v.get_by_role("button").count()):
            v.get_by_role("button").nth(i).click()


def test_profile_bookmark_toggle(page: Page):
    open_page(page, PROF)
    b = page.get_by_role("button", name="Bookmark", exact=True)
    if b.count() == 0:
        pytest.skip("no tables")
    if not __import__("settings").ALLOW_WRITES:
        pytest.skip("bookmark persists (writes)")
    b.first.click()
    page.get_by_role("button", name="Remove bookmark").first.click()


def test_profile_drawer(page: Page):
    open_page(page, PROF)
    row = page.locator("tbody tr, [data-testid^='profile-']").first
    if row.count() == 0:
        pytest.skip("no profiles")
    row.click()
    expect(page.get_by_role("button", name="Re-profile")).to_be_visible()
    page.get_by_role("button", name="Soda and GX checks").click()
    for t in ["SodaCL", "Great Expectations"]:
        page.get_by_role("tab", name=t).or_(page.get_by_role("button", name=t)).first.click()
    ann = page.locator('[data-testid^="annotate-"]').first
    if ann.count():
        ann.click()
        for f in ["#an-desc", "#an-note"]:
            expect(page.locator(f)).to_be_visible()
        expect(page.get_by_test_id("send-annotation")).to_be_visible()
        page.keyboard.press("Escape")


def test_schedule_modal_presets(page: Page):
    open_page(page, f"{PROF}?section=schedules")
    expect(page.get_by_test_id("profile-schedules")).to_be_visible()
    page.get_by_test_id("add-schedule").click()
    cron = page.locator("#ps-cron")
    for p in ["Daily 05:00", "Weekdays 06:00", "Weekly Sunday", "Every 6 hours"]:
        before = cron.input_value()
        page.get_by_role("button", name=p).click()
        assert cron.input_value() and (cron.input_value() != before or p == "Daily 05:00")
    expect(page.get_by_test_id("save-schedule")).to_be_visible()
    page.keyboard.press("Escape")


@pytest.mark.writes
def test_journey_schedule_create_run_remove(page: Page):
    open_page(page, f"{PROF}?section=schedules")
    page.get_by_test_id("add-schedule").click()
    for f in ["#ps-db", "#ps-schema"]:
        el = page.locator(f)
        if el.evaluate("e => e.tagName") == "SELECT":
            el.select_option(index=1)
    page.get_by_role("button", name="Weekly Sunday").click()
    page.get_by_test_id("save-schedule").click()
    expect(page.get_by_text(re.compile("^Profiling scheduled"))).to_be_visible()
    sched = page.locator('[data-testid^="schedule-"]').last
    sched.get_by_role("button", name="Remove schedule").click()
    expect(page.get_by_text("Schedule removed")).to_be_visible()


def test_annotations_reject_requires_note(page: Page):
    open_page(page, f"{PROF}?section=annotations")
    expect(page.get_by_test_id("annotations")).to_be_visible()
    a = page.locator('[data-testid^="annotation-"]').first
    if a.count() == 0:
        pytest.skip("no annotations")
    a.get_by_label("Decision note").fill("ab")
    expect(a.get_by_role("button", name="Reject")).to_be_disabled()


@pytest.mark.writes
def test_journey_annotation_reject(page: Page):
    open_page(page, f"{PROF}?section=annotations")
    a = page.locator('[data-testid^="annotation-"]').first
    if a.count() == 0:
        pytest.skip("no annotations")
    a.get_by_label("Decision note").fill("UI-AUTOTEST reject")
    a.get_by_role("button", name="Reject").click()
    expect(page.get_by_text("Rejected")).to_be_visible()


# ================================================================ source store
def test_source_store(page: Page):
    open_page(page, "/os/design/sources", "Source Store")
    expect(page.get_by_test_id("source-kpis").or_(page.get_by_test_id("ingestion-table")).first).to_be_visible()


def test_ingestion_detail_expand(page: Page):
    open_page(page, "/os/design/sources")
    m = page.locator('[data-testid^="master-"]').first
    if m.count() == 0:
        pytest.skip("no ingestion configs")
    m.click()
    expect(page.get_by_test_id("ingestion-detail").or_(page.get_by_test_id("ingestion-history")).first).to_be_visible()


@pytest.mark.writes
def test_ingestion_active_switch_roundtrip(page: Page):
    open_page(page, "/os/design/sources")
    sw = page.get_by_role("switch", name=re.compile(" active$")).first
    if sw.count() == 0:
        pytest.skip("no switches")
    before = sw.get_attribute("aria-checked")
    sw.click()
    expect(page.get_by_text(re.compile("(activated|deactivated)"))).to_be_visible()
    expect(sw).not_to_have_attribute("aria-checked", before)
    sw.click()
    expect(sw).to_have_attribute("aria-checked", before)
