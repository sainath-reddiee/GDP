"""Network shaping and fault injection via Chrome DevTools Protocol and Playwright routing."""
import contextlib

from playwright.sync_api import Page, Route

PROFILES = {
    "slow-3g": {"latency": 400, "downloadThroughput": 50_000, "uploadThroughput": 50_000},
    "fast-3g": {"latency": 150, "downloadThroughput": 200_000, "uploadThroughput": 90_000},
}


@contextlib.contextmanager
def throttled(page: Page, profile: str):
    """Chromium only: emulate a slow network."""
    cdp = page.context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.emulateNetworkConditions", {"offline": False, **PROFILES[profile]})
    try:
        yield
    finally:
        cdp.send("Network.emulateNetworkConditions",
                 {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1})


@contextlib.contextmanager
def offline(page: Page):
    page.context.set_offline(True)
    try:
        yield
    finally:
        page.context.set_offline(False)


@contextlib.contextmanager
def failing(page: Page, pattern: str, status: int = 500, body: str = '{"detail":"injected failure"}'):
    """Make browser-side requests matching `pattern` (e.g. '**/bff/**') return an error."""
    def handler(route: Route):
        route.fulfill(status=status, body=body, content_type="application/json")
    page.route(pattern, handler)
    try:
        yield
    finally:
        page.unroute(pattern, handler)


@contextlib.contextmanager
def delayed(page: Page, pattern: str, ms: int):
    def handler(route: Route):
        page.wait_for_timeout(ms)
        route.continue_()
    page.route(pattern, handler)
    try:
        yield
    finally:
        page.unroute(pattern, handler)
