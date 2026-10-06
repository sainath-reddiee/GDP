import pytest

from services.dbt.onboard import classify, conform, generate, patch_hub, quote

ADDR_COLS = {"ADDRESS_LID": "TEXT", "CITY": "TEXT", "ZIP": "TEXT", "IS_PRIMARY_ADDRESS": "NUMBER(38,0)",
             "LATITUDE": "FLOAT", "ADDRESS_CONFIDENCE_SCORE": "NUMBER(38,0)", "GEOMETRY": "GEOGRAPHY",
             "INSTALL_DATE": "DATE"}
BLDG_COLS = {"BUILDING_LID": "TEXT", "ADDRESS_LID": "TEXT", "HEIGHT": "FLOAT"}


def line(col, dtype, src=None, table="ADDRESSES", mapping="DIRECT", transformation=None, **kw):
    return {"target_column": col, "target_datatype": dtype, "source_table": table if src else None,
            "source_column": src, "mapping_type": mapping if src or transformation else "UNMAPPED",
            "transformation": transformation, **kw}


def inputs(**over):
    base = {
        "domain": "property", "target": "ADDRESSES", "source_key": "lightbox", "source_system": "LIGHTBOX",
        "prefix": "GDP", "business_keys": [], "grain": "One row per address",
        "sources": [
            {"name": "ADDRESSES", "identifier": "LIGHTBOX__ADDRESSES", "database": "DEV_AI_PLATFORM",
             "schema": "LANDING", "columns": ADDR_COLS},
            {"name": "BUILDINGS", "identifier": "LIGHTBOX__BUILDINGS", "database": "DEV_AI_PLATFORM",
             "schema": "LANDING", "columns": BLDG_COLS},
        ],
        "target_columns": [
            {"column_name": "ADDRESS_LID", "data_type": "VARCHAR(50)"},
            {"column_name": "CITY", "data_type": "VARCHAR(100)", "is_pii": True},
            {"column_name": "ZIP", "data_type": "VARCHAR(10)", "nullable": False},
            {"column_name": "IS_PRIMARY_ADDRESS", "data_type": "NUMBER(38,0)"},
            {"column_name": "LATITUDE", "data_type": "FLOAT"},
            {"column_name": "ADDRESS_CONFIDENCE_SCORE", "data_type": "FLOAT"},
            {"column_name": "INSTALL_TS", "data_type": "TIMESTAMP_NTZ(6)"},
            {"column_name": "BUILDING_HEIGHT", "data_type": "FLOAT"},
            {"column_name": "COUNTY_SKEY", "data_type": "NUMBER"},
            {"column_name": "GEOCODE_STD", "data_type": "VARCHAR"},
            {"column_name": "GDP_IS_ACTIVE", "data_type": "BOOLEAN"},
            {"column_name": "GDP_INSERTED_TS", "data_type": "TIMESTAMP_NTZ"},
            {"column_name": "GDP_ROW_HASH", "data_type": "BINARY"},
        ],
        "lines": [
            line("ADDRESS_LID", "VARCHAR(50)", "ADDRESS_LID"),
            line("CITY", "VARCHAR(100)", "CITY"),
            line("ZIP", "VARCHAR(10)", "ZIP", nullable_rule=False),
            line("IS_PRIMARY_ADDRESS", "NUMBER(38,0)", "IS_PRIMARY_ADDRESS"),
            line("LATITUDE", "FLOAT", "LATITUDE"),
            line("ADDRESS_CONFIDENCE_SCORE", "FLOAT", "ADDRESS_CONFIDENCE_SCORE"),
            line("INSTALL_TS", "TIMESTAMP_NTZ(6)", "INSTALL_DATE"),
            line("BUILDING_HEIGHT", "FLOAT", "HEIGHT", table="BUILDINGS"),
            line("GEOCODE_STD", "VARCHAR", None, transformation="Populated by downstream geocoding standardization"),
        ],
        "joins": [{"left": "ADDRESSES", "right": "BUILDINGS", "keys": ["ADDRESS_LID"], "cardinality": "1:N"}],
    }
    base.update(over)
    return base


def test_conform_casts_only_on_mismatch():
    assert conform("x", "TEXT", "VARCHAR(10)") == ("x", None)
    assert conform("x", "NUMBER(38,0)", "NUMBER(38,0)") == ("x", None)
    assert conform("x", "NUMBER(38,0)", "FLOAT")[0] == "x::float"
    assert conform("x", "DATE", "TIMESTAMP_NTZ(6)")[0] == "x::timestamp_ntz"
    assert conform("x", "TEXT", "NUMBER(10,2)")[0] == "try_to_number(x, 10, 2)"
    assert conform("x", "NUMBER(38,0)", "NUMBER(18,6)")[0] == "x::number(18,6)"
    assert quote("CITY") == "CITY" and quote("Company Name") == '"Company Name"' and quote("Id__c") == '"Id__c"'


def test_classification_follows_rulebook():
    assert classify({"target_column": "GDP_UPDATED_TS"}, "addresses", "GDP", "REF_GDP_SOURCE_SYSTEM_SKEY") == "AUDIT"
    assert classify({"target_column": "REF_GDP_SOURCE_SYSTEM_SKEY"}, "a", "GDP", "REF_GDP_SOURCE_SYSTEM_SKEY") == "SOURCE_SYSTEM_REF"
    assert classify({"target_column": "COMPANY_CORE_SKEY"}, "a", "GDP", "R") == "HUB_FK"
    assert classify({"target_column": "COUNTY_SKEY"}, "a", "GDP", "R") == "FK_LOOKUP"
    assert classify({"target_column": "ADDRESSES_SKEY"}, "addresses", "GDP", "R") == "SEQUENCE"
    assert classify({"target_column": "X", "mapping_type": "UNMAPPED"}, "a", "GDP", "R") == "UNMAPPED"
    assert classify({"target_column": "X", "transformation": "LOV mapping"}, "a", "GDP", "R") == "LOV"
    assert classify({"target_column": "X", "source_column": "X", "mapping_type": "DIRECT"}, "a", "GDP", "R") == "PASSTHROUGH"


def test_generate_layout_and_skill_rules():
    out = generate(inputs())
    files, report = out["files"], out["report"]
    assert set(files) >= {
        "models/bronze/addresses_lightbox_source.yml",
        "models/silver/property/lightbox/lightbox_addresses.sql",
        "models/silver/property/addresses.sql",
        "models/silver/property/_addresses.yml",
        "macros/property_utils.sql", "dbt_project.yml", "release/watermark_addresses.sql",
    }
    eph = files["models/silver/property/lightbox/lightbox_addresses.sql"]
    assert "materialized = 'ephemeral'" in eph
    assert "nullif(trim(o.CITY), '') as city" in eph                       # Rule 1 text
    assert "o.LATITUDE as latitude" in eph and "nullif(trim(o.LATITUDE" not in eph
    assert "o.address_confidence_score::float as address_confidence_score" in eph   # cast only on mismatch
    assert "o.install_ts::timestamp_ntz as install_ts" in eph
    assert "o.latitude as latitude" in eph and "o.latitude::" not in eph
    assert "left join {{ source('addresses_lightbox_source', 'buildings') }} as j1" in eph
    assert "on o.ADDRESS_LID = j1.ADDRESS_LID" in eph
    assert "upper(trim(o.ADDRESS_LID)) as source_unique_id" in eph
    assert "null as county_skey /* TODO: FK lookup pending" in eph
    assert "null as geocode_std /* TODO: populated by downstream standardization step */" in eph
    assert "to_binary({{ m_addresses_hkey() }}, 'HEX') as gdp_row_hash" in eph
    assert "current_timestamp()::timestamp_ntz as gdp_inserted_ts" in eph
    # zone/DDL order follows the target registry
    assert eph.index("address_lid") < eph.index("as city") < eph.index("building_height")
    macros = files["macros/property_utils.sql"]
    assert "macro m_addresses_hkey" in macros and "macro generate_sha2_hash_key" in macros
    hkey = macros.split("macro m_addresses_hkey")[1]
    assert "'city'" in hkey and "county_skey" not in hkey and "gdp_" not in hkey
    schema = files["models/silver/property/_addresses.yml"]
    assert "pii: true" in schema and "- not_null" in schema
    assert report["source_unique_id"]["columns"] == ["ADDRESS_LID"]
    assert report["todos"] == 2 and report["counts"]["FK_LOOKUP"] == 1
    assert any("no update timestamp" in a for a in report["anomalies"])
    assert all(v == "new" for v in report["files"].values())


def test_skeleton_hub_patch_project_merge_and_macro_reuse():
    hub = ("{{ config(materialized='incremental') }}\nwith mta_source as (select * from {{ ref('mta_addresses') }}),\n"
           "unioned as (\n    select a, b from mta_source\n)\nselect * from unioned\n")
    skeleton = {
        "models/silver/property/addresses.sql": hub,
        "dbt_project.yml": "name: gdp_demo\nvars:\n  bronze_db: 'X'\nmodels:\n  gdp_demo: {}\n",
        "macros/hash.sql": "{% macro generate_sha2_hash_key(columns) %}x{% endmacro %}",
    }
    out = generate(inputs(), skeleton)
    files, report = out["files"], out["report"]
    patched = files["models/silver/property/addresses.sql"]
    assert "lightbox_source as (" in patched and "{{ ref('lightbox_addresses') }}" in patched
    assert patched.index("lightbox_source as (") < patched.index("unioned as (")
    assert "union all\n    -- LIGHTBOX" in patched and "m_is_source_active('lightbox')" in patched
    assert report["files"]["models/silver/property/addresses.sql"] == "patched"
    again, note = patch_hub(patched, "lightbox", "lightbox_addresses", ["a"], "LIGHTBOX", "u", "i")
    assert again == patched and "already" in note
    project = files["dbt_project.yml"]
    assert "bronze_schema_lightbox: 'LANDING'" in project and project.count("bronze_db") == 1
    assert "name: gdp_demo" in project
    assert "macro generate_sha2_hash_key" not in files["macros/property_utils.sql"]


def test_compound_business_keys_and_no_prefix():
    out = generate(inputs(business_keys=["ADDRESS_LID", "ZIP"], prefix=""))
    eph = out["files"]["models/silver/property/lightbox/lightbox_addresses.sql"]
    assert "upper(trim(o.ADDRESS_LID)) || '||' || coalesce(upper(trim(o.ZIP)), '') as source_unique_id" in eph
    assert "release/watermark_addresses.sql" not in out["files"]


def test_unjoined_table_columns_become_todo():
    out = generate(inputs(joins=[]))
    eph = out["files"]["models/silver/property/lightbox/lightbox_addresses.sql"]
    assert "left join" not in eph
    assert "null as building_height /* TODO: no join path to BUILDINGS */" in eph
    assert any("no join path" in a for a in out["report"]["anomalies"])


@pytest.mark.parametrize("bad", ['a"b', ""])
def test_quote_rejects_unsafe(bad):
    with pytest.raises(AssertionError):
        quote(bad)
