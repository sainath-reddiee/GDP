"""Share onboarding: mounting a share as its own step and listing a share's schemas with table counts."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402
import app.governance as gov  # noqa: E402
from services.governance import policy  # noqa: E402
from test_hub_api import FakeDb  # noqa: E402

SCHEMAS = [{"schema_name": "PUBLIC", "tables": 4, "row_count": 1200}, {"schema_name": "SALES", "tables": 2, "row_count": 50}]


@pytest.fixture
def client(monkeypatch):
    async def open_door(request, call_next):
        return await call_next(request)

    monkeypatch.setattr(gov, "middleware", open_door)
    holder = {"db": FakeDb()}
    main.app.dependency_overrides[main.current_db] = lambda: holder["db"]
    yield TestClient(main.app), holder
    main.app.dependency_overrides.clear()


def test_schemas_of_a_mounted_share(client):
    c, holder = client
    holder["db"] = FakeDb({"FROM INFORMATION_SCHEMA.DATABASES WHERE DATABASE_NAME": [{"type": "IMPORTED DATABASE"}],
                           "GROUP BY 1 ORDER BY 1": SCHEMAS})
    out = c.get("/api/hub/sources/share/schemas?database=partner_db").json()
    assert out["database"] == "PARTNER_DB" and out["schemas"] == SCHEMAS
    sql = next(s for s, _ in holder["db"].executed if "GROUP BY 1" in s)
    assert "\"PARTNER_DB\".INFORMATION_SCHEMA.TABLES" in sql and "TABLE_SCHEMA <> 'INFORMATION_SCHEMA'" in sql
    holder["db"] = FakeDb()
    assert c.get("/api/hub/sources/share/schemas?database=nope").status_code == 404


def test_mount_then_list_schemas(client):
    c, holder = client
    holder["db"] = FakeDb({"GROUP BY 1 ORDER BY 1": SCHEMAS})
    r = c.post("/api/hub/sources/share/mount", json={"provider_account": "ORG.ACME", "share_name": "SALES_SHARE", "database": "acme_sales"})
    assert r.status_code == 200 and r.json()["schemas"] == SCHEMAS
    assert any("CREATE DATABASE IF NOT EXISTS \"ACME_SALES\" FROM SHARE ORG.ACME.SALES_SHARE" in s for s, _ in holder["db"].executed)
    bad = c.post("/api/hub/sources/share/mount", json={"provider_account": "bad account!", "share_name": "S", "database": "D"})
    assert bad.status_code == 400
    holder["db"] = FakeDb(fail=("CREATE DATABASE",))
    denied = c.post("/api/hub/sources/share/mount", json={"provider_account": "ORG.ACME", "share_name": "S", "database": "D"})
    assert denied.status_code == 403 and "IMPORT SHARE" in denied.json()["detail"]


def test_mount_is_governed():
    assert policy.privilege_for("POST", "/api/hub/sources/share/mount")[0] == "SOURCE.CONNECT"
