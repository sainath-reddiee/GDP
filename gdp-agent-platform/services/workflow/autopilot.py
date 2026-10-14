"""Autopilot to the next gate (pure): what a run does next on its own, and where it stops for a person.

Both pipelines: every automatic step runs in order (select or land and validate the source, profile, identify the
domain, propose the gold model for a Gold run, generate the mapping, the STTM, the Data Quality checks, the code,
validate it) until the run reaches a human gate (approve the gold model, the mapping, the STTM, the checks, sign off
QA, review the code) or something needs a person. The decision is made from the run's state and a few facts; the API
runs the step through the same functions and governance checks a person's click uses.

next_action(state, facts) -> {"kind": ACTION | GATE | WAIT | BLOCKED | DONE, "action"?, "label"}
facts: pipeline (SILVER | GOLD), has_target, planned_tables (count), gold_model {approved, open} (Gold), has_children
(a Gold run that built its star schema), soda_generated, dbt_generated (for the current STTM), validated (the current
generation), validation_failed, failure_reason.
"""

from __future__ import annotations

from typing import Any, Dict

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
}
RUNNING = {"ACCESS_VALIDATION", "LANDING_RUNNING", "PROFILING_RUNNING", "DBT_GENERATING", "VALIDATION_RUNNING"}
MAX_STEPS = 14


def _act(action: str) -> Dict[str, Any]:
    return {"kind": "ACTION", "action": action, "label": ACTIONS[action]}


def _gate(label: str, where: str) -> Dict[str, Any]:
    return {"kind": "GATE", "label": label, "where": where}


def next_action(state: str, facts: Dict[str, Any]) -> Dict[str, Any]:
    gold = str(facts.get("pipeline") or "").upper() == "GOLD"
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
        return _gate("Review and approve the field mapping", "mapping")
    if state in ("MAPPING_APPROVED", "STTM_PENDING"):
        return _act("generate_sttm")
    if state == "STTM_REVIEW":
        return _gate("Review and approve the transformations (STTM)", "sttm")
    if state == "VALIDATION_FAILED":
        return {"kind": "BLOCKED", "label": "Validation failed: open Validation, fix the findings and regenerate"}
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
