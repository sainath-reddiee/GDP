# Airflow failure-resolution lab

Real Airflow DAGs that fail the way pipelines fail, so the whole resolution loop can be watched on a test copy of the
platform database: detect, guardrails, known fix (or human approval), Airflow rerun, resolved; or detect, root-cause
analysis, ticket, publish the fix, fixed automatically next time.

Everything runs in the platform's one database and one schema (`DEV_AI_PLATFORM.CORE`): the lab only creates the
`CORE.LAB_*` tables and stage, the `CORE.FIX_LAB_*` procedures and the lab DAGs' scopes, and `run_loop.py --teardown`
removes them. The lab worker runs without the Jira bot, so no real Jira ticket is raised.

| DAG | Failure | Expected |
|---|---|---|
| `lab_invalid_identifier` | table `CORE.LAB_ORDERS_STG` missing | known fix creates it, task cleared, rerun succeeds |
| `lab_invalid_column` | `invalid identifier 'AMOUNT_USD'` | first: miss, RCA, ticket; the fix is published from the incident; next run fixed automatically |
| `lab_duplicate_keys` | duplicate order ids (data check) | waits for human approval; approved (pass 1) runs the dedup fix; rejected (pass 2) closes it |
| `lab_multi_step` | export stage and table missing | two-step skill through the resolution agent |
| `lab_flaky` | fails try 1, passes on the retry | never queued: Airflow's own retry handles it |
| `lab_fix_breaks` | dimension table missing | its known fix fails on purpose: RCA and ticket ("the known fix failed") |
| `lab_ticket_only` | bad currency code | ticket only: RCA, no search |
| `lab_unscoped` | any error | out of scope, left alone |

## One-time setup

1. **Snowflake credential:** the DAGs sign in as an existing user that holds the account role `DATA_ENGINEER` (at a
   client, the service account Airflow already uses; for a local test, your own user) with a key pair. `setup_admin.sql`
   shows how to make the key and set it (`ALTER USER ... SET RSA_PUBLIC_KEY_2`). No new user or role; each lab table is
   granted to the platform's `DATA_ENGINEER` database role when it is created. Keep the private key to yourself.
2. **WSL Airflow:**
   ```bash
   source ~/airflow-env/bin/activate
   pip install snowflake-connector-python
   mkdir -p ~/airflow/dags && cp /mnt/c/<repo>/airflow_lab/dags/lab_dags.py ~/airflow/dags/
   airflow connections add lab_snowflake --conn-type generic --conn-login <user> \
     --conn-extra '{"account": "ANBLICKSORG-ANBLICKSORG_AWS", "warehouse": "DBT_WH", "database": "DEV_AI_PLATFORM", "role": "DATA_ENGINEER", "private_key_file": "/home/<you>/airflow/lab_key.p8"}'
   export AIRFLOW__API_AUTH__JWT_SECRET=$(openssl rand -hex 32)   # Airflow 3 API workers share one signing key
   AIRFLOW__CORE__LOAD_EXAMPLES=False airflow standalone
   ```
3. **Platform:** `python airflow_lab/start_platform.py api` and `python airflow_lab/start_platform.py worker`
   (each asks for a Snowflake sign-in). Add the environment once in Admin, Integrations, Airflow: kind
   *Self-hosted Airflow*, name `local-lab`, URL `http://localhost:8080`, poll every 60 s.

4. **Push (optional, to test push-first capture):** in Admin, Airflow, turn push on for `local-lab` and use *Push
   setup* to get the secret. Then in WSL:
   ```bash
   cp -r /mnt/c/<repo>/airflow_plugins/gdp_listener/gdp_listener /mnt/c/<repo>/airflow_plugins/gdp_listener/gdp_listener_plugin.py ~/airflow/plugins/
   export GDP_INGEST_URL=http://localhost:8021/api/ops/ingest GDP_ENV_ID=local-lab GDP_PUSH_SECRET=<the secret>
   ```
   and restart `airflow standalone` in the same shell. `localhost` reaches the API on Windows only with WSL mirrored
   networking (`networkingMode=mirrored` under `[wsl2]` in `%UserProfile%\.wslconfig`, then `wsl --shutdown`); without it,
   use the Windows host address from `ip route show default`.

## Run

```bash
python airflow_lab/run_loop.py --passes 2
```

It prints one row per DAG and pass, and writes `airflow_lab/last_run.json`. Open Operations, Resolutions to follow
along: each failure's path, the RCA, the approval, the fixes in Known fixes.

What to look for besides the outcomes:
- **Task catalogue:** the first poll reads the DAG source. Each lab DAG's page shows a Tasks section with the SQL and
  the `CORE.LAB_*` tables, and Knowledge has an *Airflow DAG* profile per DAG and the shipped *Airflow runbook* items.
  The RCA cites the task's SQL and tables (`PIPELINE [task:...]`), even when the log does not show the statement.
- **Push:** with the plugin installed, failures appear in seconds, and the worker log shows the resolve job running at
  the next tick. Outside the DAGs' run windows the environment is polled every 30 minutes (lab DAGs are manual, so
  only push and the safety poll capture them).
- **Tickets for automatic fixes:** each automatic fix queues a Jira note that raises the ticket, and the ticket is
  closed when the rerun succeeds. The lab has no Jira bot, so the incident shows the note as not sent.

## Clean up

`python airflow_lab/run_loop.py --teardown` removes the lab objects (lab failures and runs stay as history, lab fixes are
retired). Stop Airflow and the two platform processes; in Snowsight `ALTER USER <user> UNSET RSA_PUBLIC_KEY_2;` and delete the key file.
