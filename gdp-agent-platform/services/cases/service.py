"""Case workflow over a store (services/cases/store.py in the API, an in-memory store in tests): open with dedupe and
claims, change status, assign, edit, comment, link and merge. Every write records a CASE_EVENT.

Opening a case:
1. a case from a source with a reference (a Jira key, an incident, a QA or DQ result) returns the open case of the same
   source and reference when there is one (created false);
2. otherwise an open case in the same domain with the same fingerprint (normalized title and target) is returned with
   duplicate_of, and the new source is linked to it instead of opening a second case;
3. otherwise the source and reference are claimed (MERGE on CASE_EVENT.IDEMPOTENCY_KEY, one generation per earlier case
   of that reference, so a resolved case does not block a new one) and the case is written. A caller that loses the
   claim gets the winner's case.
Visibility is the caller's business: the API passes can_see(domain_id), and an existing case in a domain the caller
cannot see is refused with 409 rather than shown.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.cases import rules
from services.cases.store import NOW


class CaseError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def claim_key(source: str, ref: str, generation: int) -> str:
    return f"case:{source}:{ref}:{int(generation)}"[:500]


def _hidden() -> CaseError:
    return CaseError(409, "A case for this already exists in a domain you cannot see. Ask a member of that domain.")


def _link_sources(store, case_id: str, links: List[Dict[str, Any]], actor: str) -> int:
    added = 0
    for link in links or []:
        if link.get("kind") and link.get("ref"):
            added += bool(store.add_link(case_id, link["kind"], str(link["ref"]), link.get("label"), link.get("url"), actor))
    return added


def open_case(store, spec: Dict[str, Any], actor: str, can_see: Callable[[Optional[str]], bool],
              config: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], bool, Optional[str]]:
    """(case, created, duplicate_of). spec: title, description, kind, severity, domain_id, target_table_id, run_id,
    source, source_ref, page_context, team_id, links [{kind, ref, label, url}]."""
    source = str(spec.get("source") or "APP_REPORT").upper()
    ref = str(spec.get("source_ref") or "").strip() or None
    links = list(spec.get("links") or [])

    def reported_again(existing: Dict[str, Any], why: str) -> None:
        added = _link_sources(store, existing["case_id"], links, actor)
        store.event(existing["case_id"], "reported_again", actor,
                    {"source": source, "source_ref": ref, "why": why, "links_added": added})

    if ref:
        existing = store.find_open_by_source(source, ref)
        if existing:
            if not can_see(existing.get("domain_id")):
                raise _hidden()
            return existing, False, None
    fp = rules.fingerprint(spec["title"], spec.get("target_table_id") or spec.get("run_id"))
    existing = store.find_open_by_fingerprint(spec.get("domain_id"), fp)
    if existing and can_see(existing.get("domain_id")):
        reported_again(existing, "fingerprint")
        return existing, False, existing["case_id"]
    key = None
    if ref:
        key = claim_key(source, ref, store.count_by_source(source, ref))
        if not store.claim(key):
            winner = store.claimed_case(key)
            found = store.get(winner) if winner else store.find_open_by_source(source, ref)
            if not found:
                raise CaseError(409, "This case is being opened by someone else right now; try again in a moment.")
            if not can_see(found.get("domain_id")):
                raise _hidden()
            return found, False, None
    severity = str(spec.get("severity") or rules.DEFAULT_SEVERITY).upper()
    row = {"case_id": str(uuid.uuid4()), "domain_id": spec.get("domain_id"), "title": spec["title"],
           "description": spec.get("description"), "kind": str(spec.get("kind") or "DATA_BUG").upper(), "source": source,
           "source_ref": ref, "page_context": spec.get("page_context"), "severity": severity,
           "assignee": spec.get("assignee"), "team_id": spec.get("team_id"), "target_table_id": spec.get("target_table_id"),
           "run_id": spec.get("run_id"), "fingerprint": fp, "opened_by": actor}
    detail = {"source": source, "source_ref": ref, "severity": severity}
    try:
        store.insert(row, rules.sla_hours(config)[severity])
    except Exception:
        if key:
            store.release_claim(key)
        raise
    if key:
        store.bind(key, row["case_id"], "opened", actor, detail)
    else:
        store.event(row["case_id"], "opened", actor, detail)
    _link_sources(store, row["case_id"], links, actor)
    return store.get(row["case_id"]) or row, True, None


def change_status(store, case: Dict[str, Any], target: str, actor: str, privileges, note: Optional[str] = None,
                  override_reason: Optional[str] = None) -> Dict[str, Any]:
    current, target = str(case["status"]).upper(), str(target or "").upper()
    ok, status, message = rules.check_transition(current, target, privileges, override_reason)
    if not ok:
        raise CaseError(status, message)
    fields: Dict[str, Any] = {"STATUS": target}
    reopen = rules.is_reopen(current, target)
    if target == "RESOLVED":
        fields.update({"RESOLVED_BY": actor, "RESOLVED_AT": NOW})
        if note:
            fields["RESOLUTION"] = note[:4000]
    elif target == "CLOSED":
        fields["CLOSED_AT"] = NOW
        if note and not case.get("resolution"):
            fields["RESOLUTION"] = note[:4000]
    if reopen:
        fields.update({"RESOLVED_BY": None, "RESOLVED_AT": None, "CLOSED_AT": None})
        if current == "DUPLICATE":
            fields["DUPLICATE_OF"] = None
    if not store.update(case["case_id"], fields, expect_status=current, reopen=reopen):
        raise CaseError(409, "The case changed in the meantime; reload it and try again.")
    detail = {"from": current, "to": target, "note": note}
    if override_reason and target == "RESOLVED" and current != "VERIFIED":
        detail["override_reason"] = override_reason
    store.event(case["case_id"], "reopened" if reopen else "status", actor, detail)
    return store.get(case["case_id"])


def assign(store, case: Dict[str, Any], assignee: Optional[str], actor: str) -> Dict[str, Any]:
    value = (assignee or "").strip().upper() or None
    store.update(case["case_id"], {"ASSIGNEE": value})
    store.event(case["case_id"], "assigned", actor, {"from": case.get("assignee"), "to": value})
    return store.get(case["case_id"])


def edit(store, case: Dict[str, Any], changes: Dict[str, Any], actor: str,
         config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """changes: upper-case column -> value, only the fields given. A new severity moves the SLA from the opening time."""
    changed = {k: v for k, v in changes.items() if v != case.get(k.lower())}
    if not changed:
        return case
    sla = rules.sla_hours(config)[changed["SEVERITY"]] if "SEVERITY" in changed else None
    store.update(case["case_id"], changed, sla_hours=sla)
    shown = {k.lower(): ("(changed)" if k == "DESCRIPTION" else v) for k, v in changed.items()}
    store.event(case["case_id"], "updated", actor, {"changes": shown})
    return store.get(case["case_id"])


def comment(store, case: Dict[str, Any], text: str, actor: str) -> None:
    store.event(case["case_id"], "comment", actor, {"text": text})


def add_link(store, case: Dict[str, Any], kind: str, ref: str, label: Optional[str], url: Optional[str], actor: str) -> bool:
    added = store.add_link(case["case_id"], kind, ref, label, url, actor)
    if added:
        store.event(case["case_id"], "link_added", actor, {"kind": kind, "ref": ref})
    return added


def remove_link(store, case: Dict[str, Any], link_id: str, actor: str) -> None:
    found = [link for link in store.links(case["case_id"]) if link["link_id"] == link_id]
    if not found or not store.remove_link(case["case_id"], link_id):
        raise CaseError(404, "Link not found")
    store.event(case["case_id"], "link_removed", actor, {"kind": found[0]["kind"], "ref": found[0]["ref"]})


def merge(store, case: Dict[str, Any], into: Dict[str, Any], actor: str, privileges) -> Dict[str, Any]:
    """Mark `case` a DUPLICATE of `into`, link both ways and move the other links to `into`."""
    if case["case_id"] == into["case_id"]:
        raise CaseError(400, "A case cannot be merged into itself.")
    if str(into.get("status")).upper() == "DUPLICATE":
        raise CaseError(409, f"{rules.case_ref(into.get('case_number'))} is itself a duplicate; merge into the case it duplicates.")
    current = str(case["status"]).upper()
    if current not in rules.OPEN_STATUSES:
        raise CaseError(409, f"A {current} case cannot be merged; reopen it first.")
    ok, status, message = rules.check_transition(current, "DUPLICATE", privileges)
    if not ok:
        raise CaseError(status, message)
    if not store.update(case["case_id"], {"STATUS": "DUPLICATE", "DUPLICATE_OF": into["case_id"]}, expect_status=current):
        raise CaseError(409, "The case changed in the meantime; reload it and try again.")
    moved = 0
    for link in store.links(case["case_id"]):
        if link["kind"] == "CASE":
            continue
        moved += bool(store.add_link(into["case_id"], link["kind"], link["ref"], link.get("label"), link.get("url"), actor))
        store.remove_link(case["case_id"], link["link_id"])
    src_ref, into_ref = rules.case_ref(case.get("case_number")), rules.case_ref(into.get("case_number"))
    store.add_link(case["case_id"], "CASE", into["case_id"], into_ref, rules.link_url("CASE", into["case_id"]), actor)
    store.add_link(into["case_id"], "CASE", case["case_id"], src_ref, rules.link_url("CASE", case["case_id"]), actor)
    store.event(case["case_id"], "merged_into", actor, {"into": into["case_id"], "into_ref": into_ref, "from": current})
    store.event(into["case_id"], "merged", actor, {"from_case": case["case_id"], "from_ref": src_ref, "links_moved": moved})
    return store.get(case["case_id"])
