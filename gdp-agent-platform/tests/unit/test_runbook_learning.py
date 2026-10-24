"""Runbook learning from pulled logs: errors are matched to the GDP/Airflow runbooks, unknown errors are proposed for
review, sightings are counted once per task try, and a successful fix is counted on the error it resolved."""

import json

from services.ops import runbook_learning as rl

RUNBOOKS = [
    {"source_reference": "ops.runbook.gdp.dbt-rate-limit", "title": "GDP: dbt rate limit", "status": "ACTIVE",
     "content": "Signals: ...", "content_json": json.dumps({"signals": ["Operations rate limit exceeded", "093563"],
                                                             "meaning": "Throttled.", "action": "RERUN_ONLY",
                                                             "human_steps": ["Rerun the task."]})},
]

LOG = """[2026-10-24T02:00:01.123+0000] {taskinstance.py:441} INFO - Starting attempt 1 of 1
[2026-10-24T02:03:11.999+0000] {gdp_dbt.py:812} ERROR - dbt failed: Operations rate limit exceeded (093563) for job 8812
[2026-10-24T02:03:12.000+0000] {taskinstance.py:441} INFO - Marking task as FAILED."""

UNKNOWN = """[2026-10-24T02:00:01+0000] {gdp_utils.py:90} ERROR - ZeroDivisionError: division by zero in _ratio for 'ATLAS'"""


class Db:
    def __init__(self, runbooks=RUNBOOKS, sightings=None, updated=1):
        self.runbooks, self.sightings, self.updated = runbooks, sightings or [], updated
        self.sql = []

    def query(self, sql, params=()):
        self.sql.append((sql, params))
        if "FROM CORE.DOMAIN_KNOWLEDGE" in sql and "AIRFLOW_RUNBOOK" in sql:
            return self.runbooks
        if "FROM CORE.RUNBOOK_SIGHTING" in sql:
            return self.sightings
        return []

    def execute(self, sql, params=()):
        self.sql.append((sql, params))

    def execute_count(self, sql, params=()):
        self.sql.append((sql, params))
        return self.updated


def test_signature_ignores_what_changes_between_runs():
    a = "[2026-10-24T02:03:11+0000] {x.py:1} ERROR - Table 'BRONZE_ATLAS.T1' failed after 312 rows (000904 (42000))"
    b = "[2026-10-25T09:00:00+0000] {x.py:9} ERROR - Table 'BRONZE_PDL.T9' failed after 7 rows (000904 (42000))"
    assert rl.fingerprint(a) == rl.fingerprint(b)
    assert "000904" in rl.signature(a)          # the Snowflake error code is kept


def test_error_lines_skip_info_noise_and_keep_the_last_error_first():
    lines = rl.error_lines(LOG)
    assert len(lines) == 1 and "Operations rate limit exceeded" in lines[0]


def test_a_known_error_returns_its_solution_and_counts_a_sighting():
    db = Db(sightings=[{"runbook_key": "ops.runbook.gdp.dbt-rate-limit", "seen": 4, "fixed": 3,
                        "last_fix": "Rerun", "last_seen_at": None}])
    out = rl.learn(db, LOG, "AIRFLOW", "gdp_atlas_dev", "dbt_silver_company", "e|d|r|t|-1|1")
    assert out["proposed"] == [] and out["unmatched"] == []
    m = out["matches"][0]
    assert (m["key"], m["action"], m["human_steps"], m["seen"], m["fixed"]) == \
        ("ops.runbook.gdp.dbt-rate-limit", "RERUN_ONLY", ["Rerun the task."], 4, 3)
    merge = [s for s in db.sql if "MERGE INTO CORE.RUNBOOK_SIGHTING" in s[0]]
    assert len(merge) == 1 and merge[0][1][1] == "ops.runbook.gdp.dbt-rate-limit" and merge[0][1][2] is True
    assert "EQUAL_NULL(t.LAST_TRY_KEY" in merge[0][0]      # a panel refresh of the same try is not counted again


def test_an_unknown_error_is_proposed_for_review(monkeypatch):
    seen = {}

    def remember(session, **kw):
        seen.update(kw)
        return "k1"

    monkeypatch.setattr("services.knowledge.writer.remember", remember)
    monkeypatch.setattr("services.knowledge.procedures.ensure_domain", lambda *a, **k: "general")
    db = Db()
    out = rl.learn(db, UNKNOWN, "DATADOG", "gdp_atlas_dev", "dbt_silver_company", "incident|i1")
    assert out["proposed"] and out["proposed"][0].startswith(rl.LEARNED_PREFIX)
    assert seen["kind"] == "AIRFLOW_RUNBOOK" and seen["mode"] == "review" and seen["origin"] == "OPS"
    assert "ZeroDivisionError" in seen["content_json"]["signals"]
    assert seen["content"].startswith("Signals:") and "'ATLAS'" not in json.dumps(seen["content_json"]["signals"])
    # the proposed runbook matches the same error on another source next time
    from services.ops.pipeline import match_runbooks

    item = {"source_reference": seen["key"], "content_json": seen["content_json"]}
    assert match_runbooks([item], UNKNOWN.replace("'ATLAS'", "'PDL'"))


def test_an_error_already_proposed_is_not_proposed_again(monkeypatch):
    key = rl.LEARNED_PREFIX + rl.fingerprint(rl.error_lines(UNKNOWN)[0])[:16]
    pending = {"source_reference": key, "title": "Learned: ZeroDivisionError", "status": "PROPOSED", "content": "",
               "content_json": {"signals": ["ZeroDivisionError"]}}
    monkeypatch.setattr("services.knowledge.writer.remember", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    out = rl.learn(Db(runbooks=RUNBOOKS + [pending]), UNKNOWN, "AIRFLOW")
    assert out["proposed"] == [] and out["matches"][0]["status"] == "PROPOSED"


def test_a_successful_fix_is_counted_on_the_error():
    db = Db()
    n = rl.record_fix(db, {"exception_class": "SnowflakeError", "exception_text": "Operations rate limit exceeded (093563)"},
                      {"title": "Rerun the dbt task"})
    upd = [s for s in db.sql if "FIX_SUCCESS_COUNT = FIX_SUCCESS_COUNT + 1" in s[0]]
    assert n == 1 and upd[0][1][0] == "Rerun the dbt task"


def test_learning_never_breaks_the_caller():
    class Broken:
        def query(self, *a, **k):
            raise RuntimeError("no table")

        execute = execute_count = query

    assert rl.learn_safe(Broken(), LOG, "AIRFLOW") == {"matches": [], "unmatched": [], "proposed": []}
    assert rl.record_fix_safe(Broken(), {"exception_text": "x failed"}, {}) == 0
    assert rl.learn_safe(Broken(), "", "AIRFLOW")["matches"] == []
