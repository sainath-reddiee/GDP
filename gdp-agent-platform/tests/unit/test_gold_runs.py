"""Gold runs on the run engine: gold mapping rules (mastered keys, data source, dimension keys), the STTM in gold
terms, mapping promotion, registry semantic types, and Gold Code Generation from an approved STTM that passes the
same validation checks as Silver code."""

import jinja2
import sqlglot

from services.common.standard import run_pipeline
from services.gold import rules, run_codegen
from services.gold.propose import propose, semantic_type
from services.mapping.procedures import promote
from services.modeling import registry_sync, spec_v2
from services.validation import checks
from services.validation.procedures import gold_compile
from tests.unit.test_gold_propose import CONSUMES, HUB, MASTERED, ROLE


def _spec():
    return propose("OPPORTUNITY", [HUB, ROLE], consumes=CONSUMES, mastered=MASTERED)


def _ent(spec, name):
    return next(e for e in spec["design"]["entities"] if e["entity_name"] == name)


def _sttm_lines(out, table):
    """The STTM a reviewer would approve for one gold table: one line per gold column, from the proposal's lines."""
    entity = _ent(out["spec"], table)
    by_col = {l["gold_column"]: l for l in out["lines"] if l["gold_table"] == table}
    lines = []
    for a in entity["attributes"]:
        l = by_col.get(a["name"]) or {}
        src = (l.get("source_table") or "").split(".")
        mapped = l.get("kind") in ("COLUMN", "RESOLVE") and len(src) == 3
        lines.append({"target_column": a["name"], "target_datatype": a["datatype"], "nullable_rule": a["nullable"],
                      "source_database": src[0] if mapped else None, "source_schema": src[1] if mapped else None,
                      "source_table": src[2] if mapped else None, "source_column": l.get("source_column") if mapped else None,
                      "source_datatype": None, "mapping_type": "DIRECT" if mapped else "DERIVED", "transformation": None})
    return lines


def test_pipeline_of_a_run():
    assert run_pipeline({"PIPELINE": "GOLD"}) == "GOLD"
    assert run_pipeline({"pipeline": "gold"}) == "GOLD"
    assert run_pipeline({}) == "SILVER" and run_pipeline({"PIPELINE": None}) == "SILVER"


def test_rules_cover_what_name_matching_cannot_see():
    out = _spec()
    fact = _ent(out["spec"], "FACT_OPPORTUNITY")
    sources = [c["name"] for c in HUB["columns"]]
    r = rules.rules(fact, sources, CONSUMES, MASTERED, out["spec"]["relationships"])
    assert r["DIM_COMPANY_SKEY"]["source_column"] == "COMPANY_CORE_SKEY" and r["DIM_COMPANY_SKEY"]["kind"] == "RESOLVE_MDM"
    assert r["LANDLORD_DIM_COMPANY_SKEY"]["source_column"] == "LANDLORD_COMPANY_CORE_SKEY"
    assert r["DIM_PROPERTY_SKEY"]["source_column"] == "PROPERTY_CORE_SKEY"
    assert "COMPANY_CROSS_REFERENCE" in r["DIM_COMPANY_SKEY"]["expression"] and "-1" in r["DIM_COMPANY_SKEY"]["expression"]
    assert r["DIM_DATA_SOURCE_SKEY"]["source_column"] == "REF_GDP_SOURCE_SYSTEM_SKEY"
    assert r["DIM_OPPORTUNITY_STAGE_SKEY"] == {**r["DIM_OPPORTUNITY_STAGE_SKEY"], "source_column": "STAGE", "kind": "DIMENSION_KEY"}
    assert "OPPORTUNITY_NAME" not in r   # same-name columns are left to the scoring


def test_a_rule_needs_its_source_column():
    out = _spec()
    fact = _ent(out["spec"], "FACT_OPPORTUNITY")
    r = rules.rules(fact, ["SOURCE_UNIQUE_ID", "OPPORTUNITY_NAME"], CONSUMES, MASTERED, out["spec"]["relationships"])
    assert r == {}


def test_sttm_in_gold_terms():
    out = _spec()
    fact = _ent(out["spec"], "FACT_OPPORTUNITY")
    lines = _sttm_lines(out, "FACT_OPPORTUNITY")
    by_target = rules.rules(fact, [l["source_column"] for l in lines if l["source_column"]], CONSUMES, MASTERED,
                            out["spec"]["relationships"])
    shaped = {l["target_column"]: l for l in rules.shape_sttm(lines, fact, by_target)}
    company = shaped["DIM_COMPANY_SKEY"]
    assert company["mapping_type"] == "LOOKUP" and company["transformation"] is None and "-1" in company["lookup_logic"]
    assert shaped["FACT_OPPORTUNITY_SKEY"]["transformation"] is None and "SEQ_FACT_OPPORTUNITY_SKEY" in shaped["FACT_OPPORTUNITY_SKEY"]["business_rule"]
    assert shaped["GDP_INSERTED_BY"]["transformation"] == "CURRENT_USER()"
    assert shaped["OPPORTUNITY_NAME"]["mapping_type"] == "DIRECT"


def test_promote_puts_the_rule_first_with_full_confidence():
    t = lambda n: {"column_name": n}   # noqa: E731
    cands = [{"target": t("OPPORTUNITY_NAME"), "final_score": 0.6, "evidence": {}, "transformation": None},
             {"target": t("DIM_COMPANY_SKEY"), "final_score": 0.2, "evidence": {}, "transformation": "x"},
             {"target": t("DEAL_AMOUNT"), "final_score": 0.4, "evidence": {}, "transformation": None}]
    ranked = [cands[0], cands[2]]
    out = promote(cands, ranked, {"target_column": "DIM_COMPANY_SKEY", "kind": "RESOLVE_MDM", "expression": "via xref"}, 3)
    assert [c["target"]["column_name"] for c in out] == ["DIM_COMPANY_SKEY", "OPPORTUNITY_NAME", "DEAL_AMOUNT"]
    assert out[0]["recommendation"] == "AUTO_SUGGEST" and out[0]["final_score"] >= 0.97 and out[0]["transformation"] is None
    assert out[0]["evidence"]["gold_rule"]["kind"] == "RESOLVE_MDM" and not out[0]["ambiguous"]
    assert [c["rank"] for c in out] == [1, 2, 3]


def test_gold_columns_registered_with_system_semantic_types():
    fact = _ent(_spec()["spec"], "FACT_OPPORTUNITY")
    assert semantic_type(fact, "FACT_OPPORTUNITY_SKEY") == "SURROGATE_KEY"
    assert semantic_type(fact, "FACT_OPPORTUNITY_HKEY") == "DERIVED_KEY"
    assert semantic_type(fact, "GDP_UPDATED_BY") == "AUDIT_TIMESTAMP"
    assert semantic_type(fact, "GDP_IS_ACTIVE") is None and semantic_type(fact, "DIM_COMPANY_SKEY") is None
    fact = _ent(spec_v2.normalize_v2(_spec()["spec"], "m1"), "FACT_OPPORTUNITY")
    rows = {r[2]: r for r in registry_sync.column_rows(fact, fact["id"], [], "GOLD")}
    assert rows["FACT_OPPORTUNITY_SKEY"][7] == "SURROGATE_KEY" and rows["OPPORTUNITY_NAME"][7] == ""
    assert {r[2]: r for r in registry_sync.column_rows(fact, fact["id"], [])}["FACT_OPPORTUNITY_SKEY"][7] == ""


def _render(sql, incremental):
    class This:
        database, schema = "DEV_GDP_GOLD_OPPORTUNITY_DB", "OPPORTUNITY"

        def __str__(self):
            return "DEV_GDP_GOLD_OPPORTUNITY_DB.OPPORTUNITY.THE_MODEL"

    return jinja2.Environment(undefined=jinja2.StrictUndefined).from_string(sql).render(
        config=lambda **kw: "", ref=lambda n: f"M.{n.upper()}", source=lambda s, t: f"S.{s.upper()}.{t.upper()}",
        is_incremental=lambda: incremental, this=This())


def _build(table="FACT_OPPORTUNITY", edit=None):
    out = _spec()
    lines = _sttm_lines(out, table)
    if edit:
        lines = edit(lines)
    built = run_codegen.build(out["spec"], _ent(out["spec"], table), lines,
                              {"driving_table": "OPPORTUNITY_CORE" if table == "FACT_OPPORTUNITY" else "OPPORTUNITY_COMPANY_ROLE"},
                              mastered=MASTERED, consumes=CONSUMES, conventions={}, env="DEV",
                              soda_yaml="checks for fact_opportunity:\n  - row_count > 0\n", run_id="r1")
    return built, lines


def test_gold_run_builds_its_table_and_the_dimensions_it_references():
    built, _ = _build()
    files = built["generated"]
    assert "models/gold/opportunity/fact_opportunity.sql" in files
    assert "models/gold/opportunity/dim_opportunity_stage.sql" in files
    assert "models/gold/opportunity/fact_opportunity_company_role.sql" not in files   # another run's table
    assert not any("dim_company" in p or "dim_property" in p for p in files)          # conformed, referenced only
    assert {"dbt_project.yml", "soda/checks.yml", "mappings/sttm.json", "release/gold-generation-report.json"} <= set(files)
    assert built["report"]["hub"] == "FACT_OPPORTUNITY" and built["report"]["engine"] == run_codegen.ENGINE
    sql = files["models/gold/opportunity/fact_opportunity.sql"]
    assert "{{ source('company_mdm_source', 'company_cross_reference') }}" in sql and "nvl(" in sql
    for path, text in files.items():
        if path.startswith("models/") and path.endswith(".sql"):
            for inc in (False, True):
                sqlglot.parse_one(_render(text, inc), read="snowflake")


def test_gold_code_passes_the_same_validation_checks_as_silver():
    built, lines = _build()
    results = {r["validation_type"]: r for r in checks.run(built["generated"], lines, built["generated"]["soda/checks.yml"])}
    failing = {k: [f["message"] for f in r["findings"] if f["severity"] == "ERROR"] for k, r in results.items() if r["status"] == "FAILED"}
    assert failing == {}


def test_a_bridge_run_builds_only_the_bridge():
    built, _ = _build("FACT_OPPORTUNITY_COMPANY_ROLE")
    files = built["generated"]
    assert "models/gold/opportunity/fact_opportunity_company_role.sql" in files
    assert "models/gold/opportunity/fact_opportunity.sql" not in files


def test_a_reviewed_transformation_is_computed_on_the_silver_row():
    def edit(lines):
        for l in lines:
            if l["target_column"] == "OPPORTUNITY_NAME":
                l.update(mapping_type="TRANSFORM", transformation="UPPER(TRIM({col}))")
        return lines
    built, _ = _build(edit=edit)
    sql = built["generated"]["models/gold/opportunity/fact_opportunity.sql"]
    assert "UPPER(TRIM(OPPORTUNITY_NAME)) as tx_opportunity_name" in sql
    assert "src.tx_opportunity_name as opportunity_name" in sql
    sqlglot.parse_one(_render(sql, False), read="snowflake")


def test_a_column_from_another_silver_table_is_flagged_not_misread():
    def edit(lines):
        for l in lines:
            if l["target_column"] == "OPPORTUNITY_NAME":
                l.update(source_table="OTHER_TABLE")
        return lines
    built, _ = _build(edit=edit)
    assert any("OPPORTUNITY_NAME" in a and "driving table" in a for a in built["report"]["assumptions"])


def test_compile_without_the_project_is_a_warning_for_gold_only():
    failed = {"validation_type": "DBT_COMPILE", "status": "FAILED", "error_count": 1, "warning_count": 0,
              "findings": [{"severity": "ERROR", "message": "dbt compile failed: Model 'x' depends on a node named 'property_core' which was not found"}]}
    assert gold_compile(failed, "GOLD", {"dbt_project.yml": ""})["status"] == "SKIPPED"
    assert gold_compile(failed, "SILVER", {"dbt_project.yml": ""})["status"] == "FAILED"
    assert gold_compile(failed, "GOLD", {"dbt_project.yml": "", "models/silver/x.sql": ""})["status"] == "FAILED"
    other = {**failed, "findings": [{"severity": "ERROR", "message": "dbt compile failed: syntax error line 3"}]}
    assert gold_compile(other, "GOLD", {})["status"] == "FAILED"
