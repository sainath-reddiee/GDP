"""Multi-target mapping, phase 2: candidates are ranked per source column and target table, a source column is proposed
for every table where it is worth a decision, decisions are kept per (source column, table), and the gate checks each
table's required columns plus the hub's minimum mapping. A run with one target table behaves as before. Snowflake is
a fake."""

import contextlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.mapping import procedures as mp  # noqa: E402
from test_model_workbench import Session  # noqa: E402


def ranked(score, recommendation):
    return [{"final_score": score, "recommendation": recommendation, "rank": 1}]


# ---------------------------------------------------------------- which tables a source column is proposed for

def test_one_table_is_always_kept():
    assert mp.tables_for_source({"t-core": ranked(0.1, "MANUAL")}, set()) == ["t-core"]


def test_a_source_feeds_every_table_where_it_is_worth_a_review():
    per_table = {"t-core": ranked(0.92, "AUTO_SUGGEST"), "t-addr": ranked(0.61, "HUMAN_REVIEW"),
                 "t-use": ranked(0.2, "MANUAL"), "t-own": []}
    assert mp.tables_for_source(per_table, set()) == ["t-core", "t-addr"]


def test_a_weak_source_still_gets_its_best_table_and_rules_keep_theirs():
    per_table = {"t-core": ranked(0.2, "MANUAL"), "t-addr": ranked(0.3, "MANUAL"), "t-use": ranked(0.1, "MANUAL")}
    assert mp.tables_for_source(per_table, set()) == ["t-addr"]
    assert mp.tables_for_source(per_table, {"t-use"}) == ["t-addr", "t-use"]
    assert mp.tables_for_source({"t-core": []}, set()) == []


# ---------------------------------------------------------------- the table a decision is for

CAND = {"TARGET_TABLE_ID": "t-addr"}
COLUMN = {"target_table_id": "t-core"}


@pytest.mark.parametrize("decision, candidate, target, given, tables, table", [
    ("APPROVED", CAND, None, None, ["t-core", "t-addr"], "t-addr"),
    ("ALTERNATIVE_TARGET", CAND, COLUMN, None, ["t-core", "t-addr"], "t-core"),
    ("REJECTED", None, None, "t-addr", ["t-core", "t-addr"], "t-addr"),
    ("REJECTED", None, None, None, ["t-core"], "t-core"),
])
def test_decision_table(decision, candidate, target, given, tables, table):
    assert mp.decision_table(decision, candidate, target, given, tables) == table


@pytest.mark.parametrize("candidate, given, tables, message", [
    (None, None, ["t-core", "t-addr"], "say which target table"),
    (None, "t-x", ["t-core", "t-addr"], "not one of the run's target tables"),
    (CAND, "t-core", ["t-core", "t-addr"], "does not match"),
])
def test_decision_table_refuses(candidate, given, tables, message):
    with pytest.raises(AssertionError, match=message):
        mp.decision_table("REJECTED", candidate, None, given, tables)


def test_the_last_decision_wins_per_source_and_table():
    prepared = [("S1", "APPROVED", None, "T1", None, None, None, "t-core"),
                ("S1", "APPROVED", None, "T2", None, None, None, "t-addr"),
                ("S1", "REJECTED", None, None, None, None, None, "t-core")]
    assert mp.last_per_source(prepared) == [prepared[2], prepared[1]]


def test_minimum_mapping_reads_the_hub_spec():
    hub = {"MODEL_SPEC": json.dumps({"minimum_mapping": ["`source_unique_id`", " `property_address` ", ""]})}
    assert mp.minimum_mapping(hub) == ["SOURCE_UNIQUE_ID", "PROPERTY_ADDRESS"]
    assert mp.minimum_mapping({}) == []


# ---------------------------------------------------------------- the gate

HUB = {"TARGET_TABLE_ID": "t-core", "TARGET_TABLE": "PROPERTY_CORE", "ROLE": "HUB", "ORDINAL": 1,
       "MODEL_SPEC": json.dumps({"minimum_mapping": ["`source_unique_id`", "`property_address`", "`property_usage_desc`"]})}
SPOKE = {"TARGET_TABLE_ID": "t-addr", "TARGET_TABLE": "PROPERTY_ADDRESS", "ROLE": "SPOKE", "ORDINAL": 2}


def column(cid, name, table, nullable=True):
    return {"TARGET_COLUMN_ID": cid, "COLUMN_NAME": name, "TARGET_TABLE": table, "TARGET_TABLE_ID": None,
            "DATA_TYPE": "VARCHAR", "NULLABLE": nullable, "SEMANTIC_TYPE": "TEXT", "IS_BUSINESS_KEY": False,
            "IS_PII": False, "ACCEPTED_VALUES": None, "BUSINESS_DEFINITION": None}


COLUMNS = {"t-core": [column("c-uid", "SOURCE_UNIQUE_ID", "PROPERTY_CORE", nullable=False),
                      column("c-name", "PROPERTY_NAME", "PROPERTY_CORE")],
           "t-addr": [column("a-uid", "SOURCE_UNIQUE_ID", "PROPERTY_ADDRESS", nullable=False),
                      column("a-addr", "PROPERTY_ADDRESS", "PROPERTY_ADDRESS")]}
PAIRS = [{"SOURCE_COLUMN_ID": "s-id", "COLUMN_NAME": "ID", "TARGET_TABLE_ID": "t-core"},
         {"SOURCE_COLUMN_ID": "s-id", "COLUMN_NAME": "ID", "TARGET_TABLE_ID": "t-addr"},
         {"SOURCE_COLUMN_ID": "s-addr", "COLUMN_NAME": "ADDRESS", "TARGET_TABLE_ID": "t-addr"}]


def status_session(decisions, tables=(HUB, SPOKE)):
    return Session({
        "SELECT DISTINCT C.SOURCE_COLUMN_ID": PAIRS,
        "FROM CORE.MAPPING_DECISION D": decisions,
        "SELECT * FROM CORE.WORKFLOW_RUN": [{"RUN_ID": "r1", "DOMAIN_ID": "d1", "TARGET_MODEL": "DB.PROPERTY.PROPERTY_CORE"}],
        "FROM CORE.RUN_TARGET R": list(tables),
        "FROM CORE.TARGET_COLUMN_REGISTRY C": lambda p: COLUMNS[p[0]],
    })


def decided(source, table, target, decision="APPROVED"):
    return {"SOURCE_COLUMN_ID": source, "DECISION": decision, "TARGET_COLUMN_ID": target, "TARGET_TABLE_ID": table}


def test_the_gate_wants_every_source_decided_for_every_table_it_was_proposed_for():
    out = mp.mapping_status(status_session([decided("s-id", "t-core", "c-uid")]), "r1")
    assert out["undecided"] == ["ID", "ADDRESS"] and out["source_columns"] == 2 and out["decided"] == 0
    by = {t["table"]: t for t in out["tables"]}
    assert by["PROPERTY_CORE"]["undecided"] == [] and by["PROPERTY_ADDRESS"]["undecided"] == ["ADDRESS", "ID"]
    # required columns are named with their table when the run has several
    assert out["missing_required_targets"] == ["PROPERTY_ADDRESS.SOURCE_UNIQUE_ID"]
    assert not out["complete"]


def test_the_hub_minimum_mapping_must_be_covered_somewhere():
    done = [decided("s-id", "t-core", "c-uid"), decided("s-id", "t-addr", "a-uid"), decided("s-addr", "t-addr", None, "REJECTED")]
    out = mp.mapping_status(status_session(done), "r1")
    assert out["undecided"] == [] and out["missing_required_targets"] == []
    # PROPERTY_ADDRESS lives in the spoke and is not mapped; PROPERTY_USAGE_DESC is in none of the run's tables
    assert out["minimum_mapping_missing"] == ["PROPERTY_ADDRESS"]
    assert out["minimum_mapping_unavailable"] == ["PROPERTY_USAGE_DESC"]
    assert not out["complete"]
    mapped = done[:2] + [decided("s-addr", "t-addr", "a-addr")]
    assert mp.mapping_status(status_session(mapped), "r1")["complete"]


def test_one_table_keeps_the_old_gate():
    single = Session({
        "SELECT DISTINCT C.SOURCE_COLUMN_ID": [{"SOURCE_COLUMN_ID": "s-id", "COLUMN_NAME": "ID", "TARGET_TABLE_ID": "t-core"}],
        "FROM CORE.MAPPING_DECISION D": [{"SOURCE_COLUMN_ID": "s-id", "DECISION": "REJECTED", "TARGET_COLUMN_ID": None,
                                          "TARGET_TABLE_ID": None}],   # written before V074
        "SELECT * FROM CORE.WORKFLOW_RUN": [{"RUN_ID": "r1", "DOMAIN_ID": "d1", "TARGET_MODEL": "DB.PROPERTY.PROPERTY_CORE"}],
        "FROM CORE.TARGET_TABLE_REGISTRY WHERE ACTIVE_FLAG AND TARGET_DATABASE = ?": [HUB],
        "FROM CORE.TARGET_COLUMN_REGISTRY C": lambda p: COLUMNS[p[0]],
    })
    out = mp.mapping_status(single, "r1")
    # bare column names, and no minimum mapping check (the hub's minimum names spoke columns)
    assert out["undecided"] == [] and out["missing_required_targets"] == ["SOURCE_UNIQUE_ID"]
    assert out["minimum_mapping_missing"] == [] and out["minimum_mapping_unavailable"] == []


# ---------------------------------------------------------------- generating candidates

def target_cols(table_id):
    return [{"target_column_id": c["TARGET_COLUMN_ID"], "column_name": c["COLUMN_NAME"], "table_name": c["TARGET_TABLE"],
             "target_table_id": table_id, "data_type": "VARCHAR", "nullable": c["NULLABLE"], "semantic_type": "TEXT",
             "is_business_key": False, "is_pii": False, "accepted_values": [], "definition": None}
            for c in COLUMNS[table_id]]


def fake_features(source, target, knowledge, cosine):
    """Same name scores 1.0 on every signal, anything else 0.1."""
    v = 1.0 if source["column_name"] == target["column_name"] else 0.1
    from services.mapping import scoring
    return {"scores": {c: v for c in scoring.COMPONENTS},
            "evidence": {"semantic": {"cosine": None}, "keyword": {"source_tokens": [], "target_tokens": []},
                         "datatype": {"families": "TEXT->TEXT"}, "domain": {"d": "domain term"},
                         "historical": {"previous_mapping": "p"}},
            "transformation": None}


class GenStage:
    state = "MAPPING_PENDING"
    moved = {}

    def __init__(self, session, run_id):
        self.run = {"RUN_ID": run_id, "DOMAIN_ID": "d1", "TARGET_MODEL": "DB.PROPERTY.PROPERTY_CORE"}

    def require(self, *states):
        pass

    def walk(self, *a, **k):
        pass

    def move(self, to_state, reason, details=None, in_transaction=None):
        GenStage.moved = {"to": to_state, "details": details}
        in_transaction("event")

    def fail(self, exc):
        raise exc

    def payload(self):
        return {}


@pytest.fixture
def generating(monkeypatch):
    from services.mapping import features, scoring

    weights = {c: 1 / len(scoring.COMPONENTS) for c in scoring.COMPONENTS}
    sources = [{"source_column_id": f"s-{n.lower()}", "column_name": n, "table_name": "PARCELS", "data_type": "VARCHAR",
                "semantic_type": "TEXT", "null_percentage": 0, "distinct_percentage": 100, "description": None}
               for n in ("SOURCE_UNIQUE_ID", "PROPERTY_ADDRESS", "PROPERTY_NAME")]
    patches = {
        "Stage": GenStage, "tool_call": lambda *a, **k: contextlib.nullcontext(type("C", (), {})()),
        "use_stage": lambda *a, **k: "", "target_tables": lambda s, r: [HUB, SPOKE], "target_columns": lambda s, t: target_cols(t),
        "source_columns": lambda s, r: sources,
        "domain_knowledge": lambda *a, **k: {"notes": [], "glossary": {}, "rules": {}, "history": [], "avoid": set()},
        "_scoring_config": lambda s, d: {"config_id": "cfg", "weights": weights,
                                         "thresholds": {"auto_suggest": 0.85, "human_review": 0.5}},
        "config_value": lambda s, k, d=None: d, "embeddings": lambda *a, **k: {}, "cosine_matrix": lambda *a, **k: {},
        "_gold_rules": lambda *a, **k: {}, "_adjudicate": lambda *a, **k: {}, "current_knowledge_version": lambda *a: 1,
        "record_cost": lambda *a, **k: None,
        "scalar": lambda s, sql, params=None: "DB" if "CURRENT_DATABASE" in sql else "PROPERTY" if "DOMAIN_NAME" in sql else None,
    }
    for name, value in patches.items():
        monkeypatch.setattr(mp, name, value)
    monkeypatch.setattr(mp, "domain_context", lambda *a, **k: "")
    monkeypatch.setattr(mp.ks, "search", lambda *a, **k: [])
    monkeypatch.setattr(features, "all_features", fake_features)
    return Session()


def test_candidates_are_ranked_per_table_and_written_with_it(generating):
    out = mp.generate_mapping_candidates(generating, "r1")
    insert = generating.find("INSERT INTO CORE.MAPPING_CANDIDATE")[0]
    assert "TARGET_TABLE_ID, IS_CURRENT" in insert[0]
    binds = 24
    params = insert[1]
    written = [(params[i + 2], params[i + 3], params[i + 13], params[i + 23]) for i in range(0, len(params), binds)]
    by_source = {}
    for source, target, rank, table in written:
        by_source.setdefault(source, set()).add(table)
    # the business key feeds the hub and the spoke; the others only the table they belong to
    assert by_source == {"s-source_unique_id": {"t-core", "t-addr"}, "s-property_address": {"t-addr"},
                         "s-property_name": {"t-core"}}
    # ranks restart in each table, and each table's first pick is that table's column
    firsts = {(s, t): target for s, target, rank, t in written if rank == "1"}
    assert firsts == {("s-source_unique_id", "t-core"): "c-uid", ("s-source_unique_id", "t-addr"): "a-uid",
                      ("s-property_address", "t-addr"): "a-addr", ("s-property_name", "t-core"): "c-name"}
    summary = out["summary"]
    assert [t["table"] for t in summary["tables"]] == ["PROPERTY_CORE", "PROPERTY_ADDRESS"]
    assert summary["unmapped_targets"] == [] and summary["candidates"] == 8 and GenStage.moved["to"] == "MAPPING_REVIEW"


def test_one_target_table_generates_as_before(generating, monkeypatch):
    monkeypatch.setattr(mp, "target_tables", lambda s, r: [HUB])
    out = mp.generate_mapping_candidates(generating, "r1")
    params = generating.find("INSERT INTO CORE.MAPPING_CANDIDATE")[0][1]
    assert {params[i + 23] for i in range(0, len(params), 24)} == {"t-core"}
    # every source column is still proposed once, for the one table, and unmapped names stay bare
    assert len({params[i + 2] for i in range(0, len(params), 24)}) == 3
    assert out["summary"]["unmapped_targets"] == [] and len(out["summary"]["tables"]) == 1


# ---------------------------------------------------------------- saving decisions

class FakeStage:
    def __init__(self, session, run_id):
        self.run = {"RUN_ID": run_id, "DOMAIN_ID": "d1", "TARGET_MODEL": "DB.PROPERTY.PROPERTY_CORE"}

    def require(self, *states):
        pass


@pytest.fixture
def saving(monkeypatch):
    monkeypatch.setattr(mp, "Stage", FakeStage)
    monkeypatch.setattr(mp, "tool_call", lambda *a, **k: contextlib.nullcontext(type("C", (), {})()))
    monkeypatch.setattr(mp, "remember", lambda *a, **k: None)
    monkeypatch.setattr(mp, "forget", lambda *a, **k: None)
    monkeypatch.setattr("services.knowledge.outcomes.refresh_run", lambda *a, **k: None)
    monkeypatch.setattr(mp, "mapping_status", lambda s, r: {"complete": False})

    def session(current=()):
        return Session({
            "FROM CORE.RUN_TARGET R": [HUB, SPOKE],
            "FROM CORE.MAPPING_CANDIDATE C LEFT JOIN": [
                {"CANDIDATE_ID": "k-core", "SOURCE_COLUMN_ID": "s-id", "TARGET_COLUMN_ID": "c-uid", "RECOMMENDATION": "AUTO_SUGGEST",
                 "TRANSFORMATION": None, "TARGET_TABLE_ID": "t-core"},
                {"CANDIDATE_ID": "k-addr", "SOURCE_COLUMN_ID": "s-id", "TARGET_COLUMN_ID": "a-uid", "RECOMMENDATION": "AUTO_SUGGEST",
                 "TRANSFORMATION": None, "TARGET_TABLE_ID": "t-addr"}],
            "FROM CORE.TARGET_COLUMN_REGISTRY C": lambda p: COLUMNS[p[0]],
            "FROM CORE.MAPPING_DECISION D LEFT JOIN": list(current),
            "FROM CORE.LANDING_COLUMN_REGISTRY L": [{"LANDING_COLUMN_ID": "s-id", "COLUMN_NAME": "ID", "SOURCE_TABLE": "PARCELS"}],
        })
    return session


def test_one_source_column_feeds_the_hub_and_a_spoke(saving):
    s = saving()
    out = mp.save_mapping_decisions(s, "r1", json.dumps([{"candidate_id": "k-core", "decision": "APPROVED"},
                                                         {"candidate_id": "k-addr", "decision": "APPROVED"}]))
    assert out["saved"] == 2
    retired = [p for _, p in s.find("UPDATE CORE.MAPPING_DECISION SET IS_CURRENT = FALSE")]
    assert retired == [["r1", "s-id", "t-core", "t-core"], ["r1", "s-id", "t-core", "t-addr"]]   # each table on its own
    insert = s.find("INSERT INTO CORE.MAPPING_DECISION")[0]
    assert "TARGET_TABLE_ID" in insert[0]
    params = insert[1]
    assert params[3:6] == ["s-id", "c-uid", "APPROVED"] and params[10] == "t-core"
    assert params[14:17] == ["s-id", "a-uid", "APPROVED"] and params[21] == "t-addr"


def test_a_target_column_is_still_claimed_once(saving):
    s = saving(current=[{"SOURCE_COLUMN_ID": "s-other", "TARGET_COLUMN_ID": "a-uid", "DECISION": "APPROVED", "VERSION": 1,
                         "TARGET_TABLE_ID": "t-addr"}])
    with pytest.raises(AssertionError, match="PROPERTY_ADDRESS.SOURCE_UNIQUE_ID is already mapped"):
        mp.save_mapping_decisions(s, "r1", json.dumps([{"candidate_id": "k-addr", "decision": "APPROVED"}]))


def test_a_rejection_names_its_table_on_a_run_with_several(saving):
    with pytest.raises(AssertionError, match="say which target table"):
        mp.save_mapping_decisions(saving(), "r1", json.dumps([{"source_column_id": "s-id", "decision": "REJECTED"}]))
    s = saving()
    mp.save_mapping_decisions(s, "r1", json.dumps([{"source_column_id": "s-id", "decision": "REJECTED",
                                                   "target_table_id": "t-addr"}]))
    assert s.find("INSERT INTO CORE.MAPPING_DECISION")[0][1][10] == "t-addr"


# ---------------------------------------------------------------- the copilot, one table at a time

def test_an_accepted_suggestion_is_decided_for_its_table():
    from services.mapping import assist

    s = {"source_column_id": "s-id", "action": "NULL", "reason": "no target", "target_table_id": "t-addr"}
    assert assist.to_decision(s) == {"decision": "REJECTED", "source_column_id": "s-id",
                                     "business_justification": "AI copilot: no target", "target_table_id": "t-addr"}
    assert "target_table_id" not in assist.to_decision({**s, "target_table_id": None})


@pytest.fixture
def copilot(monkeypatch):
    from fastapi.testclient import TestClient

    import app.governance as gov
    import app.main as main

    async def open_door(request, call_next):
        return await call_next(request)

    def cand(cid, source, target, table):
        return {"candidate_id": cid, "source_column_id": source, "source_column": source.upper(), "source_table": "PARCELS",
                "source_datatype": "VARCHAR", "target_column_id": target, "target_column": target.upper(), "rank": 1,
                "final_score": 0.9, "recommendation": "AUTO_SUGGEST", "transformation": None, "target_table_id": table}

    data = {
        "candidates": [cand("k1", "s-id", "c-uid", "t-core"), cand("k2", "s-id", "a-uid", "t-addr"),
                       cand("k3", "s-addr", "a-addr", "t-addr")],
        "decisions": [{"source_column_id": "s-id", "target_column_id": "c-uid", "decision": "APPROVED", "target_table_id": "t-core"}],
        "targets": [{"target_column_id": "c-uid", "column_name": "SOURCE_UNIQUE_ID", "target_table_id": "t-core"},
                    {"target_column_id": "a-uid", "column_name": "SOURCE_UNIQUE_ID", "target_table_id": "t-addr"},
                    {"target_column_id": "a-addr", "column_name": "PROPERTY_ADDRESS", "target_table_id": "t-addr"}],
        "target_table": {"target_table_id": "t-core", "target_table": "PROPERTY_CORE"},
        "target_tables": [{"target_table_id": "t-core", "target_table": "PROPERTY_CORE"},
                          {"target_table_id": "t-addr", "target_table": "PROPERTY_ADDRESS"}],
        "profile": {},
    }
    calls = []

    def answer(db, run_id, model, prompt):
        calls.append(prompt)
        return {"columns": [{"source_column_id": "s-id", "action": "APPROVE", "target_column": "SOURCE_UNIQUE_ID",
                             "confidence": 0.9, "reason": "key"},
                            {"source_column_id": "s-addr", "action": "APPROVE", "target_column": "PROPERTY_ADDRESS",
                             "confidence": 0.8, "reason": "address"}]}, "m"

    monkeypatch.setattr(gov, "middleware", open_door)
    monkeypatch.setattr(main, "get_mapping", lambda run_id, db: data)
    monkeypatch.setattr(main, "_mapping_assist_call", answer)
    monkeypatch.setattr(main, "_mapping_assist_knowledge", lambda *a: ([], {}))
    monkeypatch.setattr(main, "_model_for", lambda db, stage: "m")
    main.app.dependency_overrides[main.current_db] = lambda: object()
    yield TestClient(main.app), calls
    main.app.dependency_overrides.clear()


def test_the_copilot_reviews_each_table_with_its_own_columns(copilot):
    client, calls = copilot
    body = client.post("/api/runs/r1/mapping/assist", json={"instructions": ""}).json()
    # the hub row of s-id is decided, so only the spoke is reviewed: one call, with the spoke's name and columns
    assert len(calls) == 1 and "PROPERTY_ADDRESS" in calls[0]
    got = {(s["source_column_id"], s["target_table_id"]): s for s in body["suggestions"]}
    assert set(got) == {("s-id", "t-addr"), ("s-addr", "t-addr")}
    assert got[("s-id", "t-addr")]["target_column_id"] == "a-uid"           # the spoke's column of that name
    assert got[("s-id", "t-addr")]["decision"]["target_table_id"] == "t-addr"


def test_the_copilot_reviews_the_rows_asked_for(copilot):
    client, calls = copilot
    body = client.post("/api/runs/r1/mapping/assist", json={"pairs": [
        {"source_column_id": "s-id", "target_table_id": "t-core"}, {"source_column_id": "nope", "target_table_id": "t-addr"}]}).json()
    assert len(calls) == 1 and [(s["source_column_id"], s["target_table_id"]) for s in body["suggestions"]] == [("s-id", "t-core")]
