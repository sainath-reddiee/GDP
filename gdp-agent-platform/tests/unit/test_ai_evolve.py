"""Unit tests for human-in-the-loop AI evolution of domain models and domain knowledge."""

from __future__ import annotations

from services.modeling import spec_v2
from services.modeling.ai_evolve import _merge_ai_entities, evolve_domain_model


def _base_spec():
    return spec_v2.normalize_v2(
        {
            "domain": "PROPERTY",
            "description": "Commercial property domain",
            "design": {
                "entities": [
                    {
                        "id": "ent-prop-1",
                        "entity_name": "PROPERTY_CORE",
                        "table_type": "DIMENSION",
                        "scd_type": 1,
                        "grain": "One row per property",
                        "business_keys": ["PROPERTY_ID"],
                        "attributes": [
                            {
                                "id": "col-prop-id",
                                "name": "PROPERTY_ID",
                                "datatype": "VARCHAR",
                                "nullable": False,
                                "is_pk": True,
                                "description": "Primary property identifier",
                            },
                            {
                                "id": "col-owner-email",
                                "name": "OWNER_EMAIL",
                                "datatype": "VARCHAR",
                                "nullable": True,
                                "is_pk": False,
                                "description": "Owner contact email",
                            },
                        ],
                    }
                ]
            },
            "relationships": [],
        },
        "model-prop-1",
    )


def test_evolve_domain_model_fallback_preserves_ids_and_adds_entities_columns_and_knowledge():
    base = _base_spec()
    res = evolve_domain_model(
        None,
        base,
        "Add fact table LEASE_AGREEMENT with columns LEASE_START_DATE, LEASE_END_DATE, MONTHLY_RENT_AMOUNT. "
        "Add column PROPERTY_CLASS to PROPERTY_CORE. Mark OWNER_EMAIL as PII. "
        "Add audit columns. Business rule: LEASE_END_DATE must be >= LEASE_START_DATE.",
        "model-prop-1",
        [],
    )
    evolved = res["spec"]
    entities = {e["entity_name"]: e for e in evolved["design"]["entities"]}
    assert "PROPERTY_CORE" in entities
    assert "LEASE_AGREEMENT" in entities

    # Existing entity and column UUIDs are preserved so model_diff sees additive changes instead of drop+add
    prop = entities["PROPERTY_CORE"]
    assert prop["id"] == "ent-prop-1"
    cols = {a["name"]: a for a in prop["attributes"]}
    assert cols["PROPERTY_ID"]["id"] == "col-prop-id"
    assert cols["OWNER_EMAIL"]["id"] == "col-owner-email"
    assert cols["OWNER_EMAIL"]["pii"]["flag"] is True
    assert "PROPERTY_CLASS" in cols
    assert "EFFECTIVE_START_TS" in cols

    # Diff classification is additive, not breaking
    assert res["breaking"] is False
    assert res["counts"]["ADDITIVE"] > 0
    assert len(res["knowledge_proposals"]) >= 1


def test_merge_ai_entities_preserves_existing_ids_and_omitted_columns():
    base = _base_spec()
    ai_answer = {
        "rationale": "Added LEASE_AGREEMENT and linked to PROPERTY_CORE.",
        "entities": [
            {
                "entity_name": "PROPERTY_CORE",
                "table_type": "DIMENSION",
                "attributes": [
                    {"name": "PROPERTY_ID", "datatype": "VARCHAR", "nullable": False, "is_pk": True},
                    {"name": "SQUARE_FEET", "datatype": "NUMBER(18,2)", "nullable": True, "is_pk": False},
                ],
            },
            {
                "entity_name": "LEASE_AGREEMENT",
                "table_type": "FACT",
                "grain": "One row per lease",
                "business_keys": ["LEASE_ID"],
                "attributes": [
                    {"name": "LEASE_ID", "datatype": "VARCHAR", "nullable": False, "is_pk": True},
                    {"name": "PROPERTY_ID", "datatype": "VARCHAR", "nullable": False, "is_pk": False},
                ],
            },
        ],
        "relationships": [
            {
                "from_entity": "LEASE_AGREEMENT",
                "from_column": "PROPERTY_ID",
                "to_entity": "PROPERTY_CORE",
                "to_column": "PROPERTY_ID",
                "cardinality": "N:1",
            }
        ],
    }
    merged = _merge_ai_entities(base, ai_answer, "model-prop-1")
    by_name = {e["entity_name"]: e for e in merged["design"]["entities"]}
    assert by_name["PROPERTY_CORE"]["id"] == "ent-prop-1"
    col_names = {a["name"]: a for a in by_name["PROPERTY_CORE"]["attributes"]}
    assert col_names["PROPERTY_ID"]["id"] == "col-prop-id"
    # Omitted OWNER_EMAIL is kept intact
    assert col_names["OWNER_EMAIL"]["id"] == "col-owner-email"
    assert "SQUARE_FEET" in col_names
    assert len(merged["relationships"]) == 1
