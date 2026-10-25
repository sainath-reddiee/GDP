"""Shared fixtures for the UI suite.

- Verifies the web app is reachable once per session (fails fast with a clear message).
- Authenticates once per session (dev cookie or PAT UI login) and reuses storage state.
- Collects console / page errors per test and fails the test on uncaught page errors.
- Saves a screenshot + HTML on failure under tests/ui/artifacts.
"""
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
from playwright.sync_api import Browser, BrowserContext, Page, expect

sys.path.insert(0, os.path.dirname(__file__))

import settings  # noqa: E402

expect.set_options(timeout=settings.EXPECT_TIMEOUT_MS)


def pytest_configure(config):
    for name, desc in [
        ("ui", "browser UI test"),
        ("smoke", "fast critical-path checks"),
        ("writes", "creates or changes data; needs AIP_UI_ALLOW_WRITES=1"),
        ("pat_only", "only meaningful when AIP_AUTH=pat"),
        ("journey", "end-to-end user flow"),
        ("component", "component / page feature test"),
        ("crawler", "generic every-control crawler"),
        ("behavior", "cross-cutting behaviour (scroll, keyboard, resilience, browser)"),
        ("scroll", "scrolling behaviour"),
        ("keyboard", "keyboard and focus"),
        ("responsive", "breakpoints, zoom, colour scheme"),
        ("visual", "visual regression snapshots"),
        ("resilience", "network failure, session, hostile input"),
        ("quality", "a11y, performance, console, links"),
    ]:
        config.addinivalue_line("markers", f"{name}: {desc}")


# file name prefix -> markers applied automatically, so any category can be run with -m
_FILE_MARKERS = {
    "test_journey_": ["journey"], "test_e2e_": ["journey"],
    "test_interactions_exhaustive": ["crawler"],
    "test_behavior_scrolling": ["behavior", "scroll"],
    "test_behavior_keyboard": ["behavior", "keyboard"],
    "test_behavior_responsive": ["behavior", "responsive"],
    "test_behavior_resilience": ["behavior", "resilience"],
    "test_behavior_browser": ["behavior"],
    "test_quality_gates": ["quality"],
}


def pytest_collection_modifyitems(config, items):
    here = Path(__file__).parent
    skip_writes = pytest.mark.skip(reason="set AIP_UI_ALLOW_WRITES=1 to run data-changing tests")
    skip_pat = pytest.mark.skip(reason="requires AIP_AUTH=pat")
    for item in items:
        if here in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.ui)
            fname = Path(str(item.fspath)).stem
            marks = next((m for p, m in _FILE_MARKERS.items() if fname.startswith(p)), ["component"])
            for m in marks:
                item.add_marker(getattr(pytest.mark, m))
            if "test_visual_snapshot" in item.name:
                item.add_marker(pytest.mark.visual)
        if "writes" in item.keywords and not settings.ALLOW_WRITES:
            item.add_marker(skip_writes)
        if "pat_only" in item.keywords and settings.AUTH_MODE != "pat":
            item.add_marker(skip_pat)


# ---------------------------------------------------------------- session setup

@pytest.fixture(scope="session")
def base_url():
    return settings.WEB_URL


@pytest.fixture(scope="session", autouse=True)
def _web_is_up():
    try:
        httpx.get(settings.WEB_URL + "/login", timeout=60, follow_redirects=True)
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.exit(
            f"Web app not reachable at {settings.WEB_URL} ({exc}). Start `npm run dev` in apps/web "
            f"and the API (`python -m uvicorn app.main:app --port 8001` in apps/api).",
            returncode=3,
        )
    if os.environ.get("AIP_UI_SKIP_API_CHECK") != "1":
        try:
            httpx.get(settings.API_URL + "/docs", timeout=30)
        except Exception as exc:  # pragma: no cover - environment guard
            pytest.exit(
                f"API not reachable at {settings.API_URL} ({exc}). From apps/api run: "
                f"python -m uvicorn app.main:app --host 127.0.0.1 --port 8001",
                returncode=3,
            )


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        "base_url": settings.WEB_URL,
        "viewport": {"width": 1440, "height": 900},
        "ignore_https_errors": True,
    }


@pytest.fixture(scope="session")
def auth_state(browser: Browser, browser_context_args, tmp_path_factory) -> str:
    """Sign in once and return a storage-state file path."""
    path = tmp_path_factory.mktemp("auth") / "state.json"
    ctx = browser.new_context(**browser_context_args)
    if settings.AUTH_MODE == "pat":
        if not (settings.E2E_USER and settings.E2E_TOKEN):
            pytest.exit("AIP_AUTH=pat needs AIP_E2E_USER and AIP_E2E_TOKEN", returncode=3)
        page = ctx.new_page()
        page.set_default_navigation_timeout(settings.NAV_TIMEOUT_MS)
        page.goto("/login")
        page.get_by_label("Snowflake user").fill(settings.E2E_USER)
        page.get_by_label("Programmatic access token").fill(settings.E2E_TOKEN)
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.wait_for_url(re.compile(r"/os"), timeout=settings.NAV_TIMEOUT_MS)
    else:
        host = urlparse(settings.WEB_URL).hostname
        ctx.add_cookies([{"name": "aip_dev", "value": "1", "domain": host, "path": "/"}])
    ctx.storage_state(path=str(path))
    ctx.close()
    return str(path)


# ---------------------------------------------------------------- per-test fixtures

@pytest.fixture
def context(browser: Browser, browser_context_args, auth_state) -> BrowserContext:
    ctx = browser.new_context(**browser_context_args, storage_state=auth_state)
    ctx.set_default_navigation_timeout(settings.NAV_TIMEOUT_MS)
    ctx.set_default_timeout(settings.EXPECT_TIMEOUT_MS)
    yield ctx
    ctx.close()


@pytest.fixture
def anon_context(browser: Browser, browser_context_args) -> BrowserContext:
    ctx = browser.new_context(**browser_context_args)
    ctx.set_default_navigation_timeout(settings.NAV_TIMEOUT_MS)
    yield ctx
    ctx.close()


def _attach_error_capture(page: Page):
    page._ui_errors = []  # type: ignore[attr-defined]
    page._ui_console = []  # type: ignore[attr-defined]
    page.on("pageerror", lambda exc: page._ui_errors.append(str(exc)))
    page.on(
        "console",
        lambda msg: page._ui_console.append(msg.text) if msg.type == "error" else None,
    )
    return page


@pytest.fixture
def page(context: BrowserContext) -> Page:
    p = _attach_error_capture(context.new_page())
    yield p
    errors = [e for e in p._ui_errors if "ResizeObserver" not in e]
    if errors:
        pytest.fail("Uncaught page errors:\n" + "\n".join(errors), pytrace=False)


@pytest.fixture
def anon_page(anon_context: BrowserContext) -> Page:
    return _attach_error_capture(anon_context.new_page())


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when != "call" or not rep.failed:
        return
    out = Path(settings.ARTIFACTS_DIR)
    out.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.-]+", "_", item.nodeid)[-150:]
    for name in ("page", "anon_page"):
        pg = item.funcargs.get(name)
        if pg is None or pg.is_closed():
            continue
        try:
            pg.screenshot(path=str(out / f"{safe}.png"), full_page=True)
            (out / f"{safe}.html").write_text(pg.content(), encoding="utf-8")
        except Exception:
            pass
