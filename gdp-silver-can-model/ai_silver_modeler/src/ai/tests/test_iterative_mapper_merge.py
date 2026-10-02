from ai.iterative_mapper import merge_mapping_states


def test_merge_preserves_prior_chunk_results():
    previous = {
        "mapped_columns": [
            {
                "target_column_name": "CUSTOMER_ID",
                "source_column_name": [
                    {
                        "source_table_name": "CRM",
                        "source_column_name": "CUST_ID",
                        "mapping_score": 75
                    }
                ],
                "final_transformation_logic": "CUST_ID",
                "work_notes": "Initial mapping"
            }
        ]
    }

    # New chunk forgets CUSTOMER_ID entirely
    new_state = {"mapped_columns": []}

    merged = merge_mapping_states(previous, new_state)
    assert merged["mapped_columns"][0]["source_column_name"][0]["source_column_name"] == "CUST_ID"


def test_merge_updates_by_target_column():
    previous = {
        "mapped_columns": [
            {
                "target_column_name": "EMAIL_ADDRESS",
                "source_column_name": [
                    {
                        "source_table_name": "BRONZE_CONTACT",
                        "source_column_name": "EMAIL",
                        "mapping_score": 60,
                        "justification": "Lower confidence"
                    }
                ],
                "final_transformation_logic": "EMAIL",
                "work_notes": "Needs format cleanup"
            }
        ]
    }

    new_state = {
        "mapped_columns": [
            {
                "target_column_name": "EMAIL_ADDRESS",
                "source_column_name": [
                    {
                        "source_table_name": "BRONZE_CONTACT",
                        "source_column_name": "EMAIL",
                        "mapping_score": 92,
                        "justification": "Exact semantic match"
                    }
                ],
                "final_transformation_logic": "LOWER(TRIM(EMAIL))",
                "work_notes": "Normalize casing"
            }
        ]
    }

    merged = merge_mapping_states(previous, new_state)
    entry = merged["mapped_columns"][0]
    assert entry["final_transformation_logic"] == "LOWER(TRIM(EMAIL))"
    assert entry["source_column_name"][0]["mapping_score"] == 92
    assert entry["source_column_name"][0]["justification"].startswith("Exact")


def test_merge_appends_new_targets():
    previous = {"mapped_columns": []}
    new_state = {
        "mapped_columns": [
            {
                "target_column_name": "ORDER_DATE",
                "source_column_name": [
                    {
                        "source_table_name": "ORDERS",
                        "source_column_name": "ORD_DT"
                    }
                ],
                "final_transformation_logic": "TO_DATE(ORD_DT, 'MM/DD/YYYY')",
                "work_notes": "Format conversion"
            }
        ]
    }

    merged = merge_mapping_states(previous, new_state)
    assert merged["mapped_columns"][0]["target_column_name"] == "ORDER_DATE"
    assert merged["mapped_columns"][0]["source_column_name"][0]["source_column_name"] == "ORD_DT"
