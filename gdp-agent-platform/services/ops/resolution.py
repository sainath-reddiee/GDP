"""Resolution knowledge: when a person resolves an incident with a real note (20 characters or more), what fixed it is
remembered as domain knowledge (KNOWLEDGE.DOMAIN_KNOWLEDGE, type INCIDENT_RESOLUTION), so the next diagnosis finds it.

Written through services.knowledge.writer.remember, like every other knowledge row: one lineage per domain and
fingerprint (reference ops.incident.<fingerprint>), a new version when the same failure is resolved again with a
different fix, and the learning policy decides whether it is ACTIVE at once or waits for a steward (auto by default).
Cortex Search (KNOWLEDGE.KNOWLEDGE_SEARCH) indexes current rows within its target lag. The domain is the DAG's DOMAIN_ID,
else GENERAL. Only redacted text is stored: the title, the fingerprint, the AI's cause when there is one and the note.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from services.ops.redact import redact
from services.ops.sqlio import as_db, as_session

MIN_NOTE = 20
KIND = "INCIDENT_RESOLUTION"
SYSTEM = "system"


def reference(fingerprint: str) -> str:
    return f"ops.incident.{fingerprint}"


def should_remember(incident: Dict[str, Any], resolution: Optional[str], actor: Optional[str], auto: bool = False) -> bool:
    note = (resolution or "").strip()
    return bool(not auto and actor and actor != SYSTEM and len(note) >= MIN_NOTE and incident.get("fingerprint")
                and not incident.get("parent_incident_id"))


def entry(incident: Dict[str, Any], resolution: str, actor: str) -> Dict[str, Any]:
    """{title, content, content_json, tags} with every string redacted."""
    ai = incident.get("ai") if isinstance(incident.get("ai"), dict) else {}
    title = redact(f"Resolved: {incident.get('title') or incident.get('dag_id')}")[:500]
    cause = redact(str(ai.get("probable_cause") or "")).strip()
    fix = redact(resolution.strip())[:4000]
    lines = [f"DAG {incident.get('dag_id')} in {incident.get('env_id')}" + (f", task {incident.get('task_id')}" if incident.get("task_id") else ""),
             f"Failure kind: {incident.get('kind')}"]
    if incident.get("error_excerpt"):
        from services.ops.detect import last_exception_line

        lines.append("Error: " + redact(last_exception_line(incident["error_excerpt"]))[:500])
    if cause:
        lines.append(f"Cause (AI, {ai.get('category')}): {cause[:800]}")
    lines.append(f"Fix: {fix}")
    content_json = {"incident_id": incident.get("incident_id"), "fingerprint": incident.get("fingerprint"),
                    "env_id": incident.get("env_id"), "dag_id": incident.get("dag_id"), "task_id": incident.get("task_id"),
                    "category": ai.get("category"), "cause": cause[:800] or None, "fix": fix, "resolved_by": actor}
    return {"title": title, "content": redact("\n".join(lines)), "content_json": content_json,
            "tags": ["OPS", "INCIDENT", str(incident.get("dag_id") or "")[:100]] + ([ai["category"]] if ai.get("category") else [])}


def domain_for(db: Any, session: Any, incident: Dict[str, Any]) -> str:
    found = db.query("SELECT DOMAIN_ID FROM OPS.DAG WHERE ENV_ID = %s AND DAG_ID = %s", (incident["env_id"], incident["dag_id"]))
    domain_id = found[0].get("domain_id") if found else None
    if domain_id and db.query("SELECT 1 AS X FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = %s", (domain_id,)):
        return domain_id
    from services.knowledge.procedures import GENERAL_DOMAIN, ensure_domain

    return ensure_domain(session, GENERAL_DOMAIN, "Targets registered without a domain pack")


def remember_resolution(source: Any, incident_id: str, actor: str, auto: bool = False,
                        remember: Optional[Any] = None) -> Optional[str]:
    """The KNOWLEDGE_ID written, or None (nothing to remember, or the same fix again). Never raises."""
    from services.ops.context import load_incident

    try:
        db, session = as_db(source), as_session(source)
        incident = load_incident(db, incident_id)
        if not incident or not should_remember(incident, incident.get("resolution"), actor, auto):
            return None
        item = entry(incident, incident["resolution"], actor)
        if remember is None:
            from services.knowledge.writer import remember
        return remember(session, domain_id=domain_for(db, session, incident), kind=KIND,
                        key=reference(incident["fingerprint"]), title=item["title"], content=item["content"],
                        content_json=item["content_json"], tags=item["tags"], origin="OPS",
                        change_note=f"Incident {incident_id[:8]} resolved by {actor}")
    except Exception:
        return None
