"""Reviewer and operator signals kept as knowledge, so the next run's prompts and rules see them.

Each builder is pure: it turns one decision into a knowledge item {kind, key, title, content, content_json, tags}, or
None when the decision teaches nothing. `keep` writes it through the learning policy (services/knowledge/writer
.remember) and never raises: the decision that produced the signal is already saved.

  gate_feedback        a reviewer sends a stage back (REQUEST_CHANGES or REJECT) with a reason
  model_rejection      a domain model version is rejected in the Model Workbench
  change_rejection     a proposal to a domain model (from a run, a table model or an annotation) is rejected
  suite_decision       a data quality suite version is approved or rejected in the DQ Store
  postmortem_item      an incident postmortem (cause and follow-ups; queued for a steward)
  dbt_review_item      the errors and warnings an AI review found in a generated dbt file
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.common.sql import clip


def _name(value: Any) -> str:
    return str(value or "").strip().strip('"').upper().split(".")[-1]


def gate_feedback(run: Dict[str, Any], stage: str, from_state: str, decision: str, justification: Optional[str],
                  comments: Optional[str], target_table: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Why a reviewer sent a stage back. One item per run and stage (the latest reason wins)."""
    if str(decision).upper() == "APPROVE" or not (justification or comments):
        return None
    reason = "; ".join(p for p in (justification, comments) if p)
    table = _name(target_table)
    return {"kind": "BUSINESS_RULE",
            "key": f"review.{str(stage).lower()}.{run['RUN_ID']}",
            "title": f"{stage} review sent back{f' for {table}' if table else ''}",
            "content": f"A reviewer sent the {stage} stage back ({decision.lower().replace('_', ' ')}) at {from_state}"
                       f"{f' for {table}' if table else ''}: {clip(reason, 3000)}",
            "content_json": {"stage": stage, "decision": decision, "from_state": from_state, "reason": clip(reason, 3000),
                             "target_table": table or None, "run_id": run["RUN_ID"]},
            "tags": ["REVIEW", "GATE", str(stage).upper()]}


def model_rejection(domain_name: str, version: int, reason: str, touched: Optional[List[str]] = None
                    ) -> Optional[Dict[str, Any]]:
    """Why a domain model version was rejected, for the next modeling prompts."""
    if not (reason or "").strip():
        return None
    entities = ", ".join((touched or [])[:12])
    return {"kind": "BUSINESS_RULE",
            "key": f"model.review.{str(domain_name).upper()}.v{int(version)}",
            "title": f"{domain_name} model v{int(version)} rejected",
            "content": f"Version {int(version)} of the {domain_name} domain model was rejected"
                       f"{f' (it changed {entities})' if entities else ''}: {clip(reason, 3000)}",
            "content_json": {"domain": domain_name, "version": int(version), "reason": clip(reason, 3000),
                             "entities": (touched or [])[:50]},
            "tags": ["MODEL", "REVIEW", "REJECTED"]}


def change_rejection(domain_name: str, entity: Optional[str], source: str, note: str) -> Optional[Dict[str, Any]]:
    """Why a proposal to the domain model was rejected (one item per entity: the latest reason wins)."""
    if not (note or "").strip():
        return None
    table = _name(entity)
    return {"kind": "BUSINESS_RULE",
            "key": f"model.change.rejected.{str(domain_name).upper()}.{table or 'MODEL'}",
            "title": f"Change to {table or domain_name} rejected",
            "content": f"A proposed change to {table or 'the model'} in {domain_name} (from {str(source).lower()}) was "
                       f"rejected: {clip(note, 3000)}",
            "content_json": {"domain": domain_name, "target_table": table or None, "source": source,
                             "reason": clip(note, 3000)},
            "tags": ["MODEL", "PROPOSAL", "REJECTED"]}


def _check_text(c: Dict[str, Any]) -> str:
    kind = str(c.get("check_type") or c.get("kind") or "check").lower()
    column = c.get("target_column") or c.get("column") or "table"
    return f"{kind} on {column}"


def suite_decision(table: str, version: int, decision: str, checks: List[Dict[str, Any]],
                   reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """An approved DQ Store suite as the table's agreed checks; a rejected one with its reason. SODA_PATTERN, so
    Soda generation and the data quality prompts see what this table's reviewers settled."""
    decision = str(decision).upper()
    name = _name(table)
    if decision == "APPROVED":
        summary = "; ".join(_check_text(c) for c in checks[:40])
        return {"kind": "SODA_PATTERN", "key": f"dq.suite.{str(table).upper()}",
                "title": f"Approved data quality checks for {name}",
                "content": f"Version {int(version)} of the data quality suite for {table} was approved with "
                           f"{len(checks)} checks: {summary}.",
                "content_json": {"target_table": name, "table": table, "decision": "SUITE_APPROVED",
                                 "version": int(version), "checks": checks[:200]},
                "tags": ["DQ", "SUITE", "APPROVED"]}
    if decision == "REJECTED" and (reason or "").strip():
        return {"kind": "SODA_PATTERN", "key": f"dq.suite.rejected.{str(table).upper()}",
                "title": f"Data quality suite for {name} rejected",
                "content": f"Version {int(version)} of the data quality suite for {table} was rejected: {clip(reason, 3000)}",
                "content_json": {"target_table": name, "table": table, "decision": "SUITE_REJECTED",
                                 "version": int(version), "reason": clip(reason, 3000)},
                "tags": ["DQ", "SUITE", "REJECTED"]}
    return None


def postmortem_item(incident: Dict[str, Any], output: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """An incident postmortem's cause, fix and follow-ups (already redacted), keyed by the incident's fingerprint
    so the next occurrence finds it. Needs a fingerprint and a cause."""
    from services.ops.redact import redact

    cause = redact(str(output.get("cause") or "")).strip()
    if not incident.get("fingerprint") or not cause:
        return None
    fix = redact(str(output.get("fix") or "")).strip()
    follow = output.get("follow_ups")
    follow_ups = [redact(str(f)).strip() for f in (follow if isinstance(follow, list) else [follow] if follow else [])
                  if str(f).strip()][:10]
    lines = [f"DAG {incident.get('dag_id')} in {incident.get('env_id')}", f"Cause: {cause[:1500]}"]
    if fix:
        lines.append(f"Fix: {fix[:1500]}")
    if follow_ups:
        lines.append("Follow-ups: " + "; ".join(f[:300] for f in follow_ups))
    return {"kind": "INCIDENT_RESOLUTION", "key": f"ops.postmortem.{incident['fingerprint']}",
            "title": redact(f"Postmortem: {incident.get('title') or incident.get('dag_id')}")[:500],
            "content": "\n".join(lines),
            "content_json": {"incident_id": incident.get("incident_id"), "fingerprint": incident["fingerprint"],
                             "dag_id": incident.get("dag_id"), "env_id": incident.get("env_id"), "cause": cause[:1500],
                             "fix": fix[:1500] or None, "follow_ups": follow_ups},
            "tags": ["OPS", "INCIDENT", "POSTMORTEM"]}


def dbt_review_item(target: Optional[str], path: str, findings: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The errors and warnings an AI review found in one generated file, as a DBT_PATTERN for the next generation
    and review of this target. Info-level findings and clean reviews teach nothing."""
    kept = [f for f in findings or [] if str(f.get("severity") or "").lower() in ("error", "warning")][:20]
    if not kept:
        return None
    table = _name(target)
    lines = [f"- [{f.get('severity')}] {f.get('rule') or 'rule'}: {clip(f.get('message'), 400)}" for f in kept]
    return {"kind": "DBT_PATTERN", "key": f"dbt.review.{table or 'RUN'}.{path}",
            "title": f"dbt review findings for {path}",
            "content": f"An AI review of {path}{f' ({table})' if table else ''} found:\n" + "\n".join(lines),
            "content_json": {"target_table": table or None, "path": path,
                             "findings": [{k: f.get(k) for k in ("severity", "rule", "message")} for f in kept]},
            "tags": ["DBT", "REVIEW"]}


def keep(session, domain_id: Optional[str], item: Optional[Dict[str, Any]], origin: str,
         run_id: Optional[str] = None, mode: Optional[str] = None) -> Optional[str]:
    """Write one item through the learning policy; the KNOWLEDGE_ID, or None (nothing to keep, or it failed)."""
    if not item or not domain_id:
        return None
    try:
        from services.knowledge.writer import remember

        return remember(session, domain_id=domain_id, kind=item["kind"], key=item["key"], title=item["title"],
                        content=item["content"], content_json=item["content_json"], tags=item["tags"], origin=origin,
                        run_id=run_id, mode=mode)
    except Exception:
        return None
