"""Cross-cutting quality checks: accessibility basics, performance budget, console, BFF."""
import time

import pytest
from playwright.sync_api import Page

from helpers import open_page
from routes import OS_PAGES

A11Y_SCRIPT = """
() => {
  const issues = [];
  document.querySelectorAll('img').forEach(i => { if (!i.hasAttribute('alt')) issues.push('img without alt: ' + i.src); });
  document.querySelectorAll('button').forEach(b => {
    const name = (b.getAttribute('aria-label') || b.innerText || b.title || '').trim();
    if (!name && !b.getAttribute('aria-labelledby') && b.offsetParent !== null) issues.push('unnamed button: ' + b.outerHTML.slice(0, 120));
  });
  document.querySelectorAll('input:not([type=hidden]), select, textarea').forEach(el => {
    if (el.offsetParent === null) return;
    const id = el.id;
    const labelled = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.placeholder ||
      (id && document.querySelector('label[for="' + id + '"]')) || el.closest('label') || el.title;
    if (!labelled) issues.push('unlabelled field: ' + el.outerHTML.slice(0, 120));
  });
  const ids = {}; document.querySelectorAll('[id]').forEach(e => { ids[e.id] = (ids[e.id] || 0) + 1; });
  Object.entries(ids).forEach(([k, v]) => { if (v > 1) issues.push('duplicate id: ' + k); });
  if (!document.documentElement.lang) issues.push('html lang missing');
  return issues;
}
"""

BUDGET_S = float(__import__("os").environ.get("AIP_UI_PAGE_BUDGET_S", "30"))


@pytest.mark.parametrize("path,h1", OS_PAGES, ids=[p for p, _ in OS_PAGES])
def test_accessibility_basics(page: Page, path, h1):
    open_page(page, path)
    issues = page.evaluate(A11Y_SCRIPT)
    assert not issues, f"{path} a11y issues:\n" + "\n".join(issues[:25])


@pytest.mark.parametrize("path,h1", OS_PAGES, ids=[p for p, _ in OS_PAGES])
def test_load_time_budget(page: Page, path, h1):
    start = time.monotonic()
    open_page(page, path)
    took = time.monotonic() - start
    assert took < BUDGET_S, f"{path} took {took:.1f}s (budget {BUDGET_S}s)"


@pytest.mark.parametrize("path,h1", OS_PAGES, ids=[p for p, _ in OS_PAGES])
def test_no_console_errors(page: Page, path, h1):
    open_page(page, path)
    page.wait_for_timeout(500)
    errs = [e for e in page._ui_console if "favicon" not in e and "Download the React DevTools" not in e]
    assert not errs, f"{path} console errors:\n" + "\n".join(errs[:10])


@pytest.mark.parametrize("path,h1", OS_PAGES, ids=[p for p, _ in OS_PAGES])
def test_no_broken_internal_links(page: Page, path, h1):
    open_page(page, path)
    hrefs = set(page.eval_on_selector_all(
        "a[href^='/']", "els => els.map(e => e.getAttribute('href').split('#')[0])"))
    bad = []
    for h in sorted(hrefs)[:40]:
        if h.startswith(("/bff/signout", "/bff/")) or not h:
            continue
        r = page.request.get(h, max_redirects=5)
        if r.status >= 400:
            bad.append(f"{h} -> {r.status}")
    assert not bad, f"broken links on {path}:\n" + "\n".join(bad)


def test_bff_nav_endpoint(page: Page):
    open_page(page, "/os")
    r = page.request.get("/bff/nav")
    assert r.status < 500
