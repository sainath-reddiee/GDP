from pathlib import Path

from services.knowledge.contracts import cte_output_column, parse_contract, ref_lookup

REFS = Path(__file__).resolve().parents[2] / "snowflake" / "skills" / "gdp" / "dbt-onboard-source" / "references"


def contract(name):
    return parse_contract((REFS / f"{name}-contract.md").read_text(encoding="utf-8"))


def test_company_contract_targets_columns_and_hkeys():
    c = contract("company")
    assert (c["database"], c["schema"], c["hub"]) == ("DEV_GDP_SILVER_DB", "COMPANY", "COMPANY_CORE")
    counts = {t["table"]: len(t["columns"]) for t in c["targets"]}
    assert counts == {"COMPANY_CORE": 18, "COMPANY_ADDRESS": 55, "COMPANY_INDUSTRY": 14, "COMPANY_SEGMENT": 16,
                      "COMPANY_RELATIONSHIP": 17, "COMPANY_HIERARCHY": 20}
    core = c["targets"][0]
    assert core["hkey_columns"] == ["DUNS_NUMBER", "COMPANY_NAME", "ALIAS_NAME", "WEBSITE_URL", "IS_CBRE_ENTITY",
                                    "COMPANY_STATUS"]
    first = core["columns"][0]
    assert first == {"name": "COMPANY_CORE_SKEY", "type": "NUMBER(19,0)", "nullable": False, "notes": "Sequence default"}
    address = next(t for t in c["targets"] if t["table"] == "COMPANY_ADDRESS")
    names = [col["name"] for col in address["columns"]]
    assert "STREET_NUMBER_3" in names and "SOURCE_GEOCODE_OVERRIDE_SOURCE" in names
    assert any(x["target_column"] == "ACCOUNT_NUMBER" and "try_to_number" in x["cast"] for x in c["casts"])
    assert c["required_mapping"][1] == "`company_name`"


def test_reference_lookups_follow_join_dependencies():
    core = contract("company")["targets"][0]
    country = ref_lookup("REF_COUNTRY_OF_REGISTRATION_SKEY", core)
    assert country["expr"] == "cntry.ref_country_skey"
    assert [j["alias"] for j in country["joins"]] == ["cm", "cntry"]
    assert country["attributes"] == ["country_of_registration"]
    assert ref_lookup("REF_ADDRESS_SKEY", core) is None


def test_opportunity_lookups_match_on_cte_output_column():
    c = contract("opportunity")
    core = c["targets"][0]
    assert c["hub"] == "OPPORTUNITY_CORE" and len(core["columns"]) == 147 and len(core["hkey_columns"]) > 100
    status = ref_lookup("REF_OPPORTUNITY_STATUS_SKEY", core)
    assert status["expr"] == "st.opportunity_status_skey" and status["attributes"] == ["stage_name"]
    assert ref_lookup("REF_GLOBAL_REGION_NAME_SKEY", core)["expr"] == "gr.ref_global_region_skey"
    assert ref_lookup("REF_MARKET_NAME_SKEY", core) is None


def test_property_stub_yields_hub_without_columns():
    c = contract("property")
    assert c["hub"] == "PROPERTY_CORE" and c["targets"] == [] or all(not t["columns"] for t in c["targets"])
    assert "`property_name`" in c["required_mapping"]


def test_cte_output_column():
    assert cte_output_column("x as (\n    select ref_country_skey, country_name\n    from t\n)") == "ref_country_skey"
    assert cte_output_column("x as (select country_skey as ref_country_skey, name from t)") == "ref_country_skey"


def test_domain_context_picks_target_contract_section_and_rules():
    from services.knowledge.usage import compose_domain_context

    root = Path("snowflake/skills/gdp/dbt-onboard-source")
    content = (root / "SKILL.md").read_text(encoding="utf-8")
    for f in sorted(root.rglob("*.md")):
        if f.name != "SKILL.md":
            content += f"\n\n# {f.relative_to(root).as_posix()}\n" + f.read_text(encoding="utf-8")
    text = compose_domain_context(content, "COMPANY", "COMPANY_ADDRESS", ["COMPANY_ADDRESS (spoke): addresses"], 6000)
    assert len(text) <= 6000 and text.startswith("TARGET MODEL:")
    assert "COMPANY_ADDRESS" in text and "## Required Mapping Keys" in text and "## GDP Standard Rules" in text
    assert "OPPORTUNITY_CORE" not in text.split("SKILL RULES:")[0]
    assert "DOMAIN CONTRACT" not in compose_domain_context(content, "GDP", None, None, 6000)
