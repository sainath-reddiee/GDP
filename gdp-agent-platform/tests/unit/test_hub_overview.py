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
    "FROM SOURCE.LANDING_TABLE_REGISTRY": [{"day": "2026-10-09", "tables": 2, "rows_landed": 100, "bytes": 2 * GB},
                                          {"day": "2026-10-10", "tables": 1, "rows_landed": 50, "bytes": GB // 2}],
    "FROM SOURCE.SOURCE_REGISTRY": [{"source_type": "SNOWFLAKE_SHARE", "n": 2}, {"source_type": "DATABASE", "n": 1},
                                    {"source_type": "SNOWFLAKE_DATABASE", "n": 1}, {"source_type": "FILE", "n": 1}],
    "FROM PLATFORM_CONFIG.INGESTION_PIPELINE_CATALOG": [{"n": 3, "active": 2}],
    "FROM GOVERNANCE.DATA_PROFILES WHERE TRUE": [{"tables": 4, "columns": 50, "high_cardinality": 6, "pii": 3, "drift": 2,
                                                  "drift_tables": 1, "healthy": 45}],
    "FROM GOVERNANCE.STTM_CATALOG": [{"mappings": 5, "verified": 3, "pending": 2}],
    "FROM GOVERNANCE.MODEL_DESIGNS": [{"designs": 4, "published": 2, "branches": 3, "in_design": 1, "failed": 1}],
}


@pytest.fixture
def client(monkeypatch):
    async def open_door(request, call_next):
        return await call_next(request)

    monkeypatch.setattr(gov, "middleware", open_door)
    monkeypatch.setattr(overview, "qa", lambda db, d: {"packs": 2, "approved_packs": 1, "rules": 4, "top_tags": ["sla"]})
    monkeypatch.setattr(overview, "dq", lambda db, d: {"deployed": True, "checks": 9, "pass_rate": 88.9, "coverage": 50.0})
    holder = {"db": FakeDb(ANSWERS)}
    main.app.dependency_overrides[main.current_db] = lambda: holder["db"]
    yield TestClient(main.app), holder
    main.app.dependency_overrides.clear()


def test_overview_blocks(client):
    c, holder = client
    out = c.get("/api/hub/overview").json()
    assert out["domain"] is None
    assert out["ingested"]["gb"] == 2.5 and out["ingested"]["tables"] == 3 and len(out["ingested"]["daily"]) == 2
    assert out["sources"] == {"total": 8, "shares": 2, "databases": 2, "other": 1, "external": 3, "external_active": 2}
    assert out["profiling"]["health"] == 90.0 and out["profiling"]["drift"] == 2
    assert out["sttm"] == {"mappings": 5, "verified": 3, "pending": 2}
    assert out["models"]["branches"] == 3 and out["qa"]["rules"] == 4 and out["dq"]["checks"] == 9
    assert not any(p for _, p in holder["db"].executed), "no domain bind without a scope"


def test_overview_scopes_every_block_to_the_domain(client):
    c, holder = client
    out = c.get("/api/hub/overview?domain=company").json()
    assert out["domain"] == {"domain_id": "d-1", "domain_name": "COMPANY"}
    executed = holder["db"].executed
    assert any("R.DOMAIN_ID = %s" in s and p == ("d-1",) for s, p in executed)
    assert any("FROM GOVERNANCE.MODEL_DESIGNS" in s and p == ("COMPANY", "COMPANY") for s, p in executed)
    assert any("FROM GOVERNANCE.STTM_CATALOG" in s and p == ("d-1",) for s, p in executed)


def test_overview_unknown_domain_and_missing_blocks(client):
    c, holder = client
    holder["db"] = FakeDb({}, fail=())
    assert c.get("/api/hub/overview?domain=nope").status_code == 404
    holder["db"] = FakeDb({**ANSWERS}, fail=("FROM GOVERNANCE.DATA_PROFILES", "FROM SOURCE.LANDING_TABLE_REGISTRY"))
    out = c.get("/api/hub/overview").json()
    assert out["profiling"] is None and out["ingested"] is None and out["sttm"]["verified"] == 3
