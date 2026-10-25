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
| `test_journey_intake.py` | Request types/intents, form validation, files, new-source chips, file → comment → upload → cancel → reopen → reject, add source, link domain |
| `test_journey_onboarding.py` | Source wizard choices, share wizard (share → schema → tables → landing → load), ingestion wizard (sections, tables, check, save), domain onboarding paths & import |
| `test_journey_runs_review.py` | Run list filters/paging, Gold run modal & start, stage rail, run menu, cancel/archive, every stage's primary action, modeling tabs, decision bar, review gate approve/send back, release signoff & publish PR, preview-role lockout |
| `test_journey_ops.py` | Incident inbox filters/bulk, detail ack/assign/resolve/mute/reopen/retry/diagnose, resolutions, known fix create/save/retire, permanent fix review |
| `test_journey_skills_copilot.py` | Skill scout, proposals, skill detail/edit/compare, skill builder (all modes), copilot compose/attach/Jira/conversation/publish |
| `test_journey_ops_admin_tools.py` | Tickets views/filters/sprints, QA suites/assistant, DQ store/suite, people personas/preview, every admin section, lineage trace/direction, code search |
| `test_e2e_business_flows.py` | Chained use cases: persona tours, request → onboarding handoff → history, Silver pipeline happy path, knowledge contribute → find → verify → retire, incident → known fix |
| `test_domain_detail.py` | Domain page: 9 sections × 3 layers, description, people, rules, versions, documents (upload/review/re-read/unlink), references, accuracy, MDM, playbook, examples, lineage map, learning, contract → AI pack draft, pack JSON |
| `test_design_stores.py` | Modeling store tabs, domain models (fork), model workbench (all tabs, import modes, exports, entities, relationships, validate, draft → save → submit → withdraw, comments), table models, dbt viewer, Data Mapping (filters, tabs, chips, line drawer, staging, submit, joins, versions, publish), profiling (browser, drawer, annotations, schedules), source store switches |
| `test_runs_quality_boards.py` | Run audit, Data Quality scan, QA inner tabs/rename/Jira bug, tickets workspace (Jira inbox, triage), DQ run checks (filters, coverage, approve/reject), DAG (tasks, settings, logs, poll), quality center, enable/ready/history/design boards, My Work tiles & actions |
| `test_stores_qa_dq_scm.py` | QA store (packs, import, drawer, environment dry run, git, run tests, history), DQ store (tables, seed modes, schedules, generate), DQ suite (diff, ack/approve, versions, publish, export formats), source control |
| `test_components_coverage.py` | Home start panel & platform search, overview dashboard, approvals scopes/preview, mapping board, model design, Gold model, star schema, table profile, agent panel, governance tables, store/workspace tabs, ops cards, skill editor, onboarding templates, SnapLogic, join plan, lifecycle, lineage lanes, environments grid |
| `test_coverage_closure.py` | Model workbench entities/review/rollback/publish/import, run assets, dbt review, table model columns, source intake extras, ingestion review & save results, home request focus, mapping groups |
| `test_knowledge_drawer_admin.py` | Knowledge drawer tabs/edit/verify/diff/rollback/close, review inbox approve/reject/diff/replay/view-in-document, admin models (search, test, stage select save bar), integrations (GitHub, repo settings, Jira, Airflow, Datadog, incidents, cases) |
| `test_behavior_scrolling.py` | Every page: scroll to bottom/top, sticky header & rail, inner scroll containers, keyboard scrolling (End/Home/PageDown), focused control scrolls into view, last control not hidden under fixed UI, scroll restoration on back, new page starts at top, landing anchors, load more/pagination, wide tables scroll in their container, sticky table header, mobile scroll, modal scroll lock, long dialog scroll |
| `test_behavior_keyboard_focus.py` | Every page: tab order without traps, Shift+Tab, visible focus ring, named focusable elements, no positive tabindex; Enter/Space activation, dialog focus trap + focus return + initial focus, arrow keys in tabs/menus, Escape closes popups, Enter submits, labels focus fields |
| `test_behavior_responsive_visual.py` | Every page at 6 breakpoints (320 → 1920), nav toggle on small screens, clipped text, touch target size, orientation change, live resize, 5 zoom levels, light/dark scheme, reduced motion, print/PDF, visual regression snapshots (mobile + desktop) |
| `test_behavior_resilience.py` | BFF 500/502/401/403 and aborted requests, slow network, failed server actions, offline → online, static assets, back/forward/reload state, session expiry (page + mid-action), two tabs, sign-out across tabs, XSS / 5000-char / unicode / SQL-ish input in 8 fields, bad query params & ids, double-click submit, paste, rapid navigation |
| `test_behavior_browser_misc.py` | Hover feedback, icon-button tooltips, safe external links & new tabs, unique page titles, meta/lang/favicon, images load, layout shift (CLS) & LCP, JS memory growth, diagram drag/zoom, copy-to-clipboard, downloads, httpOnly cookies, no secrets in storage, empty and loading states |

## Running the framework

```powershell
.\tests\run_tests.ps1 -Install            # first time: deps + browsers
.\tests\run_tests.ps1                     # everything (UI + API), HTML + JUnit report, traces/videos on failure
.\tests\run_tests.ps1 -Suite smoke        # or: journey, component, crawler, behavior, scroll, keyboard,
                                          #     responsive, visual, resilience, quality, api, ui
.\tests\run_tests.ps1 -Browsers chromium,firefox,webkit -Workers 4
.\tests\run_tests.ps1 -Writes             # include data-changing tests (DEV only)
.\tests\run_tests.ps1 -UpdateSnapshots    # accept current UI as the visual baseline
```

Markers are applied automatically from the file name, so `pytest tests/ui -m scroll` etc. also works.
Visual baselines live in `tests/ui/snapshots/` (first run writes them; a mismatch saves `FAILED_*.png`;
threshold `AIP_UI_VISUAL_THRESHOLD`, default 0.02 = 2 % of pixels).

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
