"""The code standards agent (services/validation/standards.py): what it reads, how its answer becomes validation
checks, and that it never breaks a validation when Cortex fails."""

from services.common.rules import DEFAULTS
from services.knowledge.usage import STAGE_SKILLS
from services.validation import checks, standards

FILES = {
    "dbt_project.yml": "name: gdp",
    "models/staging/stg_company.sql": "select cast(id as number) as company_id from {{ source('zi', 'company') }}",
    "models/staging/schema.yml": "models:\n  - name: stg_company",
    "macros/initcap_trim.sql": "{% macro initcap_trim(c) %}initcap(trim({{ c }})){% endmacro %}",
    "mappings/sttm.csv": "COMPANY_ID,NUMBER",
    "soda/checks.yml": "checks for stg_company:",
}


def answer(**areas):
    return {"summary": "reviewed", "areas": [{"area": k, "verdict": v[0], "findings": v[1]} for k, v in areas.items()]}


def finding(severity="error", rule="GDP-DBT-ONBOARD-SOURCE Rule 2", message="TRIM missing on NAME"):
    return {"severity": severity, "rule": rule, "file": "models/staging/stg_company.sql", "message": message, "line_hint": "name"}


ALL_PASS = {k: ("PASS", []) for k in standards.AREA_KEYS}


def test_rules_skill_and_binding_are_registered():
    assert DEFAULTS["validation.standards_review"] is True and DEFAULTS["validation.standards_block"] is True
    assert DEFAULTS["validation.standards_max_files"] == 40
    assert STAGE_SKILLS[standards.STAGE] == [standards.SKILL]


def test_code_is_read_first_and_mapping_exports_are_left_out():
    picked, left = standards.pick_files(FILES, max_files=40)
    paths = [p for p, _ in picked]
    assert paths[0].startswith("models/") and "mappings/sttm.csv" not in paths and "mappings/sttm.csv" not in left
    assert paths.index("macros/initcap_trim.sql") < paths.index("dbt_project.yml")
    few, rest = standards.pick_files(FILES, max_files=2)
    assert len(few) == 2 and len(rest) == 3


def test_the_project_budget_cuts_files_and_long_files_are_trimmed():
    big = {f"models/m{i}.sql": "x" * 9000 for i in range(10)}
    picked, left = standards.pick_files(big, max_files=40)
    assert all(len(body) == standards.MAX_FILE_CHARS for _, body in picked)
    assert sum(len(b) for _, b in picked) <= standards.MAX_PROJECT_CHARS and left


def test_the_prompt_carries_the_skill_the_standards_the_mapping_and_the_files():
    picked, _ = standards.pick_files(FILES, 40)
    ctx = standards.mapping_context([{"target_column": "COMPANY_ID", "target_datatype": "NUMBER", "mapping_type": "DIRECT",
                                      "source_column": "ZOOMINFO_ID", "source_datatype": "VARCHAR", "required": True}])
    assert "COMPANY_ID NUMBER DIRECT <- ZOOMINFO_ID (VARCHAR) required" in ctx
    text = standards.prompt(picked, "REVIEW RULES", "PLAYBOOK RULES", ctx, "SILVER")
    for part in ("REVIEW RULES", "PLAYBOOK RULES", "COMPANY_ID NUMBER", "=== FILE: models/staging/stg_company.sql ===",
                 "NAMING (Naming and layering)", "SILVER run", "You do not fix code"):
        assert part in text


def test_each_area_is_one_check_and_errors_fail_it():
    out = standards.to_checks(answer(**{**ALL_PASS, "TYPES": ("FAIL", [finding(), finding(message="no cast")]),
                                         "DOCS": ("WARN", [finding("warning", message="no description")])}))
    by = {c["validation_type"]: c for c in out}
    assert [c["validation_type"] for c in out] == [f"STANDARDS_{k}" for k in standards.AREA_KEYS]
    assert by["STANDARDS_TYPES"]["status"] == "FAILED" and by["STANDARDS_TYPES"]["error_count"] == 2
    assert by["STANDARDS_TYPES"]["findings"][0]["rule"].startswith("GDP-DBT-ONBOARD-SOURCE")
    assert by["STANDARDS_DOCS"]["status"] == "PASSED" and by["STANDARDS_DOCS"]["warning_count"] == 1
    # 5 of 6 areas pass: the agent alone scores 83.3%
    assert round(checks.score(out), 1) == 83.3


def test_with_blocking_off_errors_are_advisory():
    out = standards.to_checks(answer(**{**ALL_PASS, "KEYS": ("FAIL", [finding()])}), block=False)
    keys = next(c for c in out if c["validation_type"] == "STANDARDS_KEYS")
    assert keys["status"] == "PASSED" and keys["error_count"] == 0 and keys["warning_count"] == 1


def test_a_fail_verdict_without_a_finding_still_fails_and_a_missing_area_is_skipped():
    partial = answer(NAMING=("FAIL", []), TYPES=("PASS", []))
    out = {c["validation_type"]: c for c in standards.to_checks(partial)}
    assert out["STANDARDS_NAMING"]["status"] == "FAILED" and out["STANDARDS_NAMING"]["error_count"] == 1
    assert out["STANDARDS_SQL"]["status"] == "SKIPPED"
    assert checks.score(list(out.values())) == 50.0  # skipped areas are not evaluated


def test_review_turns_a_cortex_answer_into_checks_and_reports_what_it_read():
    calls = []

    def complete(text, schema, max_tokens):
        calls.append((text, schema, max_tokens))
        return answer(**{**ALL_PASS, "SQL": ("FAIL", [finding(message="SELECT * in a final model")])}), {"total_tokens": 900}, "claude-sonnet-4-5"

    result = standards.review(complete, FILES, "skill", "standards", [{"target_column": "COMPANY_ID", "required": True}])
    assert len(calls) == 1 and calls[0][1] is standards.SCHEMA and "COMPANY_ID" in calls[0][0]
    assert result["model"] == "claude-sonnet-4-5" and result["usage"]["total_tokens"] == 900
    assert "models/staging/stg_company.sql" in result["reviewed"] and result["summary"] == "reviewed"
    assert next(c for c in result["checks"] if c["validation_type"] == "STANDARDS_SQL")["status"] == "FAILED"
    assert standards.gate_note(result) == f"standards: {len(result['reviewed'])} files, failed sql"


def test_a_failed_cortex_call_never_breaks_validation():
    def boom(*_):
        raise AssertionError("Cortex returned no structured answer")

    result = standards.review(boom, FILES, "skill", "standards", [])
    assert [c["validation_type"] for c in result["checks"]] == ["STANDARDS"]
    assert result["checks"][0]["status"] == "SKIPPED" and "no structured answer" in result["checks"][0]["findings"][0]["message"]
    assert result["model"] is None and standards.gate_note(result) == "standards review skipped"
    assert checks.score(result["checks"] + [{"validation_type": "NAMING", "status": "PASSED"}]) == 100.0


def test_nothing_to_review_is_skipped_without_a_call():
    result = standards.review(lambda *a: (_ for _ in ()).throw(AssertionError("called")), {"mappings/sttm.csv": "x"}, "", "", [])
    assert result["checks"][0]["status"] == "SKIPPED" and "no files" in result["checks"][0]["findings"][0]["message"]


def test_files_left_out_are_noted_on_the_documentation_area():
    many = {f"models/m{i}.sql": "select 1" for i in range(5)}
    result = standards.review(lambda *a: (answer(**ALL_PASS), {}, "m"), many, "", "", [], max_files=2)
    docs = next(c for c in result["checks"] if c["validation_type"] == "STANDARDS_DOCS")
    assert len(result["left_out"]) == 3 and "outside the review budget" in docs["findings"][-1]["message"]


def test_the_standards_text_names_each_playbook_and_its_version():
    text = standards.standards_text([{"skill_name": "GDP-DBT-ONBOARD-SOURCE", "version": "2.1.0", "content": "Rule 1"}],
                                    lambda c, n: c[:n])
    assert text.startswith("[GDP-DBT-ONBOARD-SOURCE v2.1.0]") and "Rule 1" in text
    assert "no generation playbook" in standards.standards_text([], lambda c, n: c)


def test_the_procedure_loads_its_own_skill_and_the_run_playbooks_and_records_cost(monkeypatch):
    from contextlib import contextmanager

    from services.common import audit, llm
    from services.common.rules import merged, using
    from services.knowledge import usage
    from services.validation import procedures as vp

    calls, costs, prompts = [], [], []

    @contextmanager
    def tool_call(session, run_id, tool, inputs):
        call = type("Call", (), {"summary": ""})()
        yield call
        calls.append((tool, call.summary))

    monkeypatch.setattr(vp, "tool_call", tool_call)
    monkeypatch.setattr(usage, "bound_skills", lambda s, stage, std, rid: {"STANDARDS": ["CODE-STANDARDS-REVIEW", "COMPANY-STANDARDS"],
                                                                          "DBT": ["GDP-DBT-ONBOARD-SOURCE"]}[stage])
    monkeypatch.setattr(vp, "_load", lambda s, names, rid: [{"skill_name": n, "version": "1.0.0", "content": f"{n} body"} for n in names])
    monkeypatch.setattr(vp, "_sttm_lines", lambda s, i: [{"target_column": "COMPANY_ID"}])

    def complete_json(session, text, schema, max_tokens=4096, model=None, stage=None):
        prompts.append((text, stage))
        return answer(**ALL_PASS), {"total_tokens": 10}, "claude-sonnet-4-5"

    monkeypatch.setattr(llm, "complete_json", complete_json)
    monkeypatch.setattr(audit, "record_cost", lambda *a, **k: costs.append((a, k)))
    with using(merged({"validation.standards_max_files": 3})):
        result = vp._review_standards(object(), {"PIPELINE": "SILVER"}, "run-1", FILES, {"GENERATION_ID": "g", "STTM_ID": "s"})
    text, stage = prompts[0]
    # its own skill is the review method; a domain's standards join the playbooks it judges against
    assert "CODE-STANDARDS-REVIEW body" in text and "[COMPANY-STANDARDS v1.0.0]" in text and "[GDP-DBT-ONBOARD-SOURCE v1.0.0]" in text
    assert stage == "DBT" and len(result["reviewed"]) == 3
    assert result["skills"] == ["CODE-STANDARDS-REVIEW@1.0.0", "GDP-DBT-ONBOARD-SOURCE@1.0.0", "COMPANY-STANDARDS@1.0.0"]
    assert calls == [("review_standards", "standards: 3 files, every area passed")]
    assert costs and costs[0][1] == {"agent": "CODE_STANDARDS"} and costs[0][0][2] == "VALIDATION"
