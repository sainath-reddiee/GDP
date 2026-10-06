import json

from services.dbt.inputs import assemble, load_inputs


def fake_query(sql, params):
    s = " ".join(sql.split())
    if "FROM CORE.WORKFLOW_RUN R" in s:
        return [{"RUN_NAME": "test1123", "TARGET_MODEL": "DEV_GDP_BRONZE_DB.BRONZE_LIGHTBOX.ADDRESSES", "DOMAIN_ID": "d1",
                 "DOMAIN_NAME": "GDP", "SOURCE_SYSTEM_NAME": "LIGHTBOX"}]
    if "LANDING_TABLE_REGISTRY" in s:
        return [{"SOURCE_TABLE": "ADDRESSES", "LANDING_TABLE": "LIGHTBOX__ADDRESSES", "LANDING_DATABASE": "DEV_AI_PLATFORM",
                 "LANDING_SCHEMA": "LANDING"},
                {"SOURCE_TABLE": "BUILDINGS", "LANDING_TABLE": "LIGHTBOX__BUILDINGS", "LANDING_DATABASE": "DEV_AI_PLATFORM",
                 "LANDING_SCHEMA": "LANDING"}]
    if "INFORMATION_SCHEMA.COLUMNS" in s:
        assert params[0] == "LANDING"
        if params[1] == "LIGHTBOX__ADDRESSES":
            return [{"COLUMN_NAME": "ADDRESS_LID", "DATA_TYPE": "TEXT"},
                    {"COLUMN_NAME": "ADDRESS_CONFIDENCE_SCORE", "DATA_TYPE": "NUMBER", "NUMERIC_PRECISION": 38, "NUMERIC_SCALE": 0}]
        return [{"COLUMN_NAME": "BUILDING_LID", "DATA_TYPE": "TEXT"}, {"COLUMN_NAME": "ADDRESS_LID", "DATA_TYPE": "TEXT"}]
    if "TARGET_COLUMN_REGISTRY" in s:
        assert params == ["d1", "ADDRESSES"]
        return [{"COLUMN_NAME": "ADDRESS_LID", "DATA_TYPE": "VARCHAR(50)", "NULLABLE": True, "IS_BUSINESS_KEY": False,
                 "IS_PII": False, "BUSINESS_DEFINITION": "Lightbox address id", "ACCEPTED_VALUES": "[]"},
                {"COLUMN_NAME": "ADDRESS_CONFIDENCE_SCORE", "DATA_TYPE": "FLOAT", "NULLABLE": True},
                {"COLUMN_NAME": "GDP_INSERTED_TS", "DATA_TYPE": "TIMESTAMP_NTZ"}]
    raise AssertionError(f"unexpected sql: {s[:80]}")


LINES = [
    {"target_column": "ADDRESS_LID", "target_datatype": "VARCHAR(50)", "source_table": "ADDRESSES",
     "source_column": "ADDRESS_LID", "mapping_type": "DIRECT"},
    {"target_column": "ADDRESS_CONFIDENCE_SCORE", "target_datatype": "FLOAT", "source_table": "ADDRESSES",
     "source_column": "ADDRESS_CONFIDENCE_SCORE", "mapping_type": "DIRECT"},
]


def test_load_inputs_and_assemble_end_to_end():
    sttm = {"sttm_id": "s1", "table_design": {"target_table": "ADDRESSES", "business_keys": []}}
    inputs = load_inputs(fake_query, "r1", {"prefix": "GDP"}, sttm, LINES)
    assert inputs["domain"] == "GDP" and inputs["source_key"] == "lightbox" and inputs["source_system"] == "LIGHTBOX"
    assert inputs["sources"][0]["columns"]["ADDRESS_CONFIDENCE_SCORE"] == "NUMBER(38,0)"
    assert inputs["joins"] and inputs["joins"][0]["keys"] == ["ADDRESS_LID"]
    skeleton = {"README.md": "# demo", "models/silver/gdp/addresses.sql": "with unioned as (\n select 1\n)\nselect * from unioned"}
    built = assemble(inputs, sttm, "checks: []", skeleton)
    assert "README.md" in built["files"] and "README.md" not in built["generated"]
    assert "models/silver/gdp/lightbox/lightbox_addresses.sql" in built["generated"]
    report = json.loads(built["generated"]["release/generation-report.json"])
    assert report["files"]["models/silver/gdp/addresses.sql"] == "patched"
    base = json.loads(built["generated"]["release/skeleton-base.json"])
    assert base["models/silver/gdp/addresses.sql"].startswith("with unioned")
    eph = built["generated"]["models/silver/gdp/lightbox/lightbox_addresses.sql"]
    assert "o.address_confidence_score::float" in eph


def test_blank_prefix_is_respected():
    sttm = {"sttm_id": "s1", "table_design": {"target_table": "ADDRESSES"}}
    assert load_inputs(fake_query, "r1", {"prefix": ""}, sttm, LINES)["prefix"] == ""
    assert load_inputs(fake_query, "r1", {}, sttm, LINES)["prefix"] == "GDP"
