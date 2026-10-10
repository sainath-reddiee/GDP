# GDP Agent Platform

Agentic data engineering on Snowflake: sources and profiling, modeling runs from mapping to reviewed dbt code, QA, code context, Jira, Airflow monitoring and incidents, all governed by roles and approvals.

- **Guidebook and runbook:** [docs/guidebook/index.html](docs/guidebook/index.html), covering roles and grants, setup, every feature, configuration, deployment, secrets, acceptance checks and the table reference.
- **API:** `apps/api` (FastAPI). Settings in `apps/api/.env` (copy `apps/api/.env.example`; the file is gitignored).
- **Web:** `apps/web` (Next.js).
- **Snowflake objects:** `snowflake/` (migrations, procedures, search, agents), deployed with `infrastructure/deploy_snowflake.py`.
- **Airflow plugin:** `airflow_plugins/gdp_listener`.
