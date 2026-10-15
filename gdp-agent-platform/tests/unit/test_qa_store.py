"""QA Store (domain test packages): pack files, query validation, versioning, approval and execution."""

import json
from pathlib import Path

import pytest

from services.qa import pack_files, packs

ALLOWED = ["DB.S.CORE", "DB.S.ADDRESS"]
ROOT = Path(__file__).resolve().parents[2]


class Session:
    def __init__(self):
        self.calls = []

    def sql(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params or []))
        return self

    def collect(self):
        return []


def q(name="orphans", sql="SELECT a.ID FROM DB.S.ADDRESS a LEFT JOIN DB.S.CORE c ON c.ID = a.ID WHERE c.ID IS NULL", **kw):
    return {"query_name": name, "sql_expression": sql, **kw}


# ---------------------------------------------------------------- files


def test_pack_file_round_trip_and_errors():
    text = """-- @pack Integrity
-- @description Spokes resolve
-- @tags Reconciliation, #orphan-keys

-- @query orphan_address
-- @severity warn
-- @expect 0 rows
SELECT 1 FROM DB.S.ADDRESS;

-- @query dup_keys
SELECT ID FROM DB.S.CORE GROUP BY ID HAVING COUNT(*) > 1
"""
    parsed = pack_files.parse(text)
    assert parsed["name"] == "Integrity" and parsed["tags"] == ["reconciliation", "orphan-keys"]
    assert [x["query_name"] for x in parsed["queries"]] == ["orphan_address", "dup_keys"]
    assert parsed["queries"][0]["severity"] == "WARN" and parsed["queries"][0]["sql_expression"] == "SELECT 1 FROM DB.S.ADDRESS"
    assert pack_files.parse(pack_files.render(parsed)) == parsed
    for bad, message in [("SELECT 1", "before the first"), ("-- @pack P\n-- @query bad name\nSELECT 1", "query names"),
                         ("-- @pack P\n-- @query a\n", "no SQL"), ("-- @pack P\n-- @query a\n-- @severity HUGE\nSELECT 1", "severity"),
                         ("-- @pack P\n-- @query a\nSELECT 1\n-- @query A\nSELECT 2", "twice"), ("-- @query a\nSELECT 1", "no -- @pack")]:
        with pytest.raises(ValueError, match=message):
            pack_files.parse(bad)


def test_repository_packs_parse_and_pass_the_guard():
    from services.qa.guard import check

    files = sorted((ROOT / "domain").glob("*/qa/*.sql"))
    assert files
    company = json.loads((ROOT / "domain/company/domain_pack.json").read_text())
    allowed = [f"{company['domain']['silver_database']}.{t['schema']}.{t['table']}" for t in company["targets"]]
    for f in files:
        parsed = pack_files.parse(f.read_text())
        for query in parsed["queries"]:
            ok, problems, _ = check(query["sql_expression"], allowed)
            assert ok, (f.name, query["query_name"], problems)


# ---------------------------------------------------------------- pure


def test_ids_are_stable_and_scoped():
    assert packs.pack_id_for("d1", "Integrity") == packs.pack_id_for("d1", " integrity ")
    assert packs.pack_id_for("d1", "Integrity") != packs.pack_id_for("d2", "Integrity")
    assert packs.query_id_for("p", "Orphans") == packs.query_id_for("p", "orphans")
    assert len(packs.query_id_for("p", "x" * 128)) == 36


def test_clean_queries_validates_names_severity_and_sql():
    out = packs.clean_queries([q(severity="warn", tags="SLA, recon"), q("dups", "SELECT ID FROM DB.S.CORE")], ALLOWED)
    assert out[0]["severity"] == "WARN" and out[0]["tags"] == ["sla", "recon"] and out[1]["ordinal"] == 2
    cases = [([], "at least one"), ([q("1bad")], "names"), ([q(), q("ORPHANS")], "twice"),
             ([q(severity="FATAL")], "severity"), ([q(sql="SELECT * FROM DB.OTHER.T")], "orphans:"),
             ([q(sql="DELETE FROM DB.S.CORE")], "orphans:")]
    for queries, message in cases:
        with pytest.raises(AssertionError, match=message):
            packs.clean_queries(queries, ALLOWED)


def test_to_test_maps_severity_for_the_runner():
    test = packs.to_test({"name": "Integrity"}, {"query_id": "x", "query_name": "orphans", "severity": "INFO",
                                                 "sql_expression": "SELECT 1", "expectation": None})
    assert test == {"test_id": "x", "category": "DOMAIN_PACK", "title": "Integrity: orphans", "severity": "LOW",
                    "origin": "PACK", "expected": "0 rows", "sql": "SELECT 1", "target_column": None}


def test_diff_between_versions():
    old = [q(), q("dups", "SELECT 1", severity="ERROR")]
    new = [q(), q("dups", "SELECT 2", severity="WARN"), q("fresh", "SELECT 3")]
    d = packs.diff(old, new)
    assert d["added"] == ["fresh"] and d["removed"] == [] and d["changed"][0]["fields"] == ["sql_expression", "severity"]


# ---------------------------------------------------------------- versioning


@pytest.fixture
def store(monkeypatch):
    state = {"versions": [], "queries": {}, "inserted": [], "knowledge": [], "forgotten": []}
    monkeypatch.setattr(packs, "_versions", lambda s, pid: list(state["versions"]))
    monkeypatch.setattr(packs, "_query_rows", lambda s, pid, v: list(state["queries"].get(int(v), [])))
    monkeypatch.setattr(packs, "allowed_tables", lambda s, d: ALLOWED)

    def insert(session, pid, version, did, meta, queries, status, source, note):
        state["inserted"].append({"version": version, "status": status, "source": source, "queries": queries})
    monkeypatch.setattr(packs, "_insert_version", insert)
    import services.knowledge.writer as writer
    monkeypatch.setattr(writer, "remember", lambda session, **kw: state["knowledge"].append(kw) or "k")
    monkeypatch.setattr(writer, "forget", lambda session, d, key: state["forgotten"].append(key))
    return state


def version(n, status):
    return {"pack_id": "p", "version": n, "status": status, "domain_id": "d1", "name": "Integrity", "source": "UI"}


def test_saving_replaces_a_draft_but_never_an_approved_version(store):
    session = Session()
    store["versions"] = [version(2, "DRAFT"), version(1, "APPROVED")]
    out = packs.save_draft(session, "p", json.dumps({"queries": [q()], "base_version": 2}))
    assert out["version"] == 2 and any(c[0].startswith("DELETE FROM CORE.DOMAIN_QA_PACKS") for c in session.calls)
    store["versions"] = [version(2, "APPROVED"), version(1, "SUPERSEDED")]
    session = Session()
    out = packs.save_draft(session, "p", json.dumps({"queries": [q()]}))
    assert out["version"] == 3 and not any(c[0].startswith("DELETE") for c in session.calls)
    assert store["inserted"][-1]["status"] == "DRAFT" and store["inserted"][-1]["source"] == "UI"
    with pytest.raises(AssertionError, match="reload"):
        packs.save_draft(Session(), "p", json.dumps({"queries": [q()], "base_version": 1}))


def test_submit_approve_publishes_knowledge_and_supersedes(store):
    store["versions"] = [version(2, "DRAFT"), version(1, "APPROVED")]
    with pytest.raises(AssertionError, match="submit"):
        packs.approve(Session(), "p")
    session = Session()
    assert packs.submit(session, "p")["status"] == "PENDING_APPROVAL"
    store["versions"] = [version(2, "PENDING_APPROVAL"), version(1, "APPROVED")]
    store["queries"] = {1: [q("orphans"), q("gone")], 2: [{**q("orphans"), "query_id": "x", "severity": "ERROR"}]}
    session = Session()
    out = packs.approve(session, "p")
    assert out["status"] == "APPROVED" and out["knowledge"] == 1
    sql = [c[0] for c in session.calls]
    assert any("SET STATUS = 'SUPERSEDED'" in s for s in sql) and any("APPROVED_BY = CURRENT_USER()" in s for s in sql)
    assert store["knowledge"][0]["kind"] == "QA_TEST" and store["knowledge"][0]["status"] == "ACTIVE"
    assert store["knowledge"][0]["key"] == packs.knowledge_key("p", "orphans")
    assert store["forgotten"] == [packs.knowledge_key("p", "gone")]


def test_submit_only_drafts(store):
    store["versions"] = [version(1, "APPROVED")]
    with pytest.raises(AssertionError, match="only a draft"):
        packs.submit(Session(), "p")


def test_execute_stores_domain_scope(store, monkeypatch):
    import services.qa.run as run

    store["versions"] = [version(2, "DRAFT"), version(1, "APPROVED")]
    store["queries"] = {1: [{**q("orphans"), "query_id": "q1", "severity": "ERROR", "expectation": "0 rows"}],
                        2: [{**q("orphans"), "query_id": "q1", "severity": "WARN", "expectation": "0 rows"}]}
    monkeypatch.setattr(packs, "_pii", lambda s, a: (set(), True))
    ran, stored = [], []
    monkeypatch.setattr(run, "execute", lambda s, t, a, p, k, c: ran.append((t, a, c)) or {**t, "outcome": "PASS"})
    monkeypatch.setattr(run, "_store", lambda s, qa_run, results: stored.append((qa_run, results)))
    out = packs.execute(Session(), "d1", "p")
    assert out["trigger_detail"] == {"pack_id": "p", "version": 1, "status": "APPROVED"}
    assert ran[0][0]["severity"] == "HIGH" and ran[0][1] == ALLOWED and ran[0][2] is True
    assert stored[0][0]["scope"] == "DOMAIN" and stored[0][0]["domain_id"] == "d1" and stored[0][0]["passed"] == 1
    packs.execute(Session(), "d1", "p", version=2)
    assert ran[-1][0]["severity"] == "MEDIUM"
    adhoc = packs.execute(Session(), "d1", sql="SELECT 1 FROM DB.S.CORE")
    assert adhoc["stored"] is False and len(stored) == 2
    with pytest.raises(AssertionError, match="another domain"):
        packs.execute(Session(), "d2", "p")


# ---------------------------------------------------------------- seeding


def test_seed_inserts_new_repo_packs_and_keeps_ui_edits(monkeypatch):
    import infrastructure.seed_knowledge as seed

    executed = []

    class Cur:
        def __init__(self, newest=None):
            self.newest, self.result = newest, []

        def execute(self, sql, params=()):
            if sql.startswith("SELECT REPO_SHA FROM"):  # before V042: no repository tracking
                raise RuntimeError("invalid identifier REPO_SHA")
            executed.append((" ".join(sql.split()), params))
            if "SELECT VERSION, STATUS, SOURCE" in sql:
                self.result = [self.newest] if self.newest else []
            else:
                self.result = []

        def fetchall(self):
            return self.result

    monkeypatch.setattr(seed, "merge", lambda execute, db, data: executed.append(("MERGE", data["knowledge"])))
    log = seed.seed_qa_packs(Cur(), "DB")
    assert any("version 1" in line for line in log)
    inserts = [e for e in executed if e[0].startswith("INSERT INTO DB.CORE.DOMAIN_QA_PACKS")]
    assert inserts and all("'APPROVED', 'REPO'" in e[0] for e in inserts)
    knowledge = [e for e in executed if e[0] == "MERGE"][0][1]
    assert knowledge and all(k[2] == "QA_TEST" and k[8] == "ACTIVE" for k in knowledge)
    executed.clear()
    log = seed.seed_qa_packs(Cur(newest=(3, "APPROVED", "UI")), "DB")
    assert all("kept" in line for line in log) and not any(e[0].startswith("INSERT") for e in executed)
