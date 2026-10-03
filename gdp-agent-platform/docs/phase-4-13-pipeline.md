# Phases 4–13 — profiling through Phase 1 complete

The vertical slice is now implemented end to end: landed tables are profiled, a domain is
identified, hybrid mapping is reviewed, an STTM and Soda checks are approved, a compile-only
dbt project is generated and validated, and a human approves the code. Phase 2 deployment
states stay disabled. No Snowflake Tasks are created.

## What each stage does

| Stage | Procedure | Human gate |
|---|---|---|
| Profiling | `PROFILE.RUN_PROFILING` | none — SQL stats + one `AI_COMPLETE` per table |
| Domain | `KNOWLEDGE.IDENTIFY_DOMAIN` | none — Cortex Search + token scoring |
| Mapping | `MAPPING.GENERATE_MAPPING_CANDIDATES` / `SAVE_MAPPING_DECISIONS` | `MAPPING_REVIEW` → `MAPPING_APPROVED` only if every source column is decided and required targets are covered |
| STTM | `CONTRACT.GENERATE_STTM` | `STTM_REVIEW` → `STTM_APPROVED` |
| Soda | `CONTRACT.GENERATE_SODA` / `IMPORT_CLIENT_EXPECTATIONS` | `SODA_REVIEW` → `SODA_APPROVED` |
| dbt | `CODEGEN.GENERATE_DBT` | none |
| Validation | `CODEGEN.VALIDATE_DBT` | compile `WRITEBACK=FALSE` when dbt project objects exist |
| Review | `CORE.REVIEW_TRANSITION` | `DBT_REVIEW` → `DBT_APPROVED` → `COMPLETED` |

## Agent

One supervisor, `CORE.DATA_ENGINEERING_SUPERVISOR`, with Cortex Search plus generic procedure
tools. It recommends and calls stage procedures. It cannot approve reviews and cannot promote
to production.

## Verify

```
python -m pytest tests/unit -q
python -m pytest tests/integration/test_phase1_pipeline.py -m integration
npx playwright test e2e/smoke.spec.ts
```
