"""A MASTERED MDM contract derived from a post-MDM schema (COMPANY_MDM-shaped), with the questions for the owner."""

import json
from pathlib import Path

from services.mdm import contract as mdm
from services.mdm.derive import derive
from services.modeling.importers import parse_ddl

ROOT = Path(__file__).resolve().parents[2]
DDL = """
CREATE TABLE DEV_GDP_SILVER_DB.COMPANY_MDM.COMPANY_CROSS_REFERENCE (
  SOURCE_UNIQUE_ID VARCHAR, SOURCE_SYSTEM_NAME VARCHAR, CBRE_ENTERPRISE_COMPANY_ID VARCHAR, MDM_ENTITY_ID VARCHAR,
  MDM_STATUS VARCHAR, MDM_DELETED_DATE TIMESTAMP_NTZ, GDP_IS_ACTIVE BOOLEAN, GDP_UPDATED_TS TIMESTAMP_NTZ);
CREATE TABLE DEV_GDP_SILVER_DB.COMPANY_MDM.COMPANY_CORE (
  CBRE_ENTERPRISE_COMPANY_ID VARCHAR, MDM_ENTITY_ID VARCHAR, COMPANY_NAME VARCHAR, MDM_POST_MDM_LUD TIMESTAMP_NTZ,
  MDM_UPDATED_TS TIMESTAMP_NTZ);
CREATE TABLE DEV_GDP_SILVER_DB.COMPANY_MDM.COMPANY_HIERARCHY (CBRE_ENTERPRISE_COMPANY_ID VARCHAR, PARENT_ID VARCHAR);
CREATE TABLE DEV_GDP_SILVER_DB.COMPANY_MDM.COMPANY_CROSS_REFERENCE_STAGE (SOURCE_UNIQUE_ID VARCHAR);
"""


def test_contract_from_a_company_mdm_schema_matches_the_repository_contract():
    spec = parse_ddl(DDL)
    out = derive("company", spec)
    c = out["contract"]
    assert mdm.validate("COMPANY", c) == []
    repo = json.loads((ROOT / "domain/company/mdm.json").read_text(encoding="utf-8"))
    for part in ("pre_mdm", "source_systems"):
        assert c[part] == repo[part], part
    for k in ("database", "schema", "table", "source_key", "system_name", "mastered_id", "entity_id", "active_column",
              "deleted_column", "status_column", "exclude_systems", "latest_order"):
        assert c["cross_reference"][k] == repo["cross_reference"][k], k
    for k in ("database", "schema", "table", "mastered_id", "entity_id", "version_order"):
        assert c["golden"][k] == repo["golden"][k], k
    assert c["golden"]["related"] == ["COMPANY_HIERARCHY"]                    # the STAGE delta is not related
    for k in ("database", "schema", "dimension", "skey", "mastered_id", "active_column", "unresolved"):
        assert c["gold"][k] == repo["gold"][k], k
    assert any("pre-MDM hub is assumed" in q for q in out["questions"])
    assert "mastered id: COMPANY_CROSS_REFERENCE.CBRE_ENTERPRISE_COMPANY_ID" in out["found"]


def test_resolution_sql_renders_from_a_derived_contract():
    c = derive("COMPANY", parse_ddl(DDL))["contract"]
    sql = mdm.resolution_sql(c, "DEV")
    assert "DEV_GDP_SILVER_DB.COMPANY_MDM.COMPANY_CROSS_REFERENCE" in sql and "nvl(d.dim_company_skey, -1)" in sql


def test_a_schema_without_a_cross_reference_asks_instead_of_guessing():
    out = derive("PROPERTY", parse_ddl("CREATE TABLE X.Y.PROPERTY_CORE (A VARCHAR);"))
    assert out["contract"] is None and "No cross-reference table" in out["questions"][0]


def test_missing_columns_become_questions_and_the_contract_does_not_validate():
    out = derive("PROPERTY", parse_ddl("CREATE TABLE PRD_X.PROPERTY_MDM.PROPERTY_CROSS_REFERENCE (REC VARCHAR, ENTERPRISE_PROPERTY_ID VARCHAR);"))
    assert out["contract"]["cross_reference"]["database"] == "{ENV}_X"
    assert any("source key" in q for q in out["questions"]) and any("golden record" in q for q in out["questions"])
    assert mdm.validate("PROPERTY", out["contract"])
