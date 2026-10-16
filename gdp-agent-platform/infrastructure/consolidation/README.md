# One platform schema: CORE

Every platform object of the platform database lives in one schema, `CORE`: tables, stages, file formats, sequences,
row access policies, views, procedures, tasks, Cortex Search services, the Cortex Agent, dbt projects, git repository
clones, the network rule and the GitHub token secret. Only client data stays outside it, in `LANDING`, `GDP_SILVER`
and any landing target a user picks. Code names objects as `CORE.<NAME>` (SQL templates as `{{database}}.{{schema}}`);
the one constant is `PLATFORM_SCHEMA` in `services/common/platform_schema.py`, and
`tests/unit/test_platform_schema.py` fails the build if any code, template or web label names one of the old schemas.

## New environment: one command

```
python infrastructure/deploy_snowflake.py --connection <name> --database <NEW_DB> --warehouse <WH>
```

With an empty ledger the deploy runs `snowflake/database/baseline/B061__platform_core.sql`, which creates the final
layout directly in CORE, records V001 to V061 as applied, then applies later migrations and the procedures, views,
Cortex Search, agent and seed. No old schema is ever created. `make_baseline.py` proved on Snowflake that the baseline
builds exactly what V001 to V061 build (objects, columns, keys, defaults, policies, grants, future grants, role
hierarchy, starter rows). New migrations go in `snowflake/database/migrations/` from V062, name only
`{{database}}.CORE`, and grant every object they create (no future grants in CORE).

## Existing environment: the one-time move

V001 to V061 stay in `snowflake/database/migrations/archive/` (applied history, never edited) for environments that
predate the move. Stop the API and the worker, then:

```
python -m infrastructure.consolidation.consolidate --connection <name> --database <DB> --no-compat-views
python -m infrastructure.consolidation.cleanup     --connection <name> --database <DB>             # dry run
python -m infrastructure.consolidation.cleanup     --connection <name> --database <DB> --execute
```

`consolidate.py` runs, in one session and stopping at the first failure: V060 (manifest tables), freeze (running
tasks suspended and recorded), baseline (row counts, full-row hashes, columns, keys, grants, policies, staged files,
sequence positions, runtime objects; unknown objects stop it), safety net (Time Travel retention raised, zero-copy
backup clone `<DB>_PRECONSOLIDATION_<date>` with every staged file), V061 (`ALTER ... RENAME TO <db>.CORE.<name>`:
the same objects, so rows, files, Time Travel and grants stay with them), data verification against the baseline and
the backup, the full deploy, the runtime step (task twins, network rule), functional verification, and the resume of
the tasks that were running. `--start-at deploy` continues after a data verification that was reviewed.

`cleanup.py` then empties the old schemas: old procedures, search services, tasks and views (CORE has them) are
dropped; dbt projects are renamed into CORE with their versions; git clones are re-created in CORE from GitHub and the
rows that name them re-pointed; the GitHub integration is switched to the CORE network rule. The GitHub token cannot
be copied (a secret cannot be read): create `CORE.GITHUB_TOKEN`, add it to the integration, have its owner set the value
in Snowsight, switch `GITHUB_PUBLISH.secret` to it, refresh the publisher, test, then drop the old secret. Finally
`cleanup.py --execute --drop-schemas` drops each old schema only when nothing is left in it.

Without `--no-compat-views`, `runtime apply` leaves read-only views under the old names (the tables' own SELECT grants,
none for credential tables) for readers outside the app; they go with the cleanup.

Rollback before the cleanup: `python -m infrastructure.consolidation.rollback ... --execute` renames everything back
(rows written since stay), then deploy the previous release with `--force`. After the cleanup, restore from the backup
clone and Time Travel.

## Gate

`deploy_snowflake.py` applies V061 only to a new environment (empty ledger at the start of the deploy) or with
`--consolidate` after a completed baseline of that database. The Admin "Re-apply everything" path never passes
`--consolidate`.

## Proof

- Rehearsals on clones of DEV_AI_PLATFORM (`rehearse.py`): data checks 2,382 of 2,384 (the two were a checker bug,
  fixed), runtime 464 statements and functional 333 checks with 0 failed, rollback 329 statements with every table
  back and no row lost; every API read endpoint (133, `app_smoke.py`) answered the same on the consolidated clone with
  the new code as on the original with the old code.
- DEV_AI_PLATFORM, 2026-10-16 (`consolidate.py`, `cleanup.py`): row counts, full-row hashes, columns, Time Travel
  counts and grants of all 131 tables and all 6 stages' files matched the baseline, and the row-by-row comparison with
  the backup clone matched for 130 tables (CASE_RECORD's backup copy cannot be read once its policy's tables moved;
  its count, hash and Time Travel matched); functional 122 checks with 0 failed; cleanup 77 statements with 0 failed;
  the GitHub publisher runs on `CORE.GITHUB_TOKEN`; all 17 old schemas dropped. Backup kept:
  `DEV_AI_PLATFORM_PRECONSOLIDATION_20261016` (Time Travel retention raised to 14 days).
