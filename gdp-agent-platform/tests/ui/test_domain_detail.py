"""Domain detail page: layers, sections, description, people, rules, versions, sources/documents,
references, accuracy, MDM, playbook, examples, lineage, learning, pack editor / contract draft."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import discover, open_page, relative_url

SECTIONS = [("Overview", "overview"), ("Playbook", "playbook"), ("References", "references"),
            ("Models", "models"), ("Enterprise Memory", "knowledge"), ("Rules", "rules"),
            ("Sources", "sources"), ("Lineage", "lineage"), ("Versions", "versions")]
LAYERS = [("Silver", "SILVER"), ("MDM (Reltio)", "MDM"), ("Gold", "GOLD")]


@pytest.fixture
def domain(page: Page) -> str:
    href = discover(page, "/os/knowledge?view=domains", r"^/os/knowledge/domains/[^/?#]+$")
    return href.split("?")[0]


def _tab(page, domain, tab, layer=None):
    q = f"?tab={tab}" + (f"&layer={layer}" if layer else "")
    open_page(page, domain + q)


# ---------------------------------------------------------------- navigation
@pytest.mark.parametrize("label,tab", SECTIONS, ids=[t for _, t in SECTIONS])
def test_section_tabs(page: Page, domain, label, tab):
    open_page(page, domain)
    nav = page.get_by_role("navigation", name="Domain sections")
    nav.get_by_role("button", name=re.compile(f"^{re.escape(label)}")).click()
    expect(page).to_have_url(re.compile(f"tab={tab}"))
    expect(nav.get_by_role("button", name=re.compile(f"^{re.escape(label)}"))).to_have_attribute(
        "aria-current", "page")


@pytest.mark.parametrize("label,layer", LAYERS, ids=[l for _, l in LAYERS])
def test_layer_toggle(page: Page, domain, label, layer):
    open_page(page, domain)
    g = page.get_by_role("group", name="Layer")
    b = g.get_by_role("button", name=label, exact=True)
    if b.count() == 0:
        pytest.skip(f"layer {label} not offered")
    b.click()
    expect(page).to_have_url(re.compile(f"layer={layer}"))
    expect(g.get_by_role("button", name=label, exact=True)).to_have_attribute("aria-pressed", "true")


@pytest.mark.parametrize("label,tab", SECTIONS, ids=[t for _, t in SECTIONS])
@pytest.mark.parametrize("layer", ["SILVER", "MDM", "GOLD"])
def test_every_section_in_every_layer(page: Page, domain, label, tab, layer):
    _tab(page, domain, tab, layer)


# ---------------------------------------------------------------- overview
def test_description_edit_cancel(page: Page, domain):
    _tab(page, domain, "overview")
    edit = page.get_by_role("button", name="Edit", exact=True)
    if edit.count() == 0:
        pytest.skip("description not editable")
    edit.first.click()
    box = page.get_by_label("Description")
    original = box.input_value()
    box.fill(original + " ui-test")
    page.get_by_role("button", name="Cancel").click()
    expect(page.get_by_label("Description")).to_have_count(0)


@pytest.mark.writes
def test_description_save_new_version(page: Page, domain):
    _tab(page, domain, "overview")
    page.get_by_role("button", name="Edit", exact=True).first.click()
    box = page.get_by_label("Description")
    original = box.input_value()
    box.fill(original + " ")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_role("status").filter(has_text="Saved as a new version")).to_be_visible()


def test_people_controls(page: Page, domain):
    _tab(page, domain, "overview")
    user = page.get_by_label("User", exact=True)
    if user.count() == 0:
        pytest.skip("people editor not shown")
    expect(user).to_have_attribute("placeholder", "SNOWFLAKE.USER")
    role = page.get_by_label("Role", exact=True)
    for i in range(role.locator("option").count()):
        role.select_option(index=i)
    expect(page.get_by_role("button", name=re.compile("^(Save people|Add an owner first)$"))).to_be_visible()


# ---------------------------------------------------------------- rules / versions
def test_rules_search_and_inherit_inputs(page: Page, domain):
    _tab(page, domain, "rules")
    s = page.get_by_label("Search rules")
    s.fill("zz_no_rule")
    s.fill("")
    inputs = page.get_by_placeholder("inherit")
    if inputs.count():
        inputs.first.fill("ui-test")
        page.get_by_role("button", name="Discard").click()
        expect(inputs.first).not_to_have_value("ui-test")


def test_versions_changes_view(page: Page, domain):
    _tab(page, domain, "versions")
    ch = page.get_by_role("button", name="Changes")
    if ch.count():
        ch.first.click()
        expect(page.locator("body")).not_to_contain_text("Application error")
    expect(page.get_by_role("button", name="Roll back here").first.or_(
        page.get_by_text(re.compile("v\\d+")).first)).to_be_visible()


# ---------------------------------------------------------------- sources / documents
def test_sources_tab_controls(page: Page, domain):
    _tab(page, domain, "sources")
    docs = page.get_by_test_id("domain-documents")
    expect(docs).to_be_visible()
    expect(docs.get_by_label("Confluence link")).to_have_attribute(
        "placeholder", "Paste a Confluence page or space link")
    expect(docs.get_by_role("button", name="Upload")).to_be_visible()
    expect(docs.get_by_role("button", name="Link")).to_be_visible()


def test_document_row_actions(page: Page, domain):
    _tab(page, domain, "sources")
    row = page.get_by_test_id("document-row").first
    if row.count() == 0:
        pytest.skip("no documents")
    expect(row.get_by_role("link", name="Original")).to_have_attribute("href", re.compile("/bff/domain-document/"))
    expect(row.get_by_role("button", name=re.compile("^(Read again|Refresh)$"))).to_be_visible()
    row.get_by_role("button", name="Review").click()
    review = page.get_by_test_id("document-review")
    expect(review).to_be_visible()
    expect(review.get_by_test_id("document-text")).to_be_visible()
    page_sel = review.get_by_label("Page")
    if page_sel.count():
        for i in range(min(page_sel.locator("option").count(), 3)):
            page_sel.select_option(index=i)
    show = review.get_by_role("group", name="Show")
    if show.count():
        btns = show.get_by_role("button")
        for i in range(btns.count()):
            btns.nth(i).click()


@pytest.mark.writes
def test_document_upload_reread_unlink(page: Page, domain):
    _tab(page, domain, "sources")
    page.get_by_test_id("document-upload").set_input_files(
        files=[{"name": "ui-autotest.txt", "mimeType": "text/plain",
                "buffer": b"UI-AUTOTEST: COMPANY_NAME synonyms are ACCOUNT_NAME and CUSTOMER_NAME."}])
    expect(page.get_by_text(re.compile(r"ui-autotest\.txt: kept, being read\."))).to_be_visible(timeout=120_000)
    row = page.get_by_test_id("document-row").filter(has_text="ui-autotest.txt").first
    row.get_by_role("button", name="Read again").click()
    expect(page.get_by_text("Reading it again from the kept file.")).to_be_visible()
    row.get_by_role("button", name="Unlink").click()
    expect(page.get_by_text(re.compile("^Unlinked\\."))).to_be_visible()


@pytest.mark.writes
def test_document_review_approve_reject(page: Page, domain):
    _tab(page, domain, "sources")
    row = page.get_by_test_id("document-row").first
    if row.count() == 0:
        pytest.skip("no documents")
    row.get_by_role("button", name="Review").click()
    review = page.get_by_test_id("document-review")
    boxes = review.get_by_role("checkbox", name=re.compile("^Check "))
    if boxes.count() == 0:
        pytest.skip("nothing to review")
    boxes.first.check()
    review.get_by_role("button", name="Reject checked").click()
    expect(page.get_by_text(re.compile(r"\d+ rejected\."))).to_be_visible()


# ---------------------------------------------------------------- references / accuracy
def test_references_controls(page: Page, domain):
    _tab(page, domain, "references")
    refs = page.get_by_test_id("references")
    expect(refs).to_be_visible()
    kind = refs.get_by_label("Kind", exact=True)
    expect(kind.locator("option").first).to_have_text("Detect what it is")
    expect(refs.get_by_label("Database")).to_have_attribute("placeholder", "DATABASE")
    expect(refs.get_by_label("Schema")).to_have_attribute("placeholder", "SCHEMA")
    read_as = refs.get_by_label("Read as")
    for opt in ["Data model", "Post-MDM model"]:
        read_as.select_option(label=opt)
    expect(refs.get_by_role("button", name="Read schema")).to_be_visible()


def test_reference_rows_or_empty(page: Page, domain):
    _tab(page, domain, "references")
    refs = page.get_by_test_id("references")
    kinds = refs.get_by_label(re.compile("^Kind of "))
    if kinds.count() == 0:
        expect(refs.get_by_text("Nothing given for this layer yet.")).to_be_visible()
        return
    for b in ["Use as the model", "Use as the MDM contract", "Withdraw"]:
        refs.get_by_role("button", name=b).or_(refs.get_by_role("link", name=re.compile("Open draft v"))).first.is_visible()


def test_accuracy_rescore_gold(page: Page, domain):
    _tab(page, domain, "references", "GOLD")
    acc = page.get_by_test_id("accuracy")
    if acc.count() == 0:
        pytest.skip("no accuracy panel")
    expect(acc.get_by_role("button", name="Re-score")).to_be_visible()


@pytest.mark.writes
def test_reference_upload_and_withdraw(page: Page, domain):
    _tab(page, domain, "references")
    refs = page.get_by_test_id("references")
    refs.get_by_label("Files").set_input_files(
        files=[{"name": "ref.csv", "mimeType": "text/csv", "buffer": b"ID,NAME\n1,A\n"}])
    row = refs.get_by_label(re.compile("^Kind of ref")).first
    expect(row).to_be_visible(timeout=120_000)
    refs.get_by_role("button", name="Withdraw").last.click()


# ---------------------------------------------------------------- MDM / playbook / examples
def test_mdm_panel(page: Page, domain):
    _tab(page, domain, "overview", "MDM")
    mdm = page.get_by_test_id("domain-mdm")
    if mdm.count() == 0:
        pytest.skip("no MDM panel")
    btn = mdm.get_by_role("button", name=re.compile("^(Link to MDM|Change)$"))
    btn.click()
    expect(mdm.get_by_role("button", name=re.compile("^Save"))).to_be_visible()
    mdm.get_by_role("button", name="Cancel").click()


def test_mdm_layer_rules_link(page: Page, domain):
    _tab(page, domain, "rules", "MDM")
    layer = page.get_by_test_id("mdm-layer")
    if layer.count():
        expect(layer.get_by_role("link", name="Edit or add in Knowledge")).to_be_visible()


def test_playbook_skill_rows(page: Page, domain):
    _tab(page, domain, "playbook")
    pb = page.get_by_test_id("playbook")
    expect(pb).to_be_visible()
    skills = page.get_by_test_id("domain-skills")
    add = skills.get_by_role("button", name="Skill", exact=True)
    if add.count():
        add.click()
        for lbl in ["Skill", "How", "Layer"]:
            expect(skills.get_by_label(lbl, exact=True).last).to_be_visible()
        skills.get_by_role("button", name="Cancel").click()


def test_playbook_preview(page: Page, domain):
    _tab(page, domain, "playbook")
    stage = page.get_by_label("Stage", exact=True)
    if stage.count() == 0:
        pytest.skip("no preview")
    for i in range(stage.locator("option").count()):
        stage.select_option(index=i)
    page.get_by_label("Target table").fill("CUSTOMER")


def test_models_tab_examples_and_import_link(page: Page, domain):
    _tab(page, domain, "models")
    imp = page.get_by_test_id("import-model-file")
    if imp.count():
        imp.click()
        expect(page).to_have_url(re.compile(r"import=file"))
        return
    expect(page.get_by_test_id("table-examples").or_(page.get_by_test_id("domain-data-model")).first).to_be_visible()


# ---------------------------------------------------------------- lineage / learning
def test_lineage_map_nodes(page: Page, domain):
    _tab(page, domain, "lineage")
    tab = page.get_by_test_id("lineage-tab")
    expect(tab).to_be_visible()
    nodes = tab.locator('[role="button"][aria-pressed]')
    if nodes.count() == 0:
        pytest.skip("empty lineage map")
    nodes.first.click()
    expect(nodes.first).to_have_attribute("aria-pressed", "true")
    close = page.get_by_role("button", name="Close")
    if close.count():
        close.first.click()


def test_lineage_open_link(page: Page, domain):
    _tab(page, domain, "lineage")
    link = page.get_by_role("link", name="Open lineage").or_(page.get_by_role("button", name="Open lineage"))
    if link.count():
        link.first.click()
        expect(page).to_have_url(re.compile("lineage"))


@pytest.mark.writes
def test_learning_learn_from_decisions(page: Page, domain):
    _tab(page, domain, "knowledge")
    learn = page.get_by_test_id("learning").get_by_role("button", name="Learn from decisions")
    if learn.count() == 0:
        pytest.skip("no learning panel")
    learn.click()
    expect(page.get_by_role("button", name="Learn from decisions")).to_be_visible(timeout=600_000)


# ---------------------------------------------------------------- contract draft (onboarding)
def test_contract_draft_editor(page: Page):
    open_page(page, "/os/enable/new-domain?how=draft")
    expect(page.get_by_test_id("onboard-domain-chosen")).to_be_visible()
    draft_btn = page.get_by_role("button", name="Draft from a contract")
    if draft_btn.count():
        draft_btn.first.click()
    box = page.get_by_label("Contract document")
    expect(box).to_have_attribute("placeholder", re.compile("Paste the contract here"))
    expect(page.get_by_role("button", name="Draft pack with AI")).to_be_visible()


def test_pack_json_mode_example(page: Page):
    open_page(page, "/os/enable/new-domain?how=draft")
    page.get_by_role("button", name="Pack JSON").click()
    expect(page.get_by_role("button", name="Pack JSON")).to_have_attribute("aria-pressed", "true")
    page.get_by_role("button", name="Start from an example").click()
    assert page.get_by_label("Pack JSON").input_value().strip().startswith("{")
    expect(page.get_by_role("button", name="Import domain pack")).to_be_visible()


@pytest.mark.writes
def test_contract_draft_with_ai(page: Page):
    open_page(page, "/os/enable/new-domain?how=draft")
    page.get_by_label("Contract document").fill(
        "UI-AUTOTEST contract. Table CUSTOMER: CUSTOMER_ID (key), NAME, EMAIL (PII), CREATED_AT.")
    page.get_by_role("button", name="Draft pack with AI").click()
    expect(page.get_by_text(re.compile("^Draft ready"))).to_be_visible(timeout=300_000)
    expect(page.get_by_role("button", name="Pack JSON")).to_have_attribute("aria-pressed", "true")


def test_domain_page_reload_keeps_state(page: Page, domain):
    _tab(page, domain, "references", "GOLD")
    page.reload()
    expect(page).to_have_url(re.compile("tab=references"))
    expect(page).to_have_url(re.compile("layer=GOLD"))
    open_page(page, relative_url(page))
