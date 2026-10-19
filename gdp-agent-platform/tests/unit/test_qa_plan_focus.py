"""The AI test plan with a tester's focus ("Create test cases based on understanding the hierarchy") must run: the
focus text once shadowed the focus(ctx) helper and every plan failed with "'str' object is not callable"."""

from contextlib import contextmanager

from services.qa import procedures as qp

CTX = {"domain_id": "d1", "run_id": None, "target": {"name": "COMPANY_HIERARCHY", "fqn": "DB.S.COMPANY_HIERARCHY"},
       "columns": [{"name": "SOURCE_UNIQUE_ID"}, {"name": "PARENT_ID"}], "lines": [],
       "allowed": ["DB.S.COMPANY_HIERARCHY", "DB.S.COMPANY_CORE"], "sources": {}, "source_columns": {},
       "graph": {}, "business_keys": ["SOURCE_UNIQUE_ID"]}


def test_a_table_plan_with_a_focus_runs_and_passes_the_focus_to_the_prompt(monkeypatch):
    seen = {}

    @contextmanager
    def tool_call(*a, **k):
        class Call:
            summary = ""
        yield Call()

    def knowledge(session, domain_id, run_id, profile_run_id=None, target=None, columns=None):
        seen["knowledge"] = (target, columns)
        return {"rules": [], "profile": [], "checks": []}

    def complete_json(session, prompt, schema, max_tokens=0, stage=None):
        seen["prompt"] = prompt
        return {"tests": [{"title": "Parent exists", "category": "REFERENTIAL", "severity": "HIGH",
                           "sql": "SELECT h.SOURCE_UNIQUE_ID FROM DB.S.COMPANY_HIERARCHY h WHERE h.PARENT_ID IS NULL"}]}, {}, "m"

    monkeypatch.setattr(qp, "_table_ctx", lambda session, tid: CTX)
    monkeypatch.setattr(qp, "table_saved", lambda session, tid: [])
    monkeypatch.setattr(qp, "knowledge", knowledge)
    monkeypatch.setattr(qp, "_code", lambda session, ctx, run_id: {"text": "", "citations": []})
    monkeypatch.setattr(qp, "tool_call", tool_call)
    monkeypatch.setattr(qp, "record_cost", lambda *a, **k: None)
    monkeypatch.setattr(qp, "complete_json", complete_json)
    monkeypatch.setattr(qp, "compile_check", lambda session, sql, ctx: (None, None))

    out = qp.table_plan(None, "t1", "Create test cases based on understanding the company hierarchy")
    assert out["tests"][0]["valid"] and out["model"] == "m"
    assert "Tester focus: Create test cases based on understanding the company hierarchy" in seen["prompt"]
    assert seen["knowledge"] == ("COMPANY_HIERARCHY", ["SOURCE_UNIQUE_ID", "PARENT_ID"])   # focus(ctx) still used
