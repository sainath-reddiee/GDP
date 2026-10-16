"""Root-cause analysis for a failure no published fix covers (slide 3, step 4b): start at the failed task or its dbt node
and walk upstream DAGs, models and pipelines.

RCA_MODE AGENT asks the RCA agent (CORE.RCA_AGENT: it reads the failure context, walks the graph and searches code and
knowledge, and never writes). When the agent is not available or answers badly, and in RCA_MODE GROUNDED, the
platform's grounded incident diagnosis (services.ops.diagnose: cited evidence from logs, code, queries and similar
incidents) is combined with the graph walk. Either way the result is {cause, evidence, path, fix_steps, mode}.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from services.ops.redact import redact
from services.resolve import agent, graph

AGENT_NAME = "RCA_AGENT"


def agent_message(failure: Dict[str, Any], hops: int) -> str:
    return (f"Find the root cause of Airflow failure {failure['failure_id']}: DAG {failure.get('dag_id')}, task "
            f"{failure.get('task_id')}, {failure.get('exception_class') or 'error'}: "
            f"{str(failure.get('exception_text') or '')[:600]}.\n"
            f"Call get_failure_context with FAILURE_ID={failure['failure_id']}, then walk_graph with the same FAILURE_ID "
            f"and HOPS={hops}, and search the code and knowledge where it helps. Answer with JSON only: "
            '{"cause": "one or two sentences", "evidence": ["what you saw, with the object or file"], '
            '"path": ["upstream objects, nearest first"], "fix_steps": ["what a person should do"]}')


def _strings(value: Any, limit: int, size: int = 400) -> List[str]:
    if not isinstance(value, list):
        return []
    return [redact(str(v))[:size] for v in value if str(v).strip()][:limit]


class Analyst:
    def __init__(self, db: Any, database: str, diagnose_fn: Any = None):
        self.db, self.database, self.diagnose_fn = db, database, diagnose_fn

    def analyse(self, failure: Dict[str, Any], prefs: Dict[str, Any]) -> Dict[str, Any]:
        hops = int(prefs.get("graph_hops") or 3)
        walked = graph.walk(self.db, failure, hops)
        if prefs.get("rca_mode", "AGENT") == "AGENT":
            try:
                answer = agent.json_answer(agent.run(self.database, "CORE", AGENT_NAME, agent_message(failure, hops)))
                cause = redact(str(answer.get("cause") or "")).strip()[:1000]
                if cause:
                    return {"mode": "agent", "cause": cause, "evidence": _strings(answer.get("evidence"), 8),
                            "path": _strings(answer.get("path"), 10, 200) or graph.path_names(walked),
                            "fix_steps": _strings(answer.get("fix_steps"), 8), "graph_nodes": len(walked["nodes"])}
            except agent.AgentUnavailable as exc:
                note = str(exc)[:300]
            else:
                note = "the agent gave no cause"
        else:
            note = None
        out = self.grounded(failure, walked)
        if note:
            out["agent_note"] = note
        return out

    def grounded(self, failure: Dict[str, Any], walked: Dict[str, Any]) -> Dict[str, Any]:
        ai: Dict[str, Any] = {}
        if failure.get("incident_id"):
            from services.ops.diagnose import diagnose
            from services.ops.sqlio import as_session

            fn = self.diagnose_fn or diagnose
            ai = (fn(as_session(self.db), failure["incident_id"]) or {}).get("ai") or {}
        evidence = [f"{e.get('ref')}: {e.get('text')}" for e in (ai.get("evidence") or [])][:8]
        return {"mode": "grounded", "cause": ai.get("probable_cause") or
                (f"{failure.get('exception_class') or 'Error'} in {failure.get('task_id')}: "
                 f"{str(failure.get('exception_text') or '')[:400]}"),
                "category": ai.get("category"), "confidence": ai.get("confidence"),
                "evidence": [redact(e)[:400] for e in evidence], "path": graph.path_names(walked),
                "fix_steps": list(ai.get("fix_steps") or [])[:8], "graph_nodes": len(walked["nodes"])}


# ---------------------------------------------------------------- the RCA agent's tools (procedure handlers)

def failure_context_entry(session: Any, failure_id: str) -> Dict[str, Any]:
    """CALL CORE.SP_RCA_CONTEXT(failure): the failure, its incident and the redacted log excerpt (read only)."""
    from services.ops.sqlio import as_db
    from services.resolve.engine import get_failure

    db = as_db(session)
    failure = get_failure(db, failure_id)
    if not failure:
        return {"error": "failure not found"}
    out = {k: failure.get(k) for k in ("dag_id", "task_id", "run_id", "try_number", "exception_class", "exception_text",
                                       "query_id", "dbt_node_id", "log_url")}
    if failure.get("incident_id"):
        found = db.query("""SELECT TITLE, SEVERITY, ERROR_EXCERPT, AI_SUMMARY, OCCURRENCES, FIRST_SEEN
                              FROM CORE.INCIDENT WHERE INCIDENT_ID = %s""", (failure["incident_id"],))
        if found:
            r = found[0]
            out["incident"] = {"title": r.get("title"), "severity": r.get("severity"), "occurrences": r.get("occurrences"),
                               "first_seen": str(r.get("first_seen")), "ai_summary": r.get("ai_summary"),
                               "error_excerpt": redact(str(r.get("error_excerpt") or ""))[-3000:]}
    return json.loads(json.dumps(out, default=str))


def walk_graph_entry(session: Any, failure_id: str, hops: Any) -> Dict[str, Any]:
    """CALL CORE.SP_WALK_GRAPH(failure, hops): upstream nodes of the failed task (read only)."""
    from services.ops.sqlio import as_db
    from services.resolve.engine import get_failure

    db = as_db(session)
    failure = get_failure(db, failure_id)
    if not failure:
        return {"error": "failure not found"}
    walked = graph.walk(db, failure, max(1, min(6, int(hops or 3))))
    return json.loads(json.dumps({"start": walked["start"], "nodes": walked["nodes"][:60], "text": graph.text(walked)},
                                 default=str))
