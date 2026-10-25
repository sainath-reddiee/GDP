"""Autopilot to the next gate (pure): what a run does next on its own, and where it stops for a person.

Both pipelines: every automatic step runs in order (select or land and validate the source, profile, identify the
domain, propose the gold model for a Gold run, generate the mapping, the STTM, the Data Quality checks, the code,
validate it) until the run reaches a human gate (approve the gold model, the mapping, the STTM, the checks, sign off
QA, review the code) or something needs a person. The decision is made from the run's state and a few facts; the API
runs the step through the same functions and governance checks a person's click uses.

next_action(state, facts) -> {"kind": ACTION | GATE | WAIT | BLOCKED | DONE, "action"?, "label"}
facts: pipeline (SILVER | GOLD), has_target, planned_tables (count), gold_model {approved, open} (Gold), has_children
(a Gold run that built its star schema, or a Silver run mapped to several tables), table_runs {total, done, failed}
(a Silver parent's table runs), soda_generated, soda_proposed (checks still to decide), dbt_generated (for the current
STTM), validated (the current generation), qa_signed (the current STTM's QA tests signed off), validation_failed,
failure_reason, autonomous (setting AUTOPILOT_AUTONOMOUS), publish_ready (GitHub publishing set up), published (the
current generation has a pull request), may_signoff_qa and may_approve_code (the person who started autopilot holds
QA sign off and code review; both default to true).

Autonomous: the gates of a table run (mapping, STTM, Data Quality checks, QA sign-off, code review) are decided by the
autopilot on the evidence (best mapping candidate, proposed checks, a QA run without blocking failures, a passed
validation), recorded with an "Autopilot" justification so a person reviews or reopens them later from the stores.
After validation it commits the code and opens the pull request, then stops at code review unless the person who
started it may sign off QA and review code. It still stops where only a person can act: no target model, an open gold
model, a failed validation or QA run.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

ACTIONS = {
    "prepare_source": "Validate access and select the tables",
    "profile": "Profile the tables",
    "identify_domain": "Identify the domain",
    "propose_gold_model": "Propose the gold model from the profile",
    "build_star_schema": "Create a table run for each fact and bridge of the gold model",
    "generate_mapping": "Generate the field mapping",
    "generate_sttm": "Generate the transformations (STTM)",
    "generate_soda": "Generate the Data Quality checks",
    "generate_dbt": "Generate the code",
    "validate": "Validate the code",
    "publish_pr": "Commit the code and open the pull request",
    "approve_mapping": "Accept the best mapping candidates and approve the field mapping",
    "approve_sttm": "Approve the transformations (STTM)",
    "approve_soda": "Approve the Data Quality checks",
    "signoff_qa": "Run the QA tests and sign them off",
    "approve_code": "Approve the code (Code review)",
}
RUNNING = {"ACCESS_VALIDATION", "LANDING_RUNNING", "PROFILING_RUNNING", "DBT_GENERATING", "VALIDATION_RUNNING"}
MAX_STEPS = 24
POST_STTM_BUILD = ("STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED", "DBT_PENDING",
                   "VALIDATION_PENDING", "VALIDATION_PASSED", "DBT_REVIEW")
JUSTIFICATION = "Autopilot: {what}. Review or reopen it from the store."


def _act(action: str) -> Dict[str, Any]:
    return {"kind": "ACTION", "action": action, "label": ACTIONS[action]}


def _gate(label: str, where: str) -> Dict[str, Any]:
    return {"kind": "GATE", "label": label, "where": where}


def auto_mapping_decisions(candidates: List[Dict[str, Any]], decided: Iterable[Tuple[str, Optional[str]]],
                           claimed: Iterable[Tuple[str, Optional[str]]]) -> List[Dict[str, Any]]:
    """One decision per undecided (source column, target table): its rank-1 candidate, approved, best score first; a
    target column another source already feeds is rejected instead (pure).

    candidates: candidate_id, source_column_id, target_column_id, target_table_id, rank, final_score, recommendation.
    decided: (source_column_id, target_table_id) already decided; claimed: (target_column_id, target_table_id) mapped."""
    done, taken = set(decided), set(claimed)
    best = sorted((c for c in candidates if int(c.get("rank") or 0) == 1
                   and (c["source_column_id"], c.get("target_table_id")) not in done),
                  key=lambda c: -float(c.get("final_score") or 0))
    out = []
    for c in best:
        key = (c["source_column_id"], c.get("target_table_id"))
        if key in done:
            continue
        done.add(key)
        target = (c.get("target_column_id"), c.get("target_table_id"))
        score = float(c.get("final_score") or 0)
        if not c.get("target_column_id") or target in taken:
            out.append({"candidate_id": c["candidate_id"], "decision": "REJECTED",
                        "business_justification": JUSTIFICATION.format(what="target already mapped from a better match")})
            continue
        taken.add(target)
        out.append({"candidate_id": c["candidate_id"], "decision": "APPROVED",
                    "business_justification": JUSTIFICATION.format(
                        what=f"best candidate ({c.get('recommendation') or 'MANUAL'}, score {score:.2f})")})
    return out


def _build(state: str, facts: Dict[str, Any]) -> Dict[str, Any]:
    """Autonomous, after the STTM: checks, code, validation, pull request, QA, code review, in the order the gates
    need them. QA sign off and code review are left to a person when the caller may not do them."""
    if not facts.get("soda_generated") and state in ("STTM_APPROVED", "SODA_PENDING"):
        return _act("generate_soda")
    if state == "SODA_REVIEW" or facts.get("soda_proposed"):
        return _act("approve_soda")
    if not facts.get("dbt_generated"):
        return _act("generate_dbt")
    if state not in ("VALIDATION_PASSED", "DBT_REVIEW"):
        return _act("validate")   # also moves a run whose code was validated while it waited at the checks gate
    if facts.get("publish_ready") and not facts.get("published"):
        return _act("publish_pr")
    review = _gate("Pull request opened; waiting on the Data Developer's code review" if facts.get("published")
                   else "Code validated; waiting on the Data Developer's code review", "code review")
    if not facts.get("qa_signed"):
        return _act("signoff_qa") if facts.get("may_signoff_qa", True) else review
    if state == "DBT_REVIEW":
        return _act("approve_code") if facts.get("may_approve_code", True) else review
    return {"kind": "BLOCKED", "label": "Validation passed but the run did not reach code review; open the run"}


def next_action(state: str, facts: Dict[str, Any]) -> Dict[str, Any]:
    gold = str(facts.get("pipeline") or "").upper() == "GOLD"
    auto = bool(facts.get("autonomous"))
    if state == "COMPLETED":
        return {"kind": "DONE", "label": "The run is complete"}
    if state == "CANCELLED":
        return {"kind": "DONE", "label": "The run is cancelled"}
    if state == "FAILED":
        return {"kind": "BLOCKED", "label": f"The run failed: {facts.get('failure_reason') or 'see the run console'}"}
    if state in RUNNING:
        return {"kind": "WAIT", "label": "A step is still running"}
    if state == "CREATED":
        return _act("prepare_source") if facts.get("planned_tables") else \
            {"kind": "BLOCKED", "label": "Choose the source tables on " + ("Silver Selection" if gold else "Source Ingestion")}
    if state in ("SOURCE_REGISTERED", "ACCESS_APPROVED", "LANDING_PENDING"):
        return _act("prepare_source")
    if state in ("LANDING_COMPLETE", "PROFILING_PENDING"):
        return _act("profile")
    if state == "PROFILING_COMPLETE":
        return _act("identify_domain")
    if state in ("DOMAIN_IDENTIFIED", "MAPPING_PENDING"):
        if gold and not facts.get("has_target"):
            model = facts.get("gold_model") or {}
            if facts.get("has_children"):
                return _gate("Its gold tables are built by their table runs", "star schema")
            if model.get("open"):
                return _gate("Approve the gold model in the Model Workbench (a second model approver)", "gold model")
            if not model.get("approved"):
                return _act("propose_gold_model")
            return _act("build_star_schema")
        if not facts.get("has_target"):
            return _gate("Choose the target model on Modeling", "target model")
        return _act("generate_mapping")
    if state == "MAPPING_REVIEW":
        return _act("approve_mapping") if auto else _gate("Review and approve the field mapping", "mapping")
    if state in ("MAPPING_APPROVED", "STTM_PENDING") and facts.get("has_children") and not gold:
        runs = facts.get("table_runs") or {}
        if runs.get("total") and runs.get("done") == runs.get("total"):
            return {"kind": "DONE", "label": f"All {runs['total']} table runs have approved code"}
        return _gate(f"Each target table continues in its own table run ({runs.get('done', 0)} of "
                     f"{runs.get('total', 0)} done" + (f", {runs['failed']} failed" if runs.get("failed") else "") + ")",
                     "table runs")
    if state in ("MAPPING_APPROVED", "STTM_PENDING"):
        return _act("generate_sttm")
    if state == "STTM_REVIEW":
        return _act("approve_sttm") if auto else _gate("Review and approve the transformations (STTM)", "sttm")
    if state == "VALIDATION_FAILED":
        if facts.get("resume"):
            return _act("generate_dbt") if auto else _act("validate")
        return {"kind": "BLOCKED", "label": "Validation failed: open Validation, fix the findings and regenerate"}
    if auto and state in POST_STTM_BUILD:
        return _build(state, facts)
    if state in ("STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED", "DBT_PENDING", "VALIDATION_PENDING",
                 "VALIDATION_PASSED"):
        if not facts.get("soda_generated") and state in ("STTM_APPROVED", "SODA_PENDING"):
            return _act("generate_soda")
        if not facts.get("dbt_generated"):
            return _act("generate_dbt")
        if not facts.get("validated"):
            return _act("validate")
        if state in ("SODA_REVIEW", "SODA_PENDING"):
            return _gate("Decide the Data Quality checks, sign off the QA tests, then review the code", "quality")
        return _gate("Sign off the QA tests and review the code", "review")
    if state == "DBT_REVIEW":
        return _gate("Review and approve the code (Code review)", "code review")
    if state == "DBT_APPROVED":
        return {"kind": "DONE", "label": "The code is approved"}
    return {"kind": "BLOCKED", "label": f"No automatic step from {state.replace('_', ' ').lower()}"}
