"""The "+ New" menu (apps/web/components/shell/quick-actions.ts): every item opens a page that exists and is gated by a
privilege the governance policy knows, so an item is never shown to someone the page would then refuse."""

import re
from pathlib import Path

from services.governance import policy

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web"
SOURCE = (WEB / "components" / "shell" / "quick-actions.ts").read_text(encoding="utf-8")
ITEMS = re.findall(r'label: "([^"]+)".*?href: "([^"]+)".*?privilege: "([^"]+)"', SOURCE, re.S)


def test_the_menu_offers_every_create_action():
    labels = [label for label, _, _ in ITEMS]
    assert labels == ["Snowflake share", "Snowflake database", "Files and cloud storage", "Oracle and other databases",
                      "External pipeline (SnapLogic)", "Import a domain pack", "New table model", "New QA pack",
                      "Add a data quality check"]


def test_every_href_is_a_page():
    for label, href, _ in ITEMS:
        path = href.split("?")[0].strip("/")
        assert (WEB / "app" / path / "page.tsx").exists(), f"{label}: no page at /{path}"


def test_every_privilege_exists_in_the_policy():
    text = Path(policy.__file__).read_text(encoding="utf-8")
    for label, _, privilege in ITEMS:
        assert f'"{privilege}"' in text, f"{label}: unknown privilege {privilege}"


def test_deep_links_are_read_by_their_pages():
    """Each query flag an item relies on is read by the page (or the component) it opens."""
    readers = {
        "branch": WEB / "app" / "dashboard" / "onboarding" / "page.tsx",
        "import": WEB / "app" / "dashboard" / "onboarding" / "page.tsx",
        "connect": WEB / "app" / "sources" / "sources-section.tsx",
        "new": None,  # QA Store and Modeling, checked below
        "add": WEB / "app" / "dashboard" / "dq-store" / "page.tsx",
    }
    for flag, reader in readers.items():
        if reader:
            assert f"searchParams.{flag}" in reader.read_text(encoding="utf-8"), flag
    assert 'searchParams.new === "1"' in (WEB / "app" / "dashboard" / "qa-store" / "page.tsx").read_text(encoding="utf-8")
    assert 'searchParams.new === "1"' in (WEB / "app" / "dashboard" / "modeling" / "page.tsx").read_text(encoding="utf-8")
