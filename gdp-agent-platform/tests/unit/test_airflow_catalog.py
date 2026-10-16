"""The Airflow task catalogue (services/ops/catalog.py): parsing DAG sources, what each task reads and writes, the
catalogue rows of a DAG and the refresh that rebuilds only changed sources."""

import json
from pathlib import Path

from services.ops import catalog

LAB = Path(__file__).resolve().parents[2] / "airflow_lab" / "dags" / "lab_dags.py"

CLASSIC = '''
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.utils.task_group import TaskGroup

LOAD = """
    INSERT INTO ANALYTICS.MART.FACT_ORDERS
    SELECT O.* FROM RAW.SALES.ORDERS O JOIN RAW.SALES.CUSTOMERS C ON C.ID = O.CUSTOMER_ID
"""

def build_extract():
    return "COPY INTO RAW.SALES.ORDERS FROM @RAW.SALES.LANDING FILE_FORMAT = (TYPE = CSV)"

with DAG("orders_daily", schedule="0 2 * * *") as dag:
    extract = PythonOperator(task_id="extract", python_callable=build_extract)
    with TaskGroup("transform"):
        load = SQLExecuteQueryOperator(task_id="load_fact", conn_id="sf", sql=LOAD)
        dbt = BashOperator(task_id="dbt_run", bash_command="cd /dbt && dbt run --select stg_orders+ fct_orders")
    for region in ["us", "eu"]:
        SQLExecuteQueryOperator(task_id=f"publish_{region}", sql=f"CREATE OR REPLACE TABLE MART.ORDERS_{region} AS SELECT 1")
    snap = SnapLogicOperator(task_id="push_crm", pipeline="Orders to CRM")
    unload = SQLExecuteQueryOperator(task_id="unload", sql="COPY INTO @EXPORT.OUT FROM ANALYTICS.MART.FACT_ORDERS; TRUNCATE TABLE RAW.SALES.ORDERS")
    extract >> load >> dbt

other = DAG(dag_id="cleanup")
sweep = SQLExecuteQueryOperator(task_id="sweep", dag=other, sql="DELETE FROM RAW.SALES.ORDERS WHERE LOADED_AT < '2020-01-01'")
'''


def by_id(tasks):
    return {t["task_id"]: t for t in tasks}


# ---------------------------------------------------------------- parsing


def test_lab_file_maps_every_task_to_its_dag_and_sql():
    tasks = by_id(catalog.parse_source(LAB.read_text()))
    assert set(tasks) == {"load_orders", "publish_revenue", "check_unique_orders", "export_orders", "call_partner_api",
                          "join_customer_dim", "validate_currency", "nightly_report"}
    revenue = tasks["publish_revenue"]
    assert revenue["dag_id"] == "lab_invalid_column" and revenue["operator"] == "@task"
    info = catalog.describe(revenue)
    assert info["reads"] == ["CORE.LAB_ORDERS_CLEAN"] and info["writes"] == []
    assert info["sql_text"] == "SELECT SUM(AMOUNT_USD) FROM CORE.LAB_ORDERS_CLEAN"


def test_lab_stage_and_read_write_of_the_same_table():
    tasks = by_id(catalog.parse_source(LAB.read_text()))
    export = catalog.describe(tasks["export_orders"])
    assert export["reads"] == ["@CORE.LAB_EXPORT_STAGE", "CORE.LAB_ORDERS_EXPORT"]
    flaky = catalog.describe(tasks["call_partner_api"])
    assert flaky["reads"] == ["CORE.LAB_FLAKY_STATE"] and flaky["writes"] == ["CORE.LAB_FLAKY_STATE"]
    join = catalog.describe(tasks["join_customer_dim"])
    assert join["reads"] == ["CORE.LAB_CUSTOMER_DIM", "CORE.LAB_ORDERS_RAW"]
    assert catalog.describe(tasks["nightly_report"])["sql_text"] is None   # an error message is not SQL


def test_classic_operators_groups_callables_and_dag_variables():
    tasks = by_id(catalog.parse_source(CLASSIC))
    assert tasks["transform.load_fact"]["dag_id"] == "orders_daily"
    assert tasks["transform.load_fact"]["operator"] == "SQLExecuteQueryOperator"
    assert tasks["sweep"]["dag_id"] == "cleanup"
    load = catalog.describe(tasks["transform.load_fact"])
    assert load["writes"] == ["ANALYTICS.MART.FACT_ORDERS"]
    assert load["reads"] == ["RAW.SALES.CUSTOMERS", "RAW.SALES.ORDERS"]
    extract = catalog.describe(tasks["extract"])   # the SQL lives in the python_callable
    assert extract["writes"] == ["RAW.SALES.ORDERS"] and extract["reads"] == ["@RAW.SALES.LANDING"]
    unload = catalog.describe(tasks["unload"])
    assert unload["writes"] == ["@EXPORT.OUT", "RAW.SALES.ORDERS"] and unload["reads"] == ["ANALYTICS.MART.FACT_ORDERS"]
    assert catalog.describe(tasks["sweep"])["writes"] == ["RAW.SALES.ORDERS"]


def test_dbt_snaplogic_and_fstring_tasks():
    tasks = by_id(catalog.parse_source(CLASSIC))
    assert catalog.describe(tasks["transform.dbt_run"])["dbt_selectors"] == ["stg_orders+", "fct_orders"]
    assert catalog.describe(tasks["push_crm"])["snaplogic_pipeline"] == "Orders to CRM"
    publish = tasks["publish___VAR__"]
    assert catalog.describe(publish)["writes"] == []   # MART.ORDERS_{region}: not a real name, never guessed
    assert catalog.match_task("publish_eu", [publish]) is publish


def test_unparseable_source_falls_back_to_task_id_literals():
    text = 'broken(  \n op = SQLExecuteQueryOperator(task_id="load", sql="SELECT * FROM RAW.X")\n other(task_id="b")'
    tasks = by_id(catalog.parse_source(text))
    assert set(tasks) == {"load", "b"}
    assert catalog.describe(tasks["load"])["reads"] == ["RAW.X"]


def test_sql_detection_ignores_prose():
    assert catalog.sql_statements(["Select the right file", "Create the report", "SELECT 1", "list @S.T",
                                   "create or replace transient table A.B as select 1"]) == \
        ["SELECT 1", "list @S.T", "create or replace transient table A.B as select 1"]
    assert catalog.split_statements("SELECT ';' FROM A; INSERT INTO B SELECT 1;") == ["SELECT ';' FROM A",
                                                                                      "INSERT INTO B SELECT 1"]


# ---------------------------------------------------------------- the rows of one DAG

REST = [{"task_id": "extract", "class_ref": {"class_name": "PythonOperator"}, "downstream_task_ids": ["transform.load_fact"]},
        {"task_id": "transform.load_fact", "operator_name": "SQLExecuteQueryOperator", "downstream_task_ids": ["transform.dbt_run"]},
        {"task_id": "transform.dbt_run", "class_ref": {"class_name": "BashOperator"}, "downstream_task_ids": []},
        {"task_id": "publish_us", "class_ref": {"class_name": "SQLExecuteQueryOperator"}, "downstream_task_ids": []},
        {"task_id": "added_later", "class_ref": {"class_name": "EmptyOperator"}, "downstream_task_ids": []}]


def test_build_rows_joins_airflow_tasks_with_the_source():
    rows = by_id(catalog.build_rows("e", "orders_daily", CLASSIC, REST, "dags/orders.py", "h1"))
    assert set(rows) == {"extract", "transform.load_fact", "transform.dbt_run", "publish_us", "added_later"}
    load = rows["transform.load_fact"]
    assert load["upstream"] == ["extract"] and load["downstream"] == ["transform.dbt_run"]
    assert load["writes"] == ["ANALYTICS.MART.FACT_ORDERS"] and load["source_hash"] == "h1"
    assert rows["extract"]["operator"] == "PythonOperator"
    assert rows["added_later"]["sql_text"] is None and rows["added_later"]["operator"] == "EmptyOperator"
    assert rows["transform.dbt_run"]["dbt_selectors"] == ["stg_orders+", "fct_orders"]


def test_build_rows_without_a_task_list_uses_the_source_for_that_dag():
    rows = by_id(catalog.build_rows("e", "lab_multi_step", LAB.read_text(), []))
    assert list(rows) == ["export_orders"]
    rows = by_id(catalog.build_rows("e", "cleanup", CLASSIC, []))
    assert list(rows) == ["sweep"]


def test_sql_files_are_read_from_the_repository():
    text = 'with DAG("d"):\n    SQLExecuteQueryOperator(task_id="t", sql="sql/load.sql")\n'
    rows = catalog.build_rows("e", "d", text, [], file_sql={"sql/load.sql": "MERGE INTO M.F USING R.S ON 1=1"})
    assert rows[0]["writes"] == ["M.F"] and rows[0]["reads"] == ["R.S"]


# ---------------------------------------------------------------- refresh


class FakeDb:
    def __init__(self, dags):
        self.dags, self.statements = dags, []

    def query(self, sql, params=()):
        if "FROM CORE.DAG\n" in sql or "FROM CORE.DAG " in sql:
            return [dict(d) for d in self.dags]
        return []

    def execute(self, sql, params=()):
        self.statements.append((" ".join(sql.split())[:40], params))


class FakeAirflowClient:
    def __init__(self, text):
        self.text, self.source_calls = text, 0

    def dag_source(self, dag_id):
        self.source_calls += 1
        return self.text

    def dag_tasks(self, dag_id):
        return [t for t in REST if dag_id == "orders_daily"]


def test_refresh_rebuilds_changed_sources_and_reads_a_shared_file_once():
    text = CLASSIC
    digest = catalog.source_hash(text)
    db = FakeDb([{"dag_id": "orders_daily", "fileloc": "/opt/airflow/dags/orders.py", "source_hash": None},
                 {"dag_id": "cleanup", "fileloc": "/opt/airflow/dags/orders.py", "source_hash": digest}])
    mw = FakeAirflowClient(text)
    out = catalog.refresh(db, mw, "e", profiles=False)
    assert out["changed"] == ["orders_daily"] and out["tasks"] == 5 and not out["problems"]
    assert mw.source_calls == 1
    merge = next(p for s, p in db.statements if s.startswith("MERGE INTO CORE.DAG_TASK"))
    assert {r["task_id"] for r in json.loads(merge[0])} >= {"extract", "publish_us"}
    stamps = [p for s, p in db.statements if s.startswith("UPDATE CORE.DAG SET")]
    assert (catalog.source_hash(text, REST), "e", "orders_daily") in stamps   # the hash covers the task list too
    assert ("e", "cleanup") in stamps   # unchanged: only the time is stamped


def test_refresh_never_raises_from_a_poll():
    class Broken:
        def query(self, *a):
            raise RuntimeError("down")

    assert catalog.refresh_if_due(Broken(), None, "e")["problems"] == ["RuntimeError"]


def test_dag_source_v2_and_v1():
    from services.ops.mwaa import Mwaa

    class V2(Mwaa):
        def __init__(self, v):
            self.v, self.calls = v, []

        def version(self):
            return {"version": "3.0.0" if self.v == 2 else "2.9.0", "api_version": f"v{self.v}"}

        def invoke(self, method, path, params=None, body=None):
            self.calls.append(path)
            if path == "/dags/d":
                return {"file_token": "tok.en"}
            return {"content": "SRC"}

    v2, v1 = V2(2), V2(1)
    assert v2.dag_source("d") == "SRC" and v2.calls == ["/dagSources/d"]
    assert v1.dag_source("d") == "SRC" and v1.calls == ["/dags/d", "/dagSources/tok.en"]


# ---------------------------------------------------------------- DAG profiles and the runbook pack


def test_profile_rounds_what_changes_and_orders_tasks():
    from services.ops import dag_profile

    assert dag_profile.duration_text(30) == "under a minute"
    assert dag_profile.duration_text(7 * 60 + 20) == "7 min"
    assert dag_profile.duration_text(23 * 60) == "25 min"
    assert dag_profile.duration_text(95 * 60) == "1.5 h"
    assert [dag_profile.times_text(n) for n in (1, 3, 9)] == ["once", "a few times", "often"]
    profile = {"dag": {"dag_id": "orders_daily", "env_id": "prod", "schedule": "0 2 * * *", "owners": '["data-eng"]',
                       "criticality": "HIGH", "max_duration_min": 90, "tags": ["sales"]},
               "stats": {"p50_s": 700, "p95_s": 1500}, "upstream": ["raw_ingest"], "downstream": [],
               "tasks": [{"task_id": "load", "operator": "SQLExecuteQueryOperator", "upstream": '["extract"]',
                          "reads": ["RAW.SALES.ORDERS"], "writes": ["MART.FACT_ORDERS"]},
                         {"task_id": "extract", "operator": "PythonOperator", "upstream": [], "writes": ["RAW.SALES.ORDERS"]}],
               "failures": [{"task_id": "load", "exception_class": "ProgrammingError",
                             "exception_text": "000904: invalid identifier 'AMOUNT_USD'", "n": 3,
                             "fix_title": "Add AMOUNT_USD", "fix_status": "PUBLISHED"}]}
    item = dag_profile.entry(profile)
    text = item["content"]
    assert text.index("- extract") < text.index("- load (SQLExecuteQueryOperator): after extract")
    assert "reads RAW.SALES.ORDERS; writes MART.FACT_ORDERS" in text
    assert "about 10 min, slow runs 25 min" in text and "Waits for DAGs: raw_ingest." in text
    assert "(a few times). Known fix: Add AMOUNT_USD (published)." in text
    assert item["content_json"]["tables"] == ["MART.FACT_ORDERS", "RAW.SALES.ORDERS"]
    assert item["tags"] == ["OPS", "AIRFLOW", "orders_daily", "sales"]
    assert dag_profile.reference("prod", "orders_daily") == "ops.dag.prod.orders_daily"


def test_profile_is_remembered_as_ops_knowledge():
    from services.ops import dag_profile

    class Db:
        def query(self, sql, params=()):
            if "FROM CORE.DAG\n" in sql or "FROM CORE.DAG " in sql:
                return [{"dag_id": "d", "env_id": "e", "domain_id": None, "owners": [], "tags": []}]
            if "DOMAIN_REGISTRY" in sql:
                return [{"domain_id": "general"}]
            return []

        def execute(self, sql, params=()):
            pass

        def execute_count(self, sql, params=()):
            return 0

    seen = {}

    def remember(session, **kw):
        seen.update(kw)
        return "k1"

    assert dag_profile.remember_profile(Db(), "e", "d", remember=remember) == "k1"
    assert seen["kind"] == "AIRFLOW_DAG" and seen["origin"] == "OPS" and seen["key"] == "ops.dag.e.d"
    assert seen["domain_id"] == "general"


def test_runbook_pack_is_valid_knowledge():
    from infrastructure.seed_knowledge import load_runbooks
    from services.knowledge.validate import KNOWLEDGE_TYPES
    from services.resolve.actions import ACTIONS

    items = load_runbooks()
    assert len(items) >= 15
    assert len({k["key"] for k in items}) == len(items)
    for k in items:
        assert k["type"] == "AIRFLOW_RUNBOOK" in KNOWLEDGE_TYPES and k["key"].startswith("ops.runbook.")
        assert k["content"].startswith("Signals:") and len(k["title"]) < 200
        action = k["content_json"]["action"]
        assert action is None or action in ACTIONS


def test_seed_runbooks_qualifies_tables_and_keeps_edited_runbooks():
    from infrastructure import seed_knowledge

    class Cursor:
        def __init__(self):
            self.sql, self.description, self.rows, self.rowcount = [], None, [], 0

        def execute(self, sql, params=None):
            self.sql.append(sql)
            self.description, self.rows = [("X",)], []
            if "FROM DB1.CORE.DOMAIN_REGISTRY" in sql:
                self.description, self.rows = [("DOMAIN_ID",)], [("general",)]
            elif "SELECT ORIGIN" in sql and params and params[1] == "ops.runbook.insufficient-privileges":
                self.description, self.rows = [("ORIGIN",)], [("USER",)]

        def fetchall(self):
            return self.rows

    cur = Cursor()
    log = seed_knowledge.seed_runbooks(cur, "DB1")
    total = len(seed_knowledge.load_runbooks())
    assert log == [f"airflow runbooks: {total - 1} written, 0 unchanged, 1 edited in the app (kept)"]
    assert not any(" CORE." in s.replace("DB1.CORE.", "") for s in cur.sql)
    inserts = [s for s in cur.sql if "INSERT INTO DB1.CORE.DOMAIN_KNOWLEDGE" in s]
    assert len(inserts) == total - 1


# ---------------------------------------------------------------- the pipeline facts in every analysis

RUNBOOKS = [
    {"source_reference": "ops.runbook.invalid-identifier", "title": "Invalid identifier (000904)", "content": "Signals: ...",
     "content_json": json.dumps({"signals": ["000904", "invalid identifier"]})},
    {"source_reference": "ops.runbook.network-egress", "title": "Network", "content": "Signals: ...",
     "content_json": {"signals": ["Connection refused", "401"]}},
]


class PipelineDb:
    def query(self, sql, params=()):
        if "FROM CORE.DAG_TASK" in sql and "FLATTEN" not in sql:
            return [{"operator": "@task", "upstream": '["extract"]', "downstream": "[]",
                     "sql_text": "SELECT SUM(AMOUNT_USD) FROM CORE.LAB_ORDERS_CLEAN", "reads": '["CORE.LAB_ORDERS_CLEAN"]',
                     "writes": "[]", "dbt_selectors": "[]", "snaplogic_pipeline": None, "source_path": "lab_dags.py"}]
        if "FROM CORE.TASK_RUN" in sql:
            return [{"task_id": "extract", "state": "success"}]
        if "LATERAL FLATTEN(INPUT => T.WRITES)" in sql:
            return [{"env_id": "e", "dag_id": "lab_clean", "task_id": "build_clean", "obj": "CORE.LAB_ORDERS_CLEAN"}]
        if "FROM CORE.DAG\n" in sql or "FROM CORE.DAG " in sql:
            return [{"schedule": "0 2 * * *", "criticality": "HIGH", "expected_by_cron": None}]
        if "AIRFLOW_RUNBOOK" in sql:
            return RUNBOOKS
        return []


def test_runbooks_match_on_signals_and_whole_error_codes():
    from services.ops import pipeline

    assert [r["source_reference"] for r in pipeline.match_runbooks(RUNBOOKS, "000904 (42000): invalid identifier 'X'")] == \
        ["ops.runbook.invalid-identifier"]
    assert pipeline.match_runbooks(RUNBOOKS, "query 14010 took long") == []   # 401 inside a longer number
    assert pipeline.match_runbooks(RUNBOOKS, "") == []


def test_pipeline_facts_and_text():
    from services.ops import pipeline

    found = pipeline.facts(PipelineDb(), "e", "lab_invalid_column", "publish_revenue", "run1",
                           "000904 (42000): invalid identifier 'AMOUNT_USD'")
    text = pipeline.text(found)
    assert "Operator: @task. Runs after: extract (success)." in text
    assert "SELECT SUM(AMOUNT_USD) FROM CORE.LAB_ORDERS_CLEAN" in text
    assert "CORE.LAB_ORDERS_CLEAN is written by: lab_clean.build_clean." in text
    assert "DAG: schedule 0 2 * * *; criticality HIGH." in text
    assert "[runbook:ops.runbook.invalid-identifier]" in text


def test_context_has_a_pipeline_part_with_citations():
    from services.ops import context

    incident = {"env_id": "e", "dag_id": "lab_invalid_column", "task_id": "publish_revenue", "run_id": "run1",
                "error_excerpt": "000904 (42000): invalid identifier 'AMOUNT_USD'"}
    part = context.pipeline_part(PipelineDb(), incident, None, "")
    assert part["text"].startswith("PIPELINE [task:lab_invalid_column.publish_revenue]")
    assert [c["ref"] for c in part["citations"]] == ["task:lab_invalid_column.publish_revenue",
                                                     "runbook:ops.runbook.invalid-identifier"]
    assert context.ORDER.index("pipeline") == context.ORDER.index("log") + 1


def test_proposal_context_names_the_tables_the_task_reads():
    from services.resolve import propose

    class Db(PipelineDb):
        def query(self, sql, params=()):
            if "INFORMATION_SCHEMA" in sql.upper() or "COLUMNS" in sql.upper() or "STAGES" in sql.upper() \
                    or "TABLES" in sql.upper():
                return []
            return super().query(sql, params)

    failure = {"env_id": "e", "dag_id": "lab_invalid_column", "task_id": "publish_revenue",
               "exception_class": "ProgrammingError", "exception_text": "invalid identifier 'AMOUNT_USD'"}
    text = propose.context_text(Db(), failure, "Task failed with exception", "DB1", ["DB1.CORE.LAB_*"])
    assert "WHAT THE TASK RUNS (Airflow task catalogue, operator @task" in text
    assert "DB1.CORE.LAB_ORDERS_CLEAN" in text   # named by the catalogue, not by the log


def test_graph_starts_at_the_task_node():
    from services.resolve import graph

    class Db:
        def query(self, sql, params=()):
            return []

    assert graph.start_nodes(Db(), {"env_id": "e", "dag_id": "d", "task_id": "t"})[:2] == ["task:e:d:t", "dag:e:d"]


def test_graph_view_links_tasks_and_tables():
    view = (Path(__file__).resolve().parents[2] / "snowflake" / "views" / "resolution_graph.sql").read_text()
    for needle in ("'AIRFLOW_TASK'", "'HAS_TASK'", "'TASK_FLOW'", "'READS'", "'WRITES'", "IFF(E.KIND = 'WRITES'",
                   "COALESCE(P.SOURCE_PATH, D.REPO_PATH)"):
        assert needle in view
