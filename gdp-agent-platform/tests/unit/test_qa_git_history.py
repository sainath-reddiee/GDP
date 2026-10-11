"""QA packs and Git (V042): the deploy's sync decision and conflict recording, resolving a conflict, publishing a
pack, the repository lineage carried by drafts; and the history of saved QA tests (record, compare, restore)."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT))

from services.dbt import github, publish  # noqa: E402
from services.governance import policy  # noqa: E402
from services.qa import pack_git, packs, test_versions  # noqa: E402


class Row(dict):
    def as_dict(self):
        return dict(self)

    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else dict.__getitem__(self, key)


class Session:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def sql(self, sql, params=None):
        flat = " ".join(sql.split())
        self.calls.append((flat, list(params or [])))
        session = self

        class Result:
            def collect(self_inner):
                for needle, value in session.answers.items():
                    if needle in flat:
                        value = value(list(params or [])) if callable(value) else value
                        return [Row(r) for r in value]
                return []
        return Result()

    def find(self, needle):
        return [(s, p) for s, p in self.calls if needle in s]


Q1 = {"query_name": "orphans", "sql_expression": "SELECT 1 FROM T WHERE X IS NULL", "severity": "ERROR", "expectation": "0 rows"}
Q2 = {"query_name": "dups", "sql_expression": "SELECT K FROM T GROUP BY K HAVING COUNT(*) > 1", "severity": "WARN", "expectation": "0 rows"}


# ---------------------------------------------------------------- pure


def test_repo_sha_ignores_line_endings_and_trailing_spaces():
    assert pack_git.repo_sha("a  \r\nb\n") == pack_git.repo_sha("a\nb")
    assert pack_git.repo_sha("a\nb") != pack_git.repo_sha("a\nc")
    assert pack_git.pack_path("Company", "Referential integrity") == "domain/company/qa/referential_integrity.sql"


def test_sync_decision_covers_every_case():
    d = pack_git.sync_decision
    assert d(None, [], "s1", [Q1]) == "SEED"
    assert d({"source": "REPO", "repo_sha": "s0"}, [Q1], "s1", [Q1]) == "UNCHANGED"
    assert d({"source": "REPO", "repo_sha": "s0"}, [Q1], "s1", [Q1, Q2]) == "UPDATE"
    assert d({"source": "UI", "repo_sha": "s1"}, [Q1, Q2], "s1", [Q1]) == "AHEAD"
    assert d({"source": "UI", "repo_sha": "s0"}, [Q1, Q2], "s1", [Q1, Q2]) == "IN_SYNC"
    assert d({"source": "UI", "repo_sha": "s0"}, [Q1, Q2], "s1", [Q1]) == "CONFLICT"
    assert d({"source": "UI", "repo_sha": None}, [Q2], "s1", [Q1]) == "CONFLICT"


# ---------------------------------------------------------------- deploy seed


class Cur:
    def __init__(self, newest=None, current=(), open_conflicts=0):
        self.newest, self.current, self.open = newest, list(current), open_conflicts
        self.executed, self.result = [], []

    def execute(self, sql, params=()):
        flat = " ".join(sql.split())
        self.executed.append((flat, params))
        if "SELECT VERSION, STATUS, SOURCE" in flat:
            self.result = [self.newest] if self.newest else []
        elif "SELECT QUERY_NAME, SQL_EXPRESSION" in flat:
            self.result = [(q["query_name"], q["sql_expression"], q["severity"], q["expectation"]) for q in self.current]
        elif "SELECT COUNT(*) FROM DB.GOVERNANCE.QA_PACK_SYNC" in flat:
            self.result = [(self.open,)]
        else:
            self.result = []

    def fetchall(self):
        return self.result

    def fetchone(self):
        return self.result[0] if self.result else None

    def find(self, needle):
        return [e for e in self.executed if needle in e[0]]


@pytest.fixture()
def one_pack(monkeypatch, tmp_path):
    import infrastructure.seed_knowledge as seed

    path = tmp_path / "domain" / "sales" / "qa" / "integrity.sql"
    path.parent.mkdir(parents=True)
    path.write_text("-- @pack Integrity\n\n-- @query orphans\nSELECT 1 FROM T WHERE X IS NULL\n")
    monkeypatch.setattr(seed, "ROOT", tmp_path)
    monkeypatch.setattr(seed, "load_qa_packs", lambda: [("SALES", {"name": "Integrity", "description": "", "tags": [],
                                                                   "queries": [{**Q1, "description": "", "tags": []}]}, path)])
    monkeypatch.setattr(seed, "merge", lambda execute, db, data: None)
    return seed, pack_git.repo_sha(path.read_text())


def test_seed_records_a_conflict_instead_of_overwriting_app_edits(one_pack):
    seed, sha = one_pack
    cur = Cur(newest=(4, "APPROVED", "UI", "old-sha"), current=[Q1, Q2])
    log = seed.seed_qa_packs(cur, "DB")
    assert "CONFLICT" in log[0]
    ins = cur.find("INSERT INTO DB.GOVERNANCE.QA_PACK_SYNC")[0][1]
    assert ins[3] == "domain/sales/qa/integrity.sql" and ins[4] == sha and ins[6] == 4 and ins[7] == "old-sha"
    assert not cur.find("INSERT INTO DB.GOVERNANCE.DOMAIN_QA_PACKS")
    again = Cur(newest=(4, "APPROVED", "UI", "old-sha"), current=[Q1, Q2], open_conflicts=1)
    seed.seed_qa_packs(again, "DB")
    assert not again.find("INSERT INTO DB.GOVERNANCE.QA_PACK_SYNC")  # the same conflict is recorded once


def test_seed_does_not_flag_app_edits_made_before_tracking(one_pack):
    seed, sha = one_pack

    class Legacy(Cur):
        def execute(self, sql, params=()):
            super().execute(sql, params)
            if "SOURCE = 'REPO'" in sql:  # the pack's last seeded version still matches the file
                self.result = [(Q1["query_name"], Q1["sql_expression"], Q1["severity"], Q1["expectation"])]

    cur = Legacy(newest=(4, "APPROVED", "UI", None), current=[Q1, Q2])
    log = seed.seed_qa_packs(cur, "DB")
    assert "kept" in log[0] and not cur.find("INSERT INTO DB.GOVERNANCE.QA_PACK_SYNC")
    assert cur.find("SET REPO_SHA = %s, REPO_PATH = %s")[0][1][:2] == (sha, "domain/sales/qa/integrity.sql")


def test_seed_keeps_an_app_version_that_is_ahead_and_marks_in_sync(one_pack):
    seed, sha = one_pack
    ahead = Cur(newest=(4, "APPROVED", "UI", sha), current=[Q1, Q2])
    assert "kept" in seed.seed_qa_packs(ahead, "DB")[0] and not ahead.find("INSERT")
    synced = Cur(newest=(5, "APPROVED", "UI", "old"), current=[Q1])
    assert "in step" in seed.seed_qa_packs(synced, "DB")[0]
    assert synced.find("SET REPO_SHA = %s, REPO_PATH = %s")[0][1][:2] == (sha, "domain/sales/qa/integrity.sql")
    assert synced.find("RESOLUTION = 'IN_SYNC'")


def test_seed_updates_a_repo_owned_pack_with_its_file_hash(one_pack):
    seed, sha = one_pack
    cur = Cur(newest=(2, "APPROVED", "REPO", "old"), current=[Q2])
    log = seed.seed_qa_packs(cur, "DB")
    assert "version 3" in log[0]
    ins = cur.find("INSERT INTO DB.GOVERNANCE.DOMAIN_QA_PACKS")[0]
    assert "REPO_SHA, REPO_PATH" in ins[0] and ins[1][-2:] == (sha, "domain/sales/qa/integrity.sql")


# ---------------------------------------------------------------- resolving a conflict and drafts


def test_save_draft_carries_the_repository_lineage(monkeypatch):
    inserted = {}
    newest = {"version": 4, "status": "APPROVED", "domain_id": "d1", "name": "Integrity", "description": "", "tags": [],
              "repo_sha": "abc", "repo_path": "domain/sales/qa/integrity.sql", "pack_id": "p1"}
    monkeypatch.setattr(packs, "_versions", lambda s, pid: [newest])
    monkeypatch.setattr(packs, "allowed_tables", lambda s, d: ["T"])
    monkeypatch.setattr(packs, "clean_queries", lambda q, allowed: [{**Q1, "ordinal": 1, "tags": []}])
    monkeypatch.setattr(packs, "_query_rows", lambda s, pid, v: [])
    monkeypatch.setattr(packs, "_insert_version", lambda *a, **k: inserted.update(k=k, a=a))
    packs.save_draft(Session(), "p1", json.dumps({"queries": [Q1]}))
    assert inserted["k"]["repo"] == {"repo_sha": "abc", "repo_path": "domain/sales/qa/integrity.sql"}
    packs.save_draft(Session(), "p1", json.dumps({"queries": [Q1], "repo_sha": "new", "repo_path": "x.sql"}))
    assert inserted["k"]["repo"]["repo_sha"] == "new"


def test_take_repo_and_keep_app(monkeypatch):
    content = "-- @pack Integrity\n\n-- @query orphans\nSELECT 1 FROM T WHERE X IS NULL\n"
    row = {"SYNC_ID": "s1", "PACK_ID": "p1", "DOMAIN_ID": "d1", "REPO_PATH": "domain/sales/qa/integrity.sql", "REPO_SHA": "sha2",
           "REPO_CONTENT": content, "APP_VERSION": 4, "BASE_REPO_SHA": "sha1", "STATUS": "OPEN", "RESOLUTION": None,
           "DRAFT_VERSION": None, "DETECTED_AT": None, "RESOLVED_BY": None, "RESOLVED_AT": None}
    saved = {}
    monkeypatch.setattr(packs, "get_pack", lambda s, pid, v=None: {"pack": {"version": 4, "status": "APPROVED", "name": "Integrity"}, "queries": [Q1, Q2]})
    monkeypatch.setattr(packs, "save_draft", lambda s, pid, payload: saved.update(json.loads(payload)) or {"version": 5, "diff": {}})
    s = Session({"FROM GOVERNANCE.QA_PACK_SYNC WHERE SYNC_ID = ?": [row]})
    out = pack_git.take_repo(s, "p1", "s1", "ana")
    assert out["draft_version"] == 5 and saved["repo_sha"] == "sha2" and saved["base_version"] == 4
    assert [q["query_name"] for q in saved["queries"]] == ["orphans"]
    assert s.find("RESOLUTION = 'TAKE_REPO'")[0][1][:2] == [5, "ANA"]
    k = Session({"FROM GOVERNANCE.QA_PACK_SYNC WHERE SYNC_ID = ?": [row]})
    assert pack_git.keep_app(k, "p1", "s1", "lee")["resolution"] == "KEEP_APP"
    assert k.find("UPDATE GOVERNANCE.DOMAIN_QA_PACKS SET REPO_SHA = ?")[0][1][0] == "sha2"
    done = Session({"FROM GOVERNANCE.QA_PACK_SYNC WHERE SYNC_ID = ?": [{**row, "STATUS": "RESOLVED"}]})
    with pytest.raises(AssertionError, match="already resolved"):
        pack_git.keep_app(done, "p1", "s1", "lee")


def test_conflict_view_parses_the_repository_version(monkeypatch):
    row = {"SYNC_ID": "s1", "PACK_ID": "p1", "REPO_CONTENT": "-- @pack Integrity\n\n-- @query orphans\nSELECT 1 FROM T WHERE X IS NULL\n",
           "REPO_SHA": "x", "STATUS": "OPEN", "DETECTED_AT": None, "RESOLVED_AT": None}
    monkeypatch.setattr(packs, "get_pack", lambda s, pid, v=None: {"pack": {"version": 4, "status": "APPROVED"}, "queries": [Q1, Q2]})
    out = pack_git.conflict(Session({"FROM GOVERNANCE.QA_PACK_SYNC WHERE PACK_ID = ?": [row]}), "p1")
    assert out["diff"]["removed"] == ["dups"] and "repo_content" not in out["sync"]
    assert pack_git.conflict(Session(), "p1") is None


# ---------------------------------------------------------------- publishing


def test_publish_qa_pack_renders_the_file(monkeypatch):
    monkeypatch.setattr(publish, "_token", lambda: "t")
    monkeypatch.setattr(github, "urllib_request", lambda token: "req")
    seen = {}
    monkeypatch.setattr(github, "publish", lambda req, origin, base, head, files, **kw: seen.update(files=files, head=head) or
                        {"status": "PUBLISHED", "pull_request": {"url": "https://github.com/a/b/pull/9", "number": 9}})
    monkeypatch.setattr(packs, "get_pack", lambda s, pid, v=None: {
        "pack": {"version": 3, "status": "APPROVED", "name": "Integrity", "domain_id": "d1", "description": "", "tags": [], "repo_path": None},
        "queries": [{**Q1, "description": None, "tags": []}]})
    s = Session({"FROM KNOWLEDGE.DOMAIN_REGISTRY": [{"DOMAIN_NAME": "SALES"}],
                 "FROM CODE.REPO WHERE REPO_ID": [{"GIT_URL": "https://github.com/a/b", "BRANCH": "main", "PROVIDER": "GITHUB"}]})
    out = publish.publish_dbt_pr(s, "", json.dumps({"qa_pack": {"pack_id": "p1", "version": 3, "repo_id": "r1", "head": "feature/qa-integrity-v3"}}))
    assert out["path"] == "domain/sales/qa/integrity.sql" and "-- @query orphans" in seen["files"]["domain/sales/qa/integrity.sql"]
    upd = s.find("UPDATE GOVERNANCE.DOMAIN_QA_PACKS SET REPO_ID = ?")[0][1]
    assert upd[4] == "OPEN" and upd[6] == "domain/sales/qa/integrity.sql"
    monkeypatch.setattr(github, "pull_status", lambda req, origin, n: {"state": "MERGED", "merge_commit_sha": "m"})
    sync = Session({"FROM GOVERNANCE.DOMAIN_QA_PACKS P JOIN CODE.REPO": [{"PACK_ID": "p1", "VERSION": 3, "PR_NUMBER": 9, "GIT_URL": "https://github.com/a/b"}]})
    assert publish.sync_qa_prs(sync, {})["merged"] == [{"pack_id": "p1", "version": 3}]
    assert "qa_packs" in publish.sync_git_prs(Session(), {})


# ---------------------------------------------------------------- test history


def test_record_and_history_with_sql_diff():
    s = Session()
    assert test_versions.record(s, "t1", "EDITED") and s.calls[0][1] == ["EDITED", "t1"]
    assert test_versions.record(s, "t1", "NONSENSE") and s.calls[1][1][0] == "EDITED"
    v = [{"VERSION": 1, "CHANGE": "CREATED", "TITLE": "Orphans", "OBJECTIVE": None, "SQL_TEXT": "SELECT 1\nFROM T;",
          "EXPECTED": "0 rows", "SEVERITY": "HIGH", "SUITE_ID": "s", "IS_DELETED": False, "CHANGED_BY": "ANA", "CHANGED_AT": None},
         {"VERSION": 2, "CHANGE": "EDITED", "TITLE": "Orphan keys", "OBJECTIVE": None, "SQL_TEXT": "SELECT 1\nFROM T\nWHERE X IS NULL;",
          "EXPECTED": "0 rows", "SEVERITY": "CRITICAL", "SUITE_ID": "s", "IS_DELETED": False, "CHANGED_BY": "BEN", "CHANGED_AT": None}]
    out = test_versions.history(Session({"FROM CONTRACT.QA_TEST_VERSION WHERE TEST_ID": v}), "t1")
    newest = out["versions"][0]
    assert newest["version"] == 2 and set(newest["diff"]["fields"]) == {"title", "sql_text", "severity"}
    assert any(line.startswith("+WHERE X IS NULL") for line in newest["diff"]["sql_diff"])
    assert out["versions"][1]["diff"] is None

    class Broken:
        def sql(self, *a, **k):
            raise RuntimeError("no table")
    assert test_versions.record(Broken(), "t1", "EDITED") is False


def test_restore_goes_through_the_update_path(monkeypatch):
    from services.qa import procedures

    called = {}
    monkeypatch.setattr(procedures, "update_table_test", lambda s, tid, payload, change="EDITED": called.update(
        tid=tid, payload=json.loads(payload), change=change) or {"note": None})
    old = {"TITLE": "Orphans", "OBJECTIVE": None, "SQL_TEXT": "SELECT 1 FROM T;", "EXPECTED": "0 rows", "SEVERITY": "HIGH", "SUITE_ID": "s1"}
    s = Session({"FROM CONTRACT.QA_TEST_VERSION WHERE TEST_ID = ? AND VERSION = ?": [old],
                 "SELECT RUN_ID, SCOPE, IS_DELETED FROM CONTRACT.QA_TEST_CASE": [{"RUN_ID": None, "SCOPE": "TABLE", "IS_DELETED": True}]})
    out = test_versions.restore(s, "t1", 1)
    assert out["restored"] == 1 and called["change"] == "RESTORED" and called["payload"]["sql"] == "SELECT 1 FROM T"
    assert called["payload"]["suite_id"] == "s1" and s.find("SET IS_DELETED = FALSE")

    def fail(*a, **k):
        raise AssertionError("the query does not compile")
    monkeypatch.setattr(procedures, "update_table_test", fail)
    s2 = Session(dict(s.answers))
    with pytest.raises(AssertionError):
        test_versions.restore(s2, "t1", 1)
    assert s2.find("SET IS_DELETED = TRUE")  # put back as it was


def test_policy_rules_for_qa_git_and_history():
    assert policy.privilege_for("POST", "/api/qa-packs/p1/sync/s1/take-repo")[0] == "PACKAGE.EDIT"
    assert policy.privilege_for("POST", "/api/qa-packs/p1/publish")[0] == "PACKAGE.EDIT"
    assert policy.privilege_for("POST", "/api/qa-packs/p1/pr-sync")[0] is None
    assert policy.privilege_for("POST", "/api/qa/tests/t1/restore")[0] == "QA.EDIT"
