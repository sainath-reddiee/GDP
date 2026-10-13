"""One ticket list (services/tickets.py): filters, JQL, Jira issues and cases merged, and the platform filters."""

import pytest

from services import tickets as tk


def issue(key, status="To Do", category="new", priority="High", sprint=None, account="me-1", updated="2026-10-10T10:00:00.000+0000"):
    return {"key": key, "fields": {"summary": f"about {key}", "status": {"name": status, "statusCategory": {"key": category}},
                                   "priority": {"name": priority}, "issuetype": {"name": "Task"},
                                   "assignee": {"displayName": "Ana", "accountId": account}, "project": {"key": key.split("-")[0]},
                                   "updated": updated, "customfield_10020": sprint}}


def case(n, source="APP_REPORT", ref=None, domain="d1", breached=False, updated="2026-10-11T09:00:00+00:00", links=0, assignee=None):
    return {"case_id": f"c{n}", "number": f"CASE-{n}", "title": f"case {n}", "status": "TRIAGED", "severity": "P2",
            "kind": "DATA_BUG", "source": source, "source_ref": ref, "assignee": assignee, "domain_id": domain,
            "domain_name": "SALES", "sla_due_at": None, "sla_breached": breached, "updated_at": updated, "links_count": links}


def test_filters_are_checked_with_defaults():
    f = tk.clean({})
    assert (f["source"], f["status"], f["assignee"], f["priority"]) == ("all", "open", "any", [])
    for bad in ({"source": "github"}, {"priority": "P9"}, {"updated_from": "yesterday"}, {"sprint": "next"},
                {"project": "a b"}, {"type": "Bug; drop"}):
        with pytest.raises(ValueError):
            tk.clean(bad)
    assert tk.clean({"sla_breached": "true", "linked": "1", "copilot": "no"}) == {**tk.clean({}), "sla_breached": True, "linked": True}


def test_jql_from_filters():
    q = tk.jql(tk.clean({"assignee": "me", "priority": "P1,P2", "sprint": "active", "updated_from": "2026-10-01",
                         "project": "gdpqa", "type": "Bug,Story"}))
    assert "assignee = currentUser()" in q and "statusCategory != Done" in q and "sprint in openSprints()" in q
    assert 'priority in ("Highest", "Blocker", "Critical", "High", "Major")' in q
    assert 'issuetype in ("Bug", "Story")' in q and 'updated >= "2026-10-01"' in q and "project = GDPQA" in q
    assert q.endswith("ORDER BY updated DESC")
    assert tk.jql(tk.clean({"q": "gdpqa-12"})) == "key = GDPQA-12"
    assert 'text ~ "say \\"hi\\""' in tk.jql(tk.clean({"q": 'say "hi"'}))
    assert tk.jql(tk.clean({"status": "any"}), "GDPQA") == "project = GDPQA ORDER BY updated DESC"   # never unbounded
    assert tk.jql(tk.clean({"sprint": "none", "status": "any"})).startswith("sprint is EMPTY")


def test_jira_and_case_rows_share_one_shape_and_merge_by_key():
    jira = [tk.from_jira(issue("GDPQA-1", sprint=[{"id": 6, "name": "S6", "state": "closed"}, {"id": 7, "name": "S7", "state": "active"}]),
                         "https://x.atlassian.net", "customfield_10020", "me-1"),
            tk.from_jira(issue("GDPQA-2", priority="Lowest", category="done"), "", "customfield_10020", "other")]
    assert jira[0]["sprint"] == {"id": 7, "name": "S7", "state": "active"} and jira[0]["priority"] == "P2"
    assert jira[0]["assignee_is_me"] and not jira[1]["assignee_is_me"] and jira[1]["done"] and jira[1]["priority"] == "P4"
    assert jira[0]["url"] == "https://x.atlassian.net/browse/GDPQA-1"
    cases = [tk.from_case(case(1, "JIRA", "gdpqa-1", breached=True, links=2)), tk.from_case(case(2))]
    rows = tk.merge(jira, cases)
    assert [r["key"] for r in rows] == ["GDPQA-1", "GDPQA-2", "CASE-2"]   # the case opened from GDPQA-1 joined it
    assert rows[0]["sources"] == ["JIRA", "CASE"] and rows[0]["case"]["number"] == "CASE-1"
    assert rows[0]["sla_breached"] and rows[0]["domain_id"] == "d1" and rows[0]["linked"] == 2


def test_platform_filters_and_order():
    rows = tk.merge([tk.from_jira(issue("A-1", updated="2026-10-01T00:00:00.000+0000"))],
                    [tk.from_case(case(1, breached=True, updated="2026-10-12T00:00:00+00:00")), tk.from_case(case(2, domain="d2", links=1))])
    rows[0]["copilot"] = {"session_id": "s", "drafts": 1}
    order = tk.platform_filter(rows, tk.clean({}))
    assert [r["key"] for r in order][0] == "CASE-1"   # newest first
    assert [r["key"] for r in tk.platform_filter(rows, tk.clean({"sla_breached": "1"}))] == ["CASE-1"]
    assert [r["key"] for r in tk.platform_filter(rows, tk.clean({"domain_id": "d2"}))] == ["CASE-2"]
    assert [r["key"] for r in tk.platform_filter(rows, tk.clean({"linked": "1"}))] == ["CASE-2"]
    assert [r["key"] for r in tk.platform_filter(rows, tk.clean({"copilot": "1"}))] == ["A-1"]
    assert {r["key"] for r in tk.platform_filter(rows, tk.clean({"assignee": "unassigned"}))} == {"CASE-1", "CASE-2"}


def test_which_sources_a_filter_searches():
    assert tk.jira_wanted(tk.clean({})) and tk.cases_wanted(tk.clean({}))
    assert not tk.jira_wanted(tk.clean({"source": "case"})) and not tk.cases_wanted(tk.clean({"source": "jira"}))
    assert not tk.jira_wanted(tk.clean({"kind": "DATA_BUG"})) and not tk.jira_wanted(tk.clean({"sla_breached": "1"}))
    assert not tk.cases_wanted(tk.clean({"sprint": "active"})) and not tk.cases_wanted(tk.clean({"project": "A"}))
