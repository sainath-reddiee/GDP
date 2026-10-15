"""The learning loop in the run engine (Snowpark session): read a domain's reviewer decisions for a layer, learn the
habits behind them (services/learning/patterns.py), replay each candidate on the domain's past runs
(services/learning/replay.py) and propose the ones that do not make mapping worse to the knowledge inbox, each with
its evidence and its replay. A candidate the replay shows to be worse is held back and reported, never proposed.
Every replay is kept (CORE.CHANGE_EVAL, V058) so the inbox shows it next to the proposal.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from services.learning import patterns, replay


def events(session, domain_id: str, layer: str) -> List[Dict[str, Any]]:
    """Every current reviewer decision of the domain's runs in the layer, with what the engine had proposed."""
    from services.common.sql import rows

    found = rows(session, """
        SELECT D.RUN_ID, P.TABLE_NAME AS SOURCE_TABLE, P.COLUMN_NAME AS SOURCE_COLUMN, P.DATA_TYPE AS SOURCE_TYPE,
               P.STATISTICS_JSON:date_format::STRING AS DATE_FORMAT, P.PATTERN_JSON[0]:pattern::STRING AS PATTERN,
               TT.TARGET_TABLE, T.COLUMN_NAME AS TARGET_COLUMN, T.DATA_TYPE AS TARGET_TYPE, D.DECISION, D.TRANSFORMATION,
               CT.COLUMN_NAME AS PROPOSED_TARGET, C.TRANSFORMATION AS PROPOSED_TRANSFORMATION
          FROM CORE.MAPPING_DECISION D
          JOIN CORE.WORKFLOW_RUN R ON R.RUN_ID = D.RUN_ID
          LEFT JOIN CORE.PROFILE_REGISTRY P ON P.RUN_ID = D.RUN_ID AND P.SOURCE_COLUMN_ID = D.SOURCE_COLUMN_ID AND P.IS_CURRENT
          LEFT JOIN CORE.TARGET_COLUMN_REGISTRY T ON T.TARGET_COLUMN_ID = D.TARGET_COLUMN_ID
          LEFT JOIN CORE.TARGET_TABLE_REGISTRY TT ON TT.TARGET_TABLE_ID = T.TARGET_TABLE_ID
          LEFT JOIN CORE.MAPPING_CANDIDATE C ON C.RUN_ID = D.RUN_ID AND C.SOURCE_COLUMN_ID = D.SOURCE_COLUMN_ID
               AND C.IS_CURRENT AND C.RANK = 1
          LEFT JOIN CORE.TARGET_COLUMN_REGISTRY CT ON CT.TARGET_COLUMN_ID = C.TARGET_COLUMN_ID
         WHERE D.IS_CURRENT AND R.DOMAIN_ID = ? AND COALESCE(R.PIPELINE, 'SILVER') = ? AND R.DELETED_AT IS NULL
           AND COALESCE(R.ENVIRONMENT, '') <> 'TEST'""", [domain_id, layer])
    return [{k.lower(): v for k, v in r.items()} for r in found]


def record_eval(session, domain_id: str, layer: str, subject_kind: str, subject_id: str, result: Dict[str, Any]) -> None:
    """Keep one replay (silently nothing before V058)."""
    try:
        session.sql("""INSERT INTO CORE.CHANGE_EVAL (EVAL_ID, DOMAIN_ID, LAYER, SUBJECT_KIND, SUBJECT_ID, VERDICT, RESULT)
                       SELECT ?, ?, ?, ?, ?, ?, PARSE_JSON(?)""",
                    params=[str(uuid.uuid4()), domain_id, layer, subject_kind, subject_id, result.get("verdict") or "NO_EVIDENCE",
                            json.dumps(result, default=str)]).collect()
    except Exception:
        pass


def _settled(session, domain_id: str, keys: List[str]) -> set:
    """Learned keys a steward already decided (approved and in use, or rejected): not proposed again."""
    from services.common.sql import rows

    if not keys:
        return set()
    return {r["SOURCE_REFERENCE"] for r in rows(session, f"""SELECT DISTINCT SOURCE_REFERENCE FROM CORE.DOMAIN_KNOWLEDGE
                WHERE DOMAIN_ID = ? AND STATUS IN ('ACTIVE', 'REJECTED') AND SOURCE_REFERENCE IN ({', '.join('?' for _ in keys)})""",
                [domain_id, *keys])}


def learn_from_decisions(session, domain_id: str, layer: str = "SILVER", *, max_candidates: int = 8,
                         max_runs: int = 8) -> Dict[str, Any]:
    from services.knowledge.writer import remember

    layer = "GOLD" if str(layer).upper() == "GOLD" else "SILVER"
    evs = events(session, domain_id, layer)
    found = patterns.learn(evs, layer=layer)
    settled = _settled(session, domain_id, [c["key"] for c in found])
    candidates = [c for c in found if c["key"] not in settled][:max_candidates]
    result = replay.replay(session, domain_id, layer, changes=[[c] for c in candidates], max_runs=max_runs) if candidates \
        else {"runs": [], "baseline": None, "changes": [], "skipped": []}
    proposed, held_back = [], []
    for c, r in zip(candidates, result["changes"]):
        summary = {"verdict": r["verdict"], "baseline": result["baseline"], "candidate": r["candidate"], "delta": r["delta"],
                   "runs": len(result["runs"]), "flipped": r["flipped"][:10]}
        record_eval(session, domain_id, layer, "LEARNED", c["key"], summary)
        view = {"key": c["key"], "habit": c["habit"], "title": c["title"], "evidence": c["evidence"], "replay": summary}
        if r["verdict"] == "WORSE":
            held_back.append(view)
            continue
        knowledge_id = remember(session, domain_id=domain_id, kind=c["kind"], key=c["key"], title=c["title"],
                                content=c["content"], content_json={**c["content_json"], "replay": summary},
                                tags=["LEARNED", c["habit"]], origin="LEARNED", mode="review", layer=layer,
                                confidence=c["evidence"]["share"],
                                change_note=f"Learned from {c['evidence']['decisions']} reviewer decisions in "
                                            f"{c['evidence']['runs']} runs; replay: {r['verdict'].lower()}")
        proposed.append({**view, "knowledge_id": knowledge_id})
    return {"layer": layer, "decisions": len(evs), "found": len(found), "already_decided": len(settled & {c["key"] for c in found}),
            "proposed": proposed, "held_back": held_back, "baseline": result.get("baseline"), "runs": result.get("runs") or [],
            "skipped": result.get("skipped") or []}


def replay_knowledge(session, knowledge_id: str, layer: Optional[str] = None) -> Dict[str, Any]:
    """Replay one knowledge item (usually a proposal waiting in the inbox) on its domain's past runs."""
    from services.common.sql import rows, variant

    found = rows(session, """SELECT KNOWLEDGE_ID, DOMAIN_ID, KNOWLEDGE_TYPE, CONTENT_JSON, COALESCE(LAYER, 'ANY') AS LAYER
                               FROM CORE.DOMAIN_KNOWLEDGE WHERE KNOWLEDGE_ID = ?""", [knowledge_id])
    assert found, "knowledge item not found"
    k = found[0]
    assert k["DOMAIN_ID"], "a platform-wide item has no domain runs to replay on"
    assert k["KNOWLEDGE_TYPE"] in ("MAPPING_PATTERN", "TRANSFORMATION_RULE", "GLOSSARY", "BUSINESS_RULE"), \
        f"{k['KNOWLEDGE_TYPE'].lower().replace('_', ' ')} items do not change mapping, so there is nothing to replay"
    layer = (layer or (k["LAYER"] if k["LAYER"] in ("SILVER", "GOLD") else "SILVER")).upper()
    item = {"kind": k["KNOWLEDGE_TYPE"], "content_json": variant(k.get("CONTENT_JSON")) or {}}
    result = replay.replay(session, k["DOMAIN_ID"], layer, changes=[[item]])
    change = (result["changes"] or [{}])[0]
    summary = {"verdict": change.get("verdict", "NO_EVIDENCE"), "baseline": result["baseline"], "candidate": change.get("candidate"),
               "delta": change.get("delta"), "runs": len(result["runs"]), "flipped": (change.get("flipped") or [])[:10],
               "skipped": result.get("skipped") or []}
    record_eval(session, k["DOMAIN_ID"], layer, "KNOWLEDGE", knowledge_id, summary)
    return {"knowledge_id": knowledge_id, "layer": layer, **summary}
