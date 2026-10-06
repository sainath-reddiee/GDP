import json
from pathlib import Path

import pytest

from services.dbt.onboard import contract_cast, generate, missing_minimum_mapping

PACK = json.loads((Path(__file__).resolve().parents[2] / "domain" / "company" / "domain_pack.json").read_text(encoding="utf-8"))
TARGETS = {t["table"]: t for t in PACK["targets"]}
SOURCE = {"name": "PS_CUSTOMER", "identifier": "PS_CUSTOMER", "database": "DEV_GDP_BRONZE_DB", "schema": "BRONZE_MTA",
          "columns": {"CUST_ID": "TEXT", "NAME1": "TEXT", "DUNS": "TEXT", "COUNTRY": "TEXT", "CUST_TYPE": "TEXT",
                      "ACCOUNT_NO": "TEXT", "ADDRESS1": "TEXT", "CITY": "TEXT", "LAT": "FLOAT", "GDP_UPDATED_TS": "TIMESTAMP_NTZ(6)",
                      "GDP_INSERTED_TS": "TIMESTAMP_NTZ(6)", "GDP_IS_ACTIVE": "BOOLEAN"}}


def line(col, dtype, src=None, transformation=None):
    return {"target_column": col, "target_datatype": dtype, "source_table": "PS_CUSTOMER" if src else None,
            "source_column": src, "mapping_type": "DIRECT" if src else ("DERIVED" if transformation else "UNMAPPED"),
            "transformation": transformation}


def inputs(table, lines):
    t = TARGETS[table]
    return {"domain": "company", "target": table, "source_key": "mta", "source_system": "MTA", "prefix": "GDP",
            "business_keys": [], "grain": t["grain"], "sources": [SOURCE],
            "target_columns": [{"column_name": c["name"], "data_type": c["type"], "nullable": c["nullable"]}
                               for c in t["columns"]],
            "lines": lines, "joins": [], "model_spec": t["model_spec"]}


def core_lines():
    return [
        line("SOURCE_UNIQUE_ID", "TEXT", None, "upper(trim(o.NAME1)) || '||' || coalesce(upper(trim(o.COUNTRY)), '')"),
        line("COMPANY_NAME", "TEXT", "NAME1"),
        line("DUNS_NUMBER", "TEXT", "DUNS"),
        line("REF_COMPANY_TYPE_SKEY", "NUMBER(38,0)", "CUST_TYPE"),
        line("REF_COUNTRY_OF_REGISTRATION_SKEY", "NUMBER(38,0)", "COUNTRY"),
    ]


def test_hub_uses_contract_lookups_and_hkey_order():
    out = generate(inputs("COMPANY_CORE", core_lines()))
    model = next(v for k, v in out["files"].items() if k.endswith("mta_company_core.sql"))
    assert "ref_company_type as (" in model and "left join ref_company_type as ct" in model
    assert model.index("left join country_mapping as cm") < model.index("left join ref_country as cntry")
    assert "ct.ref_company_type_skey as ref_company_type_skey" in model
    assert "cntry.ref_country_skey as ref_country_of_registration_skey" in model
    assert "as company_type" in model and "as country_of_registration" in model
    macros = out["files"]["macros/company_utils.sql"]
    hkey = macros[macros.index("m_company_core_hkey"):]
    assert hkey.index("'duns_number'") < hkey.index("'company_name'")
    contract = out["report"]["contract"]
    assert contract["applied"] and contract["hkey_from_contract"]
    assert set(contract["lookups"]) == {"REF_COMPANY_TYPE_SKEY", "REF_COUNTRY_OF_REGISTRATION_SKEY"}


def test_spoke_resolves_hub_fk_and_contract_casts():
    lines = [
        line("SOURCE_UNIQUE_ID", "TEXT", None, "upper(trim(o.NAME1)) || '||' || coalesce(upper(trim(o.COUNTRY)), '')"),
        line("SOURCE_ADDRESS_LINE_1", "TEXT", "ADDRESS1"),
        line("SOURCE_CITY", "TEXT", "CITY"),
        line("LATITUDE", "NUMBER(12,8)", "LAT"),
        line("COMPANY_CORE_SKEY", "NUMBER(38,0)", None),
    ]
    out = generate(inputs("COMPANY_ADDRESS", lines))
    model = next(v for k, v in out["files"].items() if k.endswith("mta_company_address.sql"))
    assert "{{ ref('company_core') }}" in model and "hub.company_core_skey as company_core_skey" in model
    assert "order by gdp_inserted_ts desc" in model
    assert "::number(12,8)" in model
    assert out["report"]["contract"]["hub_fk"] and "LATITUDE" in str(out["report"]["contract"]["casts"])


def test_minimum_mapping_is_enforced():
    with pytest.raises(ValueError, match="MINIMUM_MAPPING.*company_name"):
        generate(inputs("COMPANY_CORE", [l for l in core_lines() if l["target_column"] != "COMPANY_NAME"]))
    required = TARGETS["COMPANY_CORE"]["model_spec"]["minimum_mapping"]
    assert missing_minimum_mapping(required, ["source_unique_id", "company_name", "alias_name"]) == []
    assert missing_minimum_mapping(required, ["source_unique_id", "company_name"]) == [
        "duns_number or legal_entity_name or trade_name or alias_name"]
    assert contract_cast("try_to_number(o.col)", "account_number") == "try_to_number(o.account_number)"
