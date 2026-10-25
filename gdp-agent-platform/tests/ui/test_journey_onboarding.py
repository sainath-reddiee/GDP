"""Journeys: source onboarding (share wizard + ingestion wizard) and domain onboarding."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import open_page, unique

ONB = "/os/enable/onboarding?type=source"


# ---------------------------------------------------------------- choice steps
def test_source_type_choice_and_change(page: Page):
    open_page(page, ONB)
    group = page.get_by_test_id("onboard-source")
    expect(group.get_by_test_id("onboard-source-share")).to_be_visible()
    expect(group.get_by_test_id("onboard-source-external")).to_be_visible()
    group.get_by_test_id("onboard-source-share").click()
    expect(page).to_have_url(re.compile("branch="))
    expect(page.get_by_test_id("onboard-source-chosen")).to_be_visible()
    page.get_by_test_id("onboard-source-change").click()
    expect(page.get_by_test_id("onboard-source-external")).to_be_visible()


@pytest.mark.parametrize("kind", ["database", "file"])
def test_external_kind_choice_opens_ingestion_wizard(page: Page, kind):
    open_page(page, ONB)
    page.get_by_test_id("onboard-source-external").click()
    page.get_by_test_id(f"onboard-kind-of-source-{kind}").click()
    expect(page).to_have_url(re.compile(f"kind={kind}"))
    expect(page.get_by_test_id("ingestion-wizard")).to_be_visible()


def test_choice_step_keyboard_shortcut(page: Page):
    open_page(page, ONB)
    page.keyboard.press("1")
    expect(page.get_by_test_id("onboard-source-chosen")).to_be_visible()


# ---------------------------------------------------------------- share wizard
def _share(page):
    open_page(page, ONB)
    page.get_by_test_id("onboard-source-share").click()
    wiz = page.get_by_test_id("share-wizard")
    expect(wiz).to_be_visible()
    return wiz


def test_share_wizard_steps_listed(page: Page):
    wiz = _share(page)
    steps = wiz.get_by_label("Steps")
    for s in ["Share", "Schema", "Tables", "Landing", "Load"]:
        expect(steps.get_by_text(s, exact=True).first).to_be_visible()


def test_share_wizard_search_and_unlisted_share(page: Page):
    wiz = _share(page)
    wiz.get_by_label("Search shares").fill("zz_no_such_share")
    wiz.get_by_text("The share is not listed").click()
    for f in ["Provider account", "Share name", "Mount as database"]:
        expect(wiz.get_by_label(f)).to_be_visible()


def test_share_wizard_walk_to_landing(page: Page):
    wiz = _share(page)
    shares = wiz.get_by_label("Shares").get_by_role("radio")
    if shares.count() == 0:
        pytest.skip("no shares visible")
    shares.first.check()
    if wiz.get_by_test_id("mount-share").is_visible():
        pytest.skip("share needs mounting (write)")
    wiz.get_by_test_id("share-next").click()
    schemas = wiz.get_by_label("Schemas").get_by_role("radio")
    expect(schemas.first).to_be_visible()
    schemas.first.check()
    wiz.get_by_test_id("share-next").click()
    wiz.get_by_label("Filter tables").fill("")
    wiz.get_by_role("button", name="Select shown").click()
    wiz.get_by_role("button", name="Clear").click()
    wiz.get_by_role("button", name="Select shown").click()
    wiz.get_by_test_id("share-next").click()
    expect(wiz.locator("#src")).to_be_visible()
    wiz.locator("#src").fill("UIAUTO_SHARE")
    expect(wiz.get_by_test_id("landing-preview")).to_contain_text("UIAUTO_SHARE")
    wiz.get_by_role("button", name="Back").click()
    expect(wiz.get_by_label("Filter tables")).to_be_visible()


@pytest.mark.writes
def test_journey_share_land_and_continue(page: Page):
    wiz = _share(page)
    shares = wiz.get_by_label("Shares").get_by_role("radio")
    if shares.count() == 0:
        pytest.skip("no shares")
    shares.first.check()
    if wiz.get_by_test_id("mount-share").is_visible():
        wiz.get_by_test_id("mount-share").click()
    wiz.get_by_test_id("share-next").click()
    wiz.get_by_label("Schemas").get_by_role("radio").first.check()
    wiz.get_by_test_id("share-next").click()
    wiz.get_by_role("checkbox").first.check()
    wiz.get_by_test_id("share-next").click()
    wiz.locator("#src").fill("UIAUTO_SHARE")
    wiz.locator("#run").fill(unique("UI-AUTOTEST share run"))
    if wiz.locator("#lsc option").count() > 1:
        wiz.locator("#lsc").select_option(index=1)
    while wiz.get_by_test_id("share-run").count() == 0:
        wiz.get_by_test_id("share-next").click()
    wiz.get_by_test_id("share-run").click()
    expect(wiz.get_by_test_id("share-result")).to_be_visible(timeout=600_000)
    page.get_by_role("link", name=re.compile("Continue to profiling|Open the run")).click()
    expect(page).to_have_url(re.compile(r"/os/build/runs/"))


# ---------------------------------------------------------------- ingestion wizard
def _ingest(page, kind="database"):
    open_page(page, f"{ONB}&branch=external&kind={kind}")
    wiz = page.get_by_test_id("ingestion-wizard")
    if wiz.count() == 0:
        page.get_by_test_id("onboard-source-external").click()
        page.get_by_test_id(f"onboard-kind-of-source-{kind}").click()
    expect(wiz).to_be_visible()
    return wiz


@pytest.mark.parametrize("sec", ["source", "tables", "target", "load", "snaplogic"])
def test_ingestion_section_nav(page: Page, sec):
    wiz = _ingest(page)
    wiz.get_by_test_id(f"go-{sec}").click()
    expect(wiz.get_by_test_id(f"section-{sec}")).to_be_in_viewport()


def test_ingestion_check_empty_shows_problems(page: Page):
    wiz = _ingest(page)
    wiz.get_by_test_id("check-ingestion").click()
    expect(wiz.get_by_test_id("ingestion-problems")).to_be_visible()


def test_ingestion_table_add_copy_remove(page: Page):
    wiz = _ingest(page)
    blocks = wiz.locator('[data-testid^="table-"]')
    start = blocks.count()
    wiz.get_by_role("button", name="Add table").click()
    expect(blocks).to_have_count(start + 1)
    wiz.get_by_role("button", name=f"Copy table {start + 1}").click()
    expect(blocks).to_have_count(start + 2)
    wiz.get_by_role("button", name=f"Remove table {start + 2}").click()
    wiz.get_by_role("button", name=f"Remove table {start + 1}").click()
    expect(blocks).to_have_count(start)


def test_ingestion_file_kind_fields(page: Page):
    wiz = _ingest(page, "file")
    expect(wiz.locator("#source_file_type")).to_be_visible()
    expect(wiz.locator("#source_name")).to_have_attribute("placeholder", "CRM_FILES")


def test_ingestion_check_valid_config(page: Page):
    wiz = _ingest(page)
    wiz.locator("#source_name").fill("UIAUTO_PG")
    for sel in ["#source_type", "#domain", "#load_type", "#bronze_load_type", "#load_frequency"]:
        el = wiz.locator(sel)
        if el.count() and el.evaluate("e => e.tagName") == "SELECT" and el.locator("option").count() > 1:
            el.select_option(index=1)
    wiz.locator("#sql-0").fill("PUBLIC.CUSTOMERS")
    wiz.locator("#pk-0").fill("ID")
    wiz.get_by_test_id("check-ingestion-end").click()
    expect(wiz.get_by_test_id("ingestion-problems").or_(wiz.get_by_test_id("go-review")).first).to_be_visible()


@pytest.mark.writes
def test_journey_save_ingestion_configuration(page: Page):
    wiz = _ingest(page)
    wiz.locator("#source_name").fill(unique("UIAUTO").replace(" ", "_"))
    wiz.locator("#sql-0").fill("PUBLIC.CUSTOMERS")
    wiz.locator("#pk-0").fill("ID")
    wiz.get_by_test_id("check-ingestion-end").click()
    if wiz.get_by_test_id("ingestion-problems").is_visible():
        pytest.skip("environment needs more config: " + wiz.get_by_test_id("ingestion-problems").inner_text())
    wiz.get_by_test_id("save-ingestion-end").click()
    saved = page.get_by_test_id("ingestion-saved")
    expect(saved.get_by_role("heading", name="Configuration saved")).to_be_visible()
    saved.get_by_role("link", name="Open in the Source Store").click()
    expect(page).to_have_url(re.compile("/os/design/sources"))


# ---------------------------------------------------------------- domain onboarding
@pytest.mark.parametrize("how", ["import", "draft", "design"])
def test_domain_onboarding_paths(page: Page, how):
    open_page(page, "/os/enable/new-domain", "Onboard domain")
    page.get_by_test_id(f"onboard-domain-{how}").click()
    expect(page).to_have_url(re.compile(f"how={how}"))


def test_domain_import_modal(page: Page):
    open_page(page, "/os/enable/new-domain?how=import")
    page.get_by_test_id("import-domain").click()
    dlg = page.get_by_role("dialog", name="Import a domain")
    expect(dlg).to_be_visible()
    expect(dlg.get_by_role("button", name="Validate pack")).to_be_visible()
    dlg.get_by_role("button", name="Close").click()
    expect(dlg).to_be_hidden()


def test_domain_design_links(page: Page):
    open_page(page, "/os/enable/new-domain?how=design")
    page.get_by_role("link", name="New table model").click()
    expect(page).to_have_url(re.compile(r"/os/design/modeling\?.*view=tables"))


def test_domain_handoff_banner(page: Page):
    open_page(page, "/os/enable/new-domain?request=abc")
    expect(page.get_by_test_id("domain-handoff")).to_be_visible()
    expect(page.get_by_role("link", name="Back to the request")).to_be_visible()


def test_legacy_domain_onboarding_redirect(page: Page):
    page.goto("/dashboard/onboarding?type=domain")
    expect(page).to_have_url(re.compile("/os/enable/new-domain"))
