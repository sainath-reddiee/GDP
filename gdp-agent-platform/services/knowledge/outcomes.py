"""Did the knowledge help? KNOWLEDGE.KNOWLEDGE_OUTCOME (V047) joins what went into a prompt with what reviewers then
decided on what the stage proposed.

  KNOWLEDGE.KNOWLEDGE_USAGE   which knowledge a stage used, per run (services/knowledge/writer.record_usage)
  MAPPING                     the reviewer's decision on each rank-1 candidate: kept as proposed (ACCEPTED), changed
                              to another target or modified (CORRECTED), or rejected (REJECTED)
  SODA                        each reviewed check: approved as generated (ACCEPTED), approved after a change
                              (CORRECTED), rejected (REJECTED)

An item written for a target column (CONTENT_JSON.target_column) is judged on the decisions about that column in the
runs that used it; an item without one on all of the stage's decisions in the run, counted as a single observation per
run so a general rule is not credited with every column. HELPFULNESS = (accepted + corrected / 2 + 1) / (observed + 2):
0.5 with no evidence, moving towards 0 or 1 only as decisions accumulate.

Refreshed for the items a run used when its decisions are saved (refresh_run, never raises), and for everything on
demand (refresh). Ranking in services/knowledge/select.py and mapping history weights read it.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.common.sql import rows, variant

TABLE = "KNOWLEDGE.KNOWLEDGE_OUTCOME"
STAGES = ("MAPPING", "SODA", "QA")
OUTCOMES = ("ACCEPTED", "CORRECTED", "REJECTED")
MIN_OBSERVED = 3  # decisions before helpfulness moves a ranking


def helpfulness(accepted: float, corrected: float, observed: float) -> float:
    return round((accepted + corrected / 2 + 1) / (observed + 2), 4)


def _name(value: Any) -> str:
    return str(value or "").strip().upper().split(".")[-1]


def attribute(usages: Iterable[Dict[str, Any]], contents: Dict[str, Dict[str, Any]],
              outcomes: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Outcome counts per (lineage, stage). Pure.

    usages:   {lineage_id, knowledge_id, run_id, stage, uses, last_used} (one per lineage, stage and run)
    contents: lineage_id -> CONTENT_JSON of its current version
    outcomes: {run_id, stage, table, column, outcome} (one per reviewed proposal)"""
    by_run: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for o in outcomes:
        if o.get("outcome") in OUTCOMES:
            by_run[(str(o["run_id"]), str(o["stage"]))].append(o)
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for u in usages:
        key = (str(u["lineage_id"]), str(u["stage"]))
        stat = out.setdefault(key, {"knowledge_id": u.get("knowledge_id"), "uses": 0, "runs": 0, "observed": 0.0,
                                    "accepted": 0.0, "corrected": 0.0, "rejected": 0.0, "last_used": None})
        stat["uses"] += int(u.get("uses") or 1)
        stat["runs"] += 1
        if u.get("last_used") and (stat["last_used"] is None or str(u["last_used"]) > str(stat["last_used"])):
            stat["last_used"], stat["knowledge_id"] = u["last_used"], u.get("knowledge_id") or stat["knowledge_id"]
        content = contents.get(str(u["lineage_id"])) or {}
        column, table = _name(content.get("target_column")), _name(content.get("target_table"))
        decided = [o for o in by_run.get((str(u["run_id"]), str(u["stage"])), [])
                   if not table or not o.get("table") or _name(o["table"]) == table]
        if column:
            for o in decided:
                if _name(o.get("column")) == column:
                    stat["observed"] += 1
                    stat[o["outcome"].lower()] += 1
        elif decided:  # a general item: the run's decisions for the stage, as one observation
            n = len(decided)
            stat["observed"] += 1
            for name in OUTCOMES:
                stat[name.lower()] += sum(1 for o in decided if o["outcome"] == name) / n
    for stat in out.values():
        stat["helpfulness"] = helpfulness(stat["accepted"], stat["corrected"], stat["observed"])
    return out


# ---------------------------------------------------------------- Snowflake


def _in(values: Iterable[str]) -> str:
    return json.dumps(sorted({str(v) for v in values if v})[:5000])


MAPPING_OUTCOMES = """
SELECT C.RUN_ID, 'MAPPING' AS STAGE, TT.TARGET_TABLE AS TBL, T.COLUMN_NAME AS COL,
       CASE WHEN D.DECISION = 'REJECTED' THEN 'REJECTED'
            WHEN D.DECISION = 'APPROVED' AND D.TARGET_COLUMN_ID = C.TARGET_COLUMN_ID THEN 'ACCEPTED'
            ELSE 'CORRECTED' END AS OUTCOME
  FROM MAPPING.MAPPING_CANDIDATE C
  JOIN MAPPING.MAPPING_DECISION D ON D.RUN_ID = C.RUN_ID AND D.SOURCE_COLUMN_ID = C.SOURCE_COLUMN_ID AND D.IS_CURRENT
  JOIN KNOWLEDGE.TARGET_COLUMN_REGISTRY T ON T.TARGET_COLUMN_ID = C.TARGET_COLUMN_ID
  LEFT JOIN KNOWLEDGE.TARGET_TABLE_REGISTRY TT ON TT.TARGET_TABLE_ID = T.TARGET_TABLE_ID
 WHERE C.IS_CURRENT AND C.RANK = 1 AND ARRAY_CONTAINS(C.RUN_ID::VARIANT, PARSE_JSON(?))"""

SODA_OUTCOMES = """
SELECT RUN_ID, 'SODA' AS STAGE, TARGET_TABLE AS TBL, TARGET_COLUMN AS COL,
       CASE WHEN STATUS = 'REJECTED' THEN 'REJECTED'
            WHEN {same} THEN 'ACCEPTED' ELSE 'CORRECTED' END AS OUTCOME
  FROM CONTRACT.SODA_EXPECTATION_REGISTRY
 WHERE IS_CURRENT AND REVIEWED_BY IS NOT NULL AND STATUS IN ('APPROVED', 'REJECTED')
   AND ARRAY_CONTAINS(RUN_ID::VARIANT, PARSE_JSON(?))"""


QA_OUTCOMES = """
SELECT T.RUN_ID, 'QA' AS STAGE, SPLIT_PART(COALESCE(T.TARGET_FQN, ''), '.', -1) AS TBL, T.TARGET_COLUMN AS COL,
       CASE WHEN T.IS_DELETED THEN 'REJECTED'
            WHEN EXISTS (SELECT 1 FROM CONTRACT.QA_TEST_VERSION V WHERE V.TEST_ID = T.TEST_ID AND V.CHANGE = 'EDITED')
                 THEN 'CORRECTED'
            ELSE 'ACCEPTED' END AS OUTCOME
  FROM CONTRACT.QA_TEST_CASE T
 WHERE T.ORIGIN = 'AI' AND ARRAY_CONTAINS(T.RUN_ID::VARIANT, PARSE_JSON(?))
   AND (T.IS_DELETED OR EXISTS (SELECT 1 FROM QUALITY.QA_RESULT R WHERE R.TEST_ID = T.TEST_ID))"""


def _outcomes(session, runs: List[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        out += rows(session, MAPPING_OUTCOMES, [_in(runs)])
    except Exception:
        pass
    try:  # QA: an AI test kept and run is accepted, edited is corrected, deleted is rejected
        out += rows(session, QA_OUTCOMES, [_in(runs)])
    except Exception:
        pass
    try:  # V045 keeps what the generator proposed; before it an approval counts as accepted
        out += rows(session, SODA_OUTCOMES.format(same="EQUAL_NULL(GENERATED_DEFINITION, CHECK_DEFINITION) "
                                                       "OR GENERATED_DEFINITION IS NULL"), [_in(runs)])
    except Exception:
        try:
            out += rows(session, SODA_OUTCOMES.format(same="TRUE"), [_in(runs)])
        except Exception:
            pass
    return [{"run_id": r["RUN_ID"], "stage": r["STAGE"], "table": r.get("TBL"), "column": r.get("COL"),
             "outcome": r["OUTCOME"]} for r in out]


def refresh(session, lineages: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Recompute the outcomes of these lineages (all used ones when None) from their whole usage history."""
    wanted = sorted({str(x) for x in lineages or [] if x}) if lineages is not None else None
    if wanted is not None and not wanted:
        return {"items": 0}
    where = "" if wanted is None else " AND ARRAY_CONTAINS(LINEAGE_ID::VARIANT, PARSE_JSON(?))"
    usage = rows(session, f"""SELECT LINEAGE_ID, STAGE, RUN_ID, MAX(KNOWLEDGE_ID) AS KNOWLEDGE_ID, COUNT(*) AS USES,
                                     MAX(USED_AT) AS LAST_USED
                                FROM KNOWLEDGE.KNOWLEDGE_USAGE
                               WHERE LINEAGE_ID IS NOT NULL AND RUN_ID IS NOT NULL
                                 AND ARRAY_CONTAINS(STAGE::VARIANT, PARSE_JSON(?)){where}
                               GROUP BY 1, 2, 3""", [json.dumps(list(STAGES)), *([_in(wanted)] if wanted else [])])
    if not usage:
        return {"items": 0}
    lineage_ids = {r["LINEAGE_ID"] for r in usage}
    contents = {r["LINEAGE_ID"]: variant(r["CONTENT_JSON"]) or {} for r in rows(
        session, """SELECT LINEAGE_ID, CONTENT_JSON FROM KNOWLEDGE.DOMAIN_KNOWLEDGE
                     WHERE IS_CURRENT AND ARRAY_CONTAINS(LINEAGE_ID::VARIANT, PARSE_JSON(?))""", [_in(lineage_ids)])}
    contents = {k: (v if isinstance(v, dict) else {}) for k, v in contents.items()}
    stats = attribute([{"lineage_id": r["LINEAGE_ID"], "knowledge_id": r["KNOWLEDGE_ID"], "run_id": r["RUN_ID"],
                        "stage": r["STAGE"], "uses": r["USES"], "last_used": r["LAST_USED"]} for r in usage],
                      contents, _outcomes(session, [r["RUN_ID"] for r in usage]))
    payload = [{"lineage_id": l, "stage": s, **{k: (str(v) if k == "last_used" and v is not None else v)
                                                   for k, v in st.items()}} for (l, s), st in stats.items()]
    session.sql(f"""MERGE INTO {TABLE} T
                    USING (SELECT V.VALUE:lineage_id::VARCHAR AS LINEAGE_ID, V.VALUE:stage::VARCHAR AS STAGE,
                                  V.VALUE:knowledge_id::VARCHAR AS KNOWLEDGE_ID, V.VALUE:uses::NUMBER AS USES,
                                  V.VALUE:runs::NUMBER AS RUNS, V.VALUE:observed::FLOAT AS OBSERVED,
                                  V.VALUE:accepted::FLOAT AS ACCEPTED, V.VALUE:corrected::FLOAT AS CORRECTED,
                                  V.VALUE:rejected::FLOAT AS REJECTED, V.VALUE:helpfulness::FLOAT AS HELPFULNESS,
                                  TRY_TO_TIMESTAMP_LTZ(V.VALUE:last_used::VARCHAR) AS LAST_USED_AT
                             FROM TABLE(FLATTEN(INPUT => PARSE_JSON(?))) V) S
                       ON T.LINEAGE_ID = S.LINEAGE_ID AND T.STAGE = S.STAGE
                     WHEN MATCHED THEN UPDATE SET KNOWLEDGE_ID = S.KNOWLEDGE_ID, USES = S.USES, RUNS = S.RUNS,
                          OBSERVED = S.OBSERVED, ACCEPTED = S.ACCEPTED, CORRECTED = S.CORRECTED, REJECTED = S.REJECTED,
                          HELPFULNESS = S.HELPFULNESS, LAST_USED_AT = S.LAST_USED_AT, COMPUTED_AT = CURRENT_TIMESTAMP()
                     WHEN NOT MATCHED THEN INSERT (LINEAGE_ID, STAGE, KNOWLEDGE_ID, USES, RUNS, OBSERVED, ACCEPTED,
                          CORRECTED, REJECTED, HELPFULNESS, LAST_USED_AT)
                          VALUES (S.LINEAGE_ID, S.STAGE, S.KNOWLEDGE_ID, S.USES, S.RUNS, S.OBSERVED, S.ACCEPTED,
                                  S.CORRECTED, S.REJECTED, S.HELPFULNESS, S.LAST_USED_AT)""",
                params=[json.dumps(payload, default=str)]).collect()
    return {"items": len(payload), "observed": round(sum(p["observed"] for p in payload), 2)}


def refresh_run(session, run_id: str, stage: Optional[str] = None) -> Dict[str, Any]:
    """refresh() for the items a run used (in one stage, or the tracked stages). Never raises: the decision that
    triggered it is already saved, and before V047 there is nowhere to write."""
    try:
        stages = [stage] if stage else list(STAGES)
        used = [r["LINEAGE_ID"] for r in rows(session, """SELECT DISTINCT LINEAGE_ID FROM KNOWLEDGE.KNOWLEDGE_USAGE
                                                           WHERE RUN_ID = ? AND LINEAGE_ID IS NOT NULL
                                                             AND ARRAY_CONTAINS(STAGE::VARIANT, PARSE_JSON(?))""",
                                               [run_id, json.dumps(stages)])]
        return refresh(session, used)
    except Exception as exc:
        return {"items": 0, "error": str(exc)[:300]}


def for_lineages(session, lineages: Iterable[str], stage: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Outcome per lineage (one stage, or summed over the tracked stages); {} when unavailable."""
    wanted = [l for l in lineages if l]
    if not wanted:
        return {}
    try:
        found = rows(session, f"""SELECT LINEAGE_ID, SUM(OBSERVED) AS OBSERVED, SUM(ACCEPTED) AS ACCEPTED,
                                         SUM(CORRECTED) AS CORRECTED, SUM(REJECTED) AS REJECTED, SUM(USES) AS USES
                                    FROM {TABLE} WHERE ARRAY_CONTAINS(LINEAGE_ID::VARIANT, PARSE_JSON(?))
                                     {"AND STAGE = ?" if stage else ""} GROUP BY 1""",
                     [_in(wanted), *([stage] if stage else [])])
    except Exception:
        return {}
    out = {}
    for r in found:
        observed = float(r["OBSERVED"] or 0)
        out[r["LINEAGE_ID"]] = {"observed": observed, "uses": int(r["USES"] or 0),
                                "helpfulness": helpfulness(float(r["ACCEPTED"] or 0), float(r["CORRECTED"] or 0), observed)}
    return out


def weight(outcome: Optional[Dict[str, Any]]) -> float:
    """A multiplier for evidence from this item: 1.0 until MIN_OBSERVED decisions, then 0.5 (always overridden) to
    1.5 (always kept)."""
    if not outcome or float(outcome.get("observed") or 0) < MIN_OBSERVED:
        return 1.0
    return round(0.5 + float(outcome["helpfulness"]), 4)
