"""Request timing (Server-Timing, statement counts) and the short read caches (apps/api/app/perf.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import perf  # noqa: E402


class Db:
    def __init__(self, user="ANA", role="DATA_ENGINEER"):
        self.user, self.role, self.n = user, role, 0

    def query(self, sql, params=()):
        self.n += 1
        return [{"n": self.n}]

    def execute(self, sql, params=()):
        self.n += 1


perf.instrument(Db)


def app_with(db):
    app = FastAPI()
    app.middleware("http")(perf.middleware)

    @app.get("/api/read")
    @perf.cached("read", 30)
    def read(q: str = "", db=Depends(lambda: db)):
        return {"value": db.query("SELECT 1")[0]["n"], "q": q}

    @app.post("/api/write")
    def write(db=Depends(lambda: db)):
        db.execute("UPDATE T SET X = 1")
        return {"ok": True}

    @app.get("/api/two")
    def two(db=Depends(lambda: db)):
        db.query("SELECT 1")
        db.query("SELECT 2")
        return {}

    return TestClient(app)


def test_server_timing_counts_statements():
    c = app_with(Db())
    timing = c.get("/api/two").headers["server-timing"]
    assert timing.startswith("app;dur=") and 'sf;desc="2 statements"' in timing


def test_reads_are_cached_per_arguments_and_dropped_by_a_write():
    db = Db()
    c = app_with(db)
    first = c.get("/api/read?q=a").json()["value"]
    assert c.get("/api/read?q=a").json()["value"] == first  # cached: no new statement
    assert c.get("/api/read?q=b").json()["value"] == first + 1  # other arguments, other entry
    assert c.post("/api/write").status_code == 200
    assert c.get("/api/read?q=a").json()["value"] > first + 1  # a write starts a new generation


def test_sessions_never_share_entries():
    a, b = Db("ANA"), Db("BEN")
    assert perf.read_cache(a, "k", 30, lambda: "ana") == "ana"
    assert perf.read_cache(b, "k", 30, lambda: "ben") == "ben"
    assert perf.read_cache(a, "k", 30, lambda: "other") == "ana"


def test_cached_binds_positional_calls_and_skips_without_db():
    calls = []

    @perf.cached("f", 30)
    def f(domain, db=None):
        calls.append(domain)
        return domain

    db = Db()
    assert f("x", db) == "x" and f("x", db=db) == "x" and calls == ["x"]
    f("y")
    f("y")
    assert calls == ["x", "y", "y"]  # no session: never cached
