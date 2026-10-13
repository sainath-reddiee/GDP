"""Copilot change set, plan rail and previews (pure)."""

import pytest

from services.agentic import changeset, plan as rail, preview
from services.governance import policy


def op(path="/api/hub/table-models/x"):
    return [{"method": "PUT", "path": path, "body": {}}]


def test_a_revision_replaces_the_open_draft_for_the_same_asset():
    changes = []
    first = changeset.upsert(changes, changeset.make("TABLE_MODEL:x", "MODEL", "v1", op()))
    first["change_id"] = "c1"
    changeset.upsert(changes, changeset.make("TABLE_MODEL:x", "MODEL", "v2", op()))
    assert len(changes) == 1 and changes[0]["title"] == "v2" and changes[0]["change_id"] == "c1"
    changes[0]["status"] = "APPLIED"
    changeset.upsert(changes, changeset.make("TABLE_MODEL:x", "MODEL", "v3", op()))
    assert len(changes) == 2  # a published change is never overwritten


def test_publishing_order_follows_dependencies():
    changes = []
    for key, kind in [("JIRA:A-1", "JIRA"), ("DQ:T", "DQ"), ("STTM:s", "STTM"), ("TABLE_MODEL:m", "MODEL"),
                      ("INGESTION:S", "INGESTION")]:
        changeset.upsert(changes, changeset.make(key, kind, key, op("/api/x")))
    assert [c["kind"] for c in changeset.ordered(changes)] == ["INGESTION", "MODEL", "STTM", "DQ", "JIRA"]


@pytest.mark.parametrize("path", ["/api/governance/requests/1/approve", "/api/copilot/sessions/x/turn",
                                  "/api/admin/apply", "http://evil/api/x", "/api/../admin"])
def test_a_change_cannot_target_governance_admin_or_itself(path):
    with pytest.raises(AssertionError):
        changeset.make("k", "MODEL", "t", op(path))


def test_workbench_tabs_group_previews():
    changes = [changeset.make("TABLE_MODEL:m", "MODEL", "m", op(), {"model": {"columns": []}, "files": [{"path": "a"}]}),
               changeset.make("QA_PACK:p", "QA", "q", op("/api/qa-packs/p"), {"files": [{"path": "q.sql"}]})]
    tabs = changeset.tabs(changes)
    assert len(tabs["model"]) == 1 and len(tabs["dbt"]) == 1 and len(tabs["quality"]) == 1 and tabs["sttm"] == []


def test_plan_merge_keeps_done_steps_done():
    plan = rail.initial()
    assert rail.merge(plan, "model", "DONE", "drafted")
    assert not rail.merge(plan, "model", "RUNNING")
    assert rail.merge(plan, "model", "RUNNING", force=True)
    assert not rail.merge(plan, "nope", "DONE")


def test_file_and_column_previews():
    f = preview.file_diff("a.sql", "select 1\nfrom t", "select 1, 2\nfrom t")
    assert f["status"] == "changed" and f["added"] == 1 and f["removed"] == 1
    assert preview.file_diff("b.sql", None, "x")["status"] == "added"
    old = {"design": {"entities": [{"entity_name": "T", "attributes": [{"name": "A", "datatype": "NUMBER"},
                                                                       {"name": "B", "datatype": "VARCHAR"}]}]}}
    new = {"design": {"entities": [{"entity_name": "T", "attributes": [{"name": "A", "datatype": "VARCHAR"},
                                                                       {"name": "C", "datatype": "DATE"}]}]}}
    cols = preview.columns(old, new)
    assert cols["counts"] == {"added": 1, "changed": 1, "removed": 1}


def test_copilot_routes_need_ai_use_and_are_never_queued():
    for path in ("/api/copilot/sessions", "/api/copilot/sessions/abc/turn", "/api/copilot/sessions/abc/delete",
                 "/api/copilot/sessions/abc/changes/def/discard"):
        assert policy.privilege_for("POST", path)[0] == "AI.USE"
        assert policy.no_queue("POST", path)
    assert policy.privilege_for("GET", "/api/copilot/sessions")[0] is None
