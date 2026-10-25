"""Journey: intake -> onboarding request -> request detail lifecycle."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import discover, open_page, unique

NEW = "/os/intake/new"


# ---------------------------------------------------------------- type / intent selection
@pytest.mark.parametrize("tab,qs", [("Source", "type=source"), ("Domain", "type=domain"),
                                    ("Data product", "type=product")])
def test_request_type_tabs(page: Page, tab, qs):
    open_page(page, NEW)
    page.get_by_role("tablist", name="What is requested").get_by_role("tab", name=re.compile(tab)).click()
    expect(page).to_have_url(re.compile(qs))


def test_data_product_is_coming_soon(page: Page):
    open_page(page, f"{NEW}?type=product")
    expect(page.get_by_test_id("product-coming-soon")).to_be_visible()
    expect(page.get_by_test_id("submit-request-button")).to_have_count(0)


@pytest.mark.parametrize("intent", ["new", "enhance"])
def test_intent_tabs(page: Page, intent):
    open_page(page, f"{NEW}?type=domain")
    page.get_by_test_id("intake-intent").get_by_role("tab", name=intent.capitalize(), exact=True).click()
    expect(page).to_have_url(re.compile(f"intent={intent}"))


# ---------------------------------------------------------------- domain / new form validation
def test_domain_request_requires_title_then_domain(page: Page):
    open_page(page, f"{NEW}?type=domain&intent=new")
    form = page.get_by_test_id("domain-request")
    submit = form.get_by_test_id("submit-request-button")
    expect(submit).to_be_disabled()
    expect(form.get_by_text("Give the request a title.")).to_be_visible()
    form.locator("#rq-title").fill(unique())
    expect(form.get_by_text("Name the new domain.")).to_be_visible()
    expect(submit).to_be_disabled()
    form.locator("#rq-domain").fill("UITEST")
    expect(submit).to_be_enabled()


def test_domain_request_priority_options(page: Page):
    open_page(page, f"{NEW}?type=domain")
    pr = page.locator("#rq-priority")
    expect(pr).to_have_value(re.compile("(?i)medium"))
    for label in ["Low", "Medium", "High"]:
        pr.select_option(label=label)


def test_domain_request_add_and_remove_new_source(page: Page):
    open_page(page, f"{NEW}?type=domain")
    page.get_by_label("Unregistered source name").fill("Yardi")
    page.get_by_label("What is needed from the source").fill("lease tables")
    page.get_by_role("button", name="Add", exact=True).click()
    remove = page.get_by_role("button", name="Remove Yardi")
    expect(remove).to_be_visible()
    remove.click()
    expect(remove).to_have_count(0)


def test_domain_request_file_picker_and_sample(page: Page):
    open_page(page, f"{NEW}?type=domain")
    picker = page.get_by_test_id("request-file-picker")
    expect(picker.get_by_role("button", name="Attach files")).to_be_visible()
    picker.get_by_label("Domain context files").set_input_files(
        files=[{"name": "ctx.json", "mimeType": "application/json", "buffer": b'{"domain":"UITEST"}'}])
    expect(picker.get_by_text("ctx.json")).to_be_visible()
    with page.expect_download() as dl:
        page.get_by_test_id("sample-domain-json").click()
    assert dl.value.suggested_filename.endswith(".json")


def test_domain_enhance_requires_domain(page: Page):
    open_page(page, f"{NEW}?type=domain&intent=enhance")
    sel = page.get_by_test_id("domain-enhance").locator("#domain")
    expect(sel).to_be_visible()
    opts = sel.locator("option")
    if opts.count() < 2:
        pytest.skip("no onboarded domains")
    sel.select_option(index=1)
    expect(page).to_have_url(re.compile(r"domain="))
    expect(page.get_by_text("What the domain needs")).to_be_visible()


# ---------------------------------------------------------------- source form
@pytest.mark.parametrize("intent", ["new", "enhance"])
def test_source_request_form(page: Page, intent):
    open_page(page, f"{NEW}?type=source&intent={intent}")
    expect(page.get_by_test_id("submit-request")).to_be_visible()
    expect(page.get_by_test_id("flow-preview")).to_contain_text(
        "New source flow" if intent == "new" else "Enhance flow")
    expect(page.get_by_test_id("submit-request-button")).to_be_disabled()


def test_source_request_select_source_enables_scope(page: Page):
    open_page(page, f"{NEW}?type=source&intent=enhance")
    boxes = page.get_by_test_id("sources-feeding").get_by_role("checkbox")
    if boxes.count() == 0:
        pytest.skip("no registered sources")
    name = boxes.first.get_attribute("aria-label")
    boxes.first.check()
    expect(page.get_by_test_id(f"scope-{name}")).to_be_visible()


# ---------------------------------------------------------------- full journeys (writes)
@pytest.mark.writes
def test_journey_file_domain_request_and_manage_it(page: Page):
    title = unique("UI-AUTOTEST domain request")
    open_page(page, f"{NEW}?type=domain&intent=new")
    page.locator("#rq-title").fill(title)
    page.locator("#rq-priority").select_option(label="High")
    page.locator("#rq-outcome").fill("Automated test outcome")
    page.locator("#rq-domain").fill("UIAUTO")
    page.get_by_test_id("submit-request-button").click()
    anyway = page.get_by_test_id("submit-anyway")
    if anyway.is_visible():
        anyway.click()
    page.wait_for_url(re.compile(r"/os/requests/[^/?]+$"))
    expect(page.locator("h1")).to_contain_text(title)
    expect(page.get_by_label("Request path")).to_be_visible()

    controls = page.get_by_test_id("request-controls")
    note = controls.get_by_label("Comment or reason")
    note.fill("hi")
    controls.get_by_test_id("request-comment").click()  # < 3 chars rejected
    expect(page.get_by_text("Comment added.")).to_have_count(0)
    note.fill("Automated comment")
    controls.get_by_test_id("request-comment").click()
    expect(page.get_by_text("Comment added.")).to_be_visible()

    files = page.get_by_test_id("request-files")
    files.locator("input[type=file]").set_input_files(
        files=[{"name": "notes.txt", "mimeType": "text/plain", "buffer": b"ui test"}])
    files.get_by_test_id("upload-request-files").click()
    expect(page.get_by_text(re.compile(r"\d+ files? attached\."))).to_be_visible()
    files.get_by_role("button", name="Remove notes.txt").click()

    note.fill("Cancelled by automated test")
    controls.get_by_test_id("request-cancel").click()
    expect(page.get_by_text("Request cancelled.")).to_be_visible()
    expect(page.get_by_text(re.compile("Cancelled by"))).to_be_visible()
    controls.get_by_test_id("request-reopen").click()
    expect(page.get_by_text("Request reopened.")).to_be_visible()
    note.fill("Rejected by automated test")
    controls.get_by_test_id("request-reject").click()
    expect(page.get_by_text("Request rejected.")).to_be_visible()


@pytest.mark.writes
def test_journey_request_add_source_and_link_domain(page: Page):
    href = discover(page, "/os/requests", r"^/os/requests/(?!history)[^/?#]+$")
    open_page(page, href)
    add = page.get_by_test_id("add-source")
    if add.count() == 0:
        pytest.skip("request does not allow adding sources")
    add.get_by_label("Unregistered source to add").fill("UIAUTO_SRC")
    add.get_by_label("What is needed").fill("all tables")
    add.get_by_role("button", name="Add source").click()
    expect(page.get_by_text("Source added.")).to_be_visible()
    item = page.locator('[data-testid^="item-controls-"]').last
    item.get_by_role("button", name="Remove").click()
    expect(page.get_by_text("Removed.")).to_be_visible()
    link = page.get_by_test_id("link-domain")
    if link.count() and link.get_by_label("Domain to link").locator("option").count() > 1:
        link.get_by_label("Domain to link").select_option(index=1)
        link.get_by_role("button", name="Link domain").click()
        expect(page.get_by_text("Domain linked.")).to_be_visible()


@pytest.mark.writes
def test_journey_source_enhance_request_lands_on_intake(page: Page):
    open_page(page, f"{NEW}?type=source&intent=enhance")
    dom = page.locator("#domain")
    if dom.locator("option").count() < 2:
        pytest.skip("no domains")
    dom.select_option(index=1)
    boxes = page.get_by_test_id("sources-feeding").get_by_role("checkbox")
    if boxes.count() == 0:
        pytest.skip("no sources")
    name = boxes.first.get_attribute("aria-label")
    boxes.first.check()
    page.get_by_test_id(f"scope-{name}").fill("add one column")
    page.locator("#rq-title").fill(unique("UI-AUTOTEST enhance"))
    page.get_by_test_id("submit-request-button").click()
    if page.get_by_test_id("submit-anyway").is_visible():
        page.get_by_test_id("submit-anyway").click()
    page.wait_for_url(re.compile(r"/os/intake\?view=selected&request="))


# ---------------------------------------------------------------- read journeys
def test_request_detail_navigation(page: Page):
    href = discover(page, "/os/requests", r"^/os/requests/(?!history)[^/?#]+$")
    open_page(page, href)
    expect(page.get_by_label("Request path")).to_be_visible()
    page.get_by_role("link", name="All requests").or_(page.get_by_role("button", name="All requests")).first.click()
    expect(page).to_have_url(re.compile(r"/os/(requests|intake)"))


def test_intake_board_to_request(page: Page):
    open_page(page, "/os/intake")
    href = discover(page, "/os/intake", r"/os/(requests/[^/?#]+|intake\?.*request=)")
    open_page(page, href)
