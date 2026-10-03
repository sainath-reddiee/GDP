# Phase 1 - Foundation

Status: deployed to `ANBLICKSORG_AWS` as `DEV_AI_PLATFORM` (role `SYSADMIN`, warehouse `DBT_WH`) on 2026-10-03.
Unit tests: 34 passed. Integration tests (`tests/integration`): 4 passed against `DEV_AI_PLATFORM`.

## What exists

| Object | Purpose |
|---|---|
| `CORE.SCHEMA_MIGRATION` | Applied migrations with checksums; editing an applied migration fails the deploy |
| `CORE.WORKFLOW_STATE`, `CORE.WORKFLOW_TRANSITION` | Graph seeded from `snowflake/database/seed/workflow_graph.json` (34 states, 125 transitions; Phase 2 states disabled) |
| `CORE.WORKFLOW_RUN`, `CORE.WORKFLOW_EVENT`, `CORE.REVIEW_DECISION` | Runs, append-only transition log, human gate decisions |
| `SOURCE.*`, `PROFILE.*`, `KNOWLEDGE.*`, `MAPPING.*`, `CONTRACT.*`, `CODEGEN.*`, `AUDIT.*` | Registries from the master prompt (empty until their phases) |
| `CORE.CODE_STAGE` | Content-addressed `services-<sha>.zip` packages imported by procedures |
| `CORE.CREATE_RUN`, `GET_WORKFLOW_STATE`, `TRANSITION_RUN`, `REVIEW_TRANSITION` | Owner's-rights procedures; the only write path |
| Database roles `VIEWER`, `SERVICE_AGENT`, `DATA_ENGINEER`, `REVIEWER`, `DATA_STEWARD`, `DOMAIN_OWNER`, `PLATFORM_ADMIN` | SELECT only on tables; USAGE on procedures per role |

Admin step still required to use the roles from other accounts' roles:
`GRANT DATABASE ROLE DEV_AI_PLATFORM.<ROLE> TO ROLE <account_role>;`

## Workflow rules (enforced in Snowflake)

- Transitions not in the graph are rejected (`TRANSITION_REJECTED`), so stages cannot be skipped.
- `TRANSITION_RUN` performs SYSTEM transitions only and refuses the four approval gates (mapping, STTM, Soda, dbt).
- `REVIEW_TRANSITION` performs HUMAN transitions, requires a business justification to approve, and writes `REVIEW_DECISION` in the same transaction as the state change.
- Any active state may go to `FAILED` or `CANCELLED`; a retry from `FAILED` must return to the failed state's `retry_to` and increments `RETRY_COUNT`.
- Optimistic concurrency via `STATE_VERSION`.
- Order chosen for dbt: `DBT_GENERATING -> VALIDATION_* -> DBT_REVIEW -> DBT_APPROVED -> COMPLETED` (validation before human code review).

## Verified against the live account

- `CURRENT_USER()` inside an owner's-rights procedure returns the calling user (reviewer identity in `REVIEW_DECISION.REVIEWER` and `WORKFLOW_EVENT.ACTOR`).
- `BEGIN TRANSACTION` / `COMMIT` / `ROLLBACK` work inside owner's-rights Python procedures.
- Handlers load from `IMPORTS = ('@...CODE_STAGE/services-<sha>.zip')` with a nested `services.workflow.procedures.<fn>` handler path.

## Findings

- **Snowpark binds Python `None` as the string `'None'`.** All nullable parameters are passed through `_n()` and wrapped in `NULLIF(?, '')`. The integration tests assert no literal `'None'` is stored. Four `ENVIRONMENT='TEST'` runs created before the fix contain `'None'` strings; they are test debris and are left in place (tables are append-only).
- **Connector token cache breaks this account's OAuth login on Windows** (`CredWrite` error 1783: token larger than Credential Manager allows). `keyring` was uninstalled; each connection opens one browser login.

## Commands

```powershell
python -m pytest                                   # unit tests
python infrastructure/deploy_snowflake.py --dry-run --database DEV_AI_PLATFORM
python infrastructure/deploy_snowflake.py --connection anblicksorg-anblicksorg_aws --database DEV_AI_PLATFORM --warehouse DBT_WH
$env:AIP_SNOWFLAKE_CONNECTION = "anblicksorg-anblicksorg_aws"; $env:AIP_DATABASE = "DEV_AI_PLATFORM"
python -m pytest tests/integration -m integration
```
