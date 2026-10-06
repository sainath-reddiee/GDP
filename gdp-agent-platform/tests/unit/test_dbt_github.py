import pytest

from services.dbt.github import PublishError, check_branch, explain, parse_origin, preflight, publish, tree_entries


class FakeGitHub:
    def __init__(self, head_exists=False, open_pr=None, same_tree=False):
        self.calls = []
        self.head_exists, self.open_pr, self.same_tree = head_exists, open_pr, same_tree

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        if method == "GET" and path == "/repos/o/r":
            return 200, {"full_name": "o/r", "default_branch": "main", "permissions": {"push": True}}
        if method == "GET" and path.endswith("/git/ref/heads/main"):
            return 200, {"object": {"sha": "base1"}}
        if method == "GET" and "/git/ref/heads/feat/gdp-run" in path:
            return (200, {"object": {"sha": "head1"}}) if self.head_exists else (404, {"message": "Not Found"})
        if method == "GET" and "/git/commits/" in path:
            return 200, {"tree": {"sha": "tree-" + path.rsplit("/", 1)[1]}}
        if method == "POST" and path.endswith("/git/trees"):
            return 201, {"sha": body["base_tree"] if self.same_tree else "newtree"}
        if method == "POST" and path.endswith("/git/commits"):
            return 201, {"sha": "commit2"}
        if path.endswith("/git/refs") or "/git/refs/heads/" in path:
            return (201 if method == "POST" else 200), {}
        if method == "GET" and "/pulls?" in path:
            return 200, [self.open_pr] if self.open_pr else []
        if method == "POST" and path.endswith("/pulls"):
            return 201, {"number": 7, "html_url": "https://github.com/o/r/pull/7", "state": "open", "draft": False}
        return 500, {"message": f"unexpected {method} {path}"}


FILES = {"models/marts/addresses.sql": "select 1", "release/workspace.json": "{}", "dbt_project.yml": "name: x"}


def test_parse_origin_variants():
    assert parse_origin("https://github.com/sainath-reddiee/dbt_demo") == ("sainath-reddiee", "dbt_demo")
    assert parse_origin("https://github.com/Bansilal9900/DBT-DEMO.git") == ("Bansilal9900", "DBT-DEMO")
    assert parse_origin("git@github.com:org/repo.git") == ("org", "repo")
    with pytest.raises(ValueError):
        parse_origin("https://gitlab.com/org/repo")


def test_branch_and_tree_validation():
    assert check_branch("feat/gdp-test1123") == "feat/gdp-test1123"
    for bad in ("feat/../x", "/x", "a b", "x.lock", "feat//x"):
        with pytest.raises(ValueError):
            check_branch(bad)
    paths = [e["path"] for e in tree_entries({**FILES, "../evil": "x"})]
    assert paths == ["dbt_project.yml", "models/marts/addresses.sql"]


def test_new_branch_keeps_base_tree_and_opens_pr():
    gh = FakeGitHub()
    out = publish(gh, "https://github.com/o/r", "main", "feat/gdp-run", FILES, "t", "b", "m")
    tree_call = next(c for c in gh.calls if c[1].endswith("/git/trees"))
    assert tree_call[2]["base_tree"] == "tree-base1"
    commit_call = next(c for c in gh.calls if c[1].endswith("/git/commits") and c[0] == "POST")
    assert commit_call[2]["parents"] == ["base1"]
    ref_call = next(c for c in gh.calls if c[1].endswith("/git/refs"))
    assert ref_call[2] == {"ref": "refs/heads/feat/gdp-run", "sha": "commit2"}
    assert out["status"] == "PUBLISHED" and out["branch_created"] and out["pull_request"]["number"] == 7
    assert out["files_pushed"] == 2


def test_existing_branch_appends_commit_and_reuses_open_pr():
    gh = FakeGitHub(head_exists=True, open_pr={"number": 3, "html_url": "u", "state": "open"})
    out = publish(gh, "https://github.com/o/r", "main", "feat/gdp-run", FILES, "t", "b", "m")
    assert any(c[0] == "PATCH" for c in gh.calls)
    assert not any(c[0] == "POST" and c[1].endswith("/pulls") for c in gh.calls)
    assert out["pull_request"] == {"number": 3, "url": "u", "created": False, "state": "open", "draft": None}


def test_no_changes_skips_pr_and_errors_surface():
    out = publish(FakeGitHub(same_tree=True), "https://github.com/o/r", "main", "feat/gdp-run", FILES, "t", "b", "m")
    assert out["status"] == "NO_CHANGES" and out["pull_request"]["created"] is False

    def denied(method, path, body):
        return 403, {"message": "Resource not accessible by personal access token"}
    with pytest.raises(PublishError, match="read repository failed \\(403\\)") as caught:
        publish(denied, "https://github.com/o/r", "main", "feat/gdp-run", FILES, "t", "b", "m")
    assert explain(caught.value, "https://github.com/o/r")["status"] == "TOKEN_SCOPE"
    with pytest.raises(ValueError):
        publish(FakeGitHub(), "https://github.com/o/r", "main", "main", FILES, "t", "b", "m")


def test_preflight_and_write_scope_errors():
    gh = FakeGitHub()
    assert preflight(gh, "https://github.com/o/r")["push"] is True

    def tree_denied(method, path, body):
        if path == "/repos/o/r":
            return 200, {"full_name": "o/r", "permissions": {"push": True}}
        if path.endswith("/git/trees"):
            return 403, {"message": "Resource not accessible by personal access token"}
        return gh(method, path, body)
    with pytest.raises(PublishError) as caught:
        publish(tree_denied, "https://github.com/o/r", "main", "feat/gdp-run", FILES, "t", "b", "m")
    info = explain(caught.value, "https://github.com/o/r")
    assert info["status"] == "TOKEN_SCOPE" and "Contents: Read and write" in info["detail"]
    assert explain(PublishError("x", 401, {"message": "Bad credentials"}))["status"] == "AUTH"

    def missing(method, path, body):
        return 404, {"message": "Not Found"}
    with pytest.raises(PublishError) as caught:
        preflight(missing, "https://github.com/o/r")
    assert explain(caught.value, "https://github.com/o/r")["status"] == "TOKEN_SCOPE"
