import json
from pathlib import Path

from services.mapping.features import propose_transformation, rule_expression

ROOT = Path(__file__).resolve().parents[2]


def test_contract_casts_apply_and_rules_without_sql_are_ignored():
    knowledge = {"rules": {}, "transforms": [
        {"target_column": "LATITUDE", "transformation": "o.col::number(12,8)", "target_type": "NUMBER(12,8)"},
        {"target_column": "NOTES"},
    ]}
    assert propose_transformation({"column_name": "LAT", "data_type": "TEXT"},
                                  {"column_name": "LATITUDE", "data_type": "NUMBER(12,8)"}, knowledge) \
        == ("LAT::number(12,8)", "transformation rule")
    assert propose_transformation({"column_name": "N", "data_type": "TEXT"},
                                  {"column_name": "NOTES", "data_type": "TEXT"}, knowledge) == (None, None)
    assert rule_expression({"expression": "TRIM({col})"}) == "TRIM({col})"
    assert rule_expression({"transformation": "CURRENT_TIMESTAMP()"}) is None


def test_domain_pack_transformation_rules_are_usable_by_mapping():
    for pack in sorted(ROOT.glob("domain/*/domain_pack.json")):
        for item in json.loads(pack.read_text(encoding="utf-8"))["knowledge"]:
            if item["type"] != "TRANSFORMATION_RULE":
                continue
            content = item.get("content_json") or {}
            expr = rule_expression(content)
            if content.get("transformation"):  # contract casts: must apply to whichever source column is mapped
                assert expr and "{col}" in expr, (pack.parent.name, item["key"])
            if expr:
                assert expr.format(col="x"), (pack.parent.name, item["key"])
