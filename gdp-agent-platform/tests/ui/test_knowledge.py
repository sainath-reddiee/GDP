"""Knowledge hub: views, catalog, search/ask, browser, editor, inbox, health, policy."""
import re
import time

import pytest
from playwright.sync_api import Page, expect

from helpers import open_page, relative_url
from routes import KNOWLEDGE_FAMILIES, KNOWLEDGE_VIEWS

KH = "/os/knowledge"


def _view(page: Page, view: str):
    open_page(page, f"{KH}?view={view}" if view else KH, "Knowledge hub")


@pytest.mark.smoke
def test_hub_header_and_actions(page: Page):
    open_page(page, KH, "Knowledge hub")
    expect(page.get_by_role("button", name="Add knowledge").first).to_be_visible()
    expect(page.get_by_role("button", name="Learning policy")).to_be_visible()
    expect(page.get_by_role("link", name="Open skills")).to_have_attribute("href", re.compile("/os/skills"))


@pytest.mark.parametrize("kpi", ["Active records", "Learned in 7 days", "Awaiting curation",
                                 "Verified", "Stale", "Used by runs"])
def test_hub_kpis_visible(page: Page, kpi):
    open_page(page, KH)
    expect(page.get_by_text(kpi, exact=False).first).to_be_visible()


@pytest.mark.parametrize("kpi,view", [("Learned in 7 days", "feed"), ("Awaiting curation", "inbox"),
                                      ("Stale", "health")])
def test_kpi_links_to_view(page: Page, kpi, view):
    open_page(page, KH)
    page.locator(f'a[href*="view={view}"]').first.click()
    expect(page).to_have_url(re.compile(f"view={view}"))


@pytest.mark.parametrize("label,view", KNOWLEDGE_VIEWS, ids=[l for l, _ in KNOWLEDGE_VIEWS])
def test_view_tab_selects(page: Page, label, view):
    open_page(page, KH)
    tablist = page.get_by_role("tablist", name="Knowledge views")
    tablist.get_by_role("tab", name=label, exact=True).click()
    if view:
        expect(page).to_have_url(re.compile(f"view={view}"))
    open_page(page, relative_url(page))
    expect(
        page.get_by_role("tablist", name="Knowledge views").get_by_role("tab", name=label, exact=True)
    ).to_have_attribute("aria-selected", "true")


@pytest.mark.parametrize("label,family", KNOWLEDGE_FAMILIES, ids=[l for l, _ in KNOWLEDGE_FAMILIES])
def test_catalog_family_filter(page: Page, label, family):
    open_page(page, KH)
    page.get_by_text(label, exact=True).first.click()
    if family:
        expect(page).to_have_url(re.compile(f"family={family}"))
    expect(page.get_by_text("Structured knowledge catalog")).to_be_visible()


def test_catalog_table_headers(page: Page):
    open_page(page, KH)
    for h in ["Knowledge record", "Family", "Domain", "Origin", "Ver.", "Updated"]:
        expect(page.get_by_role("columnheader", name=h, exact=True).first).to_be_visible()


def test_catalog_search_submits_query(page: Page):
    open_page(page, KH)
    box = page.get_by_placeholder(re.compile("Search memory"))
    box.fill("customer")
    page.get_by_role("button", name="Search", exact=True).first.click()
    expect(page).to_have_url(re.compile(r"q=customer"))
    expect(page.get_by_text("Structured knowledge catalog")).to_be_visible()


def test_catalog_nonsense_search_empty_state(page: Page):
    open_page(page, f"{KH}?q=zzqx_no_such_knowledge_{int(time.time())}")
    expect(page.get_by_text("No knowledge matches")).to_be_visible()


def test_catalog_domain_filter_options(page: Page):
    open_page(page, KH)
    sel = page.get_by_label("Domain").first
    expect(sel.locator("option").first).to_have_text("All domains")


def test_catalog_record_detail(page: Page):
    open_page(page, KH)
    row = page.locator('a[href*="id="]').first
    if row.count() == 0:
        pytest.skip("catalog is empty")
    row.click()
    expect(page).to_have_url(re.compile(r"id="))
    card = page.get_by_text("Knowledge record").last
    expect(card).to_be_visible()
    for label in ["Origin", "Owner", "Trust", "Confidence"]:
        expect(page.get_by_text(label, exact=True).first).to_be_visible()
    expect(page.get_by_text("Version history")).to_be_visible()


def test_catalog_steward_inbox_and_lifecycle_cards(page: Page):
    open_page(page, KH)
    expect(page.get_by_text("Steward inbox")).to_be_visible()
    expect(page.get_by_text("Contribution lifecycle")).to_be_visible()


# ---------------------------------------------------------------- Ask
def test_ask_buttons_disabled_until_input(page: Page):
    _view(page, "ask")
    box = page.get_by_label("Search or ask")
    search = page.get_by_role("button", name="Search", exact=True)
    ask = page.get_by_role("button", name="Ask AI")
    expect(search).to_be_disabled()
    expect(ask).to_be_disabled()
    box.fill("ab")
    expect(search).to_be_enabled()
    expect(ask).to_be_disabled()
    box.fill("abc")
    expect(ask).to_be_enabled()


def test_ask_search_returns_results_or_empty(page: Page):
    _view(page, "ask")
    page.get_by_label("Search or ask").fill("customer")
    page.get_by_role("button", name="Search", exact=True).click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("body")).not_to_contain_text("Application error")


def test_ask_nonsense_shows_empty(page: Page):
    _view(page, "ask")
    page.get_by_label("Search or ask").fill(f"qqzz_nothing_{int(time.time())}")
    page.get_by_role("button", name="Search", exact=True).click()
    expect(page.get_by_text("No active knowledge matches that.")).to_be_visible()


def test_ask_ai_answers(page: Page):
    _view(page, "ask")
    page.get_by_label("Search or ask").fill("What are the synonyms for the company name?")
    page.get_by_role("button", name="Ask AI").click()
    expect(page.get_by_role("button", name="Ask AI")).to_be_enabled(timeout=120_000)
    expect(page.locator("body")).not_to_contain_text("Application error")


# ---------------------------------------------------------------- All items
def test_items_filters(page: Page):
    _view(page, "items")
    expect(page.get_by_placeholder("Filter by title, content or key")).to_be_visible()
    pipeline = page.get_by_label("Pipeline")
    for opt in ["Both pipelines", "Silver Pipeline", "Gold Pipeline", "Shared only"]:
        expect(pipeline.locator("option", has_text=opt)).to_have_count(1)
    status = page.get_by_label("Status")
    for opt in ["In use", "Retired", "Draft", "Any status"]:
        expect(status.locator("option", has_text=opt)).to_have_count(1)


@pytest.mark.parametrize("label", ["Silver Pipeline", "Gold Pipeline", "Shared only"])
def test_items_pipeline_filter_applies(page: Page, label):
    _view(page, "items")
    page.get_by_label("Pipeline").select_option(label=label)
    page.wait_for_load_state("networkidle")
    expect(page.locator("body")).not_to_contain_text("Application error")


@pytest.mark.parametrize("label", ["Retired", "Draft", "Any status"])
def test_items_status_filter_applies(page: Page, label):
    _view(page, "items")
    page.get_by_label("Status").select_option(label=label)
    page.wait_for_load_state("networkidle")
    expect(page.locator("body")).not_to_contain_text("Application error")


def test_items_text_filter_empty(page: Page):
    _view(page, "items")
    page.get_by_label("Filter knowledge").fill(f"zzqq_none_{int(time.time())}")
    expect(page.get_by_text("No knowledge matches these filters.")).to_be_visible()


def test_items_table_headers(page: Page):
    _view(page, "items")
    for h in ["Item", "Domain", "Origin", "Version", "Updated"]:
        expect(page.get_by_role("columnheader", name=h, exact=True).first).to_be_visible()


def test_items_select_all_shows_bulk_bar(page: Page):
    _view(page, "items")
    sel_all = page.get_by_label("Select all")
    if page.get_by_role("checkbox", name=re.compile("^Select (?!all)")).count() == 0:
        pytest.skip("no items")
    sel_all.check()
    expect(page.get_by_text(re.compile(r"\d+ selected"))).to_be_visible()
    expect(page.get_by_role("button", name="Mark verified")).to_be_visible()
    expect(page.get_by_role("button", name="Retire")).to_be_visible()
    page.get_by_role("button", name="Clear").click()
    expect(page.get_by_text(re.compile(r"\d+ selected"))).to_be_hidden()


def test_items_pagination(page: Page):
    _view(page, "items")
    nxt = page.get_by_role("button", name="Next")
    prev = page.get_by_role("button", name="Previous")
    expect(prev).to_be_visible()
    if nxt.is_enabled():
        nxt.click()
        expect(prev).to_be_enabled()
        prev.click()


def test_item_drawer_opens(page: Page):
    _view(page, "items")
    rows = page.locator("tbody tr")
    if rows.count() == 0:
        pytest.skip("no items")
    rows.first.locator("a, button").filter(has_not_text=re.compile("^$")).first.click()
    drawer = page.get_by_role("dialog")
    expect(drawer).to_be_visible()
    expect(drawer.get_by_text("Where it came from")).to_be_visible()
    drawer.get_by_role("button", name="Close").first.click()
    expect(drawer).to_be_hidden()


def test_editor_validation(page: Page):
    _view(page, "items")
    page.get_by_role("button", name="Add knowledge").last.click()
    dlg = page.get_by_role("dialog", name="Add knowledge")
    expect(dlg).to_be_visible()
    save = dlg.get_by_role("button", name="Save")
    expect(save).to_be_disabled()
    dlg.get_by_label("Title").fill("UI test draft")
    expect(save).to_be_disabled()
    structured = dlg.get_by_label(re.compile("^Structured content"))
    structured.fill("{not json")
    dlg.get_by_label(re.compile(r"^Content \(what people")).fill("body")
    domain = dlg.get_by_label("Domain")
    if domain.locator("option").count() > 1:
        domain.select_option(index=1)
        save.click()
        expect(dlg.get_by_text("Structured content is not valid JSON.")).to_be_visible()
    dlg.get_by_role("button", name="Cancel").click()
    expect(dlg).to_be_hidden()


@pytest.mark.writes
def test_create_edit_retire_knowledge(page: Page):
    title = f"UI-AUTOTEST {int(time.time())}"
    _view(page, "items")
    page.get_by_role("button", name="Add knowledge").last.click()
    dlg = page.get_by_role("dialog", name="Add knowledge")
    dlg.get_by_label("Domain").select_option(index=1)
    dlg.get_by_label("Title").fill(title)
    dlg.get_by_label(re.compile(r"^Content \(what people")).fill("Created by automated UI test.")
    dlg.get_by_role("button", name="Save").click()
    expect(dlg).to_be_hidden()
    page.get_by_label("Filter knowledge").fill(title)
    expect(page.get_by_text(title).first).to_be_visible()
    page.get_by_label(f"Select {title}").check()
    page.get_by_role("button", name="Retire").click()
    page.get_by_label("Filter knowledge").fill(title)
    expect(page.get_by_text("No knowledge matches these filters.")).to_be_visible()


# ---------------------------------------------------------------- Inbox / health / feed / domains
def test_inbox_view(page: Page):
    _view(page, "inbox")
    empty = page.get_by_text("Inbox zero")
    if empty.count():
        expect(empty).to_be_visible()
        return
    expect(page.get_by_placeholder("Review note (optional)")).to_be_visible()
    expect(page.get_by_role("button", name=re.compile(r"^Approve \d+"))).to_be_visible()
    page.get_by_role("button", name="Diff").first.click()


def test_health_view(page: Page):
    _view(page, "health")
    expect(page.get_by_text(re.compile(r"Stale \(\d+\)|Nothing is stale\."))).to_be_visible()
    expect(page.get_by_text(re.compile(r"Likely duplicates \(\d+\)|No likely duplicates\."))).to_be_visible()


def test_feed_view(page: Page):
    _view(page, "feed")


def test_domains_view_and_domain_page(page: Page):
    _view(page, "domains")
    link = page.locator('a[href*="/os/knowledge/domains/"]').first
    if link.count() == 0:
        pytest.skip("no domains")
    link.click()
    expect(page).to_have_url(re.compile(r"/os/knowledge/domains/"))
    open_page(page, relative_url(page))
    expect(page.get_by_test_id("playbook").or_(page.get_by_test_id("domain-readiness")).first).to_be_visible()


def test_domain_page_layer_toggle(page: Page):
    _view(page, "domains")
    link = page.locator('a[href*="/os/knowledge/domains/"]').first
    if link.count() == 0:
        pytest.skip("no domains")
    link.click()
    group = page.get_by_role("group", name="Layer")
    if group.count() == 0:
        pytest.skip("no layer toggle on this domain")
    group.get_by_role("button", name="Gold").click()
    group.get_by_role("button", name="Silver").click()


# ---------------------------------------------------------------- Policy
def test_learning_policy_dialog(page: Page):
    open_page(page, KH)
    page.get_by_role("button", name="Learning policy").click()
    dlg = page.get_by_role("dialog", name="Learning policy")
    expect(dlg).to_be_visible()
    expect(dlg.get_by_role("button", name="Use at once").first).to_be_visible()
    expect(dlg.get_by_role("button", name="Review first").first).to_be_visible()
    first = dlg.get_by_role("button", name="Review first").first
    first.click()
    expect(first).to_have_attribute("aria-pressed", "true")
    dlg.get_by_role("button", name="Close").click()
    expect(dlg).to_be_hidden()


@pytest.mark.writes
def test_learning_policy_save(page: Page):
    open_page(page, KH)
    page.get_by_role("button", name="Learning policy").click()
    dlg = page.get_by_role("dialog", name="Learning policy")
    dlg.get_by_role("button", name="Save policy").click()
    expect(dlg.get_by_text("Saved. New learning follows this policy.")).to_be_visible()
