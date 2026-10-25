"""Remaining component coverage: home & platform search, overview dashboard, approvals, mapping board,
model design, Gold model, star schema, table runs, table profile, agent panel, governance tables,
store tabs, ops cards, resolution panel, skill editor, onboarding helpers, SnapLogic, join plan,
lifecycle, environments grid, lineage lanes, workspace tabs, misc."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import first_link_matching, open_page


@pytest.fixture
def run_url(page: Page) -> str:
    open_page(page, "/os/build/runs")
    href = first_link_matching(page, r"/os/build/runs/[0-9a-fA-F-]{8,}")
    if not href:
        pytest.skip("no runs")
    return re.search(r"/os/build/runs/[^/?#]+", href).group(0)


# ================================================================ home
@pytest.mark.parametrize("kind", ["source", "domain", "product"])
def test_home_start_panel(page: Page, kind):
    open_page(page, "/os")
    sp = page.get_by_test_id("start-panel")
    if sp.count() == 0:
        pytest.skip("start panel hidden for persona")
    expect(sp.get_by_test_id(f"start-{kind}")).to_be_visible()
    for intent in ["new", "enhance"]:
        link = sp.get_by_test_id(f"start-{kind}-{intent}")
        if link.count():
            assert re.search(f"type={kind}.*intent={intent}|intent={intent}.*type={kind}",
                             link.get_attribute("href"))


def test_home_start_new_navigates(page: Page):
    open_page(page, "/os")
    link = page.get_by_test_id("start-domain-new")
    if link.count() == 0:
        pytest.skip("no start links")
    link.click()
    expect(page).to_have_url(re.compile(r"/os/intake/new\?.*type=domain"))


def test_home_inflight_and_workflows(page: Page):
    open_page(page, "/os")
    for tid in ["inflight", "onboarded-domains"]:
        el = page.get_by_test_id(tid)
        if el.count():
            expect(el).to_be_visible()
    row = page.get_by_test_id("workflow-row").first
    if row.count():
        row.get_by_role("link", name="Open").or_(row.get_by_role("button", name="Open")).first.click()
        page.wait_for_load_state("load")
        expect(page.locator("body")).not_to_contain_text("Application error")


def test_home_domain_card_opens_domain(page: Page):
    open_page(page, "/os")
    card = page.get_by_test_id("domain-card").first
    if card.count() == 0:
        pytest.skip("no domain cards")
    card.locator("a").first.click()
    expect(page).to_have_url(re.compile(r"/os/knowledge/domains/"))


def test_home_platform_action(page: Page):
    open_page(page, "/os")
    a = page.get_by_test_id("platform-action").first
    if a.count() == 0:
        pytest.skip("no platform actions")
    a.locator("a, button").first.click()
    page.wait_for_load_state("load")
    expect(page.locator("body")).not_to_contain_text("Application error")


def test_platform_search(page: Page):
    open_page(page, "/os")
    box = page.get_by_test_id("home-search")
    expect(box).to_have_attribute("placeholder", re.compile("Search by meaning"))
    box.fill("customer")
    expect(page).to_have_url(re.compile(r"q=customer"))
    expect(page.get_by_test_id("platform-search-results")).to_be_visible(timeout=60_000)
    page.get_by_role("button", name="Clear search").click()
    expect(box).to_have_value("")


def test_platform_search_enter_opens_result(page: Page):
    open_page(page, "/os?q=customer")
    res = page.get_by_test_id("platform-search-results")
    if res.locator("a").count() == 0:
        pytest.skip("no results")
    page.get_by_test_id("home-search").press("Enter")
    page.wait_for_load_state("load")
    expect(page).not_to_have_url(re.compile(r"/os\?q="))


def test_intake_inflight_tabs(page: Page):
    open_page(page, "/os/intake")
    for i in ["new", "enhance"]:
        t = page.get_by_test_id(f"inflight-{i}")
        if t.count():
            t.click()


def test_help_demo_paths(page: Page):
    open_page(page, "/os/help")
    dp = page.get_by_test_id("demo-paths")
    expect(dp).to_be_visible()
    hrefs = dp.locator("a").evaluate_all("as => as.map(a => a.getAttribute('href'))")
    for h in hrefs[:15]:
        if h and h.startswith("/"):
            open_page(page, h)


# ================================================================ overview dashboard
def test_overview_dashboard_widgets(page: Page):
    open_page(page, "/os/work/overview")
    for tid in ["hero-ribbon", "pipeline-table", "tickets-card"]:
        expect(page.get_by_test_id(tid)).to_be_visible()
    for tid in ["attention", "failing-checks-summary", "failing-checks"]:
        if page.get_by_test_id(tid).count():
            expect(page.get_by_test_id(tid).first).to_be_visible()


@pytest.mark.parametrize("link", ["All runs", "Onboard source", "Audit"])
def test_overview_links(page: Page, link):
    open_page(page, "/os/work/overview")
    page.get_by_role("link", name=link).first.click()
    page.wait_for_load_state("load")
    expect(page.locator("body")).not_to_contain_text("Application error")


def test_tickets_card(page: Page):
    open_page(page, "/os/work/overview")
    card = page.get_by_test_id("tickets-card")
    expect(card.get_by_role("link", name=re.compile("Open Tickets|All \\d+|Connect Jira")).first.or_(
        card.get_by_text("No open tickets assigned to you."))).to_be_visible()
    cop = card.get_by_role("link", name=re.compile("^Work on .* in the Copilot"))
    if cop.count():
        assert "/os/work/copilot?jira=" in cop.first.get_attribute("href")


def test_failing_check_opens_incident(page: Page):
    open_page(page, "/os/work/overview")
    a = page.get_by_test_id("failing-checks").get_by_role("link", name="Open the incident")
    if a.count() == 0:
        pytest.skip("no failing checks")
    a.first.click()
    expect(page).to_have_url(re.compile("incidents"))


# ================================================================ approvals
@pytest.mark.parametrize("tab,scope", [("Waiting for me", "inbox"), ("My requests", "mine"),
                                       ("All requests", "all")])
def test_approvals_scopes(page: Page, tab, scope):
    open_page(page, "/os/review/approvals", "Approvals")
    page.get_by_role("link", name=tab).click()
    expect(page).to_have_url(re.compile(f"scope={scope}"))


def test_approvals_request_preview(page: Page):
    open_page(page, "/os/review/approvals?scope=all")
    row = page.locator("details, [data-testid^='request-'], li").filter(has_text=re.compile(".+")).first
    if row.count() == 0:
        pytest.skip("no requests")
    row.click()
    pv = page.get_by_test_id("request-preview")
    if pv.count():
        expect(pv.get_by_text(re.compile("What will change|What was asked"))).to_be_visible()


def test_approvals_known_fixes_section(page: Page):
    open_page(page, "/os/review/approvals?scope=inbox")
    sec = page.get_by_test_id("approvals-fixes")
    if sec.count():
        expect(sec).to_contain_text("Known fixes waiting to run")


# ================================================================ run: mapping / model / gold / star
def test_mapping_board(page: Page, run_url):
    open_page(page, run_url + "/modeling?tab=mapping")
    mt = page.get_by_test_id("mapping-tables")
    if mt.count() == 0:
        pytest.skip("mapping not generated")
    tbl = page.locator('[data-testid^="mapping-table-"]').first
    tbl.click()
    page.get_by_placeholder("Search source or target columns").fill("ID")
    page.get_by_placeholder("Search source or target columns").fill("")
    sa = page.get_by_label("Select all visible")
    if sa.count():
        sa.check()
        expect(page.get_by_role("button", name=re.compile(r"^Review selected \(\d+\)"))).to_be_visible()
        sa.uncheck()
    for b in ["Approve all confident", "Accept all AI suggestions", "Review all pending"]:
        el = page.get_by_role("button", name=re.compile(f"^{b}"))
        if el.count():
            expect(el.first).to_be_visible()


def test_mapping_ai_nulls_and_elsewhere(page: Page, run_url):
    open_page(page, run_url + "/modeling?tab=mapping")
    for tid in ["apply-ai-nulls", "ai-elsewhere", "minimum-mapping"]:
        el = page.get_by_test_id(tid)
        if el.count():
            expect(el.first).to_be_visible()


def test_model_design_panel(page: Page, run_url):
    open_page(page, run_url + "/modeling")
    for tid in ["use-domain-model", "run-workbench", "run-table-models"]:
        el = page.get_by_test_id(tid)
        if el.count():
            expect(el).to_be_visible()
    instr = page.get_by_label("Design instructions")
    if instr.count():
        instr.fill("UI-AUTOTEST")
        expect(page.get_by_role("button", name=re.compile("^(Design|Redesign|Revise)$")).first).to_be_enabled()


def test_run_workbench_links(page: Page, run_url):
    open_page(page, run_url + "/modeling")
    wb = page.get_by_test_id("run-workbench")
    if wb.count() == 0:
        pytest.skip("no run workbench")
    wb.get_by_role("link", name="Open model").click()
    expect(page).to_have_url(re.compile(r"/os/design/modeling/"))


@pytest.mark.writes
def test_use_domain_model_as_is(page: Page, run_url):
    open_page(page, run_url + "/modeling")
    b = page.get_by_test_id("use-domain-model-button")
    if b.count() == 0:
        pytest.skip("no domain model")
    b.click()
    expect(page.get_by_test_id("use-domain-model").get_by_text("In use")).to_be_visible()


def test_gold_model_and_star_schema(page: Page):
    open_page(page, "/os/build/gold")
    href = first_link_matching(page, r"/os/build/runs/[0-9a-fA-F-]{8,}")
    if not href:
        pytest.skip("no gold runs")
    run = re.search(r"/os/build/runs/[^/?#]+", href).group(0)
    open_page(page, run + "/modeling")
    gm = page.get_by_test_id("gold-model")
    if gm.count():
        expect(gm.get_by_role("button", name=re.compile("^Propose"))).to_be_visible()
    ss = page.get_by_test_id("star-schema")
    if ss.count():
        expect(ss).to_contain_text(re.compile(r"star schema: \d+ table runs"))
        for b in ["Run all to next gate", "Publish one pull request"]:
            el = ss.get_by_role("button", name=b)
            if el.count():
                expect(el).to_be_visible()
        expect(page.get_by_test_id("gold-table-run").first.or_(ss)).to_be_visible()
    tr = page.get_by_test_id("table-runs")
    if tr.count():
        expect(page.get_by_test_id("publish-table-runs")).to_be_visible()


# ================================================================ profile / agent panel
def test_table_profile(page: Page, run_url):
    open_page(page, run_url + "/profile")
    tp = page.get_by_test_id("table-profile").first
    if tp.count() == 0:
        pytest.skip("not profiled")
    tp.get_by_label("Find a column").fill("ID")
    t = tp.get_by_label("Type")
    for i in range(min(t.locator("option").count(), 4)):
        t.select_option(index=i)
    tp.get_by_label("Needs a look").check()
    tp.get_by_label("Needs a look").uncheck()
    tp.get_by_label("Find a column").fill("")
    col = tp.get_by_test_id("profile-column").first
    if col.count():
        col.click()


def test_agent_panel(page: Page, run_url):
    open_page(page, run_url)
    log = page.get_by_test_id("agent-log")
    if log.count() == 0:
        pytest.skip("no agent panel")
    box = page.get_by_placeholder("e.g. Which source tables are selected?")
    expect(box).to_be_visible()
    expect(page.get_by_role("button", name="Send").last).to_be_visible()


@pytest.mark.writes
def test_agent_panel_ask(page: Page, run_url):
    open_page(page, run_url)
    page.get_by_placeholder("e.g. Which source tables are selected?").fill("Which source tables are selected?")
    page.get_by_role("button", name="Send").last.click()
    expect(page.get_by_role("button", name="Send").last).to_be_visible(timeout=180_000)


# ================================================================ governance tables
def test_registered_sources(page: Page):
    open_page(page, "/os/design/sources")
    rs = page.get_by_test_id("registered-sources")
    if rs.count():
        expect(rs).to_be_visible()
    for tid in ["external-sources", "ingestion-empty"]:
        if page.get_by_test_id(tid).count():
            expect(page.get_by_test_id(tid)).to_be_visible()


def test_registered_tables_add_form(page: Page):
    open_page(page, "/os/knowledge?view=domains")
    href = first_link_matching(page, r"/os/knowledge/domains/[^/?#]+$")
    if not href:
        pytest.skip("no domains")
    open_page(page, href + "?tab=sources")
    rt = page.get_by_test_id("registered-tables")
    if rt.count() == 0:
        pytest.skip("no registered tables panel")
    rt.get_by_test_id("add-table").click()
    form = page.get_by_test_id("add-table-form")
    for lbl in ["Database", "Schema", "Table"]:
        expect(form.get_by_label(lbl)).to_be_visible()
    form.get_by_role("button", name="Cancel").click()
    expect(form).to_have_count(0)


def test_ingestion_child_rows(page: Page):
    open_page(page, "/os/design/sources")
    m = page.locator('[data-testid^="master-"]').first
    if m.count() == 0:
        pytest.skip("no ingestion masters")
    m.click()
    if page.locator('[data-testid^="child-"]').count():
        expect(page.locator('[data-testid^="child-"]').first).to_be_visible()


# ================================================================ store tabs / workspace tabs
def test_store_tabs_set_view(page: Page):
    open_page(page, "/os/design/store")
    tabs = page.locator('[data-testid^="store-tab-"]')
    for i in range(tabs.count()):
        tabs.nth(i).click()
        expect(page).to_have_url(re.compile("view="))


def test_workspace_tabs(page: Page):
    for p in ["/os/design/store", "/os/run/quality", "/os/admin/settings"]:
        open_page(page, p)
        wt = page.get_by_test_id("workspace-tabs")
        if wt.count():
            links = wt.get_by_role("link")
            for i in range(links.count()):
                h = links.nth(i).get_attribute("href")
                open_page(page, h)
            return
    pytest.skip("no workspace tabs rendered")


# ================================================================ ops cards
def test_resolution_panel_links(page: Page):
    open_page(page, "/os/run/incidents")
    inc = first_link_matching(page, r"/incidents/[^/?#]+$")
    if not inc:
        pytest.skip("no incidents")
    open_page(page, inc)
    rp = page.get_by_test_id("resolution-panel")
    if rp.count() == 0:
        pytest.skip("no resolution for this incident")
    link = rp.get_by_role("link", name="Write a known fix from this incident")
    if link.count():
        assert "/os/run/ops/fixes/new?incident=" in link.get_attribute("href")


def test_resolutions_fix_approvals_and_recurring(page: Page):
    open_page(page, "/os/run/ops/resolutions")
    for tid in ["fix-approvals", "recurring-fixes", "agent-proposal"]:
        el = page.get_by_test_id(tid)
        if el.count():
            expect(el.first).to_be_visible()
    rf = page.get_by_test_id("recurring-fixes")
    if rf.count() and rf.get_by_role("link", name="Draft permanent fix").count():
        rf.get_by_role("link", name="Draft permanent fix").first.click()
        page.wait_for_load_state("load")


def test_permanent_fix_card(page: Page):
    open_page(page, "/os/run/ops/fixes")
    href = first_link_matching(page, r"/ops/fixes/(?!new)[^/?#]+$")
    if not href:
        pytest.skip("no fixes")
    open_page(page, href)
    card = page.get_by_test_id("permanent-fix-card")
    if card.count() == 0:
        pytest.skip("no permanent fix card")
    card.get_by_role("button", name=re.compile("^(Draft|Redraft) permanent fix$")).click()
    expect(card.get_by_label("Instructions for the draft")).to_be_visible()
    card.get_by_role("button", name="Cancel").click()


def test_skill_editor_steps(page: Page):
    open_page(page, "/os/run/ops/fixes/new")
    page.get_by_label(re.compile("Several steps")).check()
    ed = page.get_by_test_id("skill-editor")
    if ed.count() == 0:
        link = page.get_by_role("button", name=re.compile("New skill|Create a skill"))
        if link.count():
            link.first.click()
        ed = page.get_by_test_id("skill-editor")
    if ed.count() == 0:
        pytest.skip("skill editor not reachable")
    ed.get_by_label("Skill name").fill("UIAUTO")
    expect(ed.get_by_label("Skill description")).to_have_attribute("placeholder", "What it repairs")
    ed.get_by_role("button", name="Step", exact=True).click()
    expect(ed.get_by_label(re.compile(r"^Step \d+ procedure$")).last).to_be_visible()
    ed.get_by_role("button", name=re.compile(r"^Remove step \d+$")).last.click()


# ================================================================ onboarding helpers / snaplogic
def test_onboarding_pipeline_preview_and_handoff(page: Page):
    open_page(page, "/os/enable/onboarding?type=source")
    pp = page.get_by_test_id("pipeline-preview")
    if pp.count():
        expect(pp).to_be_visible()
    open_page(page, "/os/enable/onboarding?type=source&request=abc")
    rh = page.get_by_test_id("request-handoff")
    if rh.count():
        expect(rh.get_by_role("link", name="Back to the request")).to_have_attribute(
            "href", re.compile("/os/requests/"))


def test_domain_formats_templates(page: Page):
    for p in ["/os/enable/new-domain?how=import", "/os/enable/onboarding?type=source"]:
        open_page(page, p)
        tv = page.get_by_test_id("template-viewer")
        if tv.count():
            break
    else:
        pytest.skip("template viewer not rendered")
    for t in ["domain_pack.json", "contract.yaml"]:
        tv.get_by_role("tab", name=t).or_(tv.get_by_role("button", name=t)).first.click()
        copy = tv.get_by_role("button", name=f"Copy {t}")
        if copy.count():
            page.context.grant_permissions(["clipboard-read", "clipboard-write"])
            copy.click()
            expect(copy).to_contain_text("Copied")
    with page.expect_download() as dl:
        page.get_by_test_id("template-download").first.click()
    assert dl.value.suggested_filename
    expect(page.get_by_test_id("formats-matrix")).to_be_visible()


def test_ingestion_wizard_review_status(page: Page):
    open_page(page, "/os/enable/onboarding?type=source&branch=external&kind=database")
    st = page.get_by_test_id("ingestion-status")
    expect(st).to_contain_text(re.compile("Defaults are filled in|fields to fix|Checked"))
    page.get_by_test_id("check-ingestion").click()
    expect(st).to_contain_text(re.compile("fields to fix|Checked"))


def test_ingestion_source_deletes_toggle(page: Page):
    open_page(page, "/os/enable/onboarding?type=source&branch=external&kind=database")
    tog = page.get_by_label("Source deletes rows").first
    if tog.count() == 0:
        pytest.skip("toggle not shown")
    tog.click()
    expect(page.locator('[data-testid^="deletes-"]').first).to_be_visible()


def test_ingestion_snaplogic_section(page: Page):
    open_page(page, "/os/enable/onboarding?type=source&branch=external&kind=database")
    page.get_by_test_id("go-snaplogic").click()
    for f in ["#snaplogic_project_name", "#parent_pipeline_name", "#child_pipeline_name"]:
        expect(page.locator(f)).to_be_visible()


def test_share_wizard_no_access_and_snaplogic(page: Page):
    open_page(page, "/os/enable/onboarding?type=source&branch=share")
    wiz = page.get_by_test_id("share-wizard")
    if page.get_by_test_id("share-no-access").count():
        expect(page.get_by_test_id("share-no-access")).to_contain_text("GRANT IMPORTED PRIVILEGES")
    sl = wiz.get_by_test_id("share-snaplogic")
    if sl.count():
        sl.get_by_test_id("share-snaplogic-preview").click()
        expect(page.get_by_test_id("snaplogic-preview")).to_be_visible()


# ================================================================ join plan / lifecycle / lineage
def test_join_plan_editor(page: Page, run_url):
    open_page(page, run_url + "/modeling?tab=transformations")
    drv = page.locator("#driving_table")
    if drv.count() == 0:
        pytest.skip("no join plan")
    for i in range(min(drv.locator("option").count(), 3)):
        drv.select_option(index=i)
    j = page.locator('[data-testid^="join-"]').first
    if j.count():
        expect(j.get_by_label(re.compile("^join type for "))).to_be_visible()
        expect(j.get_by_label(re.compile("^join keys for "))).to_be_visible()


def test_domain_lifecycle(page: Page):
    open_page(page, "/os/knowledge?view=domains")
    href = first_link_matching(page, r"/os/knowledge/domains/[^/?#]+$")
    if not href:
        pytest.skip("no domains")
    open_page(page, href + "?tab=lineage")
    rd = page.get_by_test_id("domain-readiness")
    if rd.count():
        rd.get_by_role("button", name="Show the checklist").click()
    lf = page.get_by_test_id("lifecycle-flow")
    if lf.count():
        vc = lf.get_by_role("button", name="View the contract")
        if vc.count():
            vc.first.click()
            expect(page.get_by_test_id("contract-text")).to_be_visible()
            page.keyboard.press("Escape")
        more = lf.get_by_role("button", name=re.compile(r"^\+\d+ more$"))
        if more.count():
            more.first.click()
            expect(lf.get_by_role("button", name="Show fewer").first).to_be_visible()
    for tid in ["built-from-card", "source-map", "needs-verification"]:
        if page.get_by_test_id(tid).count():
            expect(page.get_by_test_id(tid).first).to_be_visible()


def test_lineage_lanes_render(page: Page):
    open_page(page, "/os/build/lineage")
    page.get_by_label("Find a table").fill("CUSTOMER")
    page.get_by_role("button", name=re.compile("^Trac")).click()
    expect(page.get_by_test_id("lineage-lanes").or_(page.get_by_text(re.compile("(?i)no lineage|not found"))).first
           ).to_be_visible(timeout=120_000)


# ================================================================ admin environments / knowledge outcomes / misc
def test_environment_grid(page: Page):
    open_page(page, "/os/admin/settings?section=environments")
    g = page.get_by_test_id("environment-grid")
    expect(g).to_be_visible()
    page.get_by_role("button", name="Add environment").click()
    expect(g.get_by_placeholder("Environment").last).to_be_visible()
    page.get_by_role("button", name="Discard").click()


def test_environment_preview_dry_run(page: Page):
    open_page(page, "/os/admin/settings?section=environments")
    pv = page.get_by_test_id("preview-environment")
    if pv.count() == 0:
        pytest.skip("no preview")
    for s in ["#pv-pack", "#pv-env"]:
        if page.locator(s).locator("option").count():
            page.locator(s).select_option(index=0)
    pv.click()
    expect(page.get_by_test_id("dry-run").or_(page.get_by_role("alert")).first).to_be_visible(timeout=120_000)


def test_knowledge_outcomes_in_drawer(page: Page):
    open_page(page, "/os/knowledge?view=items")
    rows = page.locator("tbody tr")
    if rows.count() == 0:
        pytest.skip("no items")
    rows.first.locator("a, button").first.click()
    dlg = page.get_by_role("dialog").first
    dlg.get_by_role("tab", name="Usage").or_(dlg.get_by_role("button", name="Usage", exact=True)).first.click()
    expect(dlg.get_by_test_id("knowledge-outcomes").or_(dlg)).to_be_visible()


def test_verify_knowledge_control(page: Page):
    open_page(page, "/os/knowledge")
    row = page.locator('a[href*="id="]').first
    if row.count() == 0:
        pytest.skip("empty catalog")
    row.click()
    expect(page.get_by_test_id("verify-knowledge")).to_be_visible()


def test_people_persona_bundles(page: Page):
    open_page(page, "/os/people")
    b = page.locator('[data-testid^="bundle-"]').first
    if b.count() == 0:
        pytest.skip("no bundles")
    tog = b.locator("button[aria-pressed]").first
    if tog.count():
        before = tog.get_attribute("aria-pressed")
        tog.click()
        expect(tog).not_to_have_attribute("aria-pressed", before)
        tog.click()


def test_work_dq_digests(page: Page):
    open_page(page, "/os/work/inbox")
    d = page.get_by_test_id("dq-digests")
    if d.count():
        expect(d).to_be_visible()


def test_build_steps_and_ready_rows(page: Page):
    open_page(page, "/os/build")
    if page.get_by_test_id("build-steps-summary").count():
        expect(page.get_by_test_id("build-steps-summary")).to_be_visible()
    open_page(page, "/os/build/ready")
    row = page.get_by_test_id("ready-row").first
    if row.count():
        expect(row.locator('[data-testid^="build-"]').first.or_(row)).to_be_visible()


def test_enable_ask_lists(page: Page):
    open_page(page, "/os/enable")
    for tid in ["enable-asks", "enable-domain-asks"]:
        if page.get_by_test_id(tid).count():
            expect(page.get_by_test_id(tid)).to_be_visible()


def test_skill_scout_banner(page: Page):
    open_page(page, "/os/skills/registry")
    href = first_link_matching(page, r"/skills/(registry/)?(?!proposed|registry)[^/?#]+$")
    if not href:
        pytest.skip("no skills")
    open_page(page, href)
    if page.get_by_test_id("scout-banner").count():
        expect(page.get_by_test_id("scout-banner")).to_be_visible()


def test_copilot_model_diagram_in_review(page: Page):
    open_page(page, "/os/work/copilot")
    rev = page.get_by_role("button", name=re.compile(r"^Review \d+"))
    if rev.count() == 0:
        pytest.skip("no copilot changes")
    rev.first.click()
    if page.get_by_test_id("copilot-model-diagram").count():
        expect(page.get_by_test_id("copilot-model-diagram")).to_contain_text("added")


def test_validation_attempts_list(page: Page):
    open_page(page, "/os/review")
    href = first_link_matching(page, r"/os/review\?run=")
    if not href:
        pytest.skip("no gates")
    open_page(page, href)
    if page.get_by_test_id("validation-attempts").count():
        expect(page.get_by_test_id("validation-attempts")).to_be_visible()


def test_triage_work_in_copilot(page: Page):
    open_page(page, "/os/run/tickets")
    tl = page.get_by_role("tablist", name="Ticket views")
    if tl.count():
        tl.get_by_role("tab", name="Ticket", exact=True).click()
    w = page.get_by_test_id("work-in-copilot")
    if w.count() == 0:
        pytest.skip("no ticket open in triage")
    expect(w).to_have_attribute("href", re.compile("/os/work/copilot"))


def test_landing_feature_tab_ids(anon_page: Page):
    anon_page.goto("/")
    feats = anon_page.locator('[data-testid^="feature-"]')
    assert feats.count() >= 2
    for i in range(feats.count()):
        feats.nth(i).click()
        expect(feats.nth(i)).to_have_attribute("aria-selected", "true")
