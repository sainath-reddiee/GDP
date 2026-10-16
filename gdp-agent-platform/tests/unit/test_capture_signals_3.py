"""Last batch of captured signals: pull requests closed without merging, dbt validation errors a later version fixed,
and AI suggestion rejections with their reasons (here and for the same columns elsewhere)."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.knowledge import signals  # noqa: E402


class Row(dict):
    def as_dict(self):
        return dict(self)


class Session:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def sql(self, sql, params=None):
        flat = " ".join(sql.split())
        self.calls.append((flat, list(params or [])))
        answers = self.answers

        class R:
            def collect(self_inner):
                for needle, value in answers.items():
                    if needle in flat:
                        return [Row(r) for r in (value(params) if callable(value) else value)]
                return []
        return R()


def test_a_closed_pull_request_is_remembered_per_asset():
    item = signals.pr_closed("TABLE_MODEL", "tm1", "CUSTOMER", 3, "feature/model-customer-v3", "https://git/pr/9")
    assert item["key"] == "git.closed.TABLE_MODEL.tm1" and "closed without merging" in item["content"]
    assert item["content_json"]["target_table"] == "CUSTOMER" and "https://git/pr/9" in item["content"]
    assert signals.pr_closed("MODEL", "", "SALES", 1, None, None) is None


def test_sync_remembers_closed_pull_requests(monkeypatch):
    from services.git import roundtrip
    from services.knowledge import writer

    seen = []
    monkeypatch.setattr(writer, "remember", lambda session, **kw: seen.append(kw))
    open_prs = [{"KIND": "DQ_SUITE", "ASSET_ID": "s1", "VERSION": 2, "ASSET_NAME": "DB.S.ORDERS", "PR_NUMBER": 7,
                 "GIT_URL": "https://github.com/o/r", "DOMAIN_ID": "d1", "BRANCH": "feature/dq-orders-v2",
                 "PR_URL": "https://github.com/o/r/pull/7"},
                {"KIND": "MODEL", "ASSET_ID": "m1", "VERSION": 4, "ASSET_NAME": "SALES", "PR_NUMBER": 8,
                 "GIT_URL": "https://github.com/o/r", "DOMAIN_ID": "d1", "BRANCH": "b", "PR_URL": "u"}]
    session = Session({"FROM CORE.GIT_PR G": open_prs})
    states = {7: {"state": "CLOSED"}, 8: {"state": "MERGED", "merge_commit_sha": "abc"}}
    out = roundtrip.sync(session, {"force": True}, lambda url, number: states[number])
    assert len(out["closed"]) == 1 and len(out["merged"]) == 1
    assert [kw["key"] for kw in seen] == ["git.closed.DQ_SUITE.s1"]


def test_validation_errors_fixed_later_are_kept():
    from services.validation.procedures import fixed_findings

    summary = json.dumps({"results": [
        {"validation_type": "STTM_COVERAGE", "findings": [{"severity": "ERROR", "message": "EMAIL has no expression"},
                                                         {"severity": "WARN", "message": "cast not visible"}]},
        {"validation_type": "DBT_COMPILE", "findings": []}]})
    findings = fixed_findings(summary)
    item = signals.validation_fixed("DB.SILVER.CUSTOMER", findings)
    assert item["kind"] == "DBT_PATTERN" and item["key"] == "dbt.validation.CUSTOMER"
    assert "EMAIL has no expression" in item["content"] and len(item["content_json"]["errors"]) == 1
    assert item["content_json"]["errors"][0]["validation_type"] == "STTM_COVERAGE"
    assert signals.validation_fixed("X", [{"severity": "WARN", "message": "m"}]) is None


def test_a_pass_after_a_failure_teaches_but_a_first_pass_does_not(monkeypatch):
    from services.knowledge import writer
    from services.validation import procedures as vp

    seen = []
    monkeypatch.setattr(writer, "remember", lambda session, **kw: seen.append(kw))
    run = {"DOMAIN_ID": "d1", "TARGET_MODEL": "DB.SILVER.CUSTOMER"}
    failed = json.dumps({"results": [{"validation_type": "NAMING", "findings": [{"severity": "ERROR", "message": "bad prefix"}]}]})
    vp._remember_fixed(Session({"CORE.VALIDATION_RUN": [{"RESULT_JSON": failed}]}), run, "r1")
    vp._remember_fixed(Session({}), run, "r2")
    assert [kw["key"] for kw in seen] == ["dbt.validation.CUSTOMER"]


def test_rejections_carry_their_reason_and_travel_to_the_same_column_elsewhere(monkeypatch):
    from services.common import ai_suggest

    elsewhere = [{"SCOPE_KEY": "s1.CRM_CONTACT", "ITEM_KEY": "EMAIL", "NOTE": "a shared mailbox, not personal"},
                 {"SCOPE_KEY": "s1.CRM_CONTACT", "ITEM_KEY": "FAX", "NOTE": "n/a"}]
    found = ai_suggest.rejected_elsewhere(Session({"CORE.AI_SUGGESTION_DECISION": elsewhere}), "PROFILING",
                                          "s1.CRM_ACCOUNT", {"table": "CRM_ACCOUNT", "columns": [{"column": "EMAIL"}]})
    assert found == ["EMAIL in s1.CRM_CONTACT (reason: a shared mailbox, not personal)"]
    spec = ai_suggest.Spec("PROFILING", 1, "Review columns.", {"type": "object"}, lambda i: i["column"], ["column"])
    prompt = spec.prompt({"columns": []}, ["EMAIL (reason: test data)"], found)
    assert "test data" in prompt and "shared mailbox" in prompt
    assert ai_suggest.mentioned("EMAIL|PII", '{"column": "EMAIL"}')
    assert not ai_suggest.mentioned("EMAIL", '{"column": "EMAIL_HASH"}')  # whole names only
