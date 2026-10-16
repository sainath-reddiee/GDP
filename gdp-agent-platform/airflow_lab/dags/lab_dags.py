"""Airflow failure-resolution lab: one small DAG per edge case, each failing the way real pipelines fail.

Copy this file into $AIRFLOW_HOME/dags. Every DAG is manual (no schedule) and runs SQL in the lab schema through the
Airflow connection `lab_snowflake` (Snowflake key pair or programmatic access token for the LAB_AIRFLOW service user;
see airflow_lab/README.md). The platform's fixes (CORE.FIX_LAB_*) repair the cause in Snowflake, the platform clears the
task, and the rerun succeeds. airflow_lab/reset.sql puts every failure back.

    lab_invalid_identifier  table LAB.ORDERS_STG is missing            -> known fix creates it
    lab_invalid_column      invalid identifier 'AMOUNT_USD'            -> miss, RCA, ticket; then a learned fix
    lab_duplicate_keys      duplicate order ids (a data check)         -> fix needs human approval (dedup)
    lab_multi_step          export stage and table are missing         -> two-step skill
    lab_flaky               fails on try 1, passes on the retry        -> never acted on (Airflow retries it)
    lab_fix_breaks          dimension table is missing                 -> its known fix fails, so RCA and a ticket
    lab_ticket_only         bad currency code (Python ValueError)      -> ticket only: RCA, no fix
    lab_unscoped            an error in a DAG outside the scope        -> left alone (out of scope)
"""

from __future__ import annotations

from datetime import datetime, timedelta

try:  # Airflow 3
    from airflow.sdk import DAG, BaseHook, task
except ImportError:  # Airflow 2
    from airflow import DAG
    from airflow.decorators import task
    from airflow.hooks.base import BaseHook

CONN_ID = "lab_snowflake"
START = datetime(2026, 1, 1)


def connect():
    """A Snowflake connection from `lab_snowflake`: login is the user; the password is a programmatic access token, or
    extra.private_key_file points at a key pair. extra holds account, warehouse, database, role (schema LAB)."""
    import snowflake.connector

    c = BaseHook.get_connection(CONN_ID)
    extra = c.extra_dejson or {}
    kwargs = dict(user=c.login, account=extra.get("account"), warehouse=extra.get("warehouse"),
                  database=extra.get("database"), role=extra.get("role"), schema="LAB", login_timeout=60)
    if extra.get("private_key_file"):
        kwargs.update(authenticator="SNOWFLAKE_JWT", private_key_file=extra["private_key_file"])
    else:
        kwargs["password"] = c.password
    return snowflake.connector.connect(**kwargs)


def run(sql: str):
    con = connect()
    try:
        cur = con.cursor()
        rows = None
        for statement in [s for s in sql.split(";") if s.strip()]:
            cur.execute(statement)
            rows = cur.fetchall() if cur.description else rows
        return rows
    finally:
        con.close()


def lab_dag(dag_id: str, doc: str, retries: int = 0):
    return DAG(dag_id, start_date=START, schedule=None, catchup=False, tags=["resolution-lab"], doc_md=doc,
               default_args={"retries": retries, "retry_delay": timedelta(seconds=5)})


with lab_dag("lab_invalid_identifier", "Reads a staging table that is missing."):
    @task(task_id="load_orders")
    def load_orders():
        return run("SELECT COUNT(*) FROM LAB.ORDERS_STG")[0][0]
    load_orders()

with lab_dag("lab_invalid_column", "Selects a column the table does not have yet."):
    @task(task_id="publish_revenue")
    def publish_revenue():
        return run("SELECT SUM(AMOUNT_USD) FROM LAB.ORDERS_CLEAN")[0][0]
    publish_revenue()

with lab_dag("lab_duplicate_keys", "A data check: order ids must be unique."):
    @task(task_id="check_unique_orders")
    def check_unique_orders():
        dupes = run("SELECT ORDER_ID, COUNT(*) FROM LAB.ORDERS_IN GROUP BY ORDER_ID HAVING COUNT(*) > 1")
        if dupes:
            raise ValueError(f"Data quality check failed: {len(dupes)} duplicate ORDER_ID values in LAB.ORDERS_IN "
                             f"(first: {dupes[0][0]})")
        return "unique"
    check_unique_orders()

with lab_dag("lab_multi_step", "Exports to a stage and a table that both have to exist."):
    @task(task_id="export_orders")
    def export_orders():
        run("LIST @LAB.EXPORT_STAGE")
        return run("SELECT COUNT(*) FROM LAB.ORDERS_EXPORT")[0][0]
    export_orders()

with lab_dag("lab_flaky", "Fails on its first try and passes when Airflow retries it.", retries=1):
    @task(task_id="call_partner_api")
    def call_partner_api():
        seen = run("SELECT COUNT(*) FROM LAB.FLAKY_STATE")[0][0]
        run("INSERT INTO LAB.FLAKY_STATE (TRIED_AT) SELECT CURRENT_TIMESTAMP()")
        if not seen:
            raise TimeoutError("Partner API did not answer within 30 seconds (transient)")
        return "ok"
    call_partner_api()

with lab_dag("lab_fix_breaks", "Joins a dimension table that is missing; its known fix is broken on purpose."):
    @task(task_id="join_customer_dim")
    def join_customer_dim():
        return run("SELECT COUNT(*) FROM LAB.ORDERS_RAW O JOIN LAB.CUSTOMER_DIM C ON C.CUSTOMER_ID = O.CUSTOMER_ID")[0][0]
    join_customer_dim()

with lab_dag("lab_ticket_only", "A bad reference value: never auto-fixed, always analysed and ticketed."):
    @task(task_id="validate_currency")
    def validate_currency():
        bad = run("SELECT DISTINCT CURRENCY FROM LAB.ORDERS_RAW WHERE CURRENCY NOT IN ('USD', 'EUR', 'GBP')")
        if bad:
            raise ValueError(f"Currency code '{bad[0][0]}' is not in the reference list")
        return "ok"
    validate_currency()

with lab_dag("lab_unscoped", "Fails, but its DAG is not in the resolution scope."):
    @task(task_id="nightly_report")
    def nightly_report():
        raise RuntimeError("Report template 'nightly_v2' not found")
    nightly_report()
