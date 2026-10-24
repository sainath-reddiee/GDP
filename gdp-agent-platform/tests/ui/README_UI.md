# UI test suite (pytest + Playwright)

Fully automated browser tests for `apps/web`, covering:

| File | Area |
|---|---|
| `test_auth_landing.py` | Public landing, sign-in dialog, dev/PAT login, route protection, sign-out |
| `test_routes.py` | Every `/os` page renders (h1, no error page), reload, all legacy redirects, 404 |
| `test_shell_navigation.py` | Header, left rail, every section tab, persona switch, copilot, mobile nav, keyboard |
| `test_knowledge.py` | Knowledge hub: KPIs, views, families, catalog, ask/AI, item browser, editor, inbox, health, domains, policy |
| `test_workflows.py` | Home, Intake/Requests, Enable, Design & Stores, Build/Runs (all stages), Review gates, Skills, Release |
| `test_operations_admin.py` | Run & DQ, ops/DAG, incidents, resolutions, fixes, tickets, people, admin, help, copilot |
| `test_quality_gates.py` | Per page: a11y basics, load-time budget, console errors, broken internal links |
| `test_interactions_exhaustive.py` | On every page and every discovered detail page: clicks **every button** (must respond), selects every tab, toggles every checkbox/switch, picks every dropdown option, types in every field, opens every menu, closes every dialog with Escape, follows every link |
| `test_classic_topbar.py` | Classic layout: New menu, tickets shortcut, theme toggle, account menu, sign out, layout switch |

Destructive buttons (delete/approve/publish/save/run...) are only clicked by the crawler when `AIP_UI_ALLOW_WRITES=1`.
API tests live in `tests/api` (see its README).

Route data lives in `routes.py`; update it when pages change.

## Setup

```powershell
pip install -r tests/ui/requirements.txt
python -m playwright install chromium
```

Start the app (two terminals):

```powershell
cd apps/api; python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
cd apps/web; npm run dev
```

## Run

```powershell
pytest tests/ui                         # everything (headless chromium)
pytest tests/ui -m smoke                # critical path only
pytest tests/ui -n 4 --reruns 1         # parallel, retry flaky once
pytest tests/ui --headed --slowmo 300   # watch it
pytest tests/ui --browser firefox --browser webkit
pytest tests/ui --html=tests/ui/artifacts/report.html --self-contained-html
pytest tests/ui --tracing retain-on-failure --video retain-on-failure
```

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `AIP_WEB_URL` | `http://localhost:3000` | Web app |
| `AIP_AUTH` | `dev` | `dev` = `aip_dev` cookie, `pat` = UI login |
| `AIP_E2E_USER` / `AIP_E2E_TOKEN` | | Credentials for `pat` mode |
| `AIP_UI_ALLOW_WRITES` | `0` | `1` enables tests that create/change data (marker `writes`) |
| `AIP_UI_PAGE_BUDGET_S` | `30` | Max seconds per page load |
| `AIP_UI_NAV_TIMEOUT_MS` | `90000` | Navigation timeout |

Tests that need data (a run, a domain, a request) discover it from the UI and **skip** when none exists.
Failure screenshots + HTML go to `tests/ui/artifacts/`.
