from services.common.standard import GDP, GENERIC, conventions, fill
from services.dbt.onboard import generate
from services.validation import checks

SOURCE = {"name": "MEMBERS", "identifier": "members", "database": "SRC_DB", "schema": "RAW",
          "columns": {"MEMBERREF": "NUMBER(38,0)", "FULL NAME": "TEXT", "STATUS": "TEXT", "LOADED_AT": "TIMESTAMP_NTZ"},
          "names": {"MEMBERREF": "memberRef", "FULL NAME": "Full Name", "STATUS": "Status", "LOADED_AT": "loaded_at"}}


def line(col, dtype, src=None, transformation=None):
    return {"target_column": col, "target_datatype": dtype, "source_table": "MEMBERS" if src else None,
            "source_column": src, "mapping_type": "DIRECT" if src else ("DERIVED" if transformation else "UNMAPPED"),
            "transformation": transformation}


LINES = [line("MEMBER_ID", "NUMBER(38,0)", "memberRef"), line("FULL_NAME", "TEXT", "Full Name"),
         line("STATUS", "TEXT", "Status"), line("LOADED_AT", "TIMESTAMP_NTZ", "loaded_at"),
         line("MEMBER_DIM_SKEY", "NUMBER(38,0)")]


def generic_inputs(**extra):
    base = {"domain": None, "target": "member_dim", "source_key": "crm", "source_system": "CRM", "prefix": "",
            "standard": GENERIC, "business_keys": ["MEMBER_ID"], "grain": "one row per member",
            "sources": [SOURCE], "target_columns": [], "lines": LINES, "joins": [], "model_spec": {}}
    return {**base, **extra}


def test_generic_project_has_no_gdp_conventions():
    out = generate(generic_inputs())
    files = out["files"]
    text = "\n".join(files.values()).lower()
    assert "gdp" not in text, [p for p, c in files.items() if "gdp" in c.lower()]
    assert "models/general/member_dim.sql" in files and "models/sources/member_dim_crm_source.yml" in files
    assert "snapshots/member_dim_snapshot.sql" in files and "release/watermark_member_dim.sql" not in files
    snapshot = files["snapshots/member_dim_snapshot.sql"]
    assert "strategy = 'timestamp'" in snapshot and "updated_at = 'loaded_at'" in snapshot  # value-chosen stamp
    assert "where dbt_valid_to is null" in files["models/general/member_dim.sql"]
    staging = files["models/general/crm/crm_member_dim.sql"]
    assert "materialized = 'view'" in staging and '"Full Name"' in staging  # exact spelling, quoted
    assert "abs(hash(source_unique_id)) as member_dim_skey" in staging
    assert "snapshot-paths" in files["dbt_project.yml"] and "profile: default" in files["dbt_project.yml"]
    assert out["report"]["standard"] == GENERIC and "SCD2" in out["report"]["convention"]


def test_generic_project_passes_the_platform_validation():
    out = generate(generic_inputs())
    lines = [{**l, "nullable_rule": True} for l in LINES if l["source_column"]]
    soda = "checks for member_dim:\n  - row_count > 0\n"
    results = checks.run({**out["files"], "soda/checks.yml": soda}, lines, soda)
    failed = {r["validation_type"]: r["findings"] for r in results if r["status"] == "FAILED"}
    assert not failed, failed


def test_organisation_overrides_change_conventions_without_code():
    conv = conventions(GENERIC, {"scd_type": 1, "model_dir": "models/core/{domain}", "dbt_profile": "acme",
                                 "unknown": "ignored", "key_strategy": 5})
    assert conv["scd_type"] == 1 and conv["key_strategy"] == "hash"  # wrong type ignored
    out = generate(generic_inputs(conventions=conv))
    assert "models/core/general/member_dim.sql" in out["files"] and "profile: acme" in out["files"]["dbt_project.yml"]
    assert not any(p.startswith("snapshots/") for p in out["files"])  # SCD1 merge model instead


def test_templates():
    assert fill(conventions(GDP)["watermark_table"], P="GDP") == "DEV_GDP_UTIL_DB.CONFIG.GDP_DBT_WATERMARK_TBL"
    assert fill(conventions(GENERIC)["pr_title"], name="Orders", version=2) == "Onboard Orders (dbt v2)"
