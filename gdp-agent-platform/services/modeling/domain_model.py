"""Model Workbench: one canonical, versioned model per domain, reviewed and approved in the app (V039).

  GOVERNANCE.DOMAIN_MODEL         one row per domain: approved version, open version, naming standard, diagram layout
  GOVERNANCE.MODEL_DESIGNS        the versions (DESIGN_ID = MODEL_ID for canonical models; rows without a MODEL_ID
                                  are legacy Modeling hub designs and stay read-only)
  GOVERNANCE.MODEL_REVIEW_COMMENT review threads per path (ENTITY, ENTITY.COLUMN, rel:<id>, model)

Lifecycle of a version: DRAFT -> IN_REVIEW -> APPROVED (-> PUBLISHED once its Git pull request is merged), or
REJECTED; the version it replaces becomes SUPERSEDED. A model has at most one open version (DRAFT or IN_REVIEW). The
draft is edited in place with an etag: a save carrying a stale etag is refused, so two modelers never overwrite each
other. Approval is four eyes (the approver is neither the submitter nor an author), blocked by unresolved BLOCKER
comments, and needs `acknowledge_breaking` when the version has breaking changes. Applying the approved version to
the domain registry is services/modeling/registry_sync (owner's rights), called by the API after approval.

Every function takes the caller's user name explicitly, so the rules are testable and the audit columns are right
whichever session runs the SQL.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Dict, List, Optional

from services.common.sql import clip, insert_rows, rows, scalar, variant
from services.modeling import model_diff, spec_v2

MODELS = "GOVERNANCE.DOMAIN_MODEL"
DESIGNS = "GOVERNANCE.MODEL_DESIGNS"
COMMENTS = "GOVERNANCE.MODEL_REVIEW_COMMENT"
OPEN = ("DRAFT", "IN_REVIEW")
LIVE = ("APPROVED", "PUBLISHED")
COMMENT_KINDS = ("COMMENT", "SUGGESTION", "BLOCKER")


class Conflict(AssertionError):
    """Someone else changed the draft (or opened one) since the caller read it."""


def model_id_for(domain_id: str, layer: str = "SILVER") -> str:
    """md5('model:' || DOMAIN_ID) as a UUID for the SILVER model (the same rule as the V039 backfill), and
    md5('model:gold:' || DOMAIN_ID) for the domain's GOLD model."""
    key = f"model:{domain_id}" if str(layer).upper() == "SILVER" else f"model:{str(layer).lower()}:{domain_id}"
    h = hashlib.md5(key.encode("utf-8")).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _low(r: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k).lower(): v for k, v in r.items()}


def _ts(value: Any) -> Optional[str]:
    return str(value)[:19] if value else None


def _header(session, model_id: str) -> Dict[str, Any]:
    found = rows(session, f"""SELECT MODEL_ID, DOMAIN_ID, DOMAIN_NAME, FORKED_FROM_MODEL_ID, FORKED_FROM_VERSION,
                                     APPROVED_VERSION, OPEN_VERSION, NAMING_STANDARD, DEFAULT_REPO_ID, DIAGRAM_LAYOUT,
                                     CREATED_BY, CREATED_AT, UPDATED_BY, UPDATED_AT, COALESCE(LAYER, 'SILVER') AS LAYER
                                FROM {MODELS} WHERE MODEL_ID = ?""", [model_id])
    assert found, "model not found"
    h = _low(found[0])
    h["naming_standard"] = variant(h.get("naming_standard")) or {"preset": "NONE"}
    h["diagram_layout"] = variant(h.get("diagram_layout")) or {}
    h["created_at"], h["updated_at"] = _ts(h.get("created_at")), _ts(h.get("updated_at"))
    return h


VERSION_FIELDS = """VERSION, STATUS, ORIGIN, BASE_VERSION, ETAG, AUTHORS, CHANGE_NOTE, ISSUES, REVIEW_SUMMARY,
                    SUBMITTED_BY, SUBMITTED_AT, APPROVED_BY, APPROVED_AT, REJECTED_BY, REJECTED_AT, REJECT_REASON,
                    BRANCH, PR_URL, PR_NUMBER, PR_STATE, MERGED_SHA, PR_SYNCED_AT, REGISTRY_SYNCED_AT, REPO_ID,
                    CREATED_BY, CREATED_AT, UPDATED_AT"""


def _version_meta(r: Dict[str, Any]) -> Dict[str, Any]:
    v = _low(r)
    v["version"] = int(v["version"])
    for k in ("authors", "issues", "review_summary"):
        v[k] = variant(v.get(k)) or ([] if k != "review_summary" else None)
    for k in ("submitted_at", "approved_at", "rejected_at", "pr_synced_at", "registry_synced_at", "created_at", "updated_at"):
        v[k] = _ts(v.get(k))
    if v.get("base_version") is not None:
        v["base_version"] = int(v["base_version"])
    return v


def _versions(session, model_id: str) -> List[Dict[str, Any]]:
    return [_version_meta(r) for r in rows(session, f"SELECT {VERSION_FIELDS} FROM {DESIGNS} WHERE MODEL_ID = ? "
                                                    "ORDER BY VERSION DESC", [model_id])]


def _spec(session, model_id: str, version: int) -> Dict[str, Any]:
    found = rows(session, f"SELECT SPEC FROM {DESIGNS} WHERE MODEL_ID = ? AND VERSION = ?", [model_id, int(version)])
    assert found, f"version {version} not found"
    return spec_v2.normalize_v2(variant(found[0]["SPEC"]) or {}, model_id)


def _open(versions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return next((v for v in versions if v["status"] in OPEN), None)


def _domains(session) -> List[str]:
    try:
        return [str(r["DOMAIN_NAME"]).upper() for r in rows(session, "SELECT DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY")]
    except Exception:
        return []


# ---------------------------------------------------------------- reads


def list_models(session, layer: str = "SILVER") -> List[Dict[str, Any]]:
    """Every domain with its model state in a layer (SILVER: the runs' targets; GOLD: facts and dimensions), including
    active domains that have no model in that layer yet (model_id None)."""
    found = rows(session, f"""SELECT R.DOMAIN_ID, R.DOMAIN_NAME, R.DESCRIPTION, R.ACTIVE_FLAG, M.MODEL_ID,
                                     M.APPROVED_VERSION, M.OPEN_VERSION, M.UPDATED_AT,
                                     O.STATUS AS OPEN_STATUS, O.SUBMITTED_BY, O.AUTHORS AS OPEN_AUTHORS,
                                     A.PR_STATE, A.PR_URL, A.APPROVED_AT, A.APPROVED_BY, A.SPEC AS APPROVED_SPEC
                                FROM KNOWLEDGE.DOMAIN_REGISTRY R
                                LEFT JOIN {MODELS} M ON M.DOMAIN_ID = R.DOMAIN_ID AND COALESCE(M.LAYER, 'SILVER') = ?
                                LEFT JOIN {DESIGNS} O ON O.MODEL_ID = M.MODEL_ID AND O.VERSION = M.OPEN_VERSION
                                LEFT JOIN {DESIGNS} A ON A.MODEL_ID = M.MODEL_ID AND A.VERSION = M.APPROVED_VERSION
                               WHERE R.ACTIVE_FLAG OR M.MODEL_ID IS NOT NULL
                               ORDER BY R.DOMAIN_NAME""", [str(layer).upper()])
    out = []
    for r in found:
        r = _low(r)
        spec = variant(r.pop("approved_spec", None))
        counts = spec_v2.summary(spec_v2.normalize_v2(spec, r.get("model_id") or "")) if spec else None
        out.append({**r, "open_authors": variant(r.get("open_authors")) or [], "counts": counts,
                    "updated_at": _ts(r.get("updated_at")), "approved_at": _ts(r.get("approved_at"))})
    return out


def get_model(session, model_id: str, version: Optional[int] = None) -> Dict[str, Any]:
    """The model header, every version (newest first, without specs) and one version with its specification: the
    asked one, else the open draft, else the approved one."""
    header = _header(session, model_id)
    versions = _versions(session, model_id)
    pick = version or ((_open(versions) or {}).get("version")) or header.get("approved_version")
    current = None
    if pick:
        meta = next((v for v in versions if v["version"] == int(pick)), None)
        assert meta, f"version {pick} not found"
        current = {**meta, "spec": _spec(session, model_id, int(pick))}
    return {"model": header, "versions": versions, "current": current, "open": _open(versions)}


def diff_versions(session, model_id: str, from_version: Optional[int], to_version: int) -> Dict[str, Any]:
    """Changes from one version to another (from None: from an empty model)."""
    old = _spec(session, model_id, int(from_version)) if from_version else None
    return {"from": from_version, "to": int(to_version), **model_diff.diff(old, _spec(session, model_id, int(to_version)))}


# ---------------------------------------------------------------- create, fork, open a draft


def _insert_version(session, model_id: str, version: int, domain: Dict[str, Any], spec: Dict[str, Any], status: str,
                    origin: str, base: Optional[int], user: str, note: str) -> str:
    etag = uuid.uuid4().hex
    issues = spec_v2.validate(spec, _domains(session))
    insert_rows(session, DESIGNS,
                ["DESIGN_ID", "VERSION", "MODEL_ID", "DOMAIN_ID", "DOMAIN_NAME", "MODE", "STATUS", "SPEC", "ISSUES",
                 "SPEC_VERSION", "BASE_VERSION", "ETAG", "AUTHORS", "ORIGIN", "CHANGE_NOTE", "CREATED_BY"],
                ["?", "?::NUMBER", "?", "?", "?", "'MAP'", "?", "PARSE_JSON(?)", "PARSE_JSON(?)", "2",
                 "NULLIF(?, '')::NUMBER", "?", "PARSE_JSON(?)::ARRAY", "?", "NULLIF(?, '')", "?"],
                [[model_id, version, model_id, domain["domain_id"], domain["domain_name"], status, spec, issues,
                  base if base else "", etag, [user.upper()], origin, clip(note, 2000), user.upper()]])
    return etag


def _claim_open(session, model_id: str, version: int) -> None:
    """Make `version` the model's only open version, or undo it when someone opened another one at the same time
    (Snowflake does not enforce unique keys, so the re-read is the guard)."""
    opened = [int(r["VERSION"]) for r in rows(session, f"SELECT VERSION FROM {DESIGNS} WHERE MODEL_ID = ? "
                                                       f"AND STATUS IN ('DRAFT', 'IN_REVIEW') ORDER BY VERSION",
                                             [model_id])]
    if opened and opened[0] != int(version):
        session.sql(f"DELETE FROM {DESIGNS} WHERE MODEL_ID = ? AND VERSION = ?", params=[model_id, int(version)]).collect()
        raise Conflict(f"CONFLICT: version {opened[0]} was opened as a draft just now; open that one instead")
    session.sql(f"UPDATE {MODELS} SET OPEN_VERSION = ?, UPDATED_AT = CURRENT_TIMESTAMP() WHERE MODEL_ID = ?",
                params=[int(version), model_id]).collect()


def _domain(session, domain_id: str) -> Dict[str, Any]:
    found = rows(session, "SELECT DOMAIN_ID, DOMAIN_NAME FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = ?", [domain_id])
    assert found, "domain not found"
    return _low(found[0])


def _registry_spec(session, domain: Dict[str, Any]) -> Dict[str, Any]:
    tables = [_low(r) for r in rows(session, "SELECT * FROM KNOWLEDGE.TARGET_TABLE_REGISTRY WHERE DOMAIN_ID = ?",
                                    [domain["domain_id"]])]
    columns = [_low(r) for r in rows(session, """SELECT C.* FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY C
                                                   JOIN KNOWLEDGE.TARGET_TABLE_REGISTRY T ON T.TARGET_TABLE_ID = C.TARGET_TABLE_ID
                                                  WHERE T.DOMAIN_ID = ?""", [domain["domain_id"]])]
    return spec_v2.spec_from_registry(domain, tables, columns)


def create_model(session, domain_id: str, payload_json: str, user: str) -> Dict[str, Any]:
    """A canonical model for a domain that has none: version 1 is a DRAFT built from the domain's registered targets
    (or the given specification), to review and approve like any change."""
    payload = json.loads(payload_json or "{}")
    domain = _domain(session, domain_id)
    layer = str(payload.get("layer") or "SILVER").upper()
    assert layer in spec_v2.LAYERS, f"layer must be one of {', '.join(spec_v2.LAYERS)}"
    model_id = model_id_for(domain_id, layer)
    assert not scalar(session, f"SELECT COUNT(*) FROM {MODELS} WHERE DOMAIN_ID = ? AND COALESCE(LAYER, 'SILVER') = ?",
                      [domain_id, layer]), \
        f"{domain['domain_name']} already has a {layer.lower()} model; open a draft on it instead"
    assert layer == "SILVER" or payload.get("spec"), "a gold model starts from a specification (a gold build proposes one)"
    spec = (spec_v2.normalize_v2(payload["spec"], model_id) if payload.get("spec")
            else _registry_spec(session, domain))
    spec["domain"] = str(domain["domain_name"]).upper()
    spec["layer"] = layer
    standard = payload.get("naming_standard") or spec.get("naming_standard") or {"preset": "NONE"}
    spec["naming_standard"] = standard
    insert_rows(session, MODELS, ["MODEL_ID", "DOMAIN_ID", "DOMAIN_NAME", "NAMING_STANDARD", "CREATED_BY", "LAYER"],
                ["?", "?", "?", "PARSE_JSON(?)", "?", "?"],
                [[model_id, domain_id, domain["domain_name"], standard, user.upper(), layer]])
    etag = _insert_version(session, model_id, 1, domain, spec, "DRAFT", payload.get("origin") or "WORKBENCH", None, user,
                           payload.get("change_note") or "Model created")
    _claim_open(session, model_id, 1)
    return {"model_id": model_id, "version": 1, "status": "DRAFT", "etag": etag}


def fork_model(session, model_id: str, payload_json: str, user: str) -> Dict[str, Any]:
    """A new domain whose first draft is a copy of another model's version (the approved one by default)."""
    from services.knowledge.procedures import ensure_domain

    payload = json.loads(payload_json or "{}")
    source = _header(session, model_id)
    version = int(payload.get("version") or source.get("approved_version") or 0)
    assert version, "the model has no approved version to fork; pass a version"
    name = str(payload.get("domain") or "").strip().upper()
    assert spec_v2.NAME.match(name) and len(name) <= 64, "the new domain name is letters, digits and _ (up to 64)"
    assert name not in _domains(session), f"{name} already exists; pick a new name"
    domain_id = ensure_domain(session, name)
    spec = _spec(session, model_id, version)
    spec.update(domain=name, mode="FORK", parent_domain=source["domain_name"])
    for e in spec["design"]["entities"]:  # new tables: fresh ids, so the fork never touches the parent's registry rows
        e["id"] = spec_v2.stable_id(name, e["entity_name"])
        for a in e["attributes"]:
            a["id"] = spec_v2.stable_id(e["id"], a["name"])
    created = create_model(session, domain_id, json.dumps({"spec": spec, "origin": "FORK",
                                                           "naming_standard": source["naming_standard"],
                                                           "change_note": f"Forked from {source['domain_name']} v{version}"}),
                           user)
    session.sql(f"UPDATE {MODELS} SET FORKED_FROM_MODEL_ID = ?, FORKED_FROM_VERSION = ? WHERE MODEL_ID = ?",
                params=[model_id, version, created["model_id"]]).collect()
    return {**created, "domain_id": domain_id, "domain": name}


def open_draft(session, model_id: str, user: str, from_version: Optional[int] = None, note: str = "") -> Dict[str, Any]:
    """Start the next version from the approved one, or from an older version (a rollback, reviewed like any change).
    Refused while another version is open."""
    header = _header(session, model_id)
    versions = _versions(session, model_id)
    current = _open(versions)
    if current:
        raise Conflict(f"CONFLICT: version {current['version']} is already open "
                       f"({current['status'].lower().replace('_', ' ')}); continue that one")
    base = int(from_version or header.get("approved_version") or 0)
    assert base, "the model has no approved version yet; edit its first draft"
    assert any(v["version"] == base for v in versions), f"version {base} not found"
    version = max(v["version"] for v in versions) + 1
    spec = _spec(session, model_id, base)
    rollback = base != int(header.get("approved_version") or 0)
    etag = _insert_version(session, model_id, version, {"domain_id": header["domain_id"], "domain_name": header["domain_name"]},
                           spec, "DRAFT", "ROLLBACK" if rollback else "WORKBENCH", header.get("approved_version"), user,
                           note or (f"Roll back to version {base}" if rollback else ""))
    _claim_open(session, model_id, version)
    return {"model_id": model_id, "version": version, "status": "DRAFT", "etag": etag, "from_version": base}


# ---------------------------------------------------------------- edit and review


def save_draft(session, model_id: str, payload_json: str, user: str) -> Dict[str, Any]:
    """Replace the open draft's specification. `etag` must be the one the caller read; a stale etag is a Conflict."""
    payload = json.loads(payload_json or "{}")
    header = _header(session, model_id)
    current = _open(_versions(session, model_id))
    assert current, "there is no open draft; open one first"
    assert current["status"] == "DRAFT", "the version is in review; withdraw it to keep editing"
    if str(payload.get("etag") or "") != str(current.get("etag") or ""):
        raise Conflict(f"CONFLICT: the draft was saved by {(current.get('authors') or ['someone'])[-1]} since you opened it; "
                       "reload to see their changes")
    spec = spec_v2.normalize_v2(payload.get("spec") or {}, model_id)
    spec["domain"] = header["domain_name"]
    spec["naming_standard"] = spec.get("naming_standard") or header["naming_standard"]
    issues = spec_v2.validate(spec, _domains(session))
    etag = uuid.uuid4().hex
    session.sql(f"""UPDATE {DESIGNS} SET SPEC = PARSE_JSON(?), ISSUES = PARSE_JSON(?), ETAG = ?,
                           AUTHORS = ARRAY_DISTINCT(ARRAY_APPEND(COALESCE(AUTHORS, ARRAY_CONSTRUCT()), ?)),
                           CHANGE_NOTE = COALESCE(NULLIF(?, ''), CHANGE_NOTE), UPDATED_AT = CURRENT_TIMESTAMP()
                     WHERE MODEL_ID = ? AND VERSION = ? AND ETAG = ? AND STATUS = 'DRAFT'""",
                params=[json.dumps(spec, default=str), json.dumps(issues), etag, user.upper(),
                        clip(payload.get("change_note"), 2000), model_id, current["version"], current["etag"]]).collect()
    now = scalar(session, f"SELECT ETAG FROM {DESIGNS} WHERE MODEL_ID = ? AND VERSION = ?", [model_id, current["version"]])
    if now != etag:
        raise Conflict("CONFLICT: the draft was saved by someone else at the same moment; reload to see their changes")
    base = header.get("approved_version")
    changes = model_diff.diff(_spec(session, model_id, int(base)) if base else None, spec)
    return {"model_id": model_id, "version": current["version"], "etag": etag, "issues": issues,
            "counts": changes["counts"], "breaking": changes["breaking"]}


def submit(session, model_id: str, user: str, note: str = "") -> Dict[str, Any]:
    header = _header(session, model_id)
    current = _open(_versions(session, model_id))
    assert current and current["status"] == "DRAFT", "there is no draft to submit"
    spec = _spec(session, model_id, current["version"])
    problems = spec_v2.blocking(spec_v2.validate(spec, _domains(session)))
    assert not problems, f"fix {len(problems)} problem(s) before submitting: {problems[0]['path']}: {problems[0]['message']}"
    base = header.get("approved_version")
    changes = model_diff.diff(_spec(session, model_id, int(base)) if base else None, spec)
    assert changes["changes"] or not base, "the draft is the same as the approved version; there is nothing to review"
    summary = {"counts": changes["counts"], "breaking": changes["breaking"], "base_version": base,
               "entities_touched": changes["entities_touched"][:200]}
    session.sql(f"""UPDATE {DESIGNS} SET STATUS = 'IN_REVIEW', SUBMITTED_BY = ?, SUBMITTED_AT = CURRENT_TIMESTAMP(),
                           REVIEW_SUMMARY = PARSE_JSON(?), BASE_VERSION = NULLIF(?, '')::NUMBER,
                           CHANGE_NOTE = COALESCE(NULLIF(?, ''), CHANGE_NOTE), UPDATED_AT = CURRENT_TIMESTAMP()
                     WHERE MODEL_ID = ? AND VERSION = ? AND STATUS = 'DRAFT'""",
                params=[user.upper(), json.dumps(summary), base if base else "", clip(note, 2000), model_id,
                        current["version"]]).collect()
    return {"model_id": model_id, "version": current["version"], "status": "IN_REVIEW", **summary}


def withdraw(session, model_id: str, user: str) -> Dict[str, Any]:
    """Take a version out of review to keep editing it (its submitter or an author)."""
    current = _open(_versions(session, model_id))
    assert current and current["status"] == "IN_REVIEW", "there is no version in review"
    people = {str(a).upper() for a in current.get("authors") or []} | {str(current.get("submitted_by") or "").upper()}
    assert user.upper() in people, "only the submitter or an author can withdraw the version"
    session.sql(f"""UPDATE {DESIGNS} SET STATUS = 'DRAFT', SUBMITTED_BY = NULL, SUBMITTED_AT = NULL,
                           UPDATED_AT = CURRENT_TIMESTAMP() WHERE MODEL_ID = ? AND VERSION = ?""",
                params=[model_id, current["version"]]).collect()
    return {"model_id": model_id, "version": current["version"], "status": "DRAFT"}


def _in_review(session, model_id: str, version: int) -> Dict[str, Any]:
    found = next((v for v in _versions(session, model_id) if v["version"] == int(version)), None)
    assert found, f"version {version} not found"
    assert found["status"] == "IN_REVIEW", f"version {version} is {found['status'].lower().replace('_', ' ')}, not in review"
    return found


def _own(found: Dict[str, Any], user: str) -> bool:
    people = {str(a).upper() for a in found.get("authors") or []} | {str(found.get("submitted_by") or "").upper()}
    return user.upper() in people


def reject(session, model_id: str, version: int, user: str, reason: str) -> Dict[str, Any]:
    found = _in_review(session, model_id, version)
    assert not _own(found, user), "you worked on this version; withdraw it instead of rejecting it"
    assert len((reason or "").strip()) >= 3, "say why the version is rejected"
    session.sql(f"""UPDATE {DESIGNS} SET STATUS = 'REJECTED', REJECTED_BY = ?, REJECTED_AT = CURRENT_TIMESTAMP(),
                           REJECT_REASON = ?, UPDATED_AT = CURRENT_TIMESTAMP() WHERE MODEL_ID = ? AND VERSION = ?""",
                params=[user.upper(), clip(reason, 2000), model_id, int(version)]).collect()
    session.sql(f"UPDATE {MODELS} SET OPEN_VERSION = NULL, UPDATED_AT = CURRENT_TIMESTAMP() WHERE MODEL_ID = ?",
                params=[model_id]).collect()
    from services.knowledge.signals import keep, model_rejection

    header = _header(session, model_id)
    touched = list((found.get("review_summary") or {}).get("entities_touched") or [])
    keep(session, header.get("domain_id"), model_rejection(header["domain_name"], int(version), reason, touched),
         "MODELING")
    return {"model_id": model_id, "version": int(version), "status": "REJECTED"}


def approve(session, model_id: str, version: int, user: str, acknowledge_breaking: bool = False) -> Dict[str, Any]:
    """Four eyes: the version becomes the domain's approved model (the previous one is superseded). The caller then
    applies it to the registry (registry_sync.apply_model_version)."""
    from services.common.sql import atomic

    header = _header(session, model_id)
    found = _in_review(session, model_id, version)
    assert not _own(found, user), "you cannot approve a version you submitted or edited; ask another model approver"
    blockers = int(scalar(session, f"SELECT COUNT(*) FROM {COMMENTS} WHERE MODEL_ID = ? AND VERSION = ? "
                                   "AND KIND = 'BLOCKER' AND NOT COALESCE(RESOLVED, FALSE)", [model_id, int(version)]) or 0)
    assert not blockers, f"{blockers} blocking comment(s) are unresolved"
    spec = _spec(session, model_id, int(version))
    problems = spec_v2.blocking(spec_v2.validate(spec, _domains(session)))
    assert not problems, f"the version has {len(problems)} error(s): {problems[0]['path']}: {problems[0]['message']}"
    base = header.get("approved_version")
    changes = model_diff.diff(_spec(session, model_id, int(base)) if base else None, spec)
    assert not changes["breaking"] or acknowledge_breaking, (
        f"BREAKING: {changes['counts']['BREAKING']} breaking change(s); review the impact and approve with "
        "acknowledge_breaking")
    summary = {"counts": changes["counts"], "breaking": changes["breaking"], "base_version": base,
               "acknowledged_breaking": bool(changes["breaking"]), "approved_by": user.upper()}

    def write() -> None:
        session.sql(f"""UPDATE {DESIGNS} SET STATUS = 'SUPERSEDED', UPDATED_AT = CURRENT_TIMESTAMP()
                         WHERE MODEL_ID = ? AND STATUS IN ('APPROVED', 'PUBLISHED')""", params=[model_id]).collect()
        session.sql(f"""UPDATE {DESIGNS} SET STATUS = 'APPROVED', APPROVED_BY = ?, APPROVED_AT = CURRENT_TIMESTAMP(),
                               REVIEW_SUMMARY = PARSE_JSON(?), UPDATED_AT = CURRENT_TIMESTAMP()
                         WHERE MODEL_ID = ? AND VERSION = ?""",
                    params=[user.upper(), json.dumps(summary), model_id, int(version)]).collect()
        session.sql(f"""UPDATE {MODELS} SET APPROVED_VERSION = ?, OPEN_VERSION = NULL, UPDATED_BY = ?,
                               UPDATED_AT = CURRENT_TIMESTAMP() WHERE MODEL_ID = ?""",
                    params=[int(version), user.upper(), model_id]).collect()

    atomic(session, write)
    return {"model_id": model_id, "version": int(version), "status": "APPROVED", **summary}


# ---------------------------------------------------------------- comments and layout


def comments(session, model_id: str, version: int) -> List[Dict[str, Any]]:
    out = []
    for r in rows(session, f"""SELECT COMMENT_ID, PATH, PARENT_ID, KIND, BODY, RESOLVED, RESOLVED_BY, RESOLVED_AT,
                                      AUTHOR, CREATED_AT
                                 FROM {COMMENTS} WHERE MODEL_ID = ? AND VERSION = ? ORDER BY CREATED_AT""",
                  [model_id, int(version)]):
        c = _low(r)
        c["created_at"], c["resolved_at"] = _ts(c.get("created_at")), _ts(c.get("resolved_at"))
        c["resolved"] = bool(c.get("resolved"))
        out.append(c)
    return out


def add_comment(session, model_id: str, version: int, payload_json: str, user: str) -> Dict[str, Any]:
    payload = json.loads(payload_json or "{}")
    _header(session, model_id)
    assert any(v["version"] == int(version) for v in _versions(session, model_id)), f"version {version} not found"
    body = str(payload.get("body") or "").strip()
    assert body, "the comment is empty"
    kind = str(payload.get("kind") or "COMMENT").upper()
    assert kind in COMMENT_KINDS, f"kind must be one of {list(COMMENT_KINDS)}"
    path = str(payload.get("path") or "model").strip()[:600] or "model"
    comment_id = str(uuid.uuid4())
    insert_rows(session, COMMENTS, ["COMMENT_ID", "MODEL_ID", "VERSION", "PATH", "PARENT_ID", "KIND", "BODY", "AUTHOR"],
                ["?", "?", "?::NUMBER", "?", "NULLIF(?, '')", "?", "?", "?"],
                [[comment_id, model_id, int(version), path, payload.get("parent_id") or "", kind, clip(body, 8000),
                  user.upper()]])
    return {"comment_id": comment_id, "path": path, "kind": kind}


def resolve_comment(session, model_id: str, comment_id: str, user: str, resolved: bool = True) -> Dict[str, Any]:
    found = rows(session, f"SELECT COMMENT_ID FROM {COMMENTS} WHERE MODEL_ID = ? AND COMMENT_ID = ?", [model_id, comment_id])
    assert found, "comment not found"
    session.sql(f"""UPDATE {COMMENTS} SET RESOLVED = ?, RESOLVED_BY = IFF(?, ?, NULL),
                           RESOLVED_AT = IFF(?, CURRENT_TIMESTAMP(), NULL) WHERE COMMENT_ID = ?""",
                params=[bool(resolved), bool(resolved), user.upper(), bool(resolved), comment_id]).collect()
    return {"comment_id": comment_id, "resolved": bool(resolved)}


def set_layout(session, model_id: str, layout_json: str, user: str) -> Dict[str, Any]:
    """Diagram positions {entity_id: {x, y}}: cosmetic, saved without review."""
    layout = json.loads(layout_json or "{}")
    assert isinstance(layout, dict), "the layout is an object"
    clean = {str(k)[:64]: {"x": round(float(v.get("x", 0)), 1), "y": round(float(v.get("y", 0)), 1)}
             for k, v in list(layout.items())[:2000] if isinstance(v, dict)}
    _header(session, model_id)
    session.sql(f"UPDATE {MODELS} SET DIAGRAM_LAYOUT = PARSE_JSON(?), UPDATED_BY = ?, UPDATED_AT = CURRENT_TIMESTAMP() "
                "WHERE MODEL_ID = ?", params=[json.dumps(clean), user.upper(), model_id]).collect()
    return {"model_id": model_id, "positions": len(clean)}


def set_standard(session, model_id: str, standard_json: str, user: str) -> Dict[str, Any]:
    """The naming standard drafts are checked against (a preset plus overrides); it does not change approved
    versions."""
    from services.modeling.design import conventions

    raw = json.loads(standard_json or "{}")
    std = conventions(raw.get("preset") or "NONE", raw)
    header = _header(session, model_id)
    session.sql(f"UPDATE {MODELS} SET NAMING_STANDARD = PARSE_JSON(?), UPDATED_BY = ?, UPDATED_AT = CURRENT_TIMESTAMP() "
                "WHERE MODEL_ID = ?", params=[json.dumps(std), user.upper(), model_id]).collect()
    _remember_standard(session, header, std)
    return {"model_id": model_id, "naming_standard": std}


def _remember_standard(session, header: Dict[str, Any], std: Dict[str, Any]) -> None:
    """The domain's naming standard as NAMING_STANDARD knowledge, which run model designs and the Modeling hub put in
    their prompts (services/modeling/design.context). Never fails the save."""
    from services.modeling.design import describe_conventions
    from services.knowledge.writer import remember

    try:
        remember(session, domain_id=header["domain_id"], kind="NAMING_STANDARD",
                 key=f"model.naming_standard.{str(header['domain_name']).upper()}",
                 title=f"{header['domain_name']} naming standard",
                 content=f"Naming standard of the {header['domain_name']} domain model: {describe_conventions(std)}",
                 content_json={**std, "model_id": header.get("model_id")}, tags=["MODEL", "NAMING", "WORKBENCH"],
                 origin="MODELING")
    except Exception:
        pass


# ---------------------------------------------------------------- baseline


def baseline(session, domain_id: str, user: str = "SYSTEM_BACKFILL") -> Optional[Dict[str, Any]]:
    """An APPROVED version 1 that is exactly what the domain's registry holds today, so the first change is reviewed
    against the real current model. Skipped when the domain already has a model or registers no tables."""
    if scalar(session, f"SELECT COUNT(*) FROM {MODELS} WHERE DOMAIN_ID = ? AND COALESCE(LAYER, 'SILVER') = 'SILVER'", [domain_id]):
        return None
    domain = _domain(session, domain_id)
    spec = _registry_spec(session, domain)
    if not spec["design"]["entities"]:
        return None
    model_id = model_id_for(domain_id)
    insert_rows(session, MODELS, ["MODEL_ID", "DOMAIN_ID", "DOMAIN_NAME", "NAMING_STANDARD", "APPROVED_VERSION",
                                  "CREATED_BY"],
                ["?", "?", "?", "PARSE_JSON(?)", "1", "?"],
                [[model_id, domain_id, domain["domain_name"], {"preset": "NONE"}, user]])
    _insert_version(session, model_id, 1, domain, spec, "APPROVED", "BACKFILL", None, user,
                    "Baseline: the domain's registered targets when the Model Workbench was introduced")
    session.sql(f"""UPDATE {DESIGNS} SET APPROVED_BY = ?, APPROVED_AT = CURRENT_TIMESTAMP(), REGISTRY_SYNCED_AT = CURRENT_TIMESTAMP()
                     WHERE MODEL_ID = ? AND VERSION = 1""", params=[user, model_id]).collect()
    session.sql("UPDATE KNOWLEDGE.TARGET_TABLE_REGISTRY SET MODEL_ID = ?, MODEL_VERSION = 1 WHERE DOMAIN_ID = ?",
                params=[model_id, domain_id]).collect()
    return {"model_id": model_id, "domain": domain["domain_name"], **spec_v2.summary(spec)}


def backfill(session, user: str = "SYSTEM_BACKFILL") -> List[str]:
    """Baselines for every domain that registers tables and has no model yet (run by the deploy; idempotent)."""
    try:
        found = rows(session, f"""SELECT DISTINCT T.DOMAIN_ID FROM KNOWLEDGE.TARGET_TABLE_REGISTRY T
                                   JOIN KNOWLEDGE.DOMAIN_REGISTRY R ON R.DOMAIN_ID = T.DOMAIN_ID
                                   LEFT JOIN {MODELS} M ON M.DOMAIN_ID = T.DOMAIN_ID AND COALESCE(M.LAYER, 'SILVER') = 'SILVER'
                                  WHERE M.MODEL_ID IS NULL""")
    except Exception as exc:
        return [f"domain models: skipped ({str(exc)[:120]})"]
    log = []
    for r in found:
        try:
            made = baseline(session, r["DOMAIN_ID"], user)
        except Exception as exc:
            log.append(f"domain model {r['DOMAIN_ID']}: baseline failed ({str(exc)[:160]})")
            continue
        if made:
            log.append(f"domain model {made['domain']}: baseline v1 ({made['entities']} entities, {made['columns']} columns)")
    return log


class CursorSession:
    """A connector cursor (%s binds) behind the small Snowpark surface services use (session.sql(...).collect()),
    so deploy scripts can call session-based services."""

    def __init__(self, cursor) -> None:
        self.cursor = cursor

    def sql(self, sql: str, params: Optional[list] = None):
        cursor = self.cursor

        class _Row(dict):
            def as_dict(self) -> Dict[str, Any]:
                return dict(self)

            def __getitem__(self, key):
                return list(self.values())[key] if isinstance(key, int) else dict.__getitem__(self, key)

        class _Result:
            def collect(self_inner):
                cursor.execute(sql.replace("?", "%s"), tuple(params or []))
                if not cursor.description:
                    return []
                names = [d[0] for d in cursor.description]
                return [_Row(zip(names, row)) for row in cursor.fetchall()]

        return _Result()
