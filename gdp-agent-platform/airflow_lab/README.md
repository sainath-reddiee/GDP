# Airflow failure-resolution lab

Real Airflow DAGs that fail the way pipelines fail, so the whole resolution loop can be watched on a test copy of the
platform database: detect, guardrails, known fix (or human approval), Airflow rerun, resolved; or detect, root-cause
analysis, ticket, publish the fix, fixed automatically next time.

Test copy only (`*_REHEARSAL`). Nothing here touches DEV or production, and no real Jira ticket is raised.

| DAG | Failure | Expected |
|---|---|---|
| `lab_invalid_identifier` | table `LAB.ORDERS_STG` missing | known fix creates it, task cleared, rerun succeeds |
| `lab_invalid_column` | `invalid identifier 'AMOUNT_USD'` | first: miss, RCA, ticket; the fix is published from the incident; next run fixed automatically |
| `lab_duplicate_keys` | duplicate order ids (data check) | waits for human approval; approved (pass 1) runs the dedup fix; rejected (pass 2) closes it |
| `lab_multi_step` | export stage and table missing | two-step skill through the resolution agent |
| `lab_flaky` | fails try 1, passes on the retry | never queued: Airflow's own retry handles it |
| `lab_fix_breaks` | dimension table missing | its known fix fails on purpose: RCA and ticket ("the known fix failed") |
| `lab_ticket_only` | bad currency code | ticket only: RCA, no search |
| `lab_unscoped` | any error | out of scope, left alone |

## One-time setup

1. **Snowflake (you, in Snowsight):** run `setup_admin.sql`. It creates the `LAB` schema in the test copy, the role
   `LAB_RUNNER` and the service user `LAB_AIRFLOW`. Then add a key pair (recommended) or a programmatic access token as
   the file explains. Keep the credential to yourself.
2. **WSL Airflow:**
   ```bash
   source ~/airflow-env/bin/activate
   pip install snowflake-connector-python
   mkdir -p ~/airflow/dags && cp /mnt/c/<repo>/airflow_lab/dags/lab_dags.py ~/airflow/dags/
   airflow connections add lab_snowflake --conn-type generic --conn-login LAB_AIRFLOW \
     --conn-extra '{"account": "ANBLICKSORG-ANBLICKSORG_AWS", "warehouse": "DBT_WH", "database": "DEV_AI_PLATFORM_APP_REHEARSAL", "role": "LAB_RUNNER", "private_key_file": "/home/<you>/airflow/lab_key.p8"}'
   export AIRFLOW__API_AUTH__JWT_SECRET=$(openssl rand -hex 32)   # Airflow 3 API workers share one signing key
   AIRFLOW__CORE__LOAD_EXAMPLES=False airflow standalone
   ```
   With a token instead of a key: drop `private_key_file` and add `--conn-password <token>`.
3. **Platform:** `python airflow_lab/start_platform.py api` and `python airflow_lab/start_platform.py worker`
   (each asks for a Snowflake sign-in). Add the environment once in Admin, Integrations, Airflow: kind
   *Self-hosted Airflow*, name `local-lab`, URL `http://localhost:8080`, poll every 60 s.

## Run

```bash
python airflow_lab/run_loop.py --passes 2
```

It prints one row per DAG and pass, and writes `airflow_lab/last_run.json`. Open Operations, Resolutions to follow
along: each failure's path, the RCA, the approval, the fixes in Known fixes.

## Clean up

Stop Airflow and the two platform processes; in Snowsight `DROP USER LAB_AIRFLOW; DROP ROLE LAB_RUNNER;`. The test copy
is disposable.
