"""Component wrappers so tests talk about UI concepts instead of raw selectors."""
import re

from playwright.sync_api import Locator, Page, expect

OVERLAYS = "[role=dialog]:visible, [role=alertdialog]:visible, [role=menu]:visible, [role=listbox]:visible"


class Dialog:
    def __init__(self, page: Page, name: str | re.Pattern | None = None):
        self.page = page
        self.root = page.get_by_role("dialog", name=name) if name else page.get_by_role("dialog").first

    def expect_open(self):
        expect(self.root).to_be_visible()
        return self

    def close_with_escape(self):
        self.page.keyboard.press("Escape")
        expect(self.root).to_be_hidden()

    def focus_is_inside(self) -> bool:
        return self.page.evaluate(
            "() => !!document.activeElement?.closest('[role=dialog],[role=alertdialog]')")


class Table:
    def __init__(self, root: Locator):
        self.root = root

    @property
    def rows(self) -> Locator:
        return self.root.locator("tbody tr")

    def headers(self) -> list[str]:
        return [h.strip() for h in self.root.locator("thead th").all_inner_texts()]

    def column_values(self, index: int) -> list[str]:
        return [c.strip() for c in self.root.locator(f"tbody tr td:nth-child({index + 1})").all_inner_texts()]


class Tabs:
    def __init__(self, root: Locator):
        self.root = root

    def all(self) -> Locator:
        return self.root.get_by_role("tab")

    def select(self, name: str):
        tab = self.root.get_by_role("tab", name=name)
        tab.click()
        expect(tab).to_have_attribute("aria-selected", "true")


def close_overlays(page: Page):
    for _ in range(3):
        if page.locator(OVERLAYS).count() == 0:
            return
        page.keyboard.press("Escape")
        page.wait_for_timeout(120)


def toast(page: Page, text) -> Locator:
    return page.get_by_role("status").filter(has_text=text).or_(page.get_by_text(text)).first


def scrollables(page: Page) -> list[str]:
    """CSS paths of elements that actually scroll (overflow auto/scroll with overflowing content)."""
    return page.evaluate("""() => {
      const out = [];
      const path = el => { const p = []; while (el && el.nodeType === 1 && p.length < 6) {
        let s = el.tagName.toLowerCase(); if (el.id) { s += '#' + CSS.escape(el.id); p.unshift(s); break; }
        const sib = [...el.parentNode.children].filter(c => c.tagName === el.tagName);
        if (sib.length > 1) s += `:nth-of-type(${sib.indexOf(el) + 1})`; p.unshift(s); el = el.parentElement; }
        return p.join(' > '); };
      document.querySelectorAll('body *').forEach(el => {
        const cs = getComputedStyle(el);
        if (/(auto|scroll)/.test(cs.overflowY + cs.overflowX) && el.offsetParent !== null &&
            (el.scrollHeight > el.clientHeight + 4 || el.scrollWidth > el.clientWidth + 4)) out.push(path(el));
      });
      return out.slice(0, 15);
    }""")
