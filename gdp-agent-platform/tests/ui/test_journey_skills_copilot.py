"""Journeys: skills (scout, registry, editor, builder), copilot conversation and publish."""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers import discover, open_page, unique

PROPOSED = "/os/skills/registry/proposed"
SKILL = r"/skills/registry/(?!proposed)[^/?#]+$|/skills/(?!proposed|registry)[^/?#]+$"


# ---------------------------------------------------------------- proposed skills / scout
def test_proposed_skills_page(page: Page):
    open_page(page, "/os/skills/registry")
    page.get_by_test_id("proposed-skills-link").click()
    expect(page.get_by_test_id("scout-plan")).to_be_visible()
    expect(page.get_by_test_id("scout-settings")).to_be_visible()


def test_scout_settings_toggle_and_reset(page: Page):
    open_page(page, PROPOSED)
    s = page.get_by_test_id("scout-settings")
    box = s.get_by_label("Scan on a schedule")
    before = box.is_checked()
    box.click()
    assert box.is_checked() != before
    s.get_by_role("button", name="Reset to defaults").click()


def test_dismiss_proposal_asks_why(page: Page):
    open_page(page, PROPOSED)
    prop = page.get_by_test_id("skill-proposal").first
    if prop.count() == 0:
        pytest.skip("no proposals")
    prop.get_by_role("button", name="Dismiss").click()
    expect(prop.get_by_label("Why dismiss")).to_be_visible()


@pytest.mark.writes
def test_journey_scout_scan(page: Page):
    open_page(page, PROPOSED)
    page.get_by_test_id("scout-plan").get_by_role("button", name="Look now").click()
    expect(page.locator("body")).not_to_contain_text("Application error", timeout=300_000)


@pytest.mark.writes
def test_journey_dismiss_proposal(page: Page):
    open_page(page, PROPOSED)
    prop = page.get_by_test_id("skill-proposal").first
    if prop.count() == 0:
        pytest.skip("no proposals")
    prop.get_by_role("button", name="Dismiss").click()
    prop.get_by_label("Why dismiss").fill("UI-AUTOTEST")
    prop.get_by_role("button", name="Dismiss").click()
    expect(page.get_by_text(re.compile("^Dismissed"))).to_be_visible()


# ---------------------------------------------------------------- skill detail
def test_skill_detail_versions_and_sections(page: Page):
    open_page(page, discover(page, "/os/skills/registry", SKILL))
    ver = page.get_by_label("Version")
    if ver.count():
        for i in range(min(ver.locator("option").count(), 4)):
            page.get_by_label("Version").select_option(index=i)
    nav = page.get_by_role("navigation", name="Skill sections")
    if nav.count():
        links = nav.get_by_role("link")
        for i in range(links.count()):
            links.nth(i).click()


def test_skill_edit_dialog_open_cancel(page: Page):
    open_page(page, discover(page, "/os/skills/registry", SKILL))
    btn = page.get_by_role("button", name="Edit as new version")
    if btn.count() == 0:
        pytest.skip("not editable")
    btn.click()
    dlg = page.get_by_role("dialog", name="Edit skill")
    expect(dlg).to_be_visible()
    expect(dlg.get_by_role("button", name="Save as draft")).to_be_visible()
    expect(dlg.get_by_role("button", name="Save as candidate")).to_be_visible()
    dlg.get_by_role("button", name="Add file").click()
    page.keyboard.press("Escape")
    expect(dlg).to_be_hidden()


def test_skill_version_compare(page: Page):
    open_page(page, discover(page, "/os/skills/registry", SKILL))
    for name in ["Changes", "Compare", "View"]:
        b = page.get_by_role("button", name=name, exact=True)
        if b.count():
            b.first.click()
            page.keyboard.press("Escape")


@pytest.mark.writes
def test_journey_skill_save_draft(page: Page):
    open_page(page, discover(page, "/os/skills/registry", SKILL))
    page.get_by_role("button", name="Edit as new version").click()
    dlg = page.get_by_role("dialog", name="Edit skill")
    dlg.get_by_placeholder("Tighten freshness checks for daily feeds").fill("UI-AUTOTEST draft")
    dlg.get_by_role("button", name="Save as draft").click()
    expect(dlg).to_be_hidden()


# ---------------------------------------------------------------- skill builder
def _builder(page):
    open_page(page, "/os/skills/registry")
    btn = page.get_by_role("button", name="New skill with AI")
    if btn.count() == 0:
        pytest.skip("skill builder not available")
    btn.click()
    dlg = page.get_by_role("dialog", name="Skill builder")
    expect(dlg).to_be_visible()
    return dlg


@pytest.mark.parametrize("mode", ["Guided interview", "From platform knowledge", "From a document",
                                  "Improve a skill"])
def test_skill_builder_modes(page: Page, mode):
    dlg = _builder(page)
    dlg.get_by_text(mode, exact=True).click()
    dlg.get_by_role("button", name="Next").click()
    expect(dlg.get_by_role("button", name="Back")).to_be_visible()
    dlg.get_by_role("button", name="Back").click()


def test_skill_builder_steps_visible(page: Page):
    dlg = _builder(page)
    for s in ["Source", "Inputs", "Draft", "Test", "Save"]:
        expect(dlg.get_by_text(s, exact=True).first).to_be_visible()
    page.keyboard.press("Escape")


@pytest.mark.writes
def test_journey_skill_builder_end_to_end(page: Page):
    dlg = _builder(page)
    dlg.get_by_text("From platform knowledge", exact=True).click()
    dlg.get_by_role("button", name="Next").click()
    dlg.locator("textarea, input[type=text]").first.fill(unique("UI-AUTOTEST skill: check row counts"))
    dlg.get_by_role("button", name=re.compile("^Draft the skill")).click()
    expect(dlg.get_by_role("button", name="Next: test")).to_be_enabled(timeout=300_000)
    dlg.get_by_role("button", name="Next: test").click()
    dlg.get_by_role("button", name="Skip tests").or_(dlg.get_by_role("button", name="Next: save")).first.click()
    dlg.get_by_role("button", name="Save as candidate").click()
    expect(dlg).to_be_hidden(timeout=120_000)


# ---------------------------------------------------------------- copilot
COP = "/os/work/copilot"


def test_copilot_composer(page: Page):
    open_page(page, COP)
    box = page.get_by_label("Message the copilot")
    expect(box).to_have_attribute("placeholder", re.compile("Ask anything"))
    expect(page.get_by_role("button", name="Send")).to_be_visible()
    expect(page.get_by_role("button", name="Attach files")).to_be_visible()


def test_copilot_attach_and_remove_file(page: Page):
    open_page(page, COP)
    page.locator("input[type=file]").first.set_input_files(
        files=[{"name": "ctx.txt", "mimeType": "text/plain", "buffer": b"hello"}])
    rm = page.get_by_role("button", name="Remove ctx.txt")
    expect(rm).to_be_visible()
    rm.click()
    expect(rm).to_have_count(0)


def test_copilot_jira_dialog(page: Page):
    open_page(page, COP)
    b = page.get_by_role("button", name="Add a Jira issue")
    if b.count() == 0:
        pytest.skip("Jira not enabled")
    b.click()
    expect(page.get_by_label("Search Jira").or_(page.get_by_role("button", name="Connect Jira")).first).to_be_visible()
    page.keyboard.press("Escape")


def test_copilot_find_conversation(page: Page):
    open_page(page, COP)
    f = page.get_by_label("Find a conversation")
    if f.count():
        f.fill("zz_nothing")
        f.fill("")


@pytest.mark.writes
def test_journey_copilot_conversation_and_delete(page: Page):
    open_page(page, COP)
    page.get_by_role("button", name="New conversation").click()
    msg = unique("UI-AUTOTEST hello")
    page.get_by_label("Message the copilot").fill(msg + ": what can you do?")
    page.get_by_role("button", name="Send").click()
    expect(page.get_by_text(msg).first).to_be_visible()
    expect(page.get_by_role("button", name="Send")).to_be_visible(timeout=180_000)
    page.wait_for_url(re.compile(r"/os/work/copilot/[^/?]+"))
    delete = page.get_by_role("button", name=re.compile(f"Delete conversation .*{re.escape(msg)}"))
    if delete.count():
        delete.first.click()
        page.get_by_role("button", name="Delete", exact=True).click()


@pytest.mark.writes
def test_journey_copilot_review_and_publish_changes(page: Page):
    open_page(page, COP)
    review = page.get_by_role("button", name=re.compile(r"^Review \d+"))
    if review.count() == 0:
        pytest.skip("no pending copilot changes")
    review.first.click()
    drawer = page.get_by_role("dialog", name="Review changes").or_(page.get_by_text("Review changes")).first
    expect(drawer).to_be_visible()
    page.get_by_role("checkbox", name=re.compile("^Publish ")).first.check()
    page.get_by_role("button", name=re.compile(r"^Publish \d+")).click()
    page.get_by_role("button", name="Back").click()
