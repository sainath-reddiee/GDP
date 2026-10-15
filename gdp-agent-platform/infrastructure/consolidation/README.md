# Platform schema consolidation runbook (V060, V061)

Moves every platform object of the platform database into one schema, `CORE`, with zero data loss. The mapping is
`services/common/platform_schema.py` (`MOVED`); V061, the compatibility views and the codemod are generated from it.

What moves and how:

| Objects | How | Why |
|---|---|---|
| Tables (112), stages (5), file formats (3), sequences (3), row access policies (2), one view | `ALTER ... RENAME TO <db>.CORE.<name>` in V061 | The same object: rows, staged files, Time Travel, constraints and grants stay with it |
| Procedures, reporting views, Cortex Search services | Created in CORE by the deploy | Code only, or derived from the moved tables |
| Cortex Agent | Already in CORE; updated in place with `ALTER AGENT ... MODIFY LIVE VERSION` | Keeps its versions and monitoring |
| Tasks, network rule | Twin in CORE by `runtime apply` (Snowflake cannot rename them); old ones stay suspended | |
| Secrets, git repository clones, dbt projects | Stay where they are; new ones are created in CORE | No RENAME for secrets or git repositories; dbt project versions must not be reset; rows store their names |
| LANDING, GDP_SILVER | Not moved | Client data, user-configurable locations |

Nothing is dropped. Old schemas keep read-only compatibility views (30 days), suspended old tasks, the old network rule
and the old Cortex Search services (suspended) until a separately approved cleanup.

## Order

Every script takes `--connection <name in connections.toml> --database <DB>`, prints what it does, and writes JSON to
`consolidation_runs/` (git-ignored) plus rows in `CORE.CONSOLIDATION_*`. Scripts that change objects run only with
`--execute`.

| Step | Command | Exit check |
|---|---|---|
| P0b rehearsal | `CREATE DATABASE <DB>_REHEARSAL CLONE <DB> INCLUDE INTERNAL STAGES` (after `ALTER STAGE ... REFRESH` on directory stages), copy the other stages with `COPY FILES`, then every step below with `--database <DB>_REHEARSAL --rehearsal` | All checks pass; results reviewed |
| V060 | `python infrastructure/deploy_snowflake.py --connection C --database DB --through 060` | Manifest tables exist |
| P3 freeze | Stop the API and the worker; `python -m infrastructure.consolidation.runtime freeze ... --execute` | Running tasks suspended and recorded |
| P1/P2 baseline | `python -m infrastructure.consolidation.baseline ...` | `COMPLETED`: no unknown objects, every capture succeeded |
| P4 safety net | Raise `DATA_RETENTION_TIME_IN_DAYS`; `CREATE DATABASE <DB>_PRECONSOLIDATION_<date> CLONE <DB> INCLUDE INTERNAL STAGES`; `COPY FILES` for every internal stage | Backup counts and file lists equal the baseline |
| P5a move | `python infrastructure/deploy_snowflake.py ... --consolidate --through 061` | V061 recorded |
| P6a verify data | `python -m infrastructure.consolidation.verify data ... --backup-database <backup>` | 0 failed (same object ids, rows, hashes, columns, keys, policies, grants, Time Travel, files, sequences) |
| P5b deploy | `python infrastructure/deploy_snowflake.py ... --consolidate` (rehearsal: add `--rehearsal`) | Procedures, views, compatibility views, search services, agent applied |
| P5c runtime | `python -m infrastructure.consolidation.runtime apply ... --execute` | Task twins, network rule, compatibility view grants, old services suspended, old procedure usage revoked |
| P6b verify functional | `python -m infrastructure.consolidation.verify functional ...` | 0 failed |
| P7 reopen | Start the API and worker on the new release; `runtime resume ... --execute`; smoke test | Journeys work |

The external access integration of GitHub publishing is account level: `runtime apply` prints the statement that
points it at `CORE.GITHUB_API_EGRESS` for its owner; the old rule keeps working until then.

## Gate

`deploy_snowflake.py` applies V061 only with `--consolidate` after a completed baseline of that database, unless the
old schemas hold no rows (a new environment). The Admin "Re-apply everything" path never passes `--consolidate`.

## Rollback

`python -m infrastructure.consolidation.rollback ... --execute` renames everything back from the manifest (rows written
since the cut-over stay), restores the references V061 changed, re-grants and resumes the old objects and removes the
V061 ledger row. Then deploy the previous release with `--force` and run `verify data` again.
