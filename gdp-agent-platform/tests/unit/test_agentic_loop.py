"""Copilot turn loop (services/agentic/loop.py) with a scripted model and a fake API."""

import asyncio
import json

from services.agentic import loop, plan as rail

SPEC = {"design": {"entities": [{"entity_name": "DIM_CUSTOMER", "attributes": [
    {"name": "CUSTOMER_ID", "datatype": "NUMBER", "nullable": False, "is_pk": True}]}]}}


class FakeApi:
    def __init__(self):
        self.calls = []

    async def __call__(self, method, path, body=None, params=None):
        self.calls.append((method, path))
        if method == "GET" and path == "/api/hub/table-models/tm1":
            return 200, {"model": {"table_model_id": "tm1", "table_name": "DIM_CUSTOMER", "etag": "e1",
                                   "table_type": "STANDARD", "domain_name": "COMPANY"},
                         "current": {"spec": SPEC, "ddl": "create table dim_customer (customer_id number);", "dbt": {}}}
        if method == "POST" and path == "/api/hub/table-models/tm1/render":
            return 200, {"spec": body["spec"], "issues": [], "ddl": "create table dim_customer (customer_id number, loyalty_tier varchar);",
                         "dbt": {"models/dim_customer.sql": "select 1"}}
        return 404, {"detail": "not found"}


def script(*outputs):
    queue = list(outputs)

    async def complete(prompt, schema):
        assert "Return JSON matching the schema" in prompt
        return queue.pop(0), {"total_tokens": 10}, "test-model"

    return complete


def run(complete, api, state):
    async def go():
        return [e async for e in loop.run_turn(complete, api, state)]
    return asyncio.run(go())


def state():
    return {"title": "t", "jira_key": None, "plan": rail.initial(), "changes": [],
            "events": [{"kind": "USER", "content": {"text": "add loyalty tier"}}]}


def step(**kw):
    return {"thought": "", "tool_calls": [], "reply": "", "done": False, **kw}


def call(tool, **args):
    return {"tool": tool, "args_json": json.dumps(args)}


def test_drafts_a_change_but_never_applies_it():
    api, s = FakeApi(), state()
    events = run(script(
        step(thought="update the model", tool_calls=[call("update_table_model", table_model_id="tm1", operations=[
            {"op": "add", "column": "LOYALTY_TIER", "datatype": "VARCHAR"}])]),
        step(reply="Drafted the model change.", done=True)), api, s)
    kinds = [e["type"] for e in events]
    assert kinds[-1] == "done" and "change" in kinds and "reply" in kinds
    assert ("PUT", "/api/hub/table-models/tm1") not in api.calls          # the save is only drafted
    change = s["changes"][0]
    assert change["operations"][0]["method"] == "PUT" and change["status"] == "PROPOSED"
    assert change["preview"]["model"]["counts"]["added"] == 1
    steps = rail.by_key(s["plan"])
    assert steps["model"]["status"] == "DONE" and steps["dryrun"]["status"] == "DONE"
    assert steps["approval"]["status"] == "RUNNING"


def test_stops_on_a_question():
    events = run(script(step(ask_user={"question": "Which primary key?", "fields": [
        {"name": "pk", "label": "Primary key", "type": "select", "options": ["ID", "CODE"]}]}),
        step(reply="never reached", done=True)), FakeApi(), state())
    ask = next(e for e in events if e["type"] == "ask")
    assert ask["fields"][0]["options"] == ["ID", "CODE"]
    assert not any(e["type"] == "reply" and e["text"] == "never reached" for e in events)


def test_unknown_tool_and_bad_arguments_are_reported_not_raised():
    events = run(script(step(tool_calls=[call("drop_everything"), {"tool": "get_table_model", "args_json": "{nope"},
                                         call("get_table_model", table_model_id="tm1", extra=1)]),
                        step(reply="ok", done=True)), FakeApi(), state())
    errors = [e["error"] for e in events if e["type"] == "tool" and not e["ok"]]
    assert len(errors) == 3
    assert "no tool named" in errors[0] and "not valid JSON" in errors[1] and "unknown argument" in errors[2]


def test_step_budget_ends_the_turn():
    looping = [step(tool_calls=[call("get_table_model", table_model_id="tm1")]) for _ in range(loop.MAX_STEPS + 3)]
    api = FakeApi()
    events = run(script(*looping), api, state())
    assert sum(1 for e in events if e["type"] == "tool") <= loop.MAX_TOOL_CALLS
    assert events[-1]["type"] == "done"
    assert any(e["type"] == "reply" for e in events)


def test_model_failure_is_an_error_event():
    async def broken(prompt, schema):
        raise RuntimeError("cortex down")

    events = run(broken, FakeApi(), state())
    assert events[0]["type"] == "error" and "cortex down" in events[0]["text"]
    assert events[-1]["type"] == "done"
