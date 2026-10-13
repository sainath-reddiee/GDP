"""Copilot tools (services/agentic/tools.py) against a fake API."""

import asyncio

import pytest

from services.agentic import tools
from services.agentic.tools import ToolError


def run(coro):
    return asyncio.run(coro)


class Api:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def __call__(self, method, path, body=None, params=None):
        self.calls.append((method, path, body))
        handler = self.routes.get((method, path))
        if handler is None:
            return 404, {"detail": "no route"}
        return handler(body) if callable(handler) else handler


def test_argument_validation():
    tool = tools.TOOLS["get_table_model"]
    assert tools.validate(tool, {"table_model_id": "abc"}) == {"table_model_id": "abc"}
    with pytest.raises(ToolError):
        tools.validate(tool, {})
    with pytest.raises(ToolError):
        tools.validate(tool, {"table_model_id": "abc", "sql": "drop"})
    with pytest.raises(ToolError):
        tools.validate(tool, {"table_model_id": 5})


def test_every_tool_is_described_with_its_step():
    described = {t["name"] for t in tools.describe()}
    assert {"update_table_model", "generate_sttm_mapping", "register_qa_sql_pack", "register_soda_checks",
            "onboard_source_config", "sync_jira_status", "fetch_domain_context", "fetch_jira_issue"} <= described
    assert all(t.step in ("intake", "context", "model", "sttm", "quality", "publish") for t in tools.TOOLS.values())


def test_soda_checks_are_parsed_and_drafted_per_check():
    def parse(body):
        if "bad" in body["dsl"]:
            return 200, {"ok": False, "problems": ["unknown check"]}
        return 200, {"ok": True, "headline": "no missing values", "check": {}}

    api = Api({("POST", "/api/dq/parse"): parse})
    out = run(tools.register_soda_checks(api, {"table": "DB.SILVER.DIM_CUSTOMER", "checks": [
        {"column": "LOYALTY_TIER", "dsl": "missing_count(LOYALTY_TIER) = 0", "severity": "FAIL"},
        {"column": "X", "dsl": "bad check"}]}))
    change = out["change"]
    assert change["kind"] == "DQ" and len(change["operations"]) == 1
    assert change["operations"][0]["path"] == "/api/dq/catalog"
    assert out["summary"]["rejected"][0]["problems"] == ["unknown check"]
    assert all(m == "POST" and p == "/api/dq/parse" for m, p, _ in api.calls)   # nothing was saved


def test_soda_table_must_be_fully_qualified():
    with pytest.raises(ToolError):
        run(tools.register_soda_checks(Api({}), {"table": "DIM_CUSTOMER", "checks": [{"dsl": "row_count > 0"}]}))


def test_jira_transition_is_resolved_by_name_and_only_drafted():
    api = Api({("GET", "/api/jira/issues/GDP-7/transitions"): (200, {"transitions": [
        {"id": "31", "name": "Start review", "to": "In Review"}, {"id": "41", "name": "Done", "to": "Done"}]})})
    out = run(tools.sync_jira_status(api, {"key": "gdp-7", "comment_markdown": "Drafted", "transition": "in review"}))
    ops = out["change"]["operations"]
    assert [o["path"] for o in ops] == ["/api/jira/issues/GDP-7/comment", "/api/jira/issues/GDP-7/transition"]
    assert ops[1]["body"] == {"transition_id": "31"}
    assert api.calls == [("GET", "/api/jira/issues/GDP-7/transitions", None)]
    with pytest.raises(ToolError):
        run(tools.sync_jira_status(api, {"key": "GDP-7", "comment_markdown": "x", "transition": "Closed"}))


def test_invalid_ingestion_config_asks_instead_of_drafting():
    api = Api({("POST", "/api/hub/ingestion-control/validate"): (200, {"valid": False, "errors": {"source.source_name": "required"}})})
    out = run(tools.onboard_source_config(api, {"source": {}}))
    assert "change" not in out and out["summary"]["errors"] == {"source.source_name": "required"}


def test_approval_responses_are_never_treated_as_success():
    api = Api({("GET", "/api/hub/table-models/tm1"): (202, {"pending_approval": True, "approver_role": "X"})})
    with pytest.raises(ToolError, match="needs approval"):
        run(tools.get_table_model(api, {"table_model_id": "tm1"}))


def test_ids_in_paths_are_checked():
    with pytest.raises(ToolError):
        run(tools.get_table_model(Api({}), {"table_model_id": "../admin"}))
