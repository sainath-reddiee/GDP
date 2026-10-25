"""Browser performance metrics: navigation timing, LCP, CLS, long tasks, DOM size, JS heap."""
from playwright.sync_api import Page

INSTALL = """
() => {
  window.__ui = {lcp: 0, cls: 0, longTasks: 0};
  new PerformanceObserver(l => l.getEntries().forEach(e => window.__ui.lcp = e.startTime))
    .observe({type: 'largest-contentful-paint', buffered: true});
  new PerformanceObserver(l => l.getEntries().forEach(e => { if (!e.hadRecentInput) window.__ui.cls += e.value; }))
    .observe({type: 'layout-shift', buffered: true});
  try { new PerformanceObserver(l => window.__ui.longTasks += l.getEntries().length)
    .observe({type: 'longtask', buffered: true}); } catch (e) {}
}
"""

READ = """
() => {
  const n = performance.getEntriesByType('navigation')[0] || {};
  return {
    ttfb: n.responseStart || 0,
    dom_content_loaded: n.domContentLoadedEventEnd || 0,
    load: n.loadEventEnd || 0,
    lcp: window.__ui ? window.__ui.lcp : 0,
    cls: window.__ui ? window.__ui.cls : 0,
    long_tasks: window.__ui ? window.__ui.longTasks : 0,
    dom_nodes: document.getElementsByTagName('*').length,
    js_heap_mb: performance.memory ? performance.memory.usedJSHeapSize / 1048576 : 0,
    resources: performance.getEntriesByType('resource').length,
  };
}
"""


def measure(page: Page, path: str) -> dict:
    page.add_init_script(f"({INSTALL})()")
    page.goto(path, wait_until="load")
    page.wait_for_timeout(1500)
    return page.evaluate(READ)
