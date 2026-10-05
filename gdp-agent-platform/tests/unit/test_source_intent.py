from services.source.intent import suggest_models


def test_suggest_models_scores_overlap_and_proposes_new():
    profile = [
        {"table_name": "CRM_CUSTOMER", "column_name": "CUST_ID"},
        {"table_name": "CRM_CUSTOMER", "column_name": "EMAIL"},
        {"table_name": "CRM_CUSTOMER", "column_name": "COUNTRY"},
    ]
    targets = [
        {
            "target_table": "DIM_CUSTOMER",
            "target_database": "GDP",
            "target_schema": "SILVER",
            "domain_name": "GDP",
            "columns": ["CUST_ID", "EMAIL", "NAME"],
        },
        {
            "target_table": "DIM_PRODUCT",
            "fqn": "GDP.SILVER.DIM_PRODUCT",
            "domain_name": "GDP",
            "columns": ["PRODUCT_ID", "SKU"],
        },
    ]
    suggestions = suggest_models(profile, targets)
    existing = [s for s in suggestions if s["kind"] == "existing"]
    proposed = [s for s in suggestions if s["kind"] == "proposed"]
    assert existing[0]["target_table"] == "DIM_CUSTOMER"
    assert existing[0]["score"] > 0
    assert {"custid", "email"} & set(existing[0]["overlap_columns"])
    assert proposed[0]["target_table"] == "DIM_CUSTOMER"
