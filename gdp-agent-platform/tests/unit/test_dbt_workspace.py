import pytest

from services.common.models import parse_show_models
from services.dbt.enhance import parse_complete
from services.dbt.procedures import merge_branch_plan
from services.dbt.procedures import POST_STTM
from services.dbt.workspace import (
    latest_branch, list_repo_branches, merge_skeleton, origin_allowed,
    parse_allowed_prefixes, parse_git_repos, parse_integrations,
    parse_listed_branches, safe_fqn,
)


def test_origin_allowed_matches_prefix():
    prefixes = ["https://github.com/anblicks/", "https://gitlab.com/gdp/"]
    assert origin_allowed("https://github.com/anblicks/gdp-dbt.git", prefixes)
    assert not origin_allowed("https://github.com/other/repo.git", prefixes)
    assert origin_allowed("https://example.com/x", []) is True
    assert origin_allowed("", prefixes) is False


def test_merge_skeleton_keeps_foreign_files():
    merged = merge_skeleton(
        {"macros/existing.sql": "keep", "models/marts/gdp/old.sql": "old"},
        {"models/marts/gdp/dim_customer.sql": "new", "release/branch.json": "{}"},
    )
    assert merged["macros/existing.sql"] == "keep"
    assert merged["models/marts/gdp/dim_customer.sql"] == "new"
    assert merged["models/marts/gdp/old.sql"] == "old"


def test_parse_integrations_and_prefixes():
    integrations = parse_integrations([
        {"name": "GIT_INT", "type": "GIT_HTTPS_API", "enabled": "true"},
        {"name": "S3", "type": "STORAGE", "enabled": "true"},
    ])
    assert [i["name"] for i in integrations] == ["GIT_INT"]
    prefixes = parse_allowed_prefixes([
        {"property": "API_ALLOWED_PREFIXES", "property_value": "['https://github.com/org/', 'https://gitlab.com/']"},
    ])
    assert "https://github.com/org/" in prefixes


def test_parse_git_repos_fqn():
    repos = parse_git_repos([{
        "name": "GDP_DBT", "database_name": "DEV_AI_PLATFORM", "schema_name": "CODEGEN",
        "origin": "https://github.com/org/gdp-dbt.git", "api_integration": "GIT_INT",
    }])
    assert repos[0]["fqn"] == "DEV_AI_PLATFORM.CODEGEN.GDP_DBT"


def test_safe_fqn_quotes_hyphenated_repo():
    assert safe_fqn("DEV_GDP_SILVER_DB.PUBLIC.DBT-DEMO") == 'DEV_GDP_SILVER_DB.PUBLIC."DBT-DEMO"'
    assert safe_fqn('DEV_GDP_SILVER_DB.PUBLIC."DBT-DEMO"') == 'DEV_GDP_SILVER_DB.PUBLIC."DBT-DEMO"'


def test_list_repo_branches_quotes_hyphenated_repo():
    sqls: list[str] = []

    def execute(sql: str):
        sqls.append(sql)
        return []

    result = list_repo_branches(execute, "DEV_GDP_SILVER_DB.PUBLIC.DBT-DEMO", True)
    quoted = 'DEV_GDP_SILVER_DB.PUBLIC."DBT-DEMO"'
    assert result["repo"] == quoted
    assert f"ALTER GIT REPOSITORY {quoted} FETCH" in sqls
    assert f"SHOW GIT BRANCHES IN GIT REPOSITORY {quoted}" in sqls
    assert f"LIST @{quoted}/branches/" in sqls


def test_safe_fqn_rejects_injection():
    with pytest.raises(AssertionError):
        safe_fqn("CODEGEN.FOO; DROP")


def test_merge_branch_plan_keeps_prior_when_request_is_empty():
    prior = {
        "base_branch": "develop",
        "cut_branch": "feat/gdp-demo",
        "origin": "https://github.com/sainath-reddice/dbt_demo.git",
        "git_repository": "DEV_AI_PLATFORM.CODEGEN.DBT_DEMO",
        "push": True,
        "fetch_skeleton": True,
    }
    plan = merge_branch_plan({}, prior, "test", "run-1")
    assert plan["base_branch"] == "develop"
    assert plan["git_repository"] == "DEV_AI_PLATFORM.CODEGEN.DBT_DEMO"
    assert plan["push"] is True
    assert plan["origin"].endswith("dbt_demo.git")


def test_merge_branch_plan_ignores_null_request_fields():
    plan = merge_branch_plan(
        {"origin": None, "git_repository": None, "push": True, "cut_branch": "feat/gdp-test"},
        {"origin": "https://github.com/org/repo.git", "git_repository": "CODEGEN.REPO"},
        "test",
        "run-1",
    )
    assert plan["origin"] == "https://github.com/org/repo.git"
    assert plan["git_repository"] == "CODEGEN.REPO"
    assert plan["cut_branch"] == "feat/gdp-test"
    assert plan["push"] is True


def test_parse_show_models_and_complete():
    models = parse_show_models([
        {"name": "MY_LLAMA", "type": "CORTEX"},
        {"NAME": "claude-account", "CATEGORY": "MODEL"},
    ], "account")
    assert {m["name"] for m in models} == {"MY_LLAMA", "claude-account"}
    assert next(m for m in models if m["name"] == "MY_LLAMA")["family"] == "llama"
    parsed = parse_complete({
        "model": "llama3.1-70b",
        "structured_output": [{"raw_message": {"content": "select 1", "rationale": "ok", "summary": "noop"}}],
        "usage": {"total_tokens": 9},
    }, "fallback")
    assert parsed["content"] == "select 1"
    assert parsed["model"] == "llama3.1-70b"


def test_listed_branches_pick_latest():
    branches = parse_listed_branches([
        {"name": "CODEGEN.DBT/branches/main/dbt_project.yml", "last_modified": "2026-01-01"},
        {"name": "CODEGEN.DBT/branches/feat/gdp-old/models/x.sql", "last_modified": "2026-03-01"},
        {"name": "CODEGEN.DBT/branches/develop/README.md", "last_modified": "2026-04-01"},
    ])
    names = {b["name"] for b in branches}
    assert names == {"main", "feat/gdp-old", "develop"}
    assert latest_branch(branches) == "develop"


def test_generate_dbt_allows_validation_pending():
    assert "VALIDATION_PENDING" in POST_STTM
    assert "SODA_REVIEW" in POST_STTM
