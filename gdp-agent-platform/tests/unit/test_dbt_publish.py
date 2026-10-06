import pytest

from services.dbt.publish import procedure_sql, setup_sql
from services.dbt.workspace import discover, grant_hint, quote_exact, read_repo_text


def test_publisher_ddl_binds_eai_and_secret():
    ddl = procedure_sql("DEV_AI_PLATFORM", "@DEV_AI_PLATFORM.CORE.CODE_STAGE/services-abc.zip",
                        "GDP_GITHUB_ACCESS", "DEV_AI_PLATFORM.CODEGEN.GITHUB_TOKEN")
    assert "EXTERNAL_ACCESS_INTEGRATIONS = (GDP_GITHUB_ACCESS)" in ddl
    assert "SECRETS = ('github_token' = DEV_AI_PLATFORM.CODEGEN.GITHUB_TOKEN)" in ddl
    with pytest.raises(AssertionError):
        procedure_sql("DB", "@x/y.zip", "EAI; DROP TABLE X", "S")


def test_setup_sql_never_contains_token():
    sql = setup_sql("DEV_AI_PLATFORM", "GDP_GITHUB_ACCESS", "DEV_AI_PLATFORM.CODEGEN.GITHUB_TOKEN", True)
    assert any("VALUE_LIST = ('api.github.com')" in s for s in sql)
    assert any("SECRET_STRING = '<github token>'" in s for s in sql)
    assert len(setup_sql("D", "E", "S", False)) == 2


def test_quote_exact_keeps_case_and_grant_hint():
    assert quote_exact("AWS_AUTOPILOT_API_INTEGRATION_stackavln") == '"AWS_AUTOPILOT_API_INTEGRATION_stackavln"'
    error = "003001 (42501): Insufficient privileges to operate on Integration 'GITHUB_API_INTEGRATION'."
    assert grant_hint(error, "SYSADMIN") == 'GRANT USAGE ON INTEGRATION "GITHUB_API_INTEGRATION" TO ROLE SYSADMIN;'
    assert grant_hint("syntax error", "SYSADMIN") is None


def test_discover_keeps_git_integrations_and_flags_unusable_repos():
    responses = {
        "SELECT CURRENT_ROLE() AS ROLE": [{"ROLE": "SYSADMIN"}],
        "SHOW API INTEGRATIONS": [
            {"name": "AWS_x", "type": "EXTERNAL_API"},
            {"name": "SAINATH_DBT_API_INTEGRATION", "type": "EXTERNAL_API"},
            {"name": "GITHUB_API_INTEGRATION", "type": "EXTERNAL_API"},
        ],
        'DESC INTEGRATION "AWS_x"': [{"property": "API_PROVIDER", "property_value": "AWS_API_GATEWAY"}],
        'DESC INTEGRATION "SAINATH_DBT_API_INTEGRATION"': [
            {"property": "API_PROVIDER", "property_value": "GIT_HTTPS_API"},
            {"property": "API_ALLOWED_PREFIXES", "property_value": "https://github.com/sainath-reddiee/dbt_demo"},
        ],
        "SHOW GIT REPOSITORIES IN ACCOUNT": [{"name": "DBT-DEMO", "database_name": "DEV_GDP_SILVER_DB",
                                              "schema_name": "PUBLIC", "origin": "https://github.com/B/DBT-DEMO",
                                              "api_integration": "GITHUB_API_INTEGRATION"}],
    }

    def execute(sql):
        if sql == 'DESC INTEGRATION "GITHUB_API_INTEGRATION"':
            raise RuntimeError("Insufficient privileges to operate on Integration 'GITHUB_API_INTEGRATION'")
        return responses.get(sql, [])

    out = discover(execute)
    names = {i["name"]: i for i in out["integrations"]}
    assert "AWS_x" not in names
    assert names["SAINATH_DBT_API_INTEGRATION"]["usable"]
    assert names["SAINATH_DBT_API_INTEGRATION"]["allowed_prefixes"] == ["https://github.com/sainath-reddiee/dbt_demo"]
    assert names["GITHUB_API_INTEGRATION"]["usable"] is False
    repo = out["git_repositories"][0]
    assert repo["usable"] is False and "GRANT USAGE ON INTEGRATION \"GITHUB_API_INTEGRATION\" TO ROLE SYSADMIN" in repo["grant_sql"]
    assert out["capabilities"]["github_publish"] is False


def test_read_repo_text_prefers_whole_file_format():
    seen = []

    def read(sql):
        seen.append(sql)
        return ["a, b\nc"]
    assert read_repo_text(read, "DB.S.R", "main", "models/x.sql") == "a, b\nc"
    assert "FILE_FORMAT => 'CODEGEN.RAW_TEXT_FORMAT'" in seen[0]
