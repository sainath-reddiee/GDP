"""Where an onboarding request stands and who acts next, worked out from what the platform already holds.

A request (CORE.ONBOARDING_REQUEST, V075) names a domain (or a domain still to create) and the sources it needs
(CORE.ONBOARDING_REQUEST_ITEM). Nothing about its progress is typed in by hand: `assess` reads the domain, each
source's registration and health, and the runs that onboard them, and says

- each source's status and the one thing it waits on (a privilege, so the right role's queue shows it);
- the request's stage, the earliest of its parts: DOMAIN (the domain does not exist), ENABLE (a source is not
  registered, reachable or landed: the Data OS Administrator), DESIGN (no approved domain model, no run yet, or a run
  being profiled and mapped: the Data Designer and the gate approvers), BUILD (code generation and validation: the
  Forward Engineering Lead), REVIEW (code review: the Data Developer), DONE;
- what needs attention (a failed or cancelled run, a source or domain that is gone, a run of another domain).

Pure: the caller (apps/api/app/requests_api.py) loads the facts and writes what changed. No I/O here.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

STAGES = ["DOMAIN", "ENABLE", "DESIGN", "BUILD", "REVIEW", "DONE"]
TYPES = ("SOURCE", "DOMAIN", "PRODUCT")
# a new domain, source or product, or more from one already on the platform (V078)
INTENTS = ("NEW", "ENHANCE")
PRIORITIES = ("LOW", "MEDIUM", "HIGH")
OPEN, DONE, CANCELLED, REJECTED = "OPEN", "DONE", "CANCELLED", "REJECTED"

# a run's workflow state -> (request stage, the privilege that acts next, what is awaited); the states are those of
# snowflake/database/seed/workflow_graph.json, grouped as the /os lifecycle groups them (apps/web/lib/os/lifecycle.ts)
_ENABLE = "Data OS Administrator"
_DESIGN = "Data Designer"
_BUILD = "Forward Engineering Lead"
RUN_STATES: Dict[str, tuple] = {
    "CREATED": ("ENABLE", "SOURCE.CONNECT", f"{_ENABLE}: choose and register the source for the run"),
    "SOURCE_REGISTERED": ("ENABLE", "SOURCE.CONNECT", f"{_ENABLE}: validate access to the source"),
    "ACCESS_VALIDATION": ("ENABLE", "SOURCE.CONNECT", f"{_ENABLE}: source access is being checked"),
    "ACCESS_APPROVED": ("ENABLE", "SOURCE.CONNECT", f"{_ENABLE}: land the tables"),
    "LANDING_PENDING": ("ENABLE", "SOURCE.CONNECT", f"{_ENABLE}: land the tables"),
    "LANDING_RUNNING": ("ENABLE", "SOURCE.CONNECT", f"{_ENABLE}: tables are landing"),
    "LANDING_COMPLETE": ("DESIGN", "RUN.OPERATE", f"{_DESIGN}: profile the landed tables"),
    "PROFILING_PENDING": ("DESIGN", "RUN.OPERATE", f"{_DESIGN}: profile the landed tables"),
    "PROFILING_RUNNING": ("DESIGN", "RUN.OPERATE", f"{_DESIGN}: profiling is running"),
    "PROFILING_COMPLETE": ("DESIGN", "RUN.OPERATE", f"{_DESIGN}: match the domain"),
    "DOMAIN_IDENTIFIED": ("DESIGN", "RUN.OPERATE", f"{_DESIGN}: generate the field mapping"),
    "MAPPING_PENDING": ("DESIGN", "MAPPING.DECIDE", f"{_DESIGN}: decide the field mapping"),
    "MAPPING_REVIEW": ("DESIGN", "MAPPING.APPROVE", "Model approver: approve the field mapping"),
    "MAPPING_APPROVED": ("DESIGN", "RUN.OPERATE", f"{_DESIGN}: generate the transformations and joins"),
    "STTM_PENDING": ("DESIGN", "STTM.EDIT", f"{_DESIGN}: complete the transformations and joins"),
    "STTM_REVIEW": ("DESIGN", "STTM.APPROVE", "Data Mapping approver: approve the transformations and joins"),
    "STTM_APPROVED": ("DESIGN", "RUN.OPERATE", f"{_DESIGN}: generate the data quality checks"),
    "SODA_PENDING": ("DESIGN", "SODA.EDIT", f"{_DESIGN}: decide the data quality checks"),
    "SODA_REVIEW": ("DESIGN", "SODA.APPROVE", "DQ approver: approve the data quality checks"),
    "SODA_APPROVED": ("BUILD", "RUN.OPERATE", f"{_BUILD}: generate the code"),
    "DBT_PENDING": ("BUILD", "RUN.OPERATE", f"{_BUILD}: generate the code"),
    "DBT_GENERATING": ("BUILD", "RUN.OPERATE", f"{_BUILD}: code is being generated"),
    "VALIDATION_PENDING": ("BUILD", "RUN.OPERATE", f"{_BUILD}: validate the generated code"),
    "VALIDATION_RUNNING": ("BUILD", "RUN.OPERATE", f"{_BUILD}: validation is running"),
    "VALIDATION_PASSED": ("BUILD", "RUN.OPERATE", f"{_BUILD}: send the code for review"),
    "VALIDATION_FAILED": ("BUILD", "RUN.OPERATE", f"{_BUILD}: validation failed; fix and validate again"),
    "DBT_REVIEW": ("REVIEW", "REVIEW.APPROVE", "Data Developer: review and approve the code"),
}
DONE_STATES = {"DBT_APPROVED", "COMPLETED", "DEPLOYMENT_PENDING", "DEPLOYED", "PIPELINE_ENABLED"}
_ORDER = list(RUN_STATES) + sorted(DONE_STATES)

# what a request (or one of its sources) is doing, in the words its owner uses; the stage says whose turn it is, the
# phase what has happened: the domain is awaited, the admin is onboarding the source (registering, reaching and landing
# it, and starting its run in the domain: onboarding is the administrator's alone), the source is onboarded and its run
# waits for the designer, the designer profiles and models, the approved design is stored (profiles, model, mapping and
# checks in the stores) and waits for the build, the build runs, validation flagged it, review, done
PHASES = ("DOMAIN", "ENABLE", "ONBOARDED", "READY", "DESIGN", "STORED", "BUILD", "FLAGGED", "REVIEW", "DONE", "CLOSED")
_STORED_STATES = {"SODA_APPROVED", "DBT_PENDING"}
# a run the administrator started, landed and waiting for the designer to profile it
_ONBOARDED_STATES = {"LANDING_COMPLETE", "PROFILING_PENDING"}
# item statuses that mean the Data OS Administrator still has the source (NO_RUN: registered and landed, its run in
# the domain not started yet); leaving them is "source onboarded"
ENABLE_STATUSES = {"MISSING", "UNREACHABLE", "NOT_LANDED", "NO_RUN"}


def phase(part: Dict[str, Any], product: bool = False) -> str:
    """The phase of one part of a request (an item's status, or the domain's part) from its stage, status and the
    state of its run."""
    stage, status, state = part.get("stage"), part.get("status"), _up(part.get("state"))
    if stage in ("DOMAIN", "ENABLE", "REVIEW", "DONE"):
        return stage
    if status == "NO_RUN":
        return "READY" if product else "ENABLE"
    if stage == "DESIGN" and not product and state in _ONBOARDED_STATES:
        return "ONBOARDED"
    if stage == "BUILD":
        return "FLAGGED" if state == "VALIDATION_FAILED" else "STORED" if state in _STORED_STATES else "BUILD"
    return "DESIGN"


def intent(request: Dict[str, Any]) -> str:
    """NEW or ENHANCE; a request filed before V078 is NEW."""
    return "ENHANCE" if _up(request.get("intent")) == "ENHANCE" else "NEW"


def _newer(approved: Any, base: Any) -> bool:
    """An approved model version newer than the one a request was filed against (any approved one when there was none)."""
    if approved is None:
        return False
    return base is None or int(approved) > int(base)


def stage_index(stage: str) -> int:
    return STAGES.index(stage) if stage in STAGES else len(STAGES)


def _up(v: Optional[str]) -> str:
    return (v or "").strip().upper()


def _wait(privilege: str, label: str, item_id: Optional[str] = None) -> Dict[str, Any]:
    return {"privilege": privilege, "label": label, "item_id": item_id}


def run_status(run: Dict[str, Any], children: Iterable[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Where one run (and, for a multi-table run, its table runs) stands: {stage, status, waits, attention}. A parent
    whose table runs carry the work stands where its least advanced live table run stands."""
    live = [c for c in children if not c.get("is_archived") and not c.get("deleted_at") and _up(c.get("current_state")) != "CANCELLED"]
    if live:
        parts = [run_status(c) for c in live]
        worst = min(parts, key=lambda p: (stage_index(p["stage"]), p["status"] != "ATTENTION"))
        # the earliest table run's wait first, so the request names the step its stage is at
        waits = [w for p in sorted(parts, key=lambda p: stage_index(p["stage"])) for w in p["waits"]]
        attention = "; ".join(p["attention"] for p in parts if p["attention"]) or None
        done = sum(p["stage"] == "DONE" for p in parts)
        return {**worst, "waits": _dedupe(waits), "attention": attention,
                "detail": f"{done} of {len(parts)} table runs done" + (f"; {worst['detail']}" if worst["detail"] and worst["stage"] != "DONE" else "")}
    state = _up(run.get("current_state"))
    name = run.get("run_name") or run.get("run_id")
    if run.get("is_archived") or run.get("deleted_at"):
        return {"stage": "DESIGN", "status": "ATTENTION", "detail": f"Run {name} was archived",
                "waits": [_wait("RUN.CREATE", "Start a new run for this source, or link another")],
                "attention": f"Run {name} was archived: link another run"}
    if state == "CANCELLED":
        return {"stage": "DESIGN", "status": "ATTENTION", "detail": f"Run {name} was cancelled",
                "waits": [_wait("RUN.CREATE", "Start a new run for this source, or link another")],
                "attention": f"Run {name} was cancelled: link another run"}
    if state == "FAILED":
        frm = _up(run.get("failed_from_state"))
        stage = RUN_STATES.get(frm, ("DESIGN",))[0] if frm not in DONE_STATES else "BUILD"
        reason = (run.get("failure_reason") or "no reason recorded")[:300]
        return {"stage": stage, "status": "ATTENTION", "detail": f"Run failed: {reason}",
                "waits": [_wait("RUN.OPERATE", f"{_BUILD}: the run failed; retry or fix it")],
                "attention": f"Run {name} failed: {reason}"}
    if state in DONE_STATES:
        return {"stage": "DONE", "status": "DONE", "detail": "Code approved" if state == "DBT_APPROVED" else "Completed",
                "waits": [], "attention": None, "state": state}
    stage, privilege, label = RUN_STATES.get(state, ("DESIGN", "RUN.OPERATE", f"{_DESIGN}: continue the run ({state.lower()})"))
    status = {"ENABLE": "NOT_LANDED", "DESIGN": "DESIGN", "BUILD": "BUILD", "REVIEW": "REVIEW"}[stage]
    attention = f"Validation failed on {name}" if state == "VALIDATION_FAILED" else None
    return {"stage": stage, "status": "ATTENTION" if attention else status, "detail": label, "waits": [_wait(privilege, label)],
            "attention": attention, "state": state}


def _dedupe(waits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for w in waits:
        key = (w["privilege"], w["label"], w.get("item_id"))
        if key not in seen:
            seen.add(key)
            out.append(w)
    return out


def item_status(item: Dict[str, Any], request: Dict[str, Any], sources: Dict[str, Dict[str, Any]],
                runs: Dict[str, Dict[str, Any]], children: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """One source of a request: {stage, status, detail, waits, attention}. `sources` maps SOURCE_SYSTEM_ID to its
    registry row with health (GET /api/sources/overview); `runs` maps RUN_ID to WORKFLOW_RUN rows; `children` maps a
    parent run to its table runs."""
    iid = item.get("item_id")
    name = item.get("source_name") or "the source"
    if item.get("removed_at"):
        return {"stage": "DONE", "status": "REMOVED", "detail": "Removed from the request", "waits": [], "attention": None}
    product = _up(request.get("request_type")) == "PRODUCT"
    sid = item.get("source_system_id")
    if not product:
        if not sid:
            return {"stage": "ENABLE", "status": "MISSING", "detail": f"{name} is not registered on the platform",
                    "waits": [_wait("SOURCE.CONNECT", f"{_ENABLE}: register and connect {name}", iid)], "attention": None}
        src = sources.get(sid)
        if not src or src.get("active_flag") is False:
            return {"stage": "ENABLE", "status": "ATTENTION", "detail": f"{name} is no longer registered",
                    "waits": [_wait("SOURCE.CONNECT", f"{_ENABLE}: register {name} again or pick another source", iid)],
                    "attention": f"{name} is no longer registered"}
        health = _up(src.get("health"))
        if health == "UNREACHABLE":
            detail = src.get("health_detail") or "the platform cannot read it"
            return {"stage": "ENABLE", "status": "UNREACHABLE", "detail": f"{name} is unreachable: {detail}"[:500],
                    "waits": [_wait("SOURCE.CONNECT", f"{_ENABLE}: restore access to {name}", iid)], "attention": None}
        if health == "NOT_LANDED":
            return {"stage": "ENABLE", "status": "NOT_LANDED", "detail": f"{name} is registered but not landed in Snowflake",
                    "waits": [_wait("SOURCE.CONNECT", f"{_ENABLE}: land {name}", iid)], "attention": None}
    rid = item.get("run_id")
    # starting the run that onboards a source into the domain is part of onboarding: the administrator's (the
    # onboarding wizard is theirs); a data product's Gold run is the Forward Engineering Lead's
    restart = (_wait("RUN.CREATE", f"{_BUILD}: start a new Gold run, or link another", iid) if product
               else _wait("SOURCE.CONNECT", f"{_ENABLE}: start a new run for {name}, or link another", iid))
    if not rid:
        if product:
            return {"stage": "BUILD", "status": "NO_RUN", "detail": "Ready; no run yet (start the Gold run for this product)",
                    "waits": [_wait("RUN.CREATE", f"{_BUILD}: start the Gold run for this product", iid)], "attention": None}
        return {"stage": "ENABLE", "status": "NO_RUN", "detail": f"Registered and landed; its run in the domain is not started yet",
                "waits": [_wait("SOURCE.CONNECT", f"{_ENABLE}: start the run that onboards {name} into the domain", iid)],
                "attention": None}
    run = runs.get(rid)
    if not run:
        return {"stage": "DESIGN", "status": "ATTENTION", "detail": "The linked run no longer exists",
                "waits": [restart], "attention": f"The run linked for {name} no longer exists"}
    st = run_status(run, children.get(rid, ()))
    st = {**st, "waits": [restart if w["privilege"] == "RUN.CREATE" else w for w in st["waits"]]}
    if request.get("domain_id") and run.get("domain_id") and run["domain_id"] != request["domain_id"]:
        st = {**st, "status": "ATTENTION", "attention": f"The run for {name} belongs to another domain"}
    return {**st, "waits": [{**w, "item_id": iid} for w in st["waits"]]}


def assess(request: Dict[str, Any], items: List[Dict[str, Any]], domain: Optional[Dict[str, Any]],
           sources: Dict[str, Dict[str, Any]], runs: Dict[str, Dict[str, Any]],
           children: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    """The whole request: {stage, status, waiting_on, waiting_label, waits, attention, items: {item_id: status}}.
    `domain` is the DOMAIN_REGISTRY row with its model's approved_version (None when the request has no domain yet or
    it is gone). A closed request keeps its status and stage CLOSED (DONE when it was completed)."""
    children = children or {}
    status = _up(request.get("status")) or OPEN
    product = _up(request.get("request_type")) == "PRODUCT"
    per_item = {i["item_id"]: item_status(i, request, sources, runs, children) for i in items}
    for st in per_item.values():
        st["phase"] = "DONE" if st["status"] == "REMOVED" else phase(st, product)
    if status != OPEN:
        return {"stage": "DONE" if status == DONE else "CLOSED", "status": status, "waiting_on": None, "waiting_label": None,
                "waits": [], "attention": None, "items": per_item, "phase": "DONE" if status == DONE else "CLOSED"}

    parts: List[Dict[str, Any]] = []
    kind = _up(request.get("request_type"))
    if not request.get("domain_id"):
        name = request.get("proposed_domain") or "the domain"
        parts.append({"stage": "DOMAIN", "waits": [_wait("DOMAIN.ONBOARD", f"{_ENABLE}: onboard the {name} domain")],
                      "attention": None})
    elif not domain or domain.get("active_flag") is False:
        parts.append({"stage": "DOMAIN", "waits": [_wait("DOMAIN.EDIT", "Data steward: the domain was deleted or retired; restore it or pick another")],
                      "attention": "The request's domain was deleted or retired"})
    elif not domain.get("approved_version") and kind != "PRODUCT":
        parts.append({"stage": "DESIGN", "waits": [_wait("MODEL.APPROVE", f"{_DESIGN}: design and approve the {domain.get('domain_name')} model")],
                      "attention": None})
    elif kind == "DOMAIN" and intent(request) == "ENHANCE" and not _newer(domain.get("approved_version"), request.get("base_model_version")):
        # extending a domain: its model was approved when the request was filed; the request waits until a newer one is
        parts.append({"stage": "DESIGN", "waits": [_wait("MODEL.EDIT", f"{_DESIGN}: extend the {domain.get('domain_name')} model and have it approved")],
                      "attention": None})
    live = [s for s in per_item.values() if s["status"] != "REMOVED"]
    parts.extend(live)

    if not parts:
        # nothing left to do: the domain is ready and every source is onboarded (or none were asked for)
        return {"stage": "DONE", "status": DONE, "waiting_on": None, "waiting_label": None, "waits": [], "attention": None,
                "items": per_item, "phase": "DONE"}
    if all(p["stage"] == "DONE" for p in parts):
        return {"stage": "DONE", "status": DONE, "waiting_on": None, "waiting_label": None, "waits": [], "attention": None,
                "items": per_item, "phase": "DONE"}
    stage = min((p["stage"] for p in parts if p["stage"] != "DONE"), key=stage_index)
    waits = _dedupe([w for p in parts for w in p["waits"]])
    now = [w for p in parts if p["stage"] == stage for w in p["waits"]]
    first = now[0] if now else (waits[0] if waits else None)
    attention = "; ".join(dict.fromkeys(p["attention"] for p in parts if p.get("attention"))) or None
    # the phase of the part that holds the request at its stage (the earliest one: a source still with the admin
    # before one already onboarded)
    at = [p for p in parts if p["stage"] == stage]
    shown = min((phase(p, product) for p in at), key=PHASES.index)
    return {"stage": stage, "status": OPEN, "waiting_on": first["privilege"] if first else None,
            "waiting_label": first["label"] if first else None, "waits": waits, "attention": attention, "items": per_item,
            "phase": shown}


def milestones(item: Dict[str, Any], now: Dict[str, Any], product: bool = False) -> List[tuple]:
    """The events an item's new status earns, as (kind, note): SOURCE_ONBOARDED when a source the administrator had
    (missing, unreachable, not landed or its run not started) is registered, reachable, landed and its run started, so
    the designer can start; DESIGN_STORED when its run's design is approved and the build can start. A data product's
    Gold run earns neither (no source is onboarded and the design is the Gold run's own). `item` is the stored row (its last status), `now` the
    assessment. A status seen for the first time (no stored status) earns nothing: attaching an onboarded source is
    its own event (SOURCE_ADDED)."""
    before, after = _up(item.get("status")), _up(now.get("status"))
    name = item.get("source_name") or "the source"
    out = []
    if product:
        return out
    if before in ENABLE_STATUSES and now.get("stage") != "ENABLE" and after != "REMOVED":
        out.append(("SOURCE_ONBOARDED", f"{name} is onboarded: registered, reachable, landed and its run started. {_DESIGN}: profile and model it"))
    if before in ("NO_RUN", "DESIGN") and now.get("stage") in ("BUILD", "REVIEW", "DONE") and after != "REMOVED":
        out.append(("DESIGN_STORED", f"The design for {name} is approved: its profiles, model, mapping and checks are in the "
                                     f"stores for reuse. {_BUILD}: build it"))
    return out


def match_source(name: str, sources: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A registered source with this name (case, spacing, underscores and punctuation ignored), so a requester who
    names an existing source, or an administrator who registers it under the name the request gave (the wizard
    pre-fills it as YARDI_VOYAGER for "Yardi Voyager"), gets it linked without anyone's help."""
    want = _key(name)
    if not want:
        return None
    for s in sources:
        if _key(s.get("source_system_name")) == want and s.get("active_flag") is not False:
            return s
    return None


def _key(name: Optional[str]) -> str:
    return "".join(ch for ch in _up(name) if ch.isalnum())


def pick_run(source_id: str, domain_id: Optional[str], runs: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The run that onboards this source into this domain, when one already exists: a top-level run (not a table run
    of a multi-table run), not archived, cancelled or deleted, of the same domain; the most advanced, then the newest."""
    fit = [r for r in runs
           if r.get("source_system_id") == source_id and not r.get("parent_run_id") and not r.get("is_archived")
           and not r.get("deleted_at") and _up(r.get("current_state")) not in ("CANCELLED", "FAILED")
           and (not domain_id or r.get("domain_id") == domain_id)]
    if not fit:
        return None
    rank = {s: i for i, s in enumerate(_ORDER)}
    return max(fit, key=lambda r: (rank.get(_up(r.get("current_state")), -1), str(r.get("created_at") or "")))


def validate(body: Dict[str, Any], domain_exists: bool) -> List[str]:
    """What is wrong with a new request, in words (empty when it can be filed)."""
    problems = []
    kind = _up(body.get("request_type"))
    if kind not in TYPES:
        problems.append("Choose what is requested: a source, a domain or a data product.")
    if not (body.get("title") or "").strip():
        problems.append("Give the request a title.")
    if _up(body.get("priority") or "MEDIUM") not in PRIORITIES:
        problems.append("Priority is LOW, MEDIUM or HIGH.")
    sources = [s for s in body.get("sources") or [] if (s.get("source_system_id") or (s.get("source_name") or "").strip())]
    enhance = _up(body.get("intent") or "NEW") == "ENHANCE"
    if _up(body.get("intent") or "NEW") not in INTENTS:
        problems.append("Choose new or enhance.")
    if kind == "DOMAIN" and enhance:
        # extending a domain already on the platform: its model gains what the request names
        if not body.get("domain_id"):
            problems.append("Pick the domain to enhance.")
        elif not domain_exists:
            problems.append("That domain does not exist or is retired.")
        if not (body.get("business_outcome") or "").strip():
            problems.append("Say what the domain needs: the entities, attributes or rules to add.")
    elif kind == "DOMAIN":
        # a domain request asks the Data OS Administrator to onboard a new domain; one already on the platform needs
        # a source request instead
        if body.get("domain_id"):
            problems.append("A domain request is for a new domain. For a domain already on the platform, request its sources.")
        elif not (body.get("proposed_domain") or "").strip():
            problems.append("Name the new domain to onboard.")
    elif not body.get("domain_id"):
        problems.append("Pick the domain the request is for.")
    elif not domain_exists:
        problems.append("That domain does not exist or is retired.")
    if kind == "SOURCE" and not sources:
        problems.append("Name at least one source: a registered one, or the name of one still to onboard."
                        if not enhance else "Pick the source to enhance.")
    names = [_up(s.get("source_system_id") or s.get("source_name")) for s in sources]
    if len(names) != len(set(names)):
        problems.append("Each source can be named once.")
    return problems
