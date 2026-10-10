"""The dbt workspace uses repositories configured in Admin: the server resolves the clone and origin, pushes under the
project folder, and never lets the browser redirect where code goes."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from services.dbt import github  # noqa: E402
from services.dbt.procedures import merge_branch_plan  # noqa: E402
from services.dbt.workspace import branch_segment, skeleton_from_listing  # noqa: E402


# --------------------------------------------------------------------------- services


def test_tree_entries_under_project_folder_still_drop_escapes():
    files = {"models/a.sql": "a", "../evil": "x", "release/branch.json": "{}"}
    paths = [e["path"] for e in github.tree_entries(files, "analytics")]
    assert "analytics/models/a.sql" in paths and not any("evil" in p for p in paths)
    assert [e["path"] for e in github.tree_entries({"models/a.sql": "a"})] == ["models/a.sql"]
    for bad in ("../x", "a/../../b", "a b", "x;y"):
        with pytest.raises(ValueError):
            github.clean_root(bad)


def test_skeleton_reads_only_the_project_folder_relative_to_it():
    names = ['repo/branches/"feat/x"/analytics/dbt_project.yml', 'repo/branches/"feat/x"/analytics/models/a.sql',
             'repo/branches/"feat/x"/README.md', 'repo/branches/"feat/x"/analytics/logo.png']
    read = []
    files = skeleton_from_listing(names, "feat/x", "analytics", lambda rel: read.append(rel) or f"text of {rel}")
    assert set(files) == {"dbt_project.yml", "models/a.sql"}
    assert read == ["analytics/dbt_project.yml", "analytics/models/a.sql"]  # read by path from the branch root
    assert set(skeleton_from_listing(names[:3], "feat/x", "", lambda rel: "t")) == {
        "analytics/dbt_project.yml", "analytics/models/a.sql", "README.md"}
    assert branch_segment("feat/x") == '"feat/x"' and branch_segment("main") == "main"


def test_project_from_branch_quotes_branch_and_uses_folder():
    from services.dbt.publish import project_from_branch

    class Session:
        def __init__(self):
            self.sql_text = []

        def sql(self, text):
            self.sql_text.append(text)
            return self

        def collect(self):
            return []

    s = Session()
    out = project_from_branch(s, "DB.CODE.DEMO", "feat/onboard-x", "DB.CODEGEN.P", "c", "analytics")
    assert out["from"] == '@DB.CODE.DEMO/branches/"feat/onboard-x"/analytics'
    assert any('FROM \'@DB.CODE.DEMO/branches/"feat/onboard-x"/analytics\'' in t for t in s.sql_text)


def test_publish_plan_ignores_origin_from_the_request(monkeypatch):
    from services.dbt import publish

    stored = {"origin": "https://github.com/acme/dbt", "git_repository": "DB.CODE.DEMO", "base_branch": "main"}
    monkeypatch.setattr(publish, "rows", lambda session, sql, params: [{"CONTENT_JSON": json.dumps(stored)}])
    plan = publish._plan(None, "r1", {"origin": "https://github.com/attacker/x", "git_repository": "X.Y.Z",
                                      "cut_branch": "feat/a", "draft": True})
    assert plan["origin"] == stored["origin"] and plan["git_repository"] == "DB.CODE.DEMO"
    assert plan["cut_branch"] == "feat/a" and plan["draft"] is True


def test_merge_branch_plan_uses_the_organisation_prefix_and_keeps_repo_keys():
    plan = merge_branch_plan({"code_repo_id": "c1", "project_dir": "analytics"}, {}, "Orders load", "r1", branch_prefix="dbt/")
    assert plan["cut_branch"] == "dbt/orders-load" and plan["code_repo_id"] == "c1" and plan["project_dir"] == "analytics"
    kept = merge_branch_plan({}, {"code_repo_id": "c1", "project_dir": "x"}, "n", "r1")
    assert kept["code_repo_id"] == "c1" and kept["project_dir"] == "x"


# --------------------------------------------------------------------------- API: repository resolution


class FakeDb:
    def __init__(self, repos, plan=None, domain="d1"):
        self.repos, self.plan, self.domain = repos, plan or {}, domain

    def query(self, sql, params=()):
        if "FROM CODE.REPO WHERE ENABLED" in sql:
            return [dict(r) for r in self.repos]
        if "FROM KNOWLEDGE.DOMAIN_KNOWLEDGE" in sql:
            return [{"content_json": json.dumps(self.plan)}] if self.plan else []
        if "FROM CORE.WORKFLOW_RUN" in sql:
            return [{"domain_id": self.domain, "run_name": "Orders"}]
        return []


def _repo(rid, domains=None, **kw):
    row = {"repo_id": rid, "name": rid.upper(), "git_url": f"https://github.com/acme/{rid}", "provider": "GITHUB",
           "branch": "main", "git_repository": f"DB.CODE.{rid.upper()}", "api_integration": "GIT", "status": "READY",
           "stats": json.dumps({"dbt_project_roots": [{"name": "p", "root": ""}]}), "domain_ids": json.dumps(domains or []),
           "dbt_project_dir": "", "open_pr": True, "draft_pr": False}
    row.update(kw)
    return row


def _main():
    import app.main as main  # noqa: E402

    return main


def test_resolution_single_several_domain_and_legacy():
    main = _main()
    repo, cands, legacy = main._dbt_repo(FakeDb([_repo("a")]), "d1", {})
    assert repo["repo_id"] == "a" and not legacy
    repo, cands, _ = main._dbt_repo(FakeDb([_repo("a"), _repo("b")]), "d1", {})
    assert repo is None and len(cands) == 2
    repo, _, _ = main._dbt_repo(FakeDb([_repo("a"), _repo("b")]), "d1", {"code_repo_id": "b"})
    assert repo["repo_id"] == "b"
    repo, cands, _ = main._dbt_repo(FakeDb([_repo("a", domains=["other"])]), "d1", {})
    assert repo is None and cands == []
    repo, _, legacy = main._dbt_repo(FakeDb([_repo("a")]), "d1", {"git_repository": "DB.CODEGEN.HAND"})
    assert repo is None and legacy


def test_generate_payload_ignores_browser_origin_and_checks_base(monkeypatch):
    main = _main()
    import app.code_api as code_api

    monkeypatch.setattr(code_api, "_branches", lambda db, fqn, fetch=True: ([{"name": "main", "commit": "1"},
                                                                             {"name": "dev", "commit": "2"}], ""))
    db = FakeDb([_repo("a", dbt_project_dir="analytics")])
    out = main._resolve_dbt_payload(db, "r1", {"origin": "https://github.com/attacker/x", "git_repository": "X.Y.Z",
                                               "allowed_prefixes": ["https://github.com/attacker"], "cut_branch": "feat/a", "push": True})
    assert out["origin"] == "https://github.com/acme/a" and out["git_repository"] == "DB.CODE.A"
    assert out["project_dir"] == "analytics" and out["base_branch"] == "main" and "allowed_prefixes" not in out
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as err:
        main._resolve_dbt_payload(db, "r1", {"base_branch": "gone"})
    assert err.value.status_code == 400 and "dev" in err.value.detail
    with pytest.raises(HTTPException) as err:
        main._resolve_dbt_payload(FakeDb([_repo("a"), _repo("b")]), "r1", {})
    assert err.value.status_code == 409
    gitlab = main._resolve_dbt_payload(FakeDb([_repo("a", provider="GITLAB")]), "r1", {"push": True})
    assert gitlab["push"] is False
    none = main._resolve_dbt_payload(FakeDb([]), "r1", {"push": True, "origin": "https://github.com/x/y"})
    assert none["push"] is False and "origin" not in none
