from services.source.er_graph import infer_joins, isolated_tables, key_columns, mapping_edges


def _cols(*pairs):
    return [{"column_name": n, "data_type": t} for n, t in pairs]


def test_shared_key_join_with_cardinality():
    tables = {
        "CUSTOMER": _cols(("CUSTOMER_ID", "NUMBER"), ("EMAIL", "TEXT")),
        "ORDERS": _cols(("ORDER_ID", "NUMBER"), ("CUSTOMER_ID", "NUMBER"), ("AMOUNT", "NUMBER")),
    }
    joins = infer_joins(tables)
    assert len(joins) == 1
    assert joins[0]["keys"] == ["CUSTOMER_ID"]
    assert joins[0]["cardinality"] == "1:N"


def test_fk_to_plain_id_and_type_mismatch():
    tables = {
        "CUSTOMERS": _cols(("ID", "NUMBER")),
        "ORDERS": _cols(("ID", "NUMBER"), ("CUSTOMER_ID", "NUMBER"), ("REGION_CODE", "TEXT")),
        "REGION": _cols(("REGION_CODE", "NUMBER")),
    }
    joins = infer_joins(tables)
    assert [(j["left"], j["right"], j["keys"]) for j in joins] == [("CUSTOMERS", "ORDERS", ["ID=CUSTOMER_ID"])]
    assert isolated_tables(tables, joins) == ["REGION"]


def test_key_columns_and_mapping_edges():
    cols = _cols(("CUST_ID", "NUMBER"), ("EMAIL", "TEXT"))
    assert key_columns("CRM_CUST", cols) == {"CUST_ID"}
    edges = mapping_edges({"CRM_CUST": cols}, [
        {"target_table": "DIM_CUSTOMER", "columns": ["EMAIL", "NAME"], "selected": True},
        {"target_table": "FCT_SALES", "columns": ["AMOUNT"]},
    ])
    assert edges == [{"from": "CRM_CUST", "to": "DIM_CUSTOMER", "kind": "planned", "weight": 1, "columns": ["EMAIL"]}]
