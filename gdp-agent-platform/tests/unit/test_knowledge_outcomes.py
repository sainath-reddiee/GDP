"""Knowledge outcomes (V047): what went into a prompt joined with what reviewers decided, and how that track record
moves ranking and mapping evidence."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.knowledge import outcomes, select  # noqa: E402


def use(lineage, run, stage="MAPPING", uses=1, last="2026-10-01"):
    return {"lineage_id": lineage, "knowledge_id": f"{lineage}-v1", "run_id": run, "stage": stage, "uses": uses,
            "last_used": last}


def decided(run, column, outcome, stage="MAPPING", table="CUSTOMER"):
    return {"run_id": run, "stage": stage, "table": table, "column": column, "outcome": outcome}


def test_helpfulness_starts_neutral_and_moves_with_evidence():
    assert outcomes.helpfulness(0, 0, 0) == 0.5
    assert outcomes.helpfulness(1, 0, 1) == round(2 / 3, 4)
    assert outcomes.helpfulness(10, 0, 10) > 0.9 and outcomes.helpfulness(0, 0, 10) < 0.1
    assert outcomes.helpfulness(0, 4, 4) == 0.5  # always corrected: half credit


def test_a_column_item_is_judged_on_its_own_column():
    contents = {"L1": {"target_column": "EMAIL", "target_table": "CUSTOMER"}}
    stats = outcomes.attribute([use("L1", "r1"), use("L1", "r2")], contents,
                               [decided("r1", "EMAIL", "ACCEPTED"), decided("r1", "PHONE", "REJECTED"),
                                decided("r2", "EMAIL", "CORRECTED")])
    s = stats[("L1", "MAPPING")]
    assert (s["observed"], s["accepted"], s["corrected"], s["rejected"], s["runs"]) == (2, 1, 1, 0, 2)
    assert s["helpfulness"] == outcomes.helpfulness(1, 1, 2)


def test_a_column_item_ignores_the_same_column_on_another_table():
    contents = {"L1": {"target_column": "STATUS", "target_table": "INVOICE"}}
    stats = outcomes.attribute([use("L1", "r1")], contents, [decided("r1", "STATUS", "REJECTED", table="CUSTOMER")])
    assert stats[("L1", "MAPPING")]["observed"] == 0


def test_a_general_item_counts_a_run_once():
    stats = outcomes.attribute([use("G", "r1")], {"G": {}},
                               [decided("r1", "A", "ACCEPTED"), decided("r1", "B", "ACCEPTED"),
                                decided("r1", "C", "REJECTED"), decided("r1", "D", "ACCEPTED")])
    s = stats[("G", "MAPPING")]
    assert s["observed"] == 1 and s["accepted"] == 0.75 and s["rejected"] == 0.25


def test_stages_and_undecided_runs_are_kept_apart():
    stats = outcomes.attribute([use("L1", "r1", "SODA"), use("L1", "r2", "MAPPING")],
                               {"L1": {"target_column": "EMAIL"}},
                               [decided("r1", "EMAIL", "REJECTED", "MAPPING")])
    assert stats[("L1", "SODA")]["observed"] == 0  # the mapping decision does not judge the Soda use
    assert stats[("L1", "MAPPING")]["observed"] == 0  # r2 has no decisions yet
    assert stats[("L1", "MAPPING")]["helpfulness"] == 0.5


def test_weight_needs_evidence_before_it_moves():
    assert outcomes.weight(None) == 1.0
    assert outcomes.weight({"observed": 2, "helpfulness": 0.1}) == 1.0
    assert outcomes.weight({"observed": 5, "helpfulness": 0.1}) == 0.6
    assert outcomes.weight({"observed": 5, "helpfulness": 0.9}) == 1.4


def _item(kid, title, helpfulness=None, observed=None, content=None):
    return {"KNOWLEDGE_ID": kid, "KNOWLEDGE_TYPE": "TRANSFORMATION_RULE", "TITLE": title, "CONTENT": "",
            "CONTENT_JSON": json.dumps(content or {}), "HELPFULNESS": helpfulness, "OBSERVED": observed}


def test_track_record_reorders_equally_relevant_items():
    items = [_item("overruled", "a", 0.1, 10), _item("trusted", "b", 0.9, 10), _item("new", "c")]
    assert [r["KNOWLEDGE_ID"] for r in select.rank(items, None, [], limit=3)] == ["trusted", "new", "overruled"]


def test_relevance_still_beats_track_record():
    items = [_item("trusted_elsewhere", "a", 0.95, 50), _item("mine", "b", 0.2, 10, {"target_column": "EMAIL"})]
    assert select.rank(items, None, ["EMAIL"], limit=1)[0]["KNOWLEDGE_ID"] == "mine"


def test_little_evidence_does_not_move_ranking():
    items = [_item("newest", "a"), _item("unlucky", "b", 0.2, 2)]
    assert [r["KNOWLEDGE_ID"] for r in select.rank(items, None, [], limit=2)] == ["newest", "unlucky"]


class _Broken:
    def sql(self, *a, **k):
        raise RuntimeError("KNOWLEDGE_OUTCOME does not exist")


def test_refresh_run_never_raises():
    out = outcomes.refresh_run(_Broken(), "r1", "MAPPING")
    assert out["items"] == 0 and "does not exist" in out["error"]


def test_mapping_history_is_weighted_by_track_record(monkeypatch):
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
                                    CONTENT_JSON=json.dumps({"source_column": "CUST_MAIL", "target_column": "EMAIL"})),
                                Row(KNOWLEDGE_ID="p2", KNOWLEDGE_TYPE="MAPPING_PATTERN", LINEAGE_ID="L2",
                                    CONTENT_JSON=json.dumps({"source_column": "TEL", "target_column": "PHONE"}))]
                    return []
            return R()

    monkeypatch.setattr("services.knowledge.outcomes.for_lineages",
                        lambda session, lineages, stage=None: {"L1": {"observed": 6, "helpfulness": 0.2}})
    known = mp.domain_knowledge(Session(), "d1", "r1", {"TARGET_TABLE": "CUSTOMER", "TARGET_TABLE_ID": "t1"})
    weights = {tgt: w for _, tgt, w in known["history"]}
    assert weights == {"EMAIL": 0.7, "PHONE": 1.0}  # overruled pattern counts less; unknown one fully
