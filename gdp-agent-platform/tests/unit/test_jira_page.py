"""The Jira page: creating issues as the signed-in person, projects, the open-ticket count, and the Copilot's Jira tools
(new tickets, and comments that report what publishing did). Jira and Snowflake are fakes."""

import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from services.agentic import changeset, publish, tools  # noqa: E402
from services.governance.policy import privilege_for  # noqa: E402
from services.jira import client as jc  # noqa: E402

CONN = {"cloud_id": "c-1", "site_url": "https://team.atlassian.net", "scopes": []}
CFG = {"default_project": "GDPQA"}


class Db:
    user = "ANA"

    def __init__(self, answers=None):
        self.answers, self.executed = dict(answers or {}), []

    def query(self, sql, params=()):
        self.executed.append((sql, params))
        for fragment, found in self.answers.items():
            if fragment in sql:
                return [dict(r) for r in found]
        return []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))


class Jira:
    def __init__(self, **kw):
        self.calls, self.kw = [], kw

    def __getattr__(self, name):
        if name in ("calls", "kw"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            args = args + tuple(kwargs.values())
            self.calls.append((name, args))
            value = self.kw.get(name)
            if isinstance(value, Exception):
                raise value
            return value(*args) if callable(value) else value
        return call


@pytest.fixture
def api(monkeypatch):
    importlib.import_module("app.main")
    import app.jira_api as module

    def use(client, fail=None):
        def fake(db):
            if fail:
                raise fail
            return client, dict(CONN), dict(CFG)
        monkeypatch.setattr(module, "_client", fake)
        module._summary_cache.clear()
        return module
    return use


TYPES = [{"id": "10001", "name": "Task", "subtask": False}, {"id": "10002", "name": "Story", "subtask": False},
         {"id": "10003", "name": "Sub-task", "subtask": True}]


# ---------------------------------------------------------------- routes

def test_an_issue_is_created_as_the_person_by_type_name_and_logged(api):
    client = Jira(issue_types=lambda p: TYPES, create_issue=lambda *a: {"id": "9", "key": "GDPQA-9"})
    module = api(client)
    db = Db()
    out = module.create_issue(module.IssueIn(project_key="gdpqa", issue_type="story", summary="Add tier",
                                             description_markdown="**Why**: loyalty", labels=["data model", " "],
                                             idempotency_key="k1"), db)
    assert out["key"] == "GDPQA-9" and out["created"] and out["url"] == "https://team.atlassian.net/browse/GDPQA-9"
    project, type_id, summary_text, description, labels, extra = next(a for n, a in client.calls if n == "create_issue")
    assert extra is None   # no sprint asked
    assert (project, type_id, summary_text, labels) == ("GDPQA", "10002", "Add tier", ["data model"])
    assert "loyalty" in json.dumps(description)
    logged = [p for s, p in db.executed if "INTO CORE.ACTION_LOG" in s]
    assert logged and logged[0][4] == "CREATE" and logged[0][-1] == "k1"


def test_a_repeated_create_returns_the_first_issue(api):
    client = Jira()
    module = api(client)
    db = Db({"IDEMPOTENCY_KEY = %s": [{"issue_key": "GDPQA-9", "detail": json.dumps({"url": "u", "summary": "Add tier"})}]})
    out = module.create_issue(module.IssueIn(project_key="GDPQA", issue_type="Task", summary="Add tier", idempotency_key="k1"), db)
    assert out == {"key": "GDPQA-9", "url": "u", "created": False, "existing": True, "summary": "Add tier", "project": None}
    assert client.calls == []


def test_an_unknown_or_sub_task_type_is_refused_with_the_choices(api):
    module = api(Jira(issue_types=lambda p: TYPES))
    with pytest.raises(HTTPException) as err:
        module.create_issue(module.IssueIn(project_key="GDPQA", issue_type="Sub-task", summary="x"), Db())
    assert err.value.status_code == 400 and "Task, Story" in err.value.detail


def test_projects_and_the_open_ticket_count(api):
    client = Jira(projects=lambda q: [{"id": "1", "key": "GDPQA", "name": "GDP QA"}],
                  search=lambda jql, page_size, max_pages: [{"key": "GDPQA-1"}, {"key": "GDPQA-2"}])
    module = api(client)
    assert module.projects("", Db()) == {"projects": [{"id": "1", "key": "GDPQA", "name": "GDP QA"}], "default": "GDPQA"}
    first = module.jira_summary(Db())
    assert first["connected"] and first["open_mine"] == 2
    module.jira_summary(Db())
    assert sum(1 for n, _ in client.calls if n == "search") == 1   # cached a minute per person
    jql = next(a for n, a in client.calls if n == "search")[0]
    assert "assignee = currentUser()" in jql and "statusCategory != Done" in jql and "project = GDPQA" in jql


def test_the_count_never_fails_when_jira_is_not_connected(api):
    module = api(Jira(), fail=HTTPException(428, "Connect your Jira account first."))
    assert module.jira_summary(Db()) == {"connected": False, "open_mine": 0, "reason": "Connect your Jira account first."}


def test_projects_client_call_is_scoped_to_create():
    class Http:
        def __init__(self):
            self.calls = []

        def __call__(self, method, url, headers, body):
            self.calls.append(url)
            return 200, {"values": [{"id": 1, "key": "A", "name": "Alpha"}]}, {}
    http = Http()
    assert jc.JiraClient(http, "11111111-2222-3333-4444-555555555555", "t").projects("al") == [{"id": "1", "key": "A", "name": "Alpha"}]
    assert "project/search?action=create" in http.calls[0] and "query=al" in http.calls[0]


def test_governance_for_the_new_routes():
    assert privilege_for("POST", "/api/jira/issues")[0] == "JIRA.WRITE"
    assert privilege_for("POST", "/api/jira/issues/A-1/comment")[0] == "JIRA.WRITE"
    for path in ("/api/jira/projects", "/api/jira/summary"):
        assert privilege_for("GET", path)[0] == "JIRA.READ"


# ---------------------------------------------------------------- the Copilot's Jira tools

class Api:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def __call__(self, method, path, body=None, params=None):
        self.calls.append((method, path, body))
        found = self.routes.get((method, path))
        return found if found else (404, {"detail": "no route"})


def run(coro):
    return asyncio.run(coro)


def test_create_jira_issue_is_only_drafted_with_a_checked_type():
    api = Api({("GET", "/api/jira/projects/GDPQA/issue-types"): (200, {"types": TYPES})})
    out = run(tools.create_jira_issue(api, {"project": "gdpqa", "issue_type": "story", "summary": "Add  tier",
                                            "description_markdown": "why", "labels": ["gdp"]}))
    op = out["change"]["operations"][0]
    assert op["method"] == "POST" and op["path"] == "/api/jira/issues"
    assert op["body"]["issue_type"] == "Story" and op["body"]["summary"] == "Add tier" and op["body"]["idempotency_key"]
    assert [c[1] for c in api.calls] == ["/api/jira/projects/GDPQA/issue-types"]   # nothing created
    with pytest.raises(tools.ToolError, match="no issue type"):
        run(tools.create_jira_issue(api, {"project": "GDPQA", "issue_type": "Epic", "summary": "x"}))
    with pytest.raises(tools.ToolError):
        run(tools.create_jira_issue(api, {"project": "bad key", "summary": "x"}))


def test_the_comment_reports_what_publishing_did():
    trans = {("GET", "/api/jira/issues/GDPQA-1/transitions"): (200, {"transitions": []})}
    model = changeset.make("STTM:c1", "STTM", "Mapping DIM_CUSTOMER", [{"method": "POST", "path": "/api/hub/sttm/c1/edit", "body": {}}])
    pr = changeset.make("GIT:x", "GIT", "Pull request for NATION", [{"method": "POST", "path": "/api/hub/table-models/t/versions/4/publish", "body": {}}])
    drafted = run(tools.sync_jira_status(Api(trans), {"key": "GDPQA-1", "comment_markdown": "Done the work."},
                                         {"changes": [{**model, "status": "PROPOSED"}]}))
    jira = drafted["change"]
    assert publish.PUBLISH_SUMMARY in jira["operations"][0]["body"]["markdown"]
    assert publish.PUBLISH_SUMMARY not in json.dumps(jira["preview"])   # the preview explains it instead
    changes = []
    for cid, c in (("c1", model), ("c2", pr), ("c3", jira)):
        changes.append({**c, "change_id": cid, "seq": len(changes) + 1})
    api = Api({("POST", "/api/hub/sttm/c1/edit"): (202, {"pending_approval": True, "request_id": "r1234567890", "approver_role": "STTM_APPROVER"}),
               ("POST", "/api/hub/table-models/t/versions/4/publish"): (200, {"status": "PUBLISHED", "pull_request": {"url": "https://gh/pr/7"}}),
               ("POST", "/api/jira/issues/GDPQA-1/comment"): (200, {"id": "1"})})
    asyncio.run(_drain(publish.run(api, changes)))
    posted = next(b for m, p, b in api.calls if p.endswith("/comment"))["markdown"]
    assert posted.startswith("Done the work.") and "{publish_summary}" not in posted
    assert "Mapping DIM_CUSTOMER: sent for approval (request r1234567)" in posted
    assert "Pull request for NATION: applied ([pull request](https://gh/pr/7))" in posted


def test_a_lone_comment_has_no_report():
    api = Api({("GET", "/api/jira/issues/A-1/transitions"): (200, {"transitions": []})})
    out = run(tools.sync_jira_status(api, {"key": "A-1", "comment_markdown": "hello"}, {"changes": []}))
    assert out["change"]["operations"][0]["body"]["markdown"] == "hello"


async def _drain(gen):
    return [e async for e in gen]


# ---------------------------------------------------------------- sprints

def test_sprint_choices_skip_kanban_and_other_projects(api):
    client = Jira(boards=lambda q: [{"id": 1, "name": "GDP board", "type": "scrum", "project_key": "GDPQA"},
                                    {"id": 2, "name": "Flow", "type": "kanban", "project_key": "GDPQA"},
                                    {"id": 3, "name": "Other", "type": "scrum", "project_key": "AI"}],
                  sprints=lambda board, states: [{"id": 7, "name": "Sprint 7", "state": "active"},
                                                 {"id": 8, "name": "Sprint 8", "state": "future"}],
                  sprint_field=lambda: "customfield_10020")
    module = api(client)
    module._sprint_fields.clear()
    out = module.sprint_choices("", Db())
    assert out["project"] == "GDPQA" and out["sprint_field"] == "customfield_10020"
    assert [(s["id"], s["board"]) for s in out["sprints"]] == [(7, "GDP board"), (8, "GDP board")]
    assert [a[0] for n, a in client.calls if n == "sprints"] == [1]   # only the project's scrum board


def test_sprint_moves_set_the_sprint_field_per_issue_and_report_failures(api):
    edits = []

    def edit(key, fields):
        if key == "GDPQA-2":
            raise jc.JiraError(400, "Field 'customfield_10020' cannot be set")
        edits.append((key, fields))
    module = api(Jira(sprint_field=lambda: "customfield_10020", edit_issue=edit))
    module._sprint_fields.clear()
    out = module.sprint_moves(module.SprintMoveIn(keys=["gdpqa-1", "GDPQA-2", "GDPQA-1"], sprint_id=7), Db())
    assert out["moved"] == ["GDPQA-1"] and edits == [("GDPQA-1", {"customfield_10020": 7})]
    assert out["failed"][0]["key"] == "GDPQA-2" and "edit screen" in out["failed"][0]["error"]
    backlog = module.sprint_moves(module.SprintMoveIn(keys=["GDPQA-1"]), Db())
    assert backlog["moved"] == ["GDPQA-1"] and edits[-1] == ("GDPQA-1", {"customfield_10020": None})


def test_create_with_a_sprint_sets_the_field(api):
    client = Jira(issue_types=lambda p: TYPES, create_issue=lambda *a: {"id": "9", "key": "GDPQA-9"},
                  sprint_field=lambda: "customfield_10020")
    module = api(client)
    module._sprint_fields.clear()
    module.create_issue(module.IssueIn(project_key="GDPQA", issue_type="Task", summary="x", sprint_id=7), Db())
    assert next(a for n, a in client.calls if n == "create_issue")[-1] == {"customfield_10020": 7}


def test_copilot_sprint_tools_draft_only():
    choices = (200, {"project": "GDPQA", "sprint_field": "customfield_10020",
                     "sprints": [{"id": 7, "name": "Sprint 7", "state": "active", "board": "B"}]})
    api = Api({("GET", "/api/jira/sprint-choices"): choices,
               ("GET", "/api/jira/projects/GDPQA/issue-types"): (200, {"types": TYPES})})
    moved = run(tools.move_to_sprint(api, {"keys": ["gdpqa-1", "GDPQA-2"], "sprint": "sprint 7"}))
    assert moved["change"]["operations"][0]["body"] == {"keys": ["GDPQA-1", "GDPQA-2"], "sprint_id": 7}
    back = run(tools.move_to_sprint(api, {"keys": ["GDPQA-1"], "sprint": "backlog"}))
    assert back["change"]["operations"][0]["body"]["sprint_id"] is None
    with pytest.raises(tools.ToolError, match="no active or future sprint"):
        run(tools.move_to_sprint(api, {"keys": ["GDPQA-1"], "sprint": "Sprint 99"}))
    created = run(tools.create_jira_issue(api, {"project": "GDPQA", "summary": "x", "sprint": "7"}))
    assert created["change"]["operations"][0]["body"]["sprint_id"] == 7
    assert all(m == "GET" for m, _, _ in api.calls)   # nothing moved or created
