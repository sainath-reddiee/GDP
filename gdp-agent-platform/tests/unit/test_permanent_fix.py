"""Permanent fixes: when to draft, which files (and which are skipped), output validation, four eyes, the dry-run token
and base-content check, the publisher's asserts, the pull request hook, recurring() before V066 and the worker caps."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from services.cases.triage import sha256
from services.common import preview_token
from services.resolve import permanent

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
MODEL_SQL = "select id,\n       amount\n  from {{ ref('raw_orders') }}\n"
SCHEMA_YML = "version: 2\nmodels:\n  - name: stg_orders\n    columns:\n      - name: id\n"


class FakeDb:
    """query() answers by the first handler whose marker is in the SQL; writes are recorded."""

    def __init__(self, user="ANA"):
        self.user = user
        self.handlers, self.queries, self.writes = [], [], []

    def on(self, marker, value):
        self.handlers.insert(0, (marker, value))
        return self

    def query(self, sql, params=()):
        self.queries.append((sql, params))
        for marker, value in self.handlers:
            if marker in sql:
                found = value(sql, params) if callable(value) else value
                return [dict(r) for r in found]
        return []

    def execute(self, sql, params=()):
        self.writes.append((" ".join(sql.split()), params))

    def execute_count(self, sql, params=()):
        self.writes.append((" ".join(sql.split()), params))
        return 1

    def wrote(self, needle):
        return [p for s, p in self.writes if needle in s]


class Row(dict):
    def as_dict(self):
        return dict(self)


class Session:
    def __init__(self, answers=None):
        self.answers, self.calls = answers or {}, []

    def sql(self, sql, params=None):
        flat = " ".join(sql.split())
        self.calls.append((flat, list(params or [])))
        answers = self.answers

        class Result:
            def collect(self_inner):
                for needle, value in answers.items():
                    if needle in flat:
                        return [Row(r) for r in (value(list(params or [])) if callable(value) else value)]
                return []
        return Result()

    def find(self, needle):
        return [(s, p) for s, p in self.calls if needle in s]


# ---------------------------------------------------------------- when to draft


def test_due_for_draft_waits_for_open_proposals_and_new_runs():
    rec = [{"issue_id": "a", "status": "PUBLISHED", "runs": 4}, {"issue_id": "b", "status": "PUBLISHED", "runs": 5},
           {"issue_id": "c", "status": "PUBLISHED", "runs": 6}, {"issue_id": "d", "status": "RETIRED", "runs": 9},
           {"issue_id": "e", "status": "PUBLISHED", "runs": 2}, {"issue_id": "f", "status": "PUBLISHED", "runs": 3},
           {"issue_id": "g", "status": "PUBLISHED", "runs": 3}]
    latest = {"b": {"status": "DRAFTED"}, "c": {"status": "REJECTED"}, "f": {"status": "MERGED"},
              "g": {"status": "UNAVAILABLE", "created_at": NOW - timedelta(hours=2)}}
    due = permanent.due_for_draft(rec, latest, {"c": 1, "f": 3}, 3, NOW)
    assert [d["issue_id"] for d in due] == ["a", "f"]   # b open, c too few runs since, d retired, e below, g too soon
    assert "did not stop it" in due[1]["note"]
    latest["g"]["created_at"] = NOW - timedelta(hours=30)
    assert "g" in [d["issue_id"] for d in permanent.due_for_draft(rec, latest, {}, 3, NOW)]


# ---------------------------------------------------------------- which files


def _locate_db(paths, dag_repo="r1"):
    db = FakeDb()
    db.on("FROM CORE.FAILURE_EVENT WHERE FAILURE_ID", [{"failure_id": "f1", "env_id": "e1", "dag_id": "orders",
                                                      "task_id": "stg_orders_run", "incident_id": "i1",
                                                      "exception_text": "boom", "rca": None, "dbt_node_id": None}])
    db.on("FILELOC, REPO_ID, REPO_PATH", [{"env_id": "e1", "dag_id": "orders", "fileloc": "/dags/orders.py",
                                    "repo_id": dag_repo, "repo_path": "dags", "domain_id": "d1"}])
    db.on("FROM CORE.CODE_CHUNK", lambda sql, p: [r for r in paths if r["kind"] == p[0]])
    db.on("FROM CORE.REPO WHERE REPO_ID", [{"repo_id": "r1", "name": "analytics", "provider": "GITHUB", "branch": "main",
                                            "git_repository": "DB.CORE.GIT_R1", "enabled": True, "last_commit": "abc"}])
    return db


def test_locate_reads_the_models_files_and_explains_skips(monkeypatch):
    from services.ops import catalog

    monkeypatch.setattr(catalog, "task_entry", lambda db, env, dag, task: {"operator": "BashOperator",
                                                                         "dbt_selectors": ["stg_orders+"]})
    paths = [{"kind": "DBT_MODEL", "repo_id": "r1", "path": "models/stg/stg_orders.sql"},
             {"kind": "DBT_SCHEMA_YML", "repo_id": "r1", "path": "models/stg/schema.yml"},
             {"kind": "DBT_SCHEMA_YML", "repo_id": "r2", "path": "other/schema.yml"}]
    files = {"models/stg/stg_orders.sql": MODEL_SQL, "models/stg/schema.yml": SCHEMA_YML + "# password=hunter22\n"}
    out = permanent.locate(_locate_db(paths), {"issue_id": "k1", "source_failure_id": "f1"},
                           read_file=lambda db, repo, path: files[path])
    assert [f["path"] for f in out["files"]] == ["models/stg/stg_orders.sql"] and out["models"] == ["stg_orders"]
    reasons = {s["path"]: s["reason"] for s in out["skipped"]}
    assert "secret" in reasons["models/stg/schema.yml"] and "another repository" in reasons["other/schema.yml"]
    big = permanent.locate(_locate_db(paths[:1]), {"issue_id": "k1", "source_failure_id": "f1"},
                           read_file=lambda db, repo, path: "x" * (61 * 1024))
    assert not big["files"] and "60 KB" in big["skipped"][0]["reason"] and "none of the files" in big["missing"][0]


def test_locate_says_what_to_map_when_nothing_is_known(monkeypatch):
    from services.ops import catalog, context

    monkeypatch.setattr(catalog, "task_entry", lambda *a: None)
    monkeypatch.setattr(context, "_repo_ids", lambda db, dag: [])
    out = permanent.locate(_locate_db([], dag_repo=None), {"issue_id": "k1", "source_failure_id": "f1"})
    assert not out["files"] and any("refresh the catalogue" in m for m in out["missing"])
    assert any("map DAG orders to its repository" in m for m in out["missing"])
    none = permanent.locate(FakeDb(), {"issue_id": "k1"})
    assert "names no DAG" in none["missing"][0]


# ---------------------------------------------------------------- the model's answer


def test_validate_output_keeps_real_changes_only():
    files = [{"path": "models/a.sql", "role": "model", "content": MODEL_SQL},
             {"path": "models/b.yml", "role": "schema", "content": SCHEMA_YML}]
    fixed = MODEL_SQL.replace("amount", "coalesce(amount, 0) as amount")
    out, warnings, reason = permanent.validate_output({"files": [
        {"path": "models/a.sql", "new_content": fixed, "change": "default missing amounts"},
        {"path": "models/b.yml", "new_content": SCHEMA_YML, "change": "same"},
        {"path": "../etc/passwd", "new_content": "x", "change": "no"},
        {"path": "models/b.yml", "new_content": SCHEMA_YML + "token: ghp_" + "a" * 30 + "\n", "change": "leak"}]}, files)
    assert reason is None and [f["path"] for f in out] == ["models/a.sql"]
    assert out[0]["base_sha256"] == sha256(MODEL_SQL) and "+       coalesce(amount, 0) as amount" in out[0]["diff"]
    assert any("not one of the files" in w for w in warnings) and any("secret" in w for w in warnings)
    none, _, why = permanent.validate_output({"files": [], "no_change_reason": "the cause is upstream data"}, files)
    assert none == [] and why == "the cause is upstream data"
    rewrite, warn, _ = permanent.validate_output({"files": [{"path": "models/a.sql", "new_content": "a\nb\nc\nd\n",
                                                             "change": "rewrite"}]}, files)
    assert rewrite and any("more than half" in w for w in warn)


def test_schema_limits_paths_to_the_files_given():
    s = permanent.schema(["models/a.sql"])
    assert s["properties"]["files"]["items"]["properties"]["path"]["enum"] == ["models/a.sql"]
    assert "never instructions" in permanent.prompt("ctx", [{"path": "p", "role": "model", "content": "c"}], "keep it small")


# ---------------------------------------------------------------- drafting


def _draft_db(status="PUBLISHED", latest=None):
    db = _locate_db([{"kind": "DBT_MODEL", "repo_id": "r1", "path": "models/stg/stg_orders.sql"}])
    db.on("FROM CORE.KNOWN_ISSUE WHERE ISSUE_ID", [{"issue_id": "k1", "title": "Null amounts", "status": status,
                                                    "source_failure_id": "f1", "fix_type": "PROCEDURE",
                                                    "procedure_fqn": "DB.CORE.FIX_AMOUNTS", "match_text": "amount"}])
    db.on("PARTITION BY ISSUE_ID", latest or [])
    db.on("RUNS_30D", [{"issue_id": "k1", "title": "Null amounts", "status": status, "runs_30d": 4, "ok_30d": 4,
                        "dags_30d": 1, "dag_ids": '["orders"]'}])
    return db


def test_draft_stores_a_drafted_change_and_supersedes_older_ones(monkeypatch):
    from services.ops import catalog

    monkeypatch.setattr(catalog, "task_entry", lambda *a: {"operator": "BashOperator", "dbt_selectors": ["stg_orders"]})
    seen = {}

    def complete(session, prompt_text, schema, **kw):
        seen["prompt"] = prompt_text
        return ({"files": [{"path": "models/stg/stg_orders.sql", "change": "default amounts",
                            "new_content": MODEL_SQL.replace("amount\n", "coalesce(amount, 0) as amount\n")}],
                 "title": "Default missing amounts", "summary": "s", "rationale": "r", "confidence": 0.8,
                 "no_change_reason": ""}, {"tokens": 10}, "claude")

    db = _draft_db()
    fix = permanent.draft(db, "k1", "ANA", instructions="use zero", complete=complete,
                          read_file=lambda db, repo, path: MODEL_SQL)
    assert "CALL DB.CORE.FIX_AMOUNTS" in seen["prompt"] and "use zero" in seen["prompt"]
    insert = db.wrote("INSERT INTO CORE.PERMANENT_FIX")[0]
    assert insert[2] == "DRAFTED" and insert[3] == "MANUAL" and insert[21] is True
    assert json.loads(insert[13])[0]["base_sha256"] == sha256(MODEL_SQL) and fix["status"] == "DRAFTED"
    assert db.wrote("SET STATUS = 'SUPERSEDED'")
    with pytest.raises(permanent.PermanentFixError, match="already open"):
        permanent.draft(_draft_db(latest=[{"fix_id": "x", "issue_id": "k1", "status": "ACCEPTED"}]), "k1", "ANA",
                        complete=complete, read_file=lambda *a: MODEL_SQL)
    with pytest.raises(permanent.PermanentFixError, match="only a published"):
        permanent.draft(_draft_db(status="DRAFT"), "k1", "ANA", complete=complete)


def test_draft_without_files_is_unavailable_and_costs_no_ai(monkeypatch):
    from services.ops import catalog, context

    monkeypatch.setattr(catalog, "task_entry", lambda *a: None)
    monkeypatch.setattr(context, "_repo_ids", lambda db, dag: [])
    db = _draft_db()
    db.on("FILELOC, REPO_ID, REPO_PATH", [{"dag_id": "orders", "repo_id": None}])
    fix = permanent.draft(db, "k1", permanent.AGENT, "AUTO",
                          complete=lambda *a, **k: pytest.fail("no AI call without files"))
    assert fix["status"] == "UNAVAILABLE" and "map DAG orders" in fix["unavailable_reason"]


# ---------------------------------------------------------------- four eyes, token, base content


def _fix(**over):
    files = [{"path": "models/stg/stg_orders.sql", "role": "model", "base_sha256": sha256(MODEL_SQL),
              "new_content": MODEL_SQL + "-- fixed\n", "change": "c"}]
    return {"fix_id": "p1", "fix_number": 7, "issue_id": "k1", "status": "ACCEPTED", "drafted_by": "ANA",
            "publishable": True, "files": json.dumps(files), "repo_id": "r1", "diff": "d", "title": "t",
            "context": "{}", "publish_result": None, **over}


def test_decide_needs_a_second_person_and_a_reason_to_reject():
    db = FakeDb().on("FROM CORE.PERMANENT_FIX WHERE FIX_ID", [_fix(status="DRAFTED")])
    with pytest.raises(permanent.PermanentFixError, match="second person") as exc:
        permanent.decide(db, "p1", "ana", True)
    assert exc.value.status == 403
    with pytest.raises(permanent.PermanentFixError, match="say why"):
        permanent.decide(db, "p1", "BEN", False, "  ")
    permanent.decide(db, "p1", "BEN", True)
    assert db.wrote("SET STATUS = %s, DECIDED_BY")[0][:2] == ("ACCEPTED", "BEN")
    agent = FakeDb().on("FROM CORE.PERMANENT_FIX WHERE FIX_ID", [_fix(status="DRAFTED", drafted_by=permanent.AGENT)])
    permanent.decide(agent, "p1", "ANA", True)   # an agent draft: anyone allowed accepts it
    done = FakeDb().on("FROM CORE.PERMANENT_FIX WHERE FIX_ID", [_fix(status="ACCEPTED")])
    with pytest.raises(permanent.PermanentFixError, match="not waiting"):
        permanent.decide(done, "p1", "BEN", True)


def test_publish_needs_the_dry_run_token_and_unchanged_files(monkeypatch):
    monkeypatch.setattr(preview_token, "_key", lambda purpose, key: f"{purpose}:test".encode())
    db = FakeDb().on("FROM CORE.PERMANENT_FIX WHERE FIX_ID", [_fix()])
    db.on("FROM CORE.REPO WHERE REPO_ID", [{"repo_id": "r1", "name": "analytics", "branch": "main", "enabled": True}])
    db.on("FROM CORE.KNOWN_ISSUE WHERE ISSUE_ID", [{"issue_id": "k1", "title": "Null amounts"}])
    read = lambda db, repo, path: MODEL_SQL   # noqa: E731
    dry = permanent.publish(db, "p1", "BEN", read_file=read)
    assert dry["branch"] == "fix/known-issue-7" and dry["files"] == ["models/stg/stg_orders.sql"]
    with pytest.raises(permanent.PermanentFixError, match="dry run"):
        permanent.publish(db, "p1", "BEN", dry_run=False, read_file=read, publisher=lambda p: p)
    with pytest.raises(permanent.PermanentFixError, match="someone else"):
        permanent.publish(db, "p1", "CARL", False, dry["preview_token"], read_file=read, publisher=lambda p: p)
    sent = permanent.publish(db, "p1", "BEN", False, dry["preview_token"], read_file=read, publisher=lambda p: p)
    assert sent["permanent_fix"]["head"] == "fix/known-issue-7" and "Null amounts" in sent["permanent_fix"]["body"]
    with pytest.raises(permanent.PermanentFixError, match="changed since"):
        permanent.publish(db, "p1", "BEN", read_file=lambda *a: MODEL_SQL + "-- someone else\n")
    drafted = FakeDb().on("FROM CORE.PERMANENT_FIX WHERE FIX_ID", [_fix(status="DRAFTED")])
    with pytest.raises(permanent.PermanentFixError, match="accept the permanent fix"):
        permanent.publish(drafted, "p1", "BEN")


# ---------------------------------------------------------------- the publisher procedure


def test_publisher_pushes_only_an_accepted_fix_of_indexed_files(monkeypatch):
    from services.dbt import github, publish

    monkeypatch.setattr(publish, "_token", lambda: "t")
    monkeypatch.setattr(github, "urllib_request", lambda token: "req")
    seen = {}
    monkeypatch.setattr(github, "publish", lambda req, origin, base, head, files, **kw: seen.update(files=files, kw=kw) or
                        {"status": "PUBLISHED", "base_branch": base, "pull_request": {"url": "https://github.com/a/b/pull/3", "number": 3}})
    fix_row = {"ISSUE_ID": "k1", "STATUS": "ACCEPTED", "PUBLISHABLE": True, "FILES": _fix()["files"], "REPO_ID": "r1",
               "DOMAIN_ID": "d1", "TITLE": "t"}
    answers = {"FROM CORE.PERMANENT_FIX WHERE FIX_ID": [fix_row], "FROM CORE.CODE_FILE": [{"X": 1}],
               "FROM CORE.REPO WHERE REPO_ID": [{"GIT_URL": "https://github.com/a/b", "BRANCH": "main", "PROVIDER": "GITHUB"}]}
    s = Session(answers)
    request = {"permanent_fix": {"fix_id": "p1", "head": "fix/known-issue-7", "actor": "BEN", "title": "Permanent fix"}}
    out = publish.publish_dbt_pr(s, "", json.dumps(request))
    assert out["status"] == "PUBLISHED" and seen["kw"]["draft"] is True
    assert seen["files"] == {"models/stg/stg_orders.sql": MODEL_SQL + "-- fixed\n"}
    upd = s.find("UPDATE CORE.PERMANENT_FIX SET STATUS = ?")[0][1]
    assert upd[0] == "PUBLISHED" and upd[4] == "OPEN"
    assert s.find("MERGE INTO CORE.GIT_PR")[0][1][0] == "PERMANENT_FIX"
    with pytest.raises(AssertionError, match="fix/known-issue-"):
        publish.publish_dbt_pr(Session(answers), "", json.dumps({"permanent_fix": {**request["permanent_fix"], "head": "main2"}}))
    drafted = {**answers, "FROM CORE.PERMANENT_FIX WHERE FIX_ID": [{**fix_row, "STATUS": "DRAFTED"}]}
    with pytest.raises(AssertionError, match="only an accepted"):
        publish.publish_dbt_pr(Session(drafted), "", json.dumps(request))
    unindexed = {**answers, "FROM CORE.CODE_FILE": []}
    with pytest.raises(AssertionError, match="not a file of the mapped repository"):
        publish.publish_dbt_pr(Session(unindexed), "", json.dumps(request))


# ---------------------------------------------------------------- the pull request's state


def test_roundtrip_hook_ends_the_proposal_and_never_retires(monkeypatch):
    from services.git import roundtrip
    from services.knowledge import signals

    kept = []
    monkeypatch.setattr(signals, "keep", lambda session, domain_id, item, origin: kept.append((domain_id, item)))
    s = Session({"FROM CORE.PERMANENT_FIX P": [{"FIX_ID": "p1", "ISSUE_ID": "k1", "DOMAIN_ID": "d1", "DAG_ID": "orders",
                                                "TITLE": "Default amounts", "SUMMARY": "s", "PR_URL": "u",
                                                "FILES": _fix()["files"], "ISSUE_TITLE": "Null amounts"}]})
    roundtrip.apply_state(s, "PERMANENT_FIX", "p1", 1, "MERGED", "sha1")
    upd = s.find("UPDATE CORE.PERMANENT_FIX SET PR_STATE")[0][1]
    assert upd == ["MERGED", "sha1", "MERGED", "p1"]
    assert not s.find("KNOWN_ISSUE SET")
    assert kept[0][0] == "d1" and kept[0][1]["key"] == "ops.permanent_fix.k1"
    assert "models/stg/stg_orders.sql" in kept[0][1]["content"]
    closed = Session()
    roundtrip.apply_state(closed, "PERMANENT_FIX", "p1", 1, "CLOSED", None)
    assert closed.find("UPDATE CORE.PERMANENT_FIX")[0][1][2] == "CLOSED" and len(kept) == 1
    assert roundtrip.link("PERMANENT_FIX", "p1", 1) == "/ops/permanent-fixes/p1"
    assert "permanent fix" in signals.pr_closed("PERMANENT_FIX", "p1", "t", 1, "b", "u")["title"]


def test_known_fix_is_retired_only_after_a_merge_by_a_person(monkeypatch):
    from services.resolve import publish as known

    retired = []
    monkeypatch.setattr(known, "retire", lambda db, issue_id, actor: retired.append((issue_id, actor)))
    open_fix = FakeDb().on("FROM CORE.PERMANENT_FIX WHERE FIX_ID", [_fix(status="PUBLISHED")])
    with pytest.raises(permanent.PermanentFixError, match="has merged"):
        permanent.known_fix_decision(open_fix, "p1", "BEN", True)
    merged = FakeDb().on("FROM CORE.PERMANENT_FIX WHERE FIX_ID", [_fix(status="MERGED")])
    permanent.known_fix_decision(merged, "p1", "BEN", True)
    assert retired == [("k1", "BEN")] and merged.wrote("SET RETIRE_DECISION")[0][0] == "RETIRED"
    permanent.known_fix_decision(merged, "p1", "BEN", False)
    assert len(retired) == 1 and merged.wrote("SET RETIRE_DECISION")[1][0] == "KEPT"


# ---------------------------------------------------------------- recurring, preferences, worker, policy


def test_recurring_carries_the_latest_proposal_and_works_before_v066():
    from services.resolve import publish as known

    db = FakeDb().on("RUNS_30D", [{"issue_id": "k1", "title": "t", "status": "PUBLISHED", "runs_30d": 3, "ok_30d": 3,
                                   "dags_30d": 1, "dag_ids": "[]"}])
    assert known.recurring(db)[0]["permanent_fix"] is None
    db.on("PARTITION BY ISSUE_ID", [{"fix_id": "p1", "issue_id": "k1", "status": "DRAFTED", "pr_url": None}])
    assert known.recurring(db)[0]["permanent_fix"] == {"fix_id": "p1", "status": "DRAFTED", "pr_url": None}

    def boom(sql, params):
        raise RuntimeError("Object 'CORE.PERMANENT_FIX' does not exist")
    db.on("PARTITION BY ISSUE_ID", boom)
    assert known.recurring(db)[0]["permanent_fix"] is None


def test_preference_defaults_for_permanent_fixes():
    from services.resolve import rules

    assert rules.preference(None)["permanent_fix_enabled"] is True
    assert rules.preference({"permanent_fix_enabled": False, "permanent_fix_min_runs": 1})["permanent_fix_min_runs"] == 2
    assert rules.preference({"permanent_fix_min_runs": 5})["permanent_fix_min_runs"] == 5


def test_auto_draft_respects_the_switch_and_the_caps(monkeypatch):
    from services.resolve import engine
    from services.resolve import publish as known

    monkeypatch.setattr(engine, "load_preference", lambda db: {"permanent_fix_enabled": False})
    assert permanent.auto_draft(FakeDb()) == {"enabled": False, "drafted": 0}
    monkeypatch.setattr(engine, "load_preference", lambda db: {"permanent_fix_enabled": True, "permanent_fix_min_runs": 3})
    monkeypatch.setattr(known, "recurring", lambda db, **kw: [{"issue_id": f"k{i}", "status": "PUBLISHED", "runs": 5}
                                                              for i in range(4)])
    drafted = []
    monkeypatch.setattr(permanent, "draft", lambda db, issue_id, actor, trigger, **kw:
                        drafted.append((issue_id, actor, trigger, kw["supersede"])) or {"status": "DRAFTED"})
    db = FakeDb().on("TRIGGER_KIND = 'AUTO'", [{"n": 0}])
    out = permanent.auto_draft(db, now=NOW)
    assert out["due"] == 4 and out["drafted"] == permanent.DRAFTS_PER_RUN
    assert drafted[0] == ("k0", permanent.AGENT, "AUTO", True)
    drafted.clear()
    db.on("TRIGGER_KIND = 'AUTO'", [{"n": permanent.DRAFTS_PER_DAY - 1}])
    assert permanent.auto_draft(db, now=NOW)["drafted"] == 1
    drafted.clear()
    assert permanent.auto_draft(FakeDb().on("TRIGGER_KIND = 'AUTO'", [{"n": 0}]), now=NOW,
                                still_held=lambda: False)["drafted"] == 0

    def missing(sql, params):
        raise RuntimeError("does not exist")
    assert "V066" in permanent.auto_draft(FakeDb().on("TRIGGER_KIND = 'AUTO'", missing))["detail"]


def test_worker_runs_the_permanent_fix_job_hourly():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))
    from app import worker

    assert worker.INTERVALS["permanent_fix"] == 3600 and callable(worker.permanent_fix)


def test_policy_governs_the_permanent_fix_routes():
    from services.governance import policy

    assert policy.privilege_for("POST", "/api/ops/permanent-fixes/draft/k1")[0] == "OPS.OPERATE"
    assert policy.privilege_for("POST", "/api/ops/permanent-fixes/p1/accept")[0] == "OPS.FIX_PUBLISH"
    assert policy.privilege_for("POST", "/api/ops/permanent-fixes/p1/reject")[0] == "OPS.FIX_PUBLISH"
    assert policy.privilege_for("POST", "/api/ops/permanent-fixes/p1/publish")[0] == "DBT.EDIT"
    assert policy.privilege_for("POST", "/api/ops/permanent-fixes/p1/known-fix")[0] == "OPS.FIX_PUBLISH"
    assert policy.privilege_for("GET", "/api/ops/permanent-fixes")[0] == "OPS.VIEW"
    assert policy.no_queue("POST", "/api/ops/permanent-fixes/p1/accept")
    assert not policy.no_queue("POST", "/api/ops/permanent-fixes/p1/publish")


def test_preview_tokens_are_bound_to_purpose_content_and_time():
    bound = {"f": "p1", "b": "fix/known-issue-7", "h": "abc"}
    token = preview_token.make(bound, "ben", "permanent-fix", now=1000, key="k")
    preview_token.check(token, bound, "BEN", "permanent-fix", now=1100, key="k")
    with pytest.raises(preview_token.TokenError, match="not valid"):
        preview_token.check(token, bound, "BEN", "case-patch", now=1100, key="k")
    with pytest.raises(preview_token.TokenError, match="other content"):
        preview_token.check(token, {**bound, "h": "zzz"}, "BEN", "permanent-fix", now=1100, key="k")
    with pytest.raises(preview_token.TokenError, match="expired"):
        preview_token.check(token, bound, "BEN", "permanent-fix", now=1000 + 601, key="k")
    preview_token.check(token, bound, "SOMEONE", "permanent-fix", now=1000 + 1200, replay=True, key="k")
