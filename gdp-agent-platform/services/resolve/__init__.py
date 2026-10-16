"""Airflow failure resolution: triage, known-fix execution, approval, root-cause analysis and ticketing.

Design: airflow-failure-resolution-architecture (October 2026). Tables in V062; the decisions are pure (rules.py), the
work queue and the steps run as Snowflake procedures (procedures.py, snowflake/procedures/resolve.sql) that the ops worker
calls (apps/api/app/worker.py: jobs resolve, resolve_resume, resolve_clear). No Snowflake tasks.
"""
