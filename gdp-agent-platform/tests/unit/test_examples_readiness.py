"""Correct-output examples (read, checked against the approved model, rendered) and the domain readiness levels."""

import pytest

from services.governance import policy
from services.knowledge import examples
from services.knowledge.examples import ExampleError
from services.knowledge.provenance import readiness
from services.knowledge.usage import compose_domain_context
from services.knowledge.validate import KNOWLEDGE_TYPES

ENTITY = {"entity_name": "COMPANY_CORE", "business_keys": ["SOURCE_UNIQUE_ID"], "grain": "one row per company",
          "attributes": [{"name": "SOURCE_UNIQUE_ID", "description": "id"}, {"name": "COMPANY_NAME", "description": "name"},
                         {"name": "DUNS_NUMBER", "description": ""}, {"name": "OLD", "status": "RETIRED"}]}


def test_read_csv_skips_blank_rows_and_normalises_headers():
    header, rows = examples.read_rows("company.csv", b"source unique id,Company Name,notes\nA1,Acme,x\n,,\nA2,Beta|Co,\n")
    assert header == ["SOURCE_UNIQUE_ID", "COMPANY_NAME", "NOTES"]
    assert rows == [["A1", "Acme", "x"], ["A2", "Beta|Co", ""]]


def test_read_rejects_other_files_and_empty_ones():
    with pytest.raises(ExampleError, match=".csv or .xlsx"):
        examples.read_rows("company.json", b"{}")
    with pytest.raises(ExampleError, match="no rows"):
        examples.read_rows("company.csv", b"\n\n")


def test_check_keeps_known_columns_and_reports_the_rest():
    header = ["SOURCE_UNIQUE_ID", "COMPANY_NAME", "NOTES", "OLD"]
    out = examples.check("COMPANY_CORE", header, [["A1", "Acme", "x", "1"], ["", "Beta", "", ""], ["A1", "Again", "", ""]], ENTITY)
    assert out["columns"] == ["SOURCE_UNIQUE_ID", "COMPANY_NAME"]
    assert out["rows"] == [["A1", "Acme"], ["", "Beta"], ["A1", "Again"]]
    assert out["unknown_columns"] == ["NOTES", "OLD"]       # a retired column is not a column any more
    assert out["absent_columns"] == ["DUNS_NUMBER"]
    assert out["empty_keys"] == ["SOURCE_UNIQUE_ID"] and out["duplicate_keys"] == 1
    assert any("left out" in w for w in out["warnings"])


def test_check_errors():
    with pytest.raises(ExampleError, match="not a table of the approved"):
        examples.check("NOPE", ["A"], [["1"]], None)
    with pytest.raises(ExampleError, match="None of the file's columns"):
        examples.check("COMPANY_CORE", ["X", "Y"], [["1", "2"]], ENTITY)
    with pytest.raises(ExampleError, match="no example rows"):
        examples.check("COMPANY_CORE", ["COMPANY_NAME"], [[""]], ENTITY)
    with pytest.raises(ExampleError, match="named twice"):
        examples.check("COMPANY_CORE", ["COMPANY_NAME", "COMPANY_NAME"], [["a", "b"]], ENTITY)


def test_check_caps_rows_and_flags_missing_keys():
    out = examples.check("COMPANY_CORE", ["COMPANY_NAME"], [[f"c{i}"] for i in range(examples.MAX_ROWS + 7)], ENTITY)
    assert out["row_count"] == examples.MAX_ROWS
    assert out["missing_keys"] == ["SOURCE_UNIQUE_ID"]
    assert any("only the first" in w for w in out["warnings"])


def test_markdown_escapes_and_limits():
    text = examples.markdown("T", ["A", "B"], [["x|y", "z" * 80], ["1", "2"], ["3", "4"]], limit=2)
    assert "| x\\|y |" in text and "..." in text and "(1 more rows)" in text
    assert text.startswith("Correct output rows of T (3 examples).")


def test_examples_enter_the_prompt_context_within_budget():
    block = examples.as_context([{"CONTENT_JSON": '{"target_table": "T", "columns": ["A"], "rows": [["1"], ["2"]]}'},
                                 {"CONTENT_JSON": {"target_table": "T"}}])
    assert len(block) == 1 and "| 1 |" in block[0]
    text = compose_domain_context("", None, "T", ["T: def"], 300, ["x" * 1000])
    assert "CORRECT OUTPUT EXAMPLES" in text and len(text) <= 300


def test_example_is_a_known_type_and_its_route_is_governed():
    assert "EXAMPLE" in KNOWLEDGE_TYPES
    assert policy.privilege_for("POST", "/api/domains/d1/examples/COMPANY_CORE")[0] == "KNOWLEDGE.EDIT"
    assert policy.no_queue("POST", "/api/domains/d1/examples/COMPANY_CORE")
    assert policy.privilege_for("POST", "/api/hub/domain-models/m1/import-file")[0] == "MODEL.EDIT"


DOMAIN = {"domain": {"domain_id": "d1", "description": "Companies", "owner": "GDP DATA OFFICE"},
          "source_systems": [{"name": "SPOC"}], "targets": [{"target_table": "COMPANY_CORE"}]}


def _levels(r):
    return {lvl["id"]: {i["id"]: i["met"] for i in lvl["items"]} for lvl in r["levels"]}


def test_readiness_without_a_model_stops_at_none():
    r = readiness(DOMAIN)
    assert r["level"] == "none"
    assert _levels(r)["minimum"] == {"described": True, "sources": True, "model": False, "keys": False}
    assert r["next"]["id"] == "model" and r["next"]["href"] == "/domains/d1?tab=sources"


def test_readiness_levels_build_on_each_other():
    described = {**ENTITY, "attributes": [{**a, "description": a.get("description") or "documented"} for a in ENTITY["attributes"]]}
    full = dict(members=[{"USER_NAME": "A", "ROLE": "STEWARD"}], entities=[described],
                knowledge_types={"GLOSSARY": 3, "TRANSFORMATION_RULE": 1}, verified=6, total=10,
                example_tables=["company_core"], dq_tables=1, documents=1)
    assert readiness(DOMAIN, **full)["level"] == "best"
    # 2 of 3 live columns described (the retired one does not count) is below 80%: recommended fails, so best cannot be reached
    partial = {**full, "entities": [ENTITY]}
    r = readiness(DOMAIN, **partial)
    assert r["level"] == "minimum" and _levels(r)["recommended"]["definitions"] is False
    assert _levels(r)["best"]["examples"] is True             # met, but the level below is not
    no_keys = {**full, "entities": [{**ENTITY, "business_keys": [], "grain": ""}]}
    r = readiness(DOMAIN, **no_keys)
    assert r["level"] == "none"
    assert "COMPANY_CORE" in next(i for i in r["levels"][0]["items"] if i["id"] == "keys")["detail"]


def test_readiness_owner_can_come_from_people():
    d = {**DOMAIN, "domain": {**DOMAIN["domain"], "owner": ""}}
    assert _levels(readiness(d))["minimum"]["described"] is False
    assert _levels(readiness(d, members=[{"role": "OWNER"}]))["minimum"]["described"] is True
