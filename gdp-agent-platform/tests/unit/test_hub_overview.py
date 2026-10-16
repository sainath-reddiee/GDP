"""Command Center overview: every block from its own catalog, domain scoping, and blocks that fail soft."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402
import app.governance as gov  # noqa: E402
import app.hub_overview_api as overview  # noqa: E402
from datetime import date, timedelta  # noqa: E402


# two days inside the overview's 7-day window, relative to today (fixed dates fail once the week moves on)
DAY_A, DAY_B = (date.today() - timedelta(days=3)).isoformat(), (date.today() - timedelta(days=2)).isoformat()


class FakeDb:
    user, role = "ANA", "DATA_ENGINEER"

    def __init__(self, answers=None, fail=()):
        self.answers, self.fail, self.executed = answers or {}, fail, []

    def query(self, sql, params=()):
        self.executed.append((" ".join(sql.split()), params))
        for needle in self.fail:
            if needle in sql:
                raise RuntimeError("missing")
        for needle, value in self.answers.items():
            if needle in sql:
                return value
        return []


ANSWERS = {
    # queries that contain another block's needle are matched first
    "AS RUN_7D": [{"executed": 50, "passed": 45, "failed": 5, "failed_targets": 1, "run_7d": 30}],
    "FROM CORE.DOMAIN_MODEL D": [{"models": 5, "approved": 5, "published": 2, "drafts": 1, "in_review": 1}],
    # developer stats: matched before the store blocks' broader needles
    "AS IN_FLIGHT": [{"in_flight": 4, "drafts": 2, "needs_review": 2}],
    "AS LEAD_MINUTES": [{"completed": 9, "failed": 1, "lead_minutes": 190, "active": 4, "started": 12}],
    "AS DAY, COUNT_IF(TO_STATE": [{"day": DAY_A, "completed": 4, "failed": 1}, {"day": DAY_B, "completed": 5, "failed": 0}],
    "AS OLDEST_MINUTES": [{"runs": 2, "oldest_minutes": 3000}],
    "FROM CORE.CHANGE_REQUEST": [{"n": 3, "oldest": 600}],
    "STATUS = 'IN_REVIEW'": [{"n": 1, "oldest": 4000}],
    "STATUS = 'PENDING_APPROVAL'": [{"n": 2, "oldest": 100}],
    "AS FAILED_LOADS": [{"tables": 20, "fresh": 15, "stale": 3, "oldest_hours": 400, "failed_loads": 2}],
    "AS IDLE FROM CORE.INGESTION_PIPELINE_CATALOG": [{"idle": 1}],
    "SELECT COUNT(DISTINCT NAME) AS N": [{"n": 1}],
    "AS TARGETS, COUNT_IF": [{"targets": 8, "covered": 6}],
    "FROM CORE.DOMAIN_REGISTRY": [{"domain_id": "d-1", "domain_name": "COMPANY"}],
    "FROM CORE.SOURCE_REGISTRY": [{"source_type": "SNOWFLAKE_SHARE", "n": 2}, {"source_type": "DATABASE", "n": 1},
                                    {"source_type": "SNOWFLAKE_DATABASE", "n": 1}, {"source_type": "FILE", "n": 1}],
    "FROM CORE.INGESTION_PIPELINE_CATALOG": [{"n": 3, "active": 2}],
    "FROM CORE.TABLE_PROFILES": [{"tables": 30, "columns": 400, "pii": 12}],
    "FROM CORE.DATA_PROFILES WHERE TRUE": [{"tables": 4, "columns": 50, "high_cardinality": 6, "pii": 3, "drift": 2,
                                                  "drift_tables": 1, "healthy": 45}],
    "FROM CORE.STTM_CATALOG": [{"mappings": 5, "approved": 3, "pending": 1, "drafts": 1}],
    "FROM CORE.TABLE_MODEL T": [{"tables": 7, "published": 3}],
    "FROM CORE.GIT_PR": [{"open": 2, "failed": 1}],
    "FROM CORE.MODEL_DESIGNS WHERE MODEL_ID IS NULL": [{"designs": 1}],
    "FROM CORE.MODEL_DESIGN M": [{"designs": 6, "in_design": 2}],
    "FROM CORE.GIT_PUBLICATION": [{"published": 4, "branches": 3, "failed": 1}],
    "FROM CORE.SODA_EXPECTATION_REGISTRY": [{"checks": 40, "tables": 5, "passed": 30, "warned": 2, "failed": 3,
                                                 "evaluated": 35}],
    "FROM CORE.DQ_CHECKS_CATALOG": [{"checks": 10, "tables": 2, "passed": 5, "warned": 0, "failed": 1,
                                          "never_run": 4, "scheduled": 1}],
    "FROM CORE.QA_TEST_CASE": [{"tests": 25}],
}


@pytest.fixture
def client(monkeypatch):
    import app.qa_api as qa_api

    async def open_door(request, call_next):
        return await call_next(request)

    monkeypatch.setattr(gov, "middleware", open_door)
    monkeypatch.setattr(qa_api, "_call", lambda db, fn, *a: {"packs": 2, "approved_packs": 1, "rules": 4, "pending": 1,
                                                              "top_tags": ["sla"], "pass_rate": None})
    holder = {"db": FakeDb(ANSWERS)}
    main.app.dependency_overrides[main.current_db] = lambda: holder["db"]
    yield TestClient(main.app), holder
    main.app.dependency_overrides.clear()


def test_overview_counts_runs_and_stores(client):
    c, holder = client
    out = c.get("/api/hub/overview").json()
    assert out["domain"] is None
    assert "ingested" not in out  # source size is not a useful number; the ribbon shows delivery, waits, freshness and failures
    assert out["sources"] == {"total": 8, "shares": 2, "databases": 2, "other": 1, "external": 3, "external_active": 2}
    prof = out["profiling"]
    assert prof["tables"] == 34 and prof["columns"] == 450 and prof["pii"] == 15 and prof["catalog_tables"] == 4
    assert prof["health"] == 90.0 and prof["drift"] == 2
    assert out["sttm"] == {"mappings": 5, "approved": 3, "pending": 1, "drafts": 1}
    m = out["models"]
    assert m == {"domain_models": 5, "approved": 5, "published": 2, "drafts": 1, "in_review": 1, "table_models": 7,
                 "tables_published": 3, "prs_open": 2, "failed": 1 + 1, "run_designs": 6, "legacy_designs": 1}
    q = out["qa"]
    assert q["rules"] == 4 and q["saved_tests"] == 25 and q["tests_7d"] == 30 and q["executed_tests"] == 50
    assert q["test_pass_rate"] == 90.0 and q["failed_tests"] == 5
    d = out["dq"]
    assert d["checks"] == 50 and d["run_checks"] == 40 and d["store_checks"] == 10
    assert d["passed"] == 35 and d["failed"] == 4 and d["pass_rate"] == round(100 * 35 / 41, 1)
    assert d["targets"] == 8 and d["covered_targets"] == 6 and d["coverage"] == 75.0
    assert out["runs"] == {"in_flight": 4, "drafts": 2, "needs_review": 2, "failed_7d": 1}
    dl = out["delivery"]
    assert {k: dl[k] for k in ("completed", "failed", "started", "active", "success_rate", "lead_minutes")} == {
        "completed": 9, "failed": 1, "started": 12, "active": 4, "success_rate": 90.0, "lead_minutes": 190}
    assert len(dl["daily"]) == 7 and {"day": DAY_A, "completed": 4, "failed": 1} in dl["daily"]
    assert out["waiting"] == {"runs": 2, "requests": 3, "versions": 1 + 1 + 2, "total": 9, "oldest_minutes": 4000}
    assert out["freshness"] == {"tables": 20, "fresh": 15, "aging": 2, "stale": 3, "failed_loads": 2, "idle_pipelines": 1,
                                "oldest_hours": 400}
    # QA failures are the QA block's own (5 failed tests on 1 target), so the tile and the card agree
    assert out["failing"] == {"dq_checks": 4, "qa_tests": 5, "total": 9, "tables": 1 + 1}
    assert not any(p for s, p in holder["db"].executed), "no domain bind without a scope"


def test_overview_scopes_every_block_to_the_domain(client):
    c, holder = client
    out = c.get("/api/hub/overview?domain=company").json()
    assert out["domain"] == {"domain_id": "d-1", "domain_name": "COMPANY"}
    executed = holder["db"].executed
    assert any("R.DOMAIN_ID = %s" in s and "LANDING_TABLE_REGISTRY" in s and p == ("d-1", "d-1") for s, p in executed)
    assert any("AS LEAD_MINUTES" in s and p == ("d-1",) * 3 for s, p in executed)
    assert any("AS OLDEST_MINUTES" in s and p == ("d-1",) for s, p in executed)
    assert any("FROM CORE.MODEL_DESIGNS WHERE MODEL_ID IS NULL" in s and p == ("COMPANY", "COMPANY") for s, p in executed)
    assert any("FROM CORE.DOMAIN_MODEL D" in s and "D.DOMAIN_ID = %s" in s and p == ("d-1",) for s, p in executed)
    assert any("FROM CORE.TABLE_MODEL T" in s and "T.DOMAIN_ID = %s" in s and p == ("d-1",) for s, p in executed)
    assert any("AS IN_FLIGHT" in s and p == ("d-1",) for s, p in executed)
    assert any("AS RUN_7D" in s and "(R.DOMAIN_ID = %s OR T.DOMAIN_ID = %s)" in s and p == ("d-1",) * 2 for s, p in executed)
    assert any("FROM CORE.MODEL_DESIGN M JOIN CORE.WORKFLOW_RUN R" in s and p == ("d-1",) for s, p in executed)
    assert any("FROM CORE.TABLE_PROFILES T JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = T.PROFILED_IN_RUN" in s
               and p == ("d-1",) for s, p in executed)
    assert any("FROM CORE.SODA_EXPECTATION_REGISTRY" in s and p == ("d-1",) for s, p in executed)
    assert any("FROM CORE.QA_TEST_CASE" in s and p == ("d-1", "d-1") for s, p in executed)


def test_developer_stats_fail_soft(client):
    c, holder = client
    holder["db"] = FakeDb(ANSWERS, fail=("FROM CORE.WORKFLOW_EVENT", "FROM CORE.CHANGE_REQUEST", "FROM CORE.QA_RESULT"))
    out = c.get("/api/hub/overview").json()
    assert out["delivery"] is None and out["waiting"] is None and out["failing"]["qa_tests"] == 0 and out["freshness"]["stale"] == 3
    holder["db"] = FakeDb({**ANSWERS, "AS LEAD_MINUTES": [{"completed": 0, "failed": 0, "lead_minutes": None, "active": 0, "started": 0}]})
    d = c.get("/api/hub/overview").json()["delivery"]
    assert d["success_rate"] is None and d["lead_minutes"] is None


def test_overview_unknown_domain_and_missing_blocks(client):
    c, holder = client
    holder["db"] = FakeDb({}, fail=())
    assert c.get("/api/hub/overview?domain=nope").status_code == 404
    holder["db"] = FakeDb({**ANSWERS}, fail=("FROM CORE.DATA_PROFILES", "FROM CORE.TABLE_PROFILES",
                                             "FROM CORE.LANDING_TABLE_REGISTRY"))
    out = c.get("/api/hub/overview").json()
    assert out["profiling"] is None and out["freshness"] is None and out["sttm"]["approved"] == 3
    # the catalog missing alone still counts the persistent profile store
    holder["db"] = FakeDb({**ANSWERS}, fail=("FROM CORE.DATA_PROFILES",))
    prof = c.get("/api/hub/overview").json()["profiling"]
    assert prof["tables"] == 30 and prof["health"] is None


def test_every_run_count_uses_the_run_list_population(client):
    """TEST, archived and deleted runs are left out of every run count, as the run list leaves them out by default."""
    c, holder = client
    c.get("/api/hub/overview")
    run_sql = [s for s, _ in holder["db"].executed if "AS IN_FLIGHT" in s or "AS LEAD_MINUTES" in s or "AS OLDEST_MINUTES" in s
               or "AS DAY" in s]
    assert len(run_sql) == 4
    for s in run_sql:
        assert "R.DELETED_AT IS NULL" in s and "NOT COALESCE(R.IS_ARCHIVED, FALSE)" in s and "R.ENVIRONMENT <> 'TEST'" in s
    delivery = next(s for s in run_sql if "AS LEAD_MINUTES" in s)
    # each run's latest outcome only: a run that failed and was then completed counts once, as completed
    assert "QUALIFY ROW_NUMBER() OVER (PARTITION BY E.RUN_ID ORDER BY E.CREATED_AT DESC) = 1" in delivery
    assert "'CREATED'" in delivery  # drafts are not in flight


def test_fill_days_zero_fills_the_week():
    days = overview.fill_days([{"day": "2026-10-10", "completed": 2, "failed": 1}], date(2026, 10, 12))
    assert [d["day"] for d in days] == ["2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-10",
                                        "2026-10-11", "2026-10-12"]
    assert days[4] == {"day": "2026-10-10", "completed": 2, "failed": 1} and days[0]["completed"] == 0
    # a day later than "today" (the warehouse clock ahead of the server's) ends the window
    assert overview.fill_days([{"day": "2026-10-13", "completed": 1, "failed": 0}], date(2026, 10, 12))[-1]["day"] == "2026-10-13"
    assert overview.rate(1, 0) is None and overview.rate(1, 3) == 33.3


def test_sttm_approval_follows_the_run_sttm(client):
    c, holder = client
    c.get("/api/hub/overview")
    sql = next(s for s, _ in holder["db"].executed if "FROM CORE.STTM_CATALOG" in s)
    assert "LEFT JOIN CORE.STTM_REGISTRY S ON S.STTM_ID = C.STTM_ID" in sql
    assert "COUNT_IF(STATUS = 'REVIEW') AS PENDING" in sql  # pending is REVIEW only, as its link filters
