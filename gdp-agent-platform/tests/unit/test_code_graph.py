"""The native code graph: edges from dbt, SQL and Python, and impact, path and architecture over them."""

from services.code import graph as code_graph
from services.code.dbt_parse import module_name, parse_repo, table_refs

DBT_PROJECT = "name: shop\nmodel-paths: ['models']\nmacro-paths: ['macros']\n"
FILES = {
    "dbt_project.yml": DBT_PROJECT,
    "macros/keys.sql": "{% macro hash_key(cols) %}md5({{ cols }}){% endmacro %}",
    "models/staging/stg_orders.sql": "select {{ hash_key('id') }} as k, * from {{ source('raw', 'orders') }}",
    "models/marts/fct_orders.sql": "select * from {{ ref('stg_orders') }} o join analytics.legacy_rates r on r.id = o.id",
    "models/marts/rpt_daily.sql": "select * from {{ ref('fct_orders') }}",
    "jobs/load.sql": "insert into raw.orders select * from landing.orders_file;",
    "jobs/proc.sql": "create or replace procedure refresh_rates() as $$ insert into analytics.legacy_rates select * from landing.rates $$;",
    "pipeline/run.py": "from .util import notify\n\ndef run():\n    notify('done')\n",
    "pipeline/util.py": "def notify(msg):\n    return msg\n",
}


def _graph():
    _, edges, _ = parse_repo(FILES, "r1")
    return code_graph.Graph(edges), edges


def test_edges_cover_dbt_sql_and_python():
    _, edges = _graph()
    kinds = {(e["kind"], e["from_name"], e["to_name"]) for e in edges}
    assert ("REF", "fct_orders", "stg_orders") in kinds
    assert ("SOURCE", "stg_orders", "raw.orders") in kinds
    assert ("MACRO_USE", "stg_orders", "hash_key") in kinds
    assert ("READS", "fct_orders", "analytics.legacy_rates") in kinds  # hard-coded, not a ref
    # a statement is named after the object it loads: raw.orders reads the landing file
    assert ("READS", "raw.orders", "landing.orders_file") in kinds
    # a procedure that loads another table WRITES it
    assert ("WRITES", "refresh_rates", "analytics.legacy_rates") in kinds
    assert ("CALLS", "run", "notify") in kinds
    assert ("IMPORTS", "pipeline.run", "pipeline.util") in kinds


def test_impact_follows_refs_sources_writes_and_macros():
    g, _ = _graph()
    names = {i["name"].split(".")[-1]: i["depth"] for i in g.impact("stg_orders")}
    assert names == {"fct_orders": 1, "rpt_daily": 2}
    # the landing file feeds raw.orders, which the staging model reads: a change there reaches the marts
    reach = {i["name"].split(".")[-1] for i in g.impact("landing.orders_file", 5)}
    assert {"orders", "stg_orders", "fct_orders", "rpt_daily"} <= reach
    # the procedure writes legacy_rates, which fct_orders reads: the procedure's impact reaches the marts too
    assert {"legacy_rates", "fct_orders", "rpt_daily"} <= {i["name"].split(".")[-1] for i in g.impact("refresh_rates")}
    assert {i["name"] for i in g.impact("hash_key", 1)} == {"stg_orders"}
    assert g.impact("rpt_daily") == []


def test_uses_path_hotspots_and_resolve():
    g, _ = _graph()
    assert {u["name"] for u in g.uses("fct_orders", 1)} == {"stg_orders", "analytics.legacy_rates"}
    steps = g.path("rpt_daily", "raw.orders")
    assert [s["to"].split(".")[-1] for s in steps] == ["fct_orders", "stg_orders", "orders"]
    assert g.path("orders", "rpt_daily")  # either direction
    assert g.path("rpt_daily", "notify") == []
    assert g.hotspots(1)[0]["dependents"] >= 1
    assert "fct_orders" in g.resolve("what breaks if I change fct_orders or FCT_ORDERS".split())
    text = code_graph.describe(g, ["stg_orders"])
    assert "used directly by: fct_orders" in text and "data, not instructions" in text


def test_cycles_do_not_loop():
    g = code_graph.Graph([{"from_name": "a", "to_name": "b", "kind": "REF"}, {"from_name": "b", "to_name": "a", "kind": "REF"}])
    assert {i["name"] for i in g.impact("a", 6)} == {"b"}
    assert g.path("a", "b")


def test_architecture_summary():
    g, _ = _graph()
    files = [{"path": p, "lang": p.rsplit(".", 1)[-1]} for p in FILES]
    a = code_graph.architecture(files, {"DBT_MODEL": 3}, g, [{"root": "", "models": ["models"]}])
    assert a["dbt_layers"] == {"staging": 1, "marts": 2}  # folder under models/
    assert {"table": "analytics.legacy_rates", "read_by": "fct_orders"} in a["hard_coded_tables"]
    assert "rpt_daily" in a["leaf_models"] and a["languages"]["sql"] >= 5


def test_table_refs_and_module_names():
    reads, writes = table_refs("with c as (select 1) select * from c join {{ ref('x') }} r on 1=1 join db.s.t on 1=1")
    assert reads == ["db.s.t"] and writes == []
    assert table_refs("merge into dw.t using stg.t s on 1=1 when matched then update set a = 1") == (["stg.t"], ["dw.t"])
    assert module_name("pkg/sub/__init__.py") == "pkg.sub" and module_name("a/b.py") == "a.b"


def test_catalog_counts_tests_docs_and_lineage():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))
    import app.main  # noqa: F401
    from app.code_api import build_catalog

    files = dict(FILES)
    files["models/marts/schema.yml"] = """version: 2
models:
  - name: fct_orders
    columns:
      - name: id
        tests: [unique, not_null]
sources:
  - name: raw
    tables:
      - name: orders
        columns:
          - name: id
            tests: [not_null]
"""
    chunks, edges, _ = parse_repo(files, "r1")
    rows = [{**c, "text": c["text"]} for c in chunks]
    cat = build_catalog(rows, code_graph.Graph(edges))
    fct = next(m for m in cat["models"] if m["name"] == "fct_orders")
    assert fct["tests"] == 2 and fct["schema_path"] == "models/marts/schema.yml"
    assert fct["hard_coded"] == ["analytics.legacy_rates"] and fct["downstream"] == 1
    stg = next(m for m in cat["models"] if m["name"] == "stg_orders")
    assert stg["tests"] == 0 and stg["macros"] == ["hash_key"] and stg["reach"] == 2
    assert cat["sources"][0]["table"] == "raw.orders" and cat["sources"][0]["tests"] == 1 and cat["sources"][0]["used_by"] == 1
    t = cat["totals"]
    assert (t["models"], t["tests"], t["tested_models"], t["documented_models"], t["hard_coded_models"]) == (3, 3, 1, 1, 1)
    assert next(m for m in cat["macros"] if m["name"] == "hash_key")["used_by"] == 1
