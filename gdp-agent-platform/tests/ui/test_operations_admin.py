"""Run & DQ / Operations, Admin, People, Help, Copilot."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import first_link_matching, open_page


def test_production_operations(page: Page):
    open_page(page, "/os/run", "Production operations")


def test_quality_center(page: Page):
    open_page(page, "/os/run/quality", "QA and Data Quality Center")


def test_pipelines_monitoring(page: Page):
    open_page(page, "/os/run/ops", "Pipelines")


def test_dag_detail(page: Page):
    open_page(page, "/os/run/ops")
    href = first_link_matching(page, r"/ops/dag")
    if not href:
        pytest.skip("no DAGs")
    open_page(page, href)


def test_incidents_and_detail(page: Page):
    open_page(page, "/os/run/incidents", "Incidents")
    href = first_link_matching(page, r"/incidents/[^/?#]+$")
    if href:
        open_page(page, href)


def test_resolutions(page: Page):
    open_page(page, "/os/run/ops/resolutions", "Resolutions")
    expect(page.get_by_test_id("failure-table").or_(page.get_by_test_id("guardrails")).first).to_be_visible()
    href = first_link_matching(page, r"/ops/resolutions/[^/?#]+$")
    if href:
        open_page(page, href)
        expect(page.get_by_test_id("rca")).to_be_visible()


def test_known_fixes(page: Page):
    open_page(page, "/os/run/ops/fixes", "Known fixes")
    expect(page.get_by_test_id("fix-table")).to_be_visible()


def test_new_known_fix_form(page: Page):
    open_page(page, "/os/run/ops/fixes")
    href = first_link_matching(page, r"/ops/fixes/new")
    if not href:
        pytest.skip("no 'new fix' link")
    open_page(page, href, "New known fix")
    expect(page.get_by_test_id("fix-form")).to_be_visible()


def test_permanent_fixes(page: Page):
    open_page(page, "/os/run/ops/permanent-fixes", "Permanent fixes")
    expect(page.get_by_test_id("permanent-fix-table")).to_be_visible()


def test_permanent_fix_missing(page: Page):
    page.goto("/os/run/ops/permanent-fixes/does-not-exist-000")
    expect(page.get_by_test_id("permanent-fix-missing")).to_be_visible()


def test_tickets(page: Page):
    open_page(page, "/os/run/tickets", "Tickets")
    expect(page.get_by_test_id("sprint-bar").or_(page.get_by_test_id("sprint-control")).first).to_be_visible()


def test_people(page: Page):
    open_page(page, "/os/people", "People and personas")
    expect(page.locator('[data-testid^="person-"]').first).to_be_visible()


def test_admin_settings_and_audit(page: Page):
    open_page(page, "/os/admin/settings", "Admin")
    open_page(page, "/os/admin/audit", "Audit")


def test_help(page: Page):
    open_page(page, "/os/help", "One workflow, many personas")


def test_copilot_page(page: Page):
    open_page(page, "/os/work/copilot")
    expect(page.locator("textarea, input[type=text]").first).to_be_visible()


@pytest.mark.writes
def test_copilot_turn(page: Page):
    open_page(page, "/os/work/copilot")
    box = page.locator("textarea").first
    box.fill("Hello, what can you do?")
    box.press("Enter")
    expect(page.get_by_text(re.compile(".{20,}")).last).to_be_visible(timeout=120_000)


def test_work_inbox_tiles(page: Page):
    open_page(page, "/os/work/inbox")
    expect(page.get_by_test_id("work-tiles")).to_be_visible()
