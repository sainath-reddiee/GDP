from services.common.copilot import allowed_actions, build_prompt, context_keys, parse_page, suggestions

RUN = "14cda6f0-6a8d-4d15-af3b-5312b45c9886"


def test_page_from_route_and_validated_identifiers():
    page = parse_page({"path": f"/runs/{RUN}/soda", "database": "de_demo_prod", "schema": "raw", "table": "x; drop"})
    assert page["run_id"] == RUN and page["stage"] == "SODA" and page["area"] == "run"
    assert page["database"] == "DE_DEMO_PROD" and page["schema"] == "RAW" and "table" not in page
    assert parse_page({"path": f"/runs/{RUN}"})["stage"] == "OVERVIEW"
    assert parse_page({"path": "/sources"})["area"] == "sources"
    assert "run_id" not in parse_page({"path": "/runs/not-a-run/profile"})


def test_suggestions_follow_the_page():
    assert "freshness" in " ".join(suggestions({"stage": "SODA"})).lower()
    assert any("RAW_ORDERS" in s for s in suggestions({"table": "RAW_ORDERS"}))
    assert suggestions({"area": "dashboard"})


def test_only_real_citations_and_navigation_survive():
    ctx = {"RUN": {"run_name": "r"}, "TABLES": [{"key": "TABLE:RAW_ORDERS", "table": "RAW_ORDERS"}],
           "CHECKS": [{"key": "CHECK:abcd1234"}]}
    hits = [{"SOURCE_REFERENCE": "gdp.business_rule.order_total", "TITLE": "t"}]
    keys = context_keys(ctx, hits)
    assert {"RUN", "TABLE:RAW_ORDERS", "CHECK:abcd1234", "gdp.business_rule.order_total"} <= keys
    page = {"run_id": RUN, "database": "DB", "schema": "RAW"}
    actions = allowed_actions([
        {"kind": "open_stage", "label": "Data quality", "target": "soda"},
        {"kind": "open_stage", "label": "Bad", "target": "../../admin"},
        {"kind": "open_table", "label": "Orders", "target": "raw_orders"},
        {"kind": "open_table", "label": "Ghost", "target": "NOT_THERE"},
        {"kind": "delete_everything", "label": "x", "target": "y"},
    ], page, ctx)
    assert actions == [{"kind": "open_stage", "label": "Data quality", "href": f"/runs/{RUN}/soda"},
                       {"kind": "open_table", "label": "Orders", "href": "/sources?mode=snowflake&db=DB&schema=RAW"}]


def test_prompt_carries_context_history_and_rules():
    prompt = build_prompt("Which table is worst?", {"area": "run", "stage": "PROFILING"},
                          {"TABLES": [{"key": "TABLE:RAW_EVENTS", "table": "RAW_EVENTS", "null_pct": 40}]},
                          [{"KNOWLEDGE_ID": "k1", "TITLE": "Events", "CONTENT": "events are append only"}],
                          [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}])
    assert "RAW_EVENTS" in prompt and "events are append only" in prompt and "USER: hi" in prompt
    assert "Never invent tables" in prompt and "open_stage" in prompt
