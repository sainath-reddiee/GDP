"""Command Center overview: every block from its own catalog, domain scoping, and blocks that fail soft."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402
import app.governance as gov  # noqa: E402
import app.hub_overview_api as overview  # noqa: E402

GB = 1024 ** 3


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
    "FROM KNOWLEDGE.DOMAIN_REGISTRY": [{"domain_id": "d-1", "domain_name": "COMPANY"}],
    # landed tables: two copies (one measurable in the landing schema) and one profiled in place
    "INFORMATION_SCHEMA.TABLES": [{"table_schema": "LANDING", "table_name": "SRC__A", "bytes": 2 * GB}],
    "FROM SOURCE.LANDING_TABLE_REGISTRY": [
        {"day": "2026-10-09", "landing_database": "AIP", "landing_schema": "LANDING", "landing_table": "SRC__A",
         "ingestion_method": "CTAS", "row_count": 100, "source_bytes": 5},
        {"day": "2026-10-10", "landing_database": "AIP", "landing_schema": "LANDING", "landing_table": "SRC__B",
         "ingestion_method": "COPY", "row_count": 50, "source_bytes": GB // 2},
        {"day": "2026-10-10", "landing_database": "RAW", "landing_schema": "CRM", "landing_table": "C",
         "ingestion_method": "IN_PLACE", "row_count": 7, "source_bytes": 99 * GB}],
    "FROM SOURCE.SOURCE_REGISTRY": [{"source_type": "SNOWFLAKE_SHARE", "n": 2}, {"source_type": "DATABASE", "n": 1},
                                    {"source_type": "SNOWFLAKE_DATABASE", "n": 1}, {"source_type": "FILE", "n": 1}],
    "FROM PLATFORM_CONFIG.INGESTION_PIPELINE_CATALOG": [{"n": 3, "active": 2}],
    "FROM METADATA.TABLE_PROFILES": [{"tables": 30, "columns": 400, "pii": 12}],
    "FROM GOVERNANCE.DATA_PROFILES WHERE TRUE": [{"tables": 4, "columns": 50, "high_cardinality": 6, "pii": 3, "drift": 2,
                                                  "drift_tables": 1, "healthy": 45}],
    "FROM GOVERNANCE.STTM_CATALOG": [{"mappings": 5, "verified": 3, "pending": 2}],
    "FROM GOVERNANCE.MODEL_DESIGNS": [{"designs": 1, "published": 1, "branches": 1, "in_design": 0, "failed": 0}],
    "FROM MODELING.MODEL_DESIGN M": [{"designs": 6, "in_design": 2}],
    "FROM CODEGEN.GIT_PUBLICATION": [{"published": 4, "branches": 3, "failed": 1}],
    "FROM CONTRACT.SODA_EXPECTATION_REGISTRY": [{"checks": 40, "tables": 5, "passed": 30, "warned": 2, "failed": 3,
                                                 "evaluated": 35}],
    "FROM GOVERNANCE.DQ_CHECKS_CATALOG": [{"checks": 10, "tables": 2, "passed": 5, "warned": 0, "failed": 1,
                                          "never_run": 4, "scheduled": 1}],
    "FROM CONTRACT.QA_TEST_CASE": [{"tests": 25}],
    "FROM QUALITY.QA_RUN": [{"tests": 50, "passed": 45, "failed": 4, "errors": 1, "suites": 6}],
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
    ing = out["ingested"]
    # the measured landed table counts its own bytes, the other falls back to its source size; in place is apart
    assert ing["bytes"] == 2 * GB + GB // 2 and ing["tables"] == 2 and ing["in_place"] == 1 and ing["measured"] == "mixed"
    assert [d["tables"] for d in ing["daily"]] == [1, 1]
    assert out["sources"] == {"total": 8, "shares": 2, "databases": 2, "other": 1, "external": 3, "external_active": 2}
    prof = out["profiling"]
    assert prof["tables"] == 34 and prof["columns"] == 450 and prof["pii"] == 15 and prof["catalog_tables"] == 4
    assert prof["health"] == 90.0 and prof["drift"] == 2
    assert out["sttm"] == {"mappings": 5, "verified": 3, "pending": 2}
    m = out["models"]
    assert m["designs"] == 7 and m["published"] == 5 and m["branches"] == 4 and m["failed"] == 1 and m["run_designs"] == 6
    q = out["qa"]
    assert q["rules"] == 4 and q["run_tests"] == 25 and q["executed_tests"] == 50 and q["pass_rate"] == 90.0
    d = out["dq"]
    assert d["checks"] == 50 and d["run_checks"] == 40 and d["store_checks"] == 10
    assert d["passed"] == 35 and d["failed"] == 4 and d["pass_rate"] == round(100 * 35 / 41, 1)
    assert d["coverage"] == round(100 * 7 / 34, 1)
    landing = [s for s, _ in holder["db"].executed if "INFORMATION_SCHEMA.TABLES" in s]
    assert landing == ["SELECT TABLE_SCHEMA, TABLE_NAME, BYTES FROM AIP.INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA IN (%s)"]
    assert not any(p for s, p in holder["db"].executed if "INFORMATION_SCHEMA" not in s), "no domain bind without a scope"


def test_overview_scopes_every_block_to_the_domain(client):
    c, holder = client
    out = c.get("/api/hub/overview?domain=company").json()
    assert out["domain"] == {"domain_id": "d-1", "domain_name": "COMPANY"}
    executed = holder["db"].executed
    assert any("R.DOMAIN_ID = %s" in s and "LANDING_TABLE_REGISTRY" in s and p == ("d-1",) for s, p in executed)
    assert any("FROM GOVERNANCE.MODEL_DESIGNS" in s and p == ("COMPANY", "COMPANY") for s, p in executed)
    assert any("FROM MODELING.MODEL_DESIGN M JOIN CORE.WORKFLOW_RUN R" in s and p == ("d-1",) for s, p in executed)
    assert any("FROM METADATA.TABLE_PROFILES T JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = T.PROFILED_IN_RUN" in s
               and p == ("d-1",) for s, p in executed)
    assert any("FROM CONTRACT.SODA_EXPECTATION_REGISTRY" in s and p == ("d-1",) for s, p in executed)
    assert any("FROM CONTRACT.QA_TEST_CASE" in s and p == ("d-1", "d-1") for s, p in executed)


def test_unreadable_landing_schema_falls_back_to_source_sizes(client):
    c, holder = client
    holder["db"] = FakeDb(ANSWERS, fail=("INFORMATION_SCHEMA.TABLES",))
    ing = c.get("/api/hub/overview").json()["ingested"]
    assert ing["bytes"] == 5 + GB // 2 and ing["measured"] == "source"


def test_quoted_identifiers():
    assert overview._quoted("AIP_DB") == "AIP_DB" and overview._quoted('my db"x') == '"my db""x"'


def test_overview_unknown_domain_and_missing_blocks(client):
    c, holder = client
    holder["db"] = FakeDb({}, fail=())
    assert c.get("/api/hub/overview?domain=nope").status_code == 404
    holder["db"] = FakeDb({**ANSWERS}, fail=("FROM GOVERNANCE.DATA_PROFILES", "FROM METADATA.TABLE_PROFILES",
                                             "FROM SOURCE.LANDING_TABLE_REGISTRY"))
    out = c.get("/api/hub/overview").json()
    assert out["profiling"] is None and out["ingested"] is None and out["sttm"]["verified"] == 3
    # the catalog missing alone still counts the persistent profile store
    holder["db"] = FakeDb({**ANSWERS}, fail=("FROM GOVERNANCE.DATA_PROFILES",))
    prof = c.get("/api/hub/overview").json()["profiling"]
    assert prof["tables"] == 30 and prof["health"] is None
