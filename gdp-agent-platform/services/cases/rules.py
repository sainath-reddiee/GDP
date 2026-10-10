"""Case rules (pure): kinds, sources, statuses and the allowed status changes, severities and their SLA, the duplicate
fingerprint, and how each link kind is shown.

Status changes (from -> to). Moving to VERIFIED, RESOLVED, CLOSED or DUPLICATE needs CASE.RESOLVE; everything else
needs CASE.WORK (the route's privilege):

    NEW           -> TRIAGED, IN_PROGRESS, CLOSED, DUPLICATE
    TRIAGED       -> NEW, IN_PROGRESS, FIX_PROPOSED, CLOSED, DUPLICATE
    IN_PROGRESS   -> TRIAGED, FIX_PROPOSED, FIX_APPLIED, VERIFIED, CLOSED, DUPLICATE
    FIX_PROPOSED  -> IN_PROGRESS, FIX_APPLIED, CLOSED, DUPLICATE
    FIX_APPLIED   -> IN_PROGRESS (verification failed), VERIFIED, CLOSED
    VERIFIED      -> IN_PROGRESS, RESOLVED, CLOSED
    RESOLVED      -> IN_PROGRESS (reopen), CLOSED
    CLOSED        -> IN_PROGRESS (reopen)
    DUPLICATE     -> IN_PROGRESS (reopen)

RESOLVED is reached from VERIFIED. From any other open status it needs an override reason of at least 15 characters
(the reason is kept on the timeline). Reopening (RESOLVED, CLOSED or DUPLICATE back to IN_PROGRESS) needs CASE.WORK only.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

KINDS = ("DATA_BUG", "CODE_BUG", "DATA_QUALITY", "PIPELINE", "QUESTION")
SOURCES = ("APP_REPORT", "JIRA", "INCIDENT", "QA_FAILURE", "DQ_FAILURE")
STATUSES = ("NEW", "TRIAGED", "IN_PROGRESS", "FIX_PROPOSED", "FIX_APPLIED", "VERIFIED", "RESOLVED", "CLOSED", "DUPLICATE")
OPEN_STATUSES = ("NEW", "TRIAGED", "IN_PROGRESS", "FIX_PROPOSED", "FIX_APPLIED", "VERIFIED")
DONE_STATUSES = ("RESOLVED", "CLOSED", "DUPLICATE")
SEVERITIES = ("P1", "P2", "P3", "P4")
DEFAULT_SEVERITY = "P3"
LINK_KINDS = ("JIRA", "INCIDENT", "QA_RESULT", "DQ_RESULT", "RUN", "PR", "KNOWLEDGE", "CASE")
ARTIFACT_TYPES = ("REPRO_TEST", "STTM_CHANGE", "CORRECTION_SQL", "DBT_PATCH", "KNOWLEDGE_DRAFT")

# hours until the SLA is due, by severity; CORE.PLATFORM_CONFIG key CASES may override with {"sla_hours": {...}}
SLA_HOURS = {"P1": 4, "P2": 24, "P3": 72, "P4": 168}
RESOLVE_PRIVILEGE = "CASE.RESOLVE"
WORK_PRIVILEGE = "CASE.WORK"
NEEDS_RESOLVE = {"VERIFIED", "RESOLVED", "CLOSED", "DUPLICATE"}
OVERRIDE_MIN = 15

TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    "NEW": ("TRIAGED", "IN_PROGRESS", "CLOSED", "DUPLICATE"),
    "TRIAGED": ("NEW", "IN_PROGRESS", "FIX_PROPOSED", "CLOSED", "DUPLICATE"),
    "IN_PROGRESS": ("TRIAGED", "FIX_PROPOSED", "FIX_APPLIED", "VERIFIED", "CLOSED", "DUPLICATE"),
    "FIX_PROPOSED": ("IN_PROGRESS", "FIX_APPLIED", "CLOSED", "DUPLICATE"),
    "FIX_APPLIED": ("IN_PROGRESS", "VERIFIED", "CLOSED"),
    "VERIFIED": ("IN_PROGRESS", "RESOLVED", "CLOSED"),
    "RESOLVED": ("IN_PROGRESS", "CLOSED"),
    "CLOSED": ("IN_PROGRESS",),
    "DUPLICATE": ("IN_PROGRESS",),
}
REOPEN_FROM = {"RESOLVED", "CLOSED", "DUPLICATE"}

# severities of the sources, mapped to case severities
_RESULT_SEVERITY = {"CRITICAL": "P1", "BLOCKER": "P1", "HIGH": "P2", "MAJOR": "P2", "MEDIUM": "P3", "NORMAL": "P3",
                    "LOW": "P4", "MINOR": "P4", "INFO": "P4", "TRIVIAL": "P4"}
_JIRA_PRIORITY = {"HIGHEST": "P1", "BLOCKER": "P1", "CRITICAL": "P1", "HIGH": "P2", "MAJOR": "P2", "MEDIUM": "P3",
                  "LOW": "P4", "LOWEST": "P4", "MINOR": "P4", "TRIVIAL": "P4"}


def check_transition(current: str, target: str, privileges: Iterable[str], override_reason: Optional[str] = None
                     ) -> Tuple[bool, int, str]:
    """(allowed, HTTP status when not, message)."""
    current, target = str(current or "").upper(), str(target or "").upper()
    privs = set(privileges or ())
    if target not in STATUSES:
        return False, 422, f"status must be one of {', '.join(STATUSES)}"
    if target == current:
        return False, 409, f"The case is already {current}."
    holds = lambda p: p in privs or "*" in privs  # noqa: E731
    if target in NEEDS_RESOLVE and not holds(RESOLVE_PRIVILEGE):
        return False, 403, f"Moving a case to {target} needs the {RESOLVE_PRIVILEGE} privilege."
    if not holds(WORK_PRIVILEGE) and not holds(RESOLVE_PRIVILEGE):
        return False, 403, f"Changing a case needs the {WORK_PRIVILEGE} privilege."
    if target == "RESOLVED" and current != "VERIFIED":
        if current not in OPEN_STATUSES:
            return False, 409, f"A {current} case cannot be resolved; reopen it first."
        if len((override_reason or "").strip()) < OVERRIDE_MIN:
            return False, 409, (f"Resolve a case once its fix is VERIFIED, or give an override reason of at least "
                                f"{OVERRIDE_MIN} characters.")
        return True, 200, ""
    if target not in TRANSITIONS.get(current, ()):
        return False, 409, f"A case cannot move from {current} to {target}."
    return True, 200, ""


def is_reopen(current: str, target: str) -> bool:
    return str(current).upper() in REOPEN_FROM and str(target).upper() == "IN_PROGRESS"


def sla_hours(config: Optional[Mapping[str, Any]] = None) -> Dict[str, int]:
    hours = dict(SLA_HOURS)
    for k, v in ((config or {}).get("sla_hours") or {}).items():
        try:
            if str(k).upper() in hours and 0 < int(v) <= 24 * 365:
                hours[str(k).upper()] = int(v)
        except (TypeError, ValueError):
            continue
    return hours


def sla_due(severity: str, opened_at: datetime, config: Optional[Mapping[str, Any]] = None) -> datetime:
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)
    return opened_at + timedelta(hours=sla_hours(config).get(str(severity).upper(), SLA_HOURS[DEFAULT_SEVERITY]))


def severity_from_result(value: Any) -> str:
    """A QA test or data quality check severity (CRITICAL, HIGH, ...) as a case severity."""
    text = str(value or "").strip().upper()
    return text if text in SEVERITIES else _RESULT_SEVERITY.get(text, DEFAULT_SEVERITY)


def severity_from_jira(priority: Any) -> str:
    text = str(priority or "").strip().upper()
    return text if text in SEVERITIES else _JIRA_PRIORITY.get(text, DEFAULT_SEVERITY)


_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
_HEX = re.compile(r"\b[0-9a-f]{12,}\b", re.IGNORECASE)
_NUM = re.compile(r"\d+")
_NONWORD = re.compile(r"[^a-z0-9#]+")


def normalize_title(title: str) -> str:
    """Lower case, ids and numbers folded to '#', punctuation to single spaces."""
    text = _UUID.sub("#", str(title or "").lower())
    text = _HEX.sub("#", text)
    text = _NUM.sub("#", text)
    return _NONWORD.sub(" ", text).strip()


def fingerprint(title: str, target: Optional[str] = None) -> str:
    """sha1 of the normalized title and the target (table id, or run id when there is no table)."""
    return hashlib.sha1(f"{normalize_title(title)}|{str(target or '').strip().upper()}".encode("utf-8")).hexdigest()


def case_ref(number: Any) -> Optional[str]:
    try:
        return f"CASE-{int(number)}"
    except (TypeError, ValueError):
        return None


def link_url(kind: str, ref: str, jira_site: Optional[str] = None) -> Optional[str]:
    """Where a link opens: Jira in the browser, platform pages as paths of the web app, pull requests as given."""
    kind, ref = str(kind or "").upper(), str(ref or "")
    if kind == "JIRA":
        return f"{jira_site.rstrip('/')}/browse/{ref}" if jira_site and ref else None
    if kind == "INCIDENT":
        return f"/incidents/{ref}"
    if kind == "RUN":
        return f"/runs/{ref}"
    if kind == "CASE":
        return f"/qa/cases/{ref}"
    if kind == "PR" and re.match(r"^https://", ref):
        return ref
    return None


_PAGE_KEYS = ("path", "run_id", "table", "check_id", "test_id", "model")


def clean_page_context(value: Optional[Mapping[str, Any]]) -> Optional[Dict[str, str]]:
    """Only the known keys, as short strings."""
    if not value:
        return None
    out = {k: str(value[k])[:500] for k in _PAGE_KEYS if value.get(k) not in (None, "")}
    return out or None
