"""Run audit, Data Quality scan, QA workspace, tickets workspace, DQ run checks, DAG page,
quality center, enable/ready/history/design boards, My Work."""
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


# ---------------------------------------------------------------- run audit / soda
def test_run_audit_table(page: Page, run_url):
    open_page(page, run_url + "/audit")
    for col in ["When", "From", "To", "Actor", "Reason"]:
        expect(page.get_by_role("columnheader", name=col).first.or_(
            page.get_by_text("No events match."))).to_be_visible()
    expect(page.get_by_text("Review decisions")).to_be_visible()


def test_soda_scan_button(page: Page, run_url):
    open_page(page, run_url + "/soda")
    btn = page.get_by_role("button", name=re.compile("^Run (first )?scan$"))
    if btn.count() == 0:
        pytest.skip("no checks to scan yet")
    expect(btn).to_be_visible()


@pytest.mark.writes
def test_soda_run_scan(page: Page, run_url):
    open_page(page, run_url + "/soda")
    btn = page.get_by_role("button", name=re.compile("^Run (first )?scan$"))
    if btn.count() == 0:
        pytest.skip("no checks")
    btn.click()
    expect(page.get_by_role("status").filter(has_text=re.compile(r"\d+ pass"))).to_be_visible(timeout=600_000)


# ---------------------------------------------------------------- QA workspace
QA = "/os/release/qa"


def test_qa_suite_inner_tabs(page: Page):
    open_page(page, QA)
    for t in ["Tests", "Ask AI", "History"]:
        tab = page.get_by_role("tab", name=t).or_(page.get_by_role("button", name=t, exact=True))
        if tab.count():
            tab.first.click()
            expect(page.locator("body")).not_to_contain_text("Application error")


def test_qa_rename_form(page: Page):
    open_page(page, QA)
    r = page.get_by_role("button", name="Rename")
    if r.count() == 0:
        pytest.skip("no suite selected")
    r.first.click()
    expect(page.get_by_label("New suite name")).to_be_visible()
    page.keyboard.press("Escape")


def test_qa_results_actions(page: Page):
    open_page(page, QA)
    page.get_by_role("tablist", name="QA views").get_by_role("tab", name="Results").click()
    for b in ["Create Jira bug", "Comment on linked ticket", "Open in Suites"]:
        el = page.get_by_role("button", name=b).or_(page.get_by_role("link", name=b))
        if el.count():
            expect(el.first).to_be_visible()


def test_qa_create_jira_bug_dialog(page: Page):
    open_page(page, QA)
    page.get_by_role("tablist", name="QA views").get_by_role("tab", name="Results").click()
    b = page.get_by_role("button", name="Create Jira bug")
    if b.count() == 0:
        pytest.skip("no failing results")
    b.first.click()
    expect(page.get_by_role("dialog", name="Create a Jira bug")).to_be_visible()
    expect(page.get_by_role("button", name="Create bug")).to_be_visible()
    page.keyboard.press("Escape")


# ---------------------------------------------------------------- tickets workspace (Jira inbox / triage)
@pytest.mark.parametrize("tab", ["All tickets", "Jira search and boards", "Ticket"])
def test_tickets_workspace_tabs(page: Page, tab):
    open_page(page, "/os/run/tickets")
    tl = page.get_by_role("tablist", name="Ticket views")
    if tl.count() == 0:
        pytest.skip("tickets workspace tabs not shown")
    tl.get_by_role("tab", name=tab, exact=True).click()
    expect(page.locator("body")).not_to_contain_text("Application error")


def test_jira_inbox_sources(page: Page):
    open_page(page, "/os/run/tickets")
    tl = page.get_by_role("tablist", name="Ticket views")
    if tl.count():
        tl.get_by_role("tab", name="Jira search and boards").click()
    src = page.get_by_role("tablist", name="Inbox source")
    if src.count() == 0:
        pytest.skip("Jira not connected")
    for t in ["Assigned to me", "JQL", "Saved filters", "Boards"]:
        src.get_by_role("tab", name=t).click()
    page.get_by_label("JQL").fill("project = NOPE")
    page.get_by_role("button", name="Run").click()


def test_ticket_triage_open(page: Page):
    open_page(page, "/os/run/tickets")
    tl = page.get_by_role("tablist", name="Ticket views")
    if tl.count():
        tl.get_by_role("tab", name="Ticket", exact=True).click()
    key = page.get_by_label("Issue key")
    if key.count() == 0:
        pytest.skip("triage not available")
    expect(key).to_have_attribute("placeholder", re.compile("QA-123"))
    key.fill("NOPE-1")
    page.get_by_role("button", name="Open").click()
    expect(page.locator("body")).not_to_contain_text("Application error")


# ---------------------------------------------------------------- DQ run checks
def _run_checks(page):
    open_page(page, "/os/design/dq-store")
    rc = page.get_by_test_id("run-checks")
    if rc.count() == 0:
        link = page.get_by_role("link", name=re.compile("Run checks|Proposed checks"))
        if link.count():
            link.first.click()
        rc = page.get_by_test_id("run-checks")
    if rc.count() == 0:
        pytest.skip("run checks view not reachable")
    return rc


def test_dq_run_checks_filters(page: Page):
    rc = _run_checks(page)
    run = rc.get_by_label("Run", exact=True)
    for i in range(min(run.locator("option").count(), 3)):
        run.select_option(index=i)
    rc.get_by_label("Filter checks").fill("zz")
    rc.get_by_label("Filter checks").fill("")


def test_dq_run_checks_coverage(page: Page):
    rc = _run_checks(page)
    rc.get_by_role("button", name="Coverage").click()
    expect(page.get_by_test_id("coverage")).to_be_visible()


def test_dq_run_checks_decision_modal(page: Page):
    rc = _run_checks(page)
    boxes = rc.get_by_role("checkbox", name=re.compile("^Select "))
    if boxes.count() == 0:
        pytest.skip("no proposed checks")
    boxes.first.check()
    rc.get_by_test_id("approve-checks").click()
    expect(page.locator("#decide-note")).to_be_visible()
    expect(page.get_by_test_id("confirm-decision")).to_be_visible()
    page.keyboard.press("Escape")


@pytest.mark.writes
def test_journey_dq_reject_checks(page: Page):
    rc = _run_checks(page)
    boxes = rc.get_by_role("checkbox", name=re.compile("^Select "))
    if boxes.count() == 0:
        pytest.skip("no proposed checks")
    boxes.first.check()
    rc.get_by_role("button", name="Reject").click()
    page.locator("#decide-note").fill("UI-AUTOTEST")
    page.get_by_test_id("confirm-decision").click()
    expect(page.get_by_text(re.compile(r"\d+ checks? rejected"))).to_be_visible()


# ---------------------------------------------------------------- DAG
@pytest.fixture
def dag_url(page: Page):
    open_page(page, "/os/run/ops")
    href = first_link_matching(page, r"/ops/dag")
    if not href:
        pytest.skip("no DAGs")
    return href


def test_dag_tasks_and_runs(page: Page, dag_url):
    open_page(page, dag_url)
    t = page.get_by_role("button", name="Show tasks")
    if t.count():
        t.click()
        expect(page.get_by_role("button", name="Hide tasks")).to_be_visible()
        page.get_by_role("button", name="Hide tasks").click()
    run = page.locator('a[href*="run="], button:has-text("run")').first
    if run.count():
        run.click()
        close = page.get_by_role("button", name="Close run")
        if close.count():
            close.click()


def test_dag_settings_form(page: Page, dag_url):
    open_page(page, dag_url)
    s = page.get_by_role("button", name="Settings")
    s.click()
    expect(s).to_have_attribute("aria-expanded", "true")
    for lbl in ["Criticality", "Expected by (cron)", "Max duration (minutes)", "Team", "Time zone"]:
        expect(page.get_by_label(lbl)).to_be_visible()
    page.get_by_role("button", name="Cancel").click()


def test_dag_log_viewer(page: Page, dag_url):
    open_page(page, dag_url)
    log = page.get_by_role("button", name="Log", exact=True)
    if log.count() == 0:
        pytest.skip("no task logs")
    log.first.click()
    page.get_by_label("Only error lines").check()
    page.get_by_role("button", name="Hide log").first.click()


def test_dag_open_in_airflow_link(page: Page, dag_url):
    open_page(page, dag_url)
    a = page.get_by_role("link", name="Open in Airflow")
    if a.count():
        assert a.get_attribute("href").startswith("http")


@pytest.mark.writes
def test_dag_poll_now_and_save_settings(page: Page, dag_url):
    open_page(page, dag_url)
    page.get_by_role("button", name="Poll now").click()
    expect(page.get_by_text(re.compile("^Polling .* now"))).to_be_visible()
    page.get_by_role("button", name="Settings").click()
    page.get_by_role("button", name="Save settings").click()
    expect(page.get_by_text("DAG settings saved.")).to_be_visible()


# ---------------------------------------------------------------- quality center
@pytest.mark.parametrize("view", ["Test executions", "Readiness gates", "Suites and expectations",
                                  "Jira incidents"])
def test_quality_views(page: Page, view):
    open_page(page, "/os/run/quality", "QA and Data Quality Center")
    page.get_by_role("navigation", name="Quality views").get_by_role("link", name=view).click()
    page.wait_for_load_state("load")
    expect(page.locator("body")).not_to_contain_text("Application error")


@pytest.mark.parametrize("btn", ["QA Store", "DQ Store", "QA testing"])
def test_quality_shortcuts(page: Page, btn):
    open_page(page, "/os/run/quality")
    el = page.get_by_role("link", name=btn).or_(page.get_by_role("button", name=btn)).first
    el.click()
    page.wait_for_load_state("load")
    expect(page.locator("body")).not_to_contain_text("Application error")


# ---------------------------------------------------------------- boards
def test_enable_source_cards(page: Page):
    open_page(page, "/os/enable", "Source readiness")
    card = page.get_by_test_id("source-config").first
    if card.count():
        for f in ["Connection", "Lands in", "Schedule"]:
            expect(card.get_by_text(f).first).to_be_visible()
        expect(card.get_by_text(re.compile("^(Ready|Not landed|Unreachable)$")).first).to_be_visible()


def test_build_ready_controls(page: Page):
    open_page(page, "/os/build/ready", "Ready to build")
    toggle = page.get_by_role("link", name=re.compile("^(Show|Hide) built runs"))
    if toggle.count():
        toggle.click()
        page.wait_for_load_state("load")
        expect(page.get_by_test_id("ready-board")).to_be_visible()


@pytest.mark.writes
def test_build_end_to_end(page: Page):
    open_page(page, "/os/build/ready")
    b = page.get_by_test_id("build-end-to-end").first
    if b.count() == 0:
        pytest.skip("nothing ready")
    b.click()
    expect(page.locator("body")).not_to_contain_text("Application error")


@pytest.mark.parametrize("outcome", ["All", "Active", "In flight", "Drafts", "Completed", "Failed", "Archived"])
def test_history_outcome_filter(page: Page, outcome):
    open_page(page, "/os/history", "Run history")
    page.locator("#h-status").select_option(label=outcome)
    page.wait_for_load_state("load")
    expect(page.get_by_test_id("run-history")).to_be_visible()


@pytest.mark.parametrize("pipeline", ["Silver and Gold", "Silver", "Gold"])
def test_history_pipeline_filter(page: Page, pipeline):
    open_page(page, "/os/history")
    page.locator("#h-pipeline").select_option(label=pipeline)
    page.wait_for_load_state("load")
    expect(page.get_by_test_id("run-history")).to_be_visible()


def test_history_search_and_domain(page: Page):
    open_page(page, "/os/history")
    expect(page.locator("#h-q")).to_have_attribute("placeholder", "Run, source or table")
    page.locator("#h-q").fill("zz_none")
    page.locator("#h-q").press("Enter")
    page.wait_for_load_state("load")
    dom = page.locator("#h-domain")
    if dom.locator("option").count() > 1:
        dom.select_option(index=1)


@pytest.mark.parametrize("tab", ["Selected", "Action required", "Active", "Completed"])
def test_design_board_tabs(page: Page, tab):
    open_page(page, "/os/design")
    tabs = page.get_by_test_id("board-tabs")
    if tabs.count() == 0:
        pytest.skip("no board tabs")
    tabs.get_by_text(tab, exact=True).first.click()
    expect(page.get_by_test_id("design-board")).to_be_visible()


def test_design_board_select_run(page: Page):
    open_page(page, "/os/design")
    card = page.get_by_test_id("run-card").first
    if card.count() == 0:
        pytest.skip("no runs on board")
    card.click()
    expect(page).to_have_url(re.compile(r"view=selected&run="))
    sel = page.get_by_test_id("design-selected")
    expect(sel).to_be_visible()
    expect(sel.get_by_role("link", name="Run workspace").or_(
        sel.get_by_role("button", name="Run workspace"))).to_be_visible()


@pytest.mark.parametrize("step", ["profile", "model", "map"])
def test_design_board_steps(page: Page, step):
    open_page(page, "/os/design")
    s = page.get_by_test_id(f"design-steps-{step}")
    if s.count():
        expect(s).to_be_visible()


# ---------------------------------------------------------------- My Work
@pytest.mark.parametrize("tile,anchor", [("Awaiting your approval", "approve"), ("Your runs in flight", "runs"),
                                         ("Needs a fix", "fix"), ("Questions and proposals", "knowledge")])
def test_work_tiles_anchor(page: Page, tile, anchor):
    open_page(page, "/os/work/inbox")
    page.get_by_test_id("work-tiles").get_by_role("link", name=re.compile(tile)).click()
    expect(page).to_have_url(re.compile(f"#{anchor}"))


def test_work_row_actions_link_somewhere(page: Page):
    open_page(page, "/os/work/inbox")
    links = page.get_by_role("link", name=re.compile("^(Review|Decide|Publish)$"))
    for i in range(min(links.count(), 5)):
        href = links.nth(i).get_attribute("href")
        assert href and href.startswith("/")
        open_page(page, href)
        open_page(page, "/os/work/inbox")


def test_work_all_approvals_link(page: Page):
    open_page(page, "/os/work/inbox")
    a = page.get_by_role("link", name="All approvals")
    if a.count():
        a.click()
        expect(page).to_have_url(re.compile("approvals"))
