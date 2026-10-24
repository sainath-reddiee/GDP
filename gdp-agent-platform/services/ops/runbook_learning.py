"""Runbook learning: the AIRFLOW_RUNBOOK knowledge grows from the logs people pull into the UI.

Every time a failed task's logs are pulled (the Airflow task log, an incident's Datadog logs):

  1. the error lines are picked out of the log and normalised (timestamps, ids, numbers and quoted values removed),
     so the same error on another run, table or date has the same fingerprint;
  2. each error is matched against the ACTIVE runbooks by their signals (services.ops.pipeline.match_runbooks);
  3. a sighting is counted in CORE.RUNBOOK_SIGHTING, once per task try (a refresh of the panel does not count again);
  4. an error no runbook knows becomes a PROPOSED runbook in the Knowledge inbox (origin OPS, review mode), with its
     signals already filled in; a person adds the fix and approves it, and from then on it matches like any other;
  5. when a fix resolves a failure (services.resolve.engine.run_fix) the fix is counted on the error's sighting.

What the UI and the RCA agent get back for a log: the matching runbooks with their solution (meaning, usual cause,
action, human steps), how often each was seen and which fix last worked. Everything here is best effort: the *_safe
entry points never raise, so pulling logs or running a fix behaves exactly as before when learning fails.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Optional

from services.ops.redact import redact

KIND = "AIRFLOW_RUNBOOK"
LEARNED_PREFIX = "ops.runbook.learned."
MAX_ERRORS = 5          # error lines learned from one log
MAX_MATCHES = 3         # runbooks returned for one log
MAX_TEXT = 256_000      # the end of a log is kept (the error is at the end)

# a line that reports an error: exception names, Snowflake error codes, failed states, Kubernetes failures
_ERROR_LINE = re.compile(
    r"(\b[A-Z][A-Za-z0-9_]*(Error|Exception|Timeout|NotFound)\b"
    r"|\b\d{6}\s*\(\w+\)"
    r"|\bERROR\b|\bCRITICAL\b|\bFAILED\b|Traceback|Task failed|exit code [1-9]|returned a failure"
    r"|OOMKilled|ImagePullBackOff|ErrImagePull|CrashLoopBackOff|exceeded quota|Broken DAG)")
_NOISE = re.compile(r"(Marking task as|Task exited with return code 0|INFO - |Dependencies all met|Starting attempt)")
_EXC_NAME = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Timeout|NotFound))\b")
_SF_CODE = re.compile(r"(?<!\d)(\d{6})\s*\(\w+\)")
_LEADER = re.compile(r"^\s*(\[[^\]]*\]\s*)*([\d\-:T., +Z]{10,}\s*)?(\{[^}]*\}\s*)?(ERROR|WARNING|CRITICAL|INFO)?\s*-?\s*")
_REPLACERS = [
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:?\d{2}|Z)?"), "<ts>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<id>"),
    (re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{16,}\b", re.I), "<hex>"),
    (re.compile(r"'[^']{0,200}'|\"[^\"]{0,200}\""), "<val>"),
    (re.compile(r"(?<![\w])(?!\d{6}(?!\d))\d+(\.\d+)?"), "<n>"),   # numbers, except 6-digit Snowflake error codes
    (re.compile(r"\s+"), " "),
]


# ---------------------------------------------------------------- reading a log

def error_lines(text: str, limit: int = MAX_ERRORS) -> List[str]:
    """The distinct error lines of a log, last first (the cause is usually the last error), at most `limit`."""
    out: List[str] = []
    seen = set()
    for raw in reversed(str(text or "")[-MAX_TEXT:].splitlines()):
        line = raw.strip()
        if not line or len(line) < 8 or _NOISE.search(line) or not _ERROR_LINE.search(line):
            continue
        key = signature(line)
        if key in seen:
            continue
        seen.add(key)
        out.append(line[:1000])
        if len(out) >= limit:
            break
    return out


def signature(line: str) -> str:
    """The error line without what changes from run to run (log prefix, timestamps, ids, numbers, quoted values)."""
    text = _LEADER.sub("", str(line or ""), count=1)
    for pattern, repl in _REPLACERS:
        text = pattern.sub(repl, text)
    return text.strip()[:1000]


def fingerprint(line: str) -> str:
    return hashlib.md5(signature(line).lower().encode("utf-8")).hexdigest()


def proposed_signals(line: str) -> List[str]:
    """Signals for a runbook learned from one error line: its exception names, its Snowflake error codes and its
    longest literal phrase (the text between the parts that change), so it matches the same error next time."""
    signals: List[str] = []
    for name in _EXC_NAME.findall(line):
        if name not in signals and name not in ("Error", "Exception"):
            signals.append(name)
    for code in _SF_CODE.findall(line):
        if code not in signals:
            signals.append(code)
    pieces = [p.strip(" :;,.-()[]") for p in re.split(r"<ts>|<id>|<hex>|<val>|<n>", signature(line))]
    phrase = max(pieces, key=len) if pieces else ""
    if len(phrase) >= 12 and phrase not in signals:
        signals.append(phrase[:160])
    return signals[:6]


# ---------------------------------------------------------------- the runbooks

def _json(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _runbooks(db: Any) -> List[Dict[str, Any]]:
    """The current runbooks, ACTIVE and PROPOSED (a proposed one is not proposed again)."""
    return db.query("""SELECT SOURCE_REFERENCE, TITLE, CONTENT, CONTENT_JSON, STATUS FROM CORE.DOMAIN_KNOWLEDGE
                        WHERE KNOWLEDGE_TYPE = 'AIRFLOW_RUNBOOK' AND STATUS IN ('ACTIVE', 'PROPOSED')
                          AND (IS_CURRENT OR STATUS = 'PROPOSED')
                        ORDER BY SOURCE_REFERENCE LIMIT 500""") or []


def stats(db: Any, keys: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """{runbook key: {seen, fixed, last_fix, last_seen_at}} from the sightings."""
    keys = [k for k in dict.fromkeys(keys) if k][:50]
    if not keys:
        return {}
    marks = ", ".join(["%s"] * len(keys))
    found = db.query(f"""SELECT RUNBOOK_KEY, SUM(SEEN_COUNT) AS SEEN, SUM(FIX_SUCCESS_COUNT) AS FIXED,
                                MAX_BY(LAST_FIX, LAST_FIXED_AT) AS LAST_FIX, MAX(LAST_SEEN_AT) AS LAST_SEEN_AT
                           FROM CORE.RUNBOOK_SIGHTING WHERE RUNBOOK_KEY IN ({marks}) GROUP BY RUNBOOK_KEY""", tuple(keys))
    return {r["runbook_key"]: {"seen": int(r.get("seen") or 0), "fixed": int(r.get("fixed") or 0),
                               "last_fix": r.get("last_fix"), "last_seen_at": str(r.get("last_seen_at") or "") or None}
            for r in (found or [])}


def solution(item: Dict[str, Any], stat: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What the UI shows for a matched runbook: the error it explains and how to fix it."""
    body = _json(item.get("content_json"))
    stat = stat or {}
    return {"key": item.get("source_reference"), "title": item.get("title"), "status": item.get("status") or "ACTIVE",
            "meaning": body.get("meaning"), "usual_cause": body.get("usual_cause"), "action": body.get("action"),
            "human_steps": list(body.get("human_steps") or []), "seen": stat.get("seen", 0), "fixed": stat.get("fixed", 0),
            "last_fix": stat.get("last_fix")}


# ---------------------------------------------------------------- sightings

def _sighting(db: Any, fp: str, key: Optional[str], matched: bool, line: str, source: str, dag_id: Optional[str],
              task_id: Optional[str], try_key: Optional[str]) -> None:
    """Count the error once per task try: a new fingerprint is inserted, a known one counted when the try is new."""
    db.execute("""MERGE INTO CORE.RUNBOOK_SIGHTING t
                  USING (SELECT %s AS FINGERPRINT, %s AS RUNBOOK_KEY, %s AS MATCHED, %s AS SIGNATURE, %s AS SAMPLE,
                                %s AS SOURCE, %s AS LAST_DAG_ID, %s AS LAST_TASK_ID, %s AS LAST_TRY_KEY) s
                     ON t.FINGERPRINT = s.FINGERPRINT
                  WHEN MATCHED THEN UPDATE SET
                       SEEN_COUNT = t.SEEN_COUNT + IFF(EQUAL_NULL(t.LAST_TRY_KEY, s.LAST_TRY_KEY), 0, 1),
                       RUNBOOK_KEY = COALESCE(s.RUNBOOK_KEY, t.RUNBOOK_KEY), MATCHED = s.MATCHED OR t.MATCHED,
                       SAMPLE = s.SAMPLE, SOURCE = s.SOURCE, LAST_DAG_ID = s.LAST_DAG_ID, LAST_TASK_ID = s.LAST_TASK_ID,
                       LAST_TRY_KEY = s.LAST_TRY_KEY, LAST_SEEN_AT = CURRENT_TIMESTAMP()
                  WHEN NOT MATCHED THEN INSERT (FINGERPRINT, RUNBOOK_KEY, MATCHED, SIGNATURE, SAMPLE, SOURCE, LAST_DAG_ID,
                                                LAST_TASK_ID, LAST_TRY_KEY)
                       VALUES (s.FINGERPRINT, s.RUNBOOK_KEY, s.MATCHED, s.SIGNATURE, s.SAMPLE, s.SOURCE, s.LAST_DAG_ID,
                               s.LAST_TASK_ID, s.LAST_TRY_KEY)""",
               (fp, key, bool(matched), signature(line)[:1000], redact(line)[:4000], source, dag_id, task_id, try_key))


def _propose(db: Any, line: str, source: str, dag_id: Optional[str], task_id: Optional[str]) -> Optional[str]:
    """A PROPOSED runbook for an error no runbook explains (the Knowledge inbox; a person adds the fix and approves)."""
    from services.knowledge.procedures import GENERAL_DOMAIN, ensure_domain
    from services.knowledge.writer import remember
    from services.ops.sqlio import DbSession

    signals = proposed_signals(line)
    if not signals:
        return None
    key = LEARNED_PREFIX + fingerprint(line)[:16]
    sample = redact(line)[:600]
    where = f"{dag_id}.{task_id}" if dag_id and task_id else (dag_id or "a GDP task")
    title = f"Learned: {signals[0]}"[:190]
    content = (f"Signals: {', '.join(repr(s) for s in signals)}. Seen in the {source.title()} logs of {where}: {sample} "
               "Meaning: not yet described. Usual causes: not yet described. Repair: describe the fix that resolved "
               "this error (and the action, if one of the catalogue actions applies) before approving this runbook.")
    session = DbSession(db)
    domain_id = ensure_domain(session, GENERAL_DOMAIN, "Targets registered without a domain pack")
    remember(session, domain_id=domain_id, kind=KIND, key=key, title=title, content=content,
             # the same shape as the Knowledge editor's AIRFLOW_RUNBOOK template, so the reviewer only fills it in
             content_json={"signals": signals, "meaning": "", "usual_cause": "", "action": None, "human_steps": [],
                           "learned_from": {"source": source, "dag_id": dag_id, "task_id": task_id, "sample": sample}},
             tags=["AIRFLOW", "RUNBOOK", "LEARNED", source.upper()], origin="OPS", mode="review",
             change_note=f"Learned from {source.lower()} logs of {where}")
    return key


# ---------------------------------------------------------------- entry points

def learn(db: Any, text: str, source: str, dag_id: Optional[str] = None, task_id: Optional[str] = None,
          try_key: Optional[str] = None, propose: bool = True) -> Dict[str, Any]:
    """Learn from one pulled log. {matches: [solution], unmatched: [error line], proposed: [runbook key]}."""
    from services.ops.pipeline import match_runbooks

    source = (source or "AIRFLOW").upper()
    lines = error_lines(text)
    out: Dict[str, Any] = {"matches": [], "unmatched": [], "proposed": []}
    if not lines:
        return out
    items = [dict(i, source_reference=i.get("source_reference")) for i in _runbooks(db)]
    active = [i for i in items if str(i.get("status") or "ACTIVE").upper() == "ACTIVE"]
    proposed = {i.get("source_reference"): i for i in items if str(i.get("status") or "").upper() == "PROPOSED"}
    for line in lines:
        fp = fingerprint(line)
        hit = match_runbooks(active, line, 1)
        if hit:
            _sighting(db, fp, hit[0]["source_reference"], True, line, source, dag_id, task_id, try_key)
            continue
        key = LEARNED_PREFIX + fp[:16]
        if key not in proposed and propose:
            key = _propose(db, line, source, dag_id, task_id) or key
            out["proposed"].append(key)
        _sighting(db, fp, key, False, line, source, dag_id, task_id, try_key)
        out["unmatched"].append(redact(line)[:500])
    matched = match_runbooks(active, "\n".join(lines), MAX_MATCHES) or []
    # a learned runbook still waiting in the inbox is shown too, so people see it was already proposed
    pending = [proposed[k] for k in dict.fromkeys(LEARNED_PREFIX + fingerprint(l)[:16] for l in lines) if k in proposed][:1]
    known = stats(db, [m["source_reference"] for m in matched + pending])
    out["matches"] = [solution(m, known.get(m["source_reference"])) for m in matched + pending]
    return out


def learn_safe(db: Any, text: str, source: str, dag_id: Optional[str] = None, task_id: Optional[str] = None,
               try_key: Optional[str] = None) -> Dict[str, Any]:
    """learn(), never raising: the logs panel works the same when learning cannot run."""
    try:
        return learn(db, text, source, dag_id, task_id, try_key)
    except Exception:
        return {"matches": [], "unmatched": [], "proposed": []}


def record_fix(db: Any, failure: Dict[str, Any], issue: Dict[str, Any]) -> int:
    """A fix resolved this failure: count it on the sightings of the failure's errors. Returns the rows counted."""
    text = f"{failure.get('exception_class') or ''}: {failure.get('exception_text') or ''}\n{failure.get('error_excerpt') or ''}"
    lines = error_lines(text) or [text.strip()]
    fix = str(issue.get("title") or issue.get("procedure_fqn") or issue.get("skill_name") or issue.get("issue_id") or "fix")[:512]
    counted = 0
    for line in lines:
        if not line:
            continue
        counted += int(db.execute_count("""UPDATE CORE.RUNBOOK_SIGHTING SET FIX_SUCCESS_COUNT = FIX_SUCCESS_COUNT + 1,
                                                  LAST_FIX = %s, LAST_FIXED_AT = CURRENT_TIMESTAMP()
                                            WHERE FINGERPRINT = %s""", (fix, fingerprint(line))) or 0)
    return counted


def record_fix_safe(db: Any, failure: Dict[str, Any], issue: Dict[str, Any]) -> int:
    try:
        return record_fix(db, failure, issue)
    except Exception:
        return 0
