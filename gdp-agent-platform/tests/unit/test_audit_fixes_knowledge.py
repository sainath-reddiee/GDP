"""Regression tests for knowledge, modeling and suggestion fixes found in the codebase audit."""

import pytest

from services.common.suggestion_stages import _target_scope
from services.knowledge import domain_ai
from services.knowledge.domain import _matches
from services.knowledge.packs import domain_id, draft_from_answer, import_pack
from services.knowledge.skill_builder import unsafe_sql
from services.knowledge.usage import assert_safe_transformation
from services.modeling import design


def test_keywords_inside_string_literals_are_data_not_statements():
    assert_safe_transformation("DECODE(col, 'I', 'INSERT', 'U', 'UPDATE', 'D', 'DELETE')")
    assert_safe_transformation("CASE WHEN op = 'it''s DROP' THEN 1 END")
    for bad in ("DELETE FROM t", "col; DROP TABLE x", "DECODE(col, 'D', 'DELETE'); DELETE FROM t",
                "'unterminated DELETE FROM t"):
        with pytest.raises(ValueError, match="MAPPING_VALIDATION"):
            assert_safe_transformation(bad)


def test_counter_example_comment_covers_only_the_next_statement():
    block = "```sql\n-- never do this\nDELETE FROM t;\nDELETE FROM u;\n```"
    assert unsafe_sql(block) == ["DELETE FROM u;"]
    spaced = "```sql\n-- do not run\nUPDATE t SET a = 1\n\nTRUNCATE TABLE t\n```"
    assert unsafe_sql(spaced) == ["TRUNCATE TABLE t"]
    multi = "```sql\n-- invalid\nDELETE FROM t\nWHERE id IN (SELECT id FROM s)\n```"
    assert unsafe_sql(multi) == []


def test_multi_word_keywords_match_underscored_identifiers():
    assert _matches("PURCHASE ORDER", "PURCHASE_ORDER_HDR")
    assert _matches("purchase-order", "PURCHASE_ORDER_HDR")
    assert not _matches("PURCHASE ORDER", "PURCHASE_REQUEST")
    assert _matches("__C", "STAGE__C")


def test_run_without_target_keeps_its_own_scope():
    assert _target_scope({}, "r1") == "run.r1"
    assert _target_scope({"TARGET_TABLE": None}, "r2") == "run.r2"
    assert _target_scope({"TARGET_DATABASE": "D", "TARGET_SCHEMA": "S", "TARGET_TABLE": "T"}, "r1") == "target.D.S.T"


@pytest.mark.parametrize("config,warned", [({}, False), ({"origin": "imported"}, False),
                                           ({"origin": "repository"}, True)])
def test_only_repository_packs_warn_about_deploy_reset(monkeypatch, config, warned):
    monkeypatch.setattr(domain_ai, "apply_item", lambda s, d, i: "signal X added")
    monkeypatch.setattr(domain_ai, "record_decision", lambda *a, **k: {"item_key": "k", "decision": "ACCEPTED"})
    monkeypatch.setattr(domain_ai, "_domain", lambda s, d: {"DOMAIN_ID": d, "CONFIG": config})
    out = domain_ai.decide(None, "d1", '{"decision": "ACCEPTED", "item": {}}')
    assert ("repository pack" in out["applied"]) is warned


def _pack():
    return draft_from_answer({"name": "claims", "targets": [{"table": "CLAIM_FACT", "columns": [
        {"name": "CLAIM_ID", "type": "VARCHAR"}, {"name": "LOSS_DATE", "type": "DATE"}]}]})


def test_import_reuses_a_domain_created_in_the_ui_with_another_id():
    issued = []
    result = import_pack(lambda sql, params=None: issued.append((sql, params)), "DB", _pack(),
                         query=lambda sql, params: [{"domain_id": "ui-random-id"}])
    assert result["domain_id"] == "ui-random-id"
    domain_merge = next(p for sql, p in issued if "MERGE INTO DB.KNOWLEDGE.DOMAIN_REGISTRY" in sql)
    assert domain_merge[0] == "ui-random-id"
    fresh = import_pack(lambda sql, params=None: None, "DB", _pack(), query=lambda sql, params: [])
    assert fresh["domain_id"] == domain_id("CLAIMS")


def test_reimport_drops_columns_the_pack_no_longer_defines():
    issued = []
    import_pack(lambda sql, params=None: issued.append((sql, params)), "DB", _pack())
    deletes = [(sql, p) for sql, p in issued if sql.lstrip().startswith("DELETE")]
    assert len(deletes) == 1 and "TARGET_COLUMN_REGISTRY" in deletes[0][0]
    assert list(deletes[0][1][1:]) == ["CLAIM_ID", "LOSS_DATE"]
    empty = draft_from_answer({"name": "claims", "targets": [{"table": "CLAIM_FACT", "columns": []}]})
    issued.clear()
    import_pack(lambda sql, params=None: issued.append((sql, params)), "DB", empty)
    assert not [sql for sql, _ in issued if sql.lstrip().startswith("DELETE")]  # no columns: left alone


class _Session:
    def __init__(self):
        self.statements = []

    def sql(self, sql, params=None):
        self.statements.append((sql, params))
        return self

    def collect(self):
        return []


def test_design_columns_merge_by_name_and_delete_only_removed_ones():
    session = _Session()
    design._set_columns(session, "t1", [["new-id", "t1", "Order_Id", "NUMBER", 1, False, "", "", True],
                                        ["new-id2", "t1", "AMOUNT", "NUMBER", 2, True, "x", "", False]])
    (delete, dparams), (merge, mparams) = session.statements
    assert delete.lstrip().startswith("DELETE") and "NOT IN" in delete and dparams == ["t1", "ORDER_ID", "AMOUNT"]
    assert "MERGE INTO KNOWLEDGE.TARGET_COLUMN_REGISTRY" in merge
    assert "UPPER(t.COLUMN_NAME) = UPPER(s.COLUMN_NAME)" in merge and "TARGET_COLUMN_ID =" not in merge.split("WHEN NOT")[0]
    assert len(mparams) == 18 and mparams[:3] == ["new-id", "t1", "Order_Id"]
