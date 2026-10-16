"""Signals that used to be dropped now become knowledge the next run uses: mapping rejections, STTM Store edits,
approved profiling annotations, domain corrections and the Model Workbench naming standard."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------- mapping rejections


def test_a_rejected_proposal_becomes_an_anti_pattern_with_the_comment():
    from services.mapping.feedback import anti_pattern, pattern

    item = pattern("PHONE_NO", "CRM", None, "DIM_CUSTOMER", "REJECTED", None, None, "FAX", "this is the mobile number")
    assert "must not be mapped to DIM_CUSTOMER.FAX" in item["content"] and "mobile number" in item["content"]
    assert anti_pattern(item["content_json"]) == ("PHONE_NO", "FAX")
    approved = pattern("PHONE_NO", "CRM", "PHONE", "DIM_CUSTOMER", "APPROVED", None, None, "PHONE")
    assert anti_pattern(approved["content_json"]) is None


def _cand(name, keyword, rejected=False):
    return {"target": {"column_name": name},
            "scores": {"semantic": 0, "keyword": keyword, "datatype": 1, "statistical": 0, "domain": 0, "context": 0,
                       "historical": 0},
            "evidence": {"historical": {"rejected_before": f"a reviewer rejected X -> {name}"} if rejected else {},
                         "domain": {}, "keyword": {"source_tokens": ["X"], "target_tokens": [name]},
                         "datatype": {"families": ["TEXT", "TEXT"]}, "semantic": {}},
            "rejected_before": rejected}


def test_a_rejected_candidate_loses_its_first_place():
    from services.mapping import scoring

    weights = {"semantic": 0.2, "keyword": 0.4, "datatype": 0.2, "statistical": 0.05, "domain": 0.05, "context": 0.05,
               "historical": 0.05}
    ranked = scoring.rank_candidates([_cand("FAX", 0.9, rejected=True), _cand("PHONE", 0.7)], weights,
                                     {"auto_suggest": 0.8, "human_review": 0.5}, 2)
    assert ranked[0]["target"]["column_name"] == "PHONE"
    assert "score halved" in scoring.explain(ranked[1])


def test_mapping_knowledge_collects_anti_patterns_and_notes():
    from services.mapping import procedures as mp

    class Row(dict):
        def as_dict(self):
            return dict(self)

    class Session:
        def sql(self, sql, params=None):
            flat = " ".join(sql.split())

            class R:
                def collect(self_inner):
                    if "FROM CORE.DOMAIN_KNOWLEDGE" in flat and "KNOWLEDGE_USAGE" not in flat:
                        return [Row(KNOWLEDGE_ID="p1", KNOWLEDGE_TYPE="MAPPING_PATTERN", LINEAGE_ID="L1",
                                    CONTENT_JSON=json.dumps({"source_column": "PHONE_NO", "target_column": None,
                                                             "target_table": "DIM_CUSTOMER", "decision": "REJECTED",
                                                             "rejected_target": "FAX", "comments": "mobile"}))]
                    return []
            return R()

    known = mp.domain_knowledge(Session(), "d1", "r1", {"TARGET_TABLE": "DIM_CUSTOMER", "TARGET_TABLE_ID": "t1"})
    assert ("PHONE_NO", "FAX") in known["avoid"]
    assert any("PHONE_NO is not FAX" in n and "mobile" in n for n in known["notes"])
    assert not known["history"]  # never counted as a positive mapping


# ---------------------------------------------------------------- STTM Store edits


def test_store_edits_teach_transformation_and_business_rules():
    from services.catalog.sttm import edit_knowledge

    entry = {"target_entity": "SILVER.SALES.CUSTOMER", "catalog_id": "c1"}
    diff = {"changed": [
        {"target_column": "EMAIL", "fields": ["transformation", "business_rule"],
         "from": {"transformation": None, "business_rule": None},
         "to": {"transformation": "LOWER(TRIM(email_addr))", "business_rule": "one address per customer"}},
        {"target_column": "NAME", "fields": ["transformation"], "from": {"transformation": "x"}, "to": {"transformation": None}}]}
    lines = [{"target_column": "EMAIL", "source_table": "CRM", "source_column": "EMAIL_ADDR"}]
    items = edit_knowledge(entry, diff, "lower-case emails", lines)
    rule = next(i for i in items if i["kind"] == "TRANSFORMATION_RULE")
    assert rule["key"] == "transform.CUSTOMER.EMAIL"  # the same key the run's transform apply writes
    assert rule["content_json"]["expression"] == "LOWER(TRIM({col}))" and rule["content_json"]["source_column"] == "EMAIL_ADDR"
    business = next(i for i in items if i["kind"] == "BUSINESS_RULE")
    assert business["content_json"] == {"target_column": "EMAIL", "target_table": "CUSTOMER",
                                        "rule": "one address per customer", "catalog_id": "c1"}
    assert len(items) == 2  # a cleared transformation teaches nothing


# ---------------------------------------------------------------- profiling annotations


def test_an_approved_annotation_becomes_the_column_rule_profiling_reads():
    from services.catalog.profile_ops import column_rule

    rule = column_rule({"table_fqn": "RAW.CRM.CUSTOMER", "column_name": "mail", "pii_classification": "EMAIL",
                        "semantic_type": "EMAIL", "description": "Primary address", "annotation_id": "a1"}, "s1")
    assert rule["key"] == "COLUMN.S1.CUSTOMER.MAIL"  # the key an accepted AI profiling suggestion uses
    c = rule["content_json"]
    assert (c["source_system_id"], c["source_table"], c["column_name"]) == ("s1", "CUSTOMER", "MAIL")
    assert c["pii_classification"] == "EMAIL" and c["description"] == "Primary address"
    assert column_rule({"table_fqn": "RAW.CRM.CUSTOMER", "column_name": "MAIL", "description": "only text"}, "s1") is None


# ---------------------------------------------------------------- domain corrections


def test_a_domain_confirmed_before_for_the_source_moves_up():
    from services.knowledge.procedures import CONFIRMED_BOOST, apply_confirmations

    ranked = [{"domain_id": "a", "domain_name": "COMPANY", "confidence": 0.5, "evidence": {"signals": ["x"]}},
              {"domain_id": "b", "domain_name": "PROPERTY", "confidence": 0.35, "evidence": {"signals": []}}]
    out = apply_confirmations(ranked, {"b": 2})
    assert out[0]["domain_id"] == "b" and out[0]["confidence"] == round(0.35 + CONFIRMED_BOOST, 4)
    assert out[0]["evidence"]["confirmed_before"] == 2 and "2 earlier run" in out[0]["evidence"]["signals"][0]
    assert apply_confirmations([{"domain_id": "a", "domain_name": "A", "confidence": 0.9, "evidence": {}}],
                               {"a": 1})[0]["confidence"] == 1.0
    assert apply_confirmations(list(ranked), {"zzz": 3})[0]["domain_id"] in ("a", "b")  # never adds a domain


# ---------------------------------------------------------------- naming standard


def test_the_workbench_naming_standard_is_remembered(monkeypatch):
    from services.knowledge import writer
    from services.modeling import domain_model as dm

    seen = {}
    monkeypatch.setattr(writer, "remember", lambda session, **kw: seen.update(kw))
    dm._remember_standard(None, {"domain_id": "d1", "domain_name": "SALES", "model_id": "m1"},
                          {"preset": "CUSTOM", "audit_columns": ["LOAD_TS"], "surrogate_key": "none"})
    assert seen["kind"] == "NAMING_STANDARD" and seen["key"] == "model.naming_standard.SALES"
    assert "LOAD_TS" in seen["content"]
