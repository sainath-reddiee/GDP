# Phases 2–3 — Console, API and source onboarding

Status: complete on `DEV_AI_PLATFORM` (2026-10-03). Services package `services-483377f90db41696.zip`.

## Phase 2 — console and API

- **Web** (`apps/web`): Next.js 14, Tailwind 3, shadcn-style components in `components/ui`.
  Routes: `/login`, `/dashboard`, `/onboarding`, `/runs`, `/runs/[id]` (overview + agent panel),
  `/runs/[id]/{source,access,landing,profile,domain,mapping,sttm,soda,dbt,validation,review,audit}`,
  `/knowledge`, `/domains`, `/skills`, `/audit`, `/admin`.
  All data is fetched server-side (`lib/api.ts`); the browser never calls Snowflake or the API directly.
  Locked stages render a lock card even when opened by URL (`components/stage-gate.tsx`).
- **API** (`apps/api`): FastAPI on port 8001 (port 8000 is blocked on the dev machine).
  - `AIP_AUTH=dev` (default): one shared session from the named connection (browser SSO). All actions are
    attributed to that user.
  - `AIP_AUTH=pat`: per-user sign-in with a programmatic access token (`authenticator=PROGRAMMATIC_ACCESS_TOKEN`).
    Needs `AIP_ACCOUNT`. Tokens live in memory only; the web app keeps an httpOnly session cookie.
    Not yet exercised with a real PAT.
  - Agent: `POST /api/agent/stream` proxies `POST /api/v2/databases/{db}/schemas/{schema}/agents/{name}:run`
    as server-sent events and drops `response.thinking*` events. It returns a "not configured" event until
    `AIP_AGENT_NAME` is set (agent is Phase 5) and requires PAT mode, because the REST API does not accept
    the SSO session.
- **E2E**: `npm run test:e2e` in `apps/web` (needs both servers running). Drives the full source → access →
  landing flow through the UI and cancels its run at the end.

## Phase 3 — source onboarding

| Procedure (`SOURCE`, owner's rights) | Transition |
|---|---|
| `REGISTER_SOURCE(run_id, payload_json)` | `CREATED → SOURCE_REGISTERED`; writes `SOURCE_REGISTRY`, `SOURCE_OBJECT` |
| `VALIDATE_SOURCE_ACCESS(run_id, selected_json)` | `SOURCE_REGISTERED → ACCESS_VALIDATION → ACCESS_APPROVED`, or back to `SOURCE_REGISTERED` with the failed checks |
| `EXECUTE_LANDING(run_id)` | `ACCESS_APPROVED → LANDING_PENDING → LANDING_RUNNING → LANDING_COMPLETE`, or `FAILED` (retry goes to `LANDING_PENDING`) |

- Adapters (`services/source/adapters.py`): `SnowflakeDatabaseAdapter` and `SnowflakeShareAdapter`.
  A share is consumed after an admin mounts it (`CREATE DATABASE … FROM SHARE`); the share adapter also checks
  `INFORMATION_SCHEMA.DATABASES.TYPE = 'IMPORTED DATABASE'` (verified on the account).
- Checks run as the procedure owner: they prove the platform can read what it will land.
- Landing: `CREATE OR REPLACE TABLE <db>.LANDING.<SOURCE>__<OBJECT> AS SELECT * FROM <source>`, then
  source and landed `COUNT(*)` must match. Column metadata (type, nullability, source comment) goes to
  `LANDING_COLUMN_REGISTRY`. Only `DATA_STEWARD` can query `LANDING` (migration V003).
- The API refuses manual transitions into these six states unless the run is retrying from `FAILED`, so the
  console cannot mark a source registered without registering one. `TRANSITION_RUN` itself stays an
  admin override (granted to `PLATFORM_ADMIN` only).
- Demo source: `deploy_snowflake.py --demo-source DEV_AIP_DEMO_SOURCE` creates `CRM.CRM_CUSTOMER` (500 rows,
  7 columns) and `CRM.CRM_ORDER` (2,000 rows) in a separate database.

## Verification

- Unit: 54 passed (`tests/unit`), including adapter checks against a fake runner and identifier quoting.
- Integration (`tests/integration/test_source_onboarding.py`): 6 passed — database source end to end,
  share source from `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1` (NATION 25, REGION 5), unknown database rejected,
  landing cannot skip access, failed access returns to source and can be corrected, share adapter rejects a
  standard database.
- E2E: Playwright smoke passed (2.0 min).

## Known limits

- Landing runs synchronously inside the API request; large tables will hold the request open. Async execution
  belongs to the later orchestration phase (no Tasks in Phase 1).
- Row counts are compared after the copy; a source that changes during the copy fails reconciliation and can
  be retried.
- The Next.js dev server can corrupt its module graph after many hot reloads ("Invalid hook call"); restart it
  and delete `.next` if pages start returning 500.
