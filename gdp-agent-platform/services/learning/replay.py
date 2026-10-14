"""Replay before approval: would a knowledge change make mapping better or worse?

The answer key is what reviewers already decided: the approved mappings of a domain's past runs in the layer. Each run
is ranked again by the same deterministic engine (features + scoring; the LLM only explains, it never re-ranks), once
with the knowledge in use and once with the proposed change added, holding out what was learned from that run's own
decisions so the run cannot grade itself. The difference in first-choice accuracy (and in transformations, when the
reviewer set one) is the change's effect, shown in the knowledge inbox before a steward approves it.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.learning import patterns


def _shape(expr: Optional[str], column: str) -> Optional[str]:
    return patterns.template(expr, column) or (re.sub(r"\s+", " ", expr.strip()).upper() if expr else None)


def evaluate(sources: List[Dict[str, Any]], targets: List[Dict[str, Any]], truth: Dict[str, Dict[str, Any]],
             knowledge: Dict[str, Any], cosines: Dict[Tuple[str, str], float], weights: Dict[str, Any],
             thresholds: Dict[str, Any], top_k: int = 3) -> Dict[str, Any]:
    """{hits, total, t_hits, t_total, picks: {source_column_id: (target, transformation)}} for one run (pure).
    truth: source_column_id -> {target_column, transformation} of the approved decisions (rejections are not scored)."""
    from services.mapping import features, scoring

    hits = total = t_hits = t_total = 0
    picks: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    for s in sources:
        answer = truth.get(s["source_column_id"])
        if not answer or not answer.get("target_column"):
            continue
        cands = [{"target": t, **features.all_features(s, t, knowledge, cosines.get((s["source_column_id"], t["target_column_id"])))}
                 for t in targets]
        ranked = scoring.rank_candidates(cands, weights, thresholds, top_k)
        first = ranked[0] if ranked else None
        target = str(first["target"]["column_name"]).upper() if first else None
        picks[s["source_column_id"]] = (target, (first or {}).get("transformation"))
        total += 1
        right = target == str(answer["target_column"]).upper()
        hits += right
        if answer.get("transformation"):
            t_total += 1
            t_hits += right and _shape((first or {}).get("transformation"), s["column_name"]) == \
                _shape(answer["transformation"], s["column_name"])
    return {"hits": hits, "total": total, "t_hits": t_hits, "t_total": t_total, "picks": picks}


def _pct(a: int, b: int) -> Optional[float]:
    return round(100.0 * a / b, 1) if b else None


def summarize(per_run: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    hits = sum(r[key]["hits"] for r in per_run)
    total = sum(r[key]["total"] for r in per_run)
    t_hits = sum(r[key]["t_hits"] for r in per_run)
    t_total = sum(r[key]["t_total"] for r in per_run)
    return {"target_pct": _pct(hits, total), "target_hits": hits, "columns": total,
            "transform_pct": _pct(t_hits, t_total), "transform_hits": t_hits, "transforms": t_total}


def verdict(baseline: Dict[str, Any], candidate: Dict[str, Any]) -> str:
    """BETTER, WORSE, SAME or NO_EVIDENCE: worse on either measure is WORSE."""
    if not baseline["columns"]:
        return "NO_EVIDENCE"
    d_t = candidate["target_hits"] - baseline["target_hits"]
    d_x = candidate["transform_hits"] - baseline["transform_hits"]
    if d_t < 0 or d_x < 0:
        return "WORSE"
    return "BETTER" if d_t > 0 or d_x > 0 else "SAME"


def _runs(session, domain_id: str, layer: str, max_runs: int) -> List[Dict[str, Any]]:
    from services.common.sql import rows

    return rows(session, """SELECT R.RUN_ID, R.RUN_NAME, R.DOMAIN_ID, R.TARGET_MODEL, COALESCE(R.PIPELINE, 'SILVER') AS PIPELINE,
                                   MAX(D.REVIEWED_AT) AS LAST_DECISION
                              FROM CORE.WORKFLOW_RUN R JOIN MAPPING.MAPPING_DECISION D ON D.RUN_ID = R.RUN_ID AND D.IS_CURRENT
                             WHERE R.DOMAIN_ID = ? AND COALESCE(R.PIPELINE, 'SILVER') = ? AND R.DELETED_AT IS NULL
                               AND COALESCE(R.ENVIRONMENT, '') <> 'TEST' AND D.DECISION <> 'REJECTED'
                             GROUP BY 1, 2, 3, 4, 5 ORDER BY LAST_DECISION DESC LIMIT ?""", [domain_id, layer, int(max_runs)])


def replay(session, domain_id: str, layer: str = "SILVER", changes: Optional[List[Iterable[Dict[str, Any]]]] = None,
           max_runs: int = 8) -> Dict[str, Any]:
    """Baseline and, for each change set in `changes` (a list of knowledge items {kind, content_json}), the
    accuracy on the domain's past runs: {runs, baseline, changes: [{candidate, verdict, delta, flipped}]}."""
    from services.common.sql import config_value, rows
    from services.mapping import features
    from services.mapping import procedures as mp

    layer = "GOLD" if str(layer).upper() == "GOLD" else "SILVER"
    changes = [list(c) for c in (changes or [])]
    per_run: List[Dict[str, Any]] = []
    skipped: List[str] = []
    try:
        cfg = mp._scoring_config(session, domain_id)
    except AssertionError as exc:
        return {"runs": [], "baseline": summarize([], "baseline"), "changes": [], "skipped": [str(exc)]}
    top_k = int(config_value(session, "MAPPING_TOP_K", 3))
    model = config_value(session, "EMBED_MODEL", mp.EMBED_MODEL_DEFAULT)
    for run in _runs(session, domain_id, layer, max_runs):
        try:
            target = mp.target_table(session, run)
            targets = features.mappable_targets(mp.target_columns(session, target["TARGET_TABLE_ID"]))
            sources = mp.source_columns(session, run["RUN_ID"])
        except AssertionError as exc:
            skipped.append(f"{run['RUN_NAME']}: {str(exc)[:160]}")
            continue
        names = {t["target_column_id"]: t["column_name"] for t in mp.target_columns(session, target["TARGET_TABLE_ID"])}
        truth = {d["SOURCE_COLUMN_ID"]: {"target_column": names.get(d["TARGET_COLUMN_ID"]), "transformation": d.get("TRANSFORMATION")}
                 for d in rows(session, """SELECT SOURCE_COLUMN_ID, TARGET_COLUMN_ID, TRANSFORMATION FROM MAPPING.MAPPING_DECISION
                                           WHERE RUN_ID = ? AND IS_CURRENT AND DECISION <> 'REJECTED'""", [run["RUN_ID"]])}
        if not sources or not truth or not targets:
            continue
        src_emb = mp.embeddings(session, "SOURCE_COLUMN", [(s["source_column_id"], mp._embed_text_source(s)) for s in sources], model)
        tgt_emb = mp.embeddings(session, "TARGET_COLUMN", [(t["target_column_id"], mp._embed_text_target(t)) for t in targets], model)
        cosines = mp.cosine_matrix(session, src_emb, tgt_emb)
        knowledge = mp.domain_knowledge(session, domain_id, run["RUN_ID"], target, holdout=True)
        entry = {"run_id": run["RUN_ID"], "run_name": run["RUN_NAME"], "target": target["TARGET_TABLE"],
                 "baseline": evaluate(sources, targets, truth, knowledge, cosines, cfg["weights"], cfg["thresholds"], top_k),
                 "changes": []}
        for change in changes:
            entry["changes"].append(evaluate(sources, targets, truth, patterns.apply(copy.deepcopy(knowledge), change),
                                             cosines, cfg["weights"], cfg["thresholds"], top_k))
        entry["_names"] = {s["source_column_id"]: s["column_name"] for s in sources}
        entry["_truth"] = truth
        per_run.append(entry)
    baseline = summarize(per_run, "baseline")
    results = []
    for i, change in enumerate(changes):
        view = [{"baseline": r["baseline"], "candidate": r["changes"][i]} for r in per_run]
        cand = summarize(view, "candidate")
        flipped = []
        for r in per_run:
            for sid, (before, _) in r["baseline"]["picks"].items():
                after = r["changes"][i]["picks"].get(sid, (None, None))[0]
                if after != before:
                    right = str(r["_truth"][sid]["target_column"]).upper()
                    flipped.append({"run": r["run_name"], "column": r["_names"].get(sid), "before": before, "after": after,
                                    "approved": right, "better": after == right})
        results.append({"candidate": cand, "verdict": verdict(baseline, cand), "flipped": flipped[:20],
                        "delta": {"target_pct": None if baseline["target_pct"] is None else round((cand["target_pct"] or 0) - baseline["target_pct"], 1),
                                  "transform_pct": None if baseline["transform_pct"] is None or cand["transform_pct"] is None
                                  else round(cand["transform_pct"] - baseline["transform_pct"], 1)}})
    return {"runs": [{"run_id": r["run_id"], "run_name": r["run_name"], "target": r["target"],
                      "columns": r["baseline"]["total"]} for r in per_run],
            "baseline": baseline, "changes": results, "skipped": skipped}
