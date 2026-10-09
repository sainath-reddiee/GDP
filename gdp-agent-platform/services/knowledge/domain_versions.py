"""Domain snapshots: every change to a domain writes its full state as a version, so changes can be compared and rolled
back. Works with any `query(sql, params)` / `execute(sql, params)` pair using %s binds (API Db or connector cursor).

A snapshot is the registry row (description, owner, active flag, config: rules, detection signals, source systems,
contract, silver location, standard) plus the target models with their columns. Writing a snapshot whose checksum equals
the newest one is a no-op, so deploys and repeated saves do not churn versions.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from typing import Any, Callable, Dict, List, Optional

Query = Callable[[str, tuple], List[Dict[str, Any]]]
Execute = Callable[[str, tuple], Any]

ROLLBACK_KEYS = ("rules", "signals", "source_systems", "contract", "silver_database", "silver_schema", "standard")
SECTIONS = {
    "description": "Description and owner", "rules": "Rules", "signals": "Detection signals",
    "source_systems": "Source systems", "contract": "Contract and location", "targets": "Target models",
}


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _low(row: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k).lower(): v for k, v in row.items()}


def state(query: Query, domain_id: str) -> Optional[Dict[str, Any]]:
    """The domain as it is now, in snapshot form."""
    found = [_low(r) for r in query("SELECT DOMAIN_ID, DOMAIN_NAME, DESCRIPTION, OWNER, ACTIVE_FLAG, CONFIG "
                                    "FROM KNOWLEDGE.DOMAIN_REGISTRY WHERE DOMAIN_ID = %s", (domain_id,))]
    if not found:
        return None
    d = found[0]
    tables = [_low(r) for r in query("""SELECT TARGET_TABLE_ID, TARGET_DATABASE, TARGET_SCHEMA, TARGET_TABLE, DESCRIPTION,
                                               ACTIVE_FLAG, TABLE_TYPE, GRAIN
                                          FROM KNOWLEDGE.TARGET_TABLE_REGISTRY WHERE DOMAIN_ID = %s""", (domain_id,))]
    columns = [_low(r) for r in query("""SELECT C.TARGET_TABLE_ID, C.COLUMN_NAME, C.DATA_TYPE, C.ORDINAL_POSITION, C.NULLABLE,
                                                C.IS_BUSINESS_KEY, C.IS_PII, C.BUSINESS_DEFINITION
                                           FROM KNOWLEDGE.TARGET_COLUMN_REGISTRY C
                                           JOIN KNOWLEDGE.TARGET_TABLE_REGISTRY T ON T.TARGET_TABLE_ID = C.TARGET_TABLE_ID
                                          WHERE T.DOMAIN_ID = %s""", (domain_id,))]
    by_table: Dict[str, List[Dict[str, Any]]] = {}
    for c in sorted(columns, key=lambda c: (int(c.get("ordinal_position") or 0), str(c.get("column_name")))):
        by_table.setdefault(c["target_table_id"], []).append({
            "name": c["column_name"], "type": c.get("data_type"), "nullable": bool(c.get("nullable")),
            "key": bool(c.get("is_business_key")), "pii": bool(c.get("is_pii")), "definition": c.get("business_definition")})
    targets = sorted(({"table": f"{t['target_database']}.{t['target_schema']}.{t['target_table']}".upper(),
                       "description": t.get("description"), "active": bool(t.get("active_flag")),
                       "type": t.get("table_type"), "grain": t.get("grain"),
                       "columns": by_table.get(t["target_table_id"], [])} for t in tables), key=lambda t: t["table"])
    return {"domain_name": d["domain_name"], "description": d.get("description"), "owner": d.get("owner"),
            "active": bool(d.get("active_flag")), "config": _json(d.get("config")) or {}, "targets": targets}


def checksum(snap: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(snap, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def latest(query: Query, domain_id: str) -> Optional[Dict[str, Any]]:
    found = query("SELECT VERSION, CHECKSUM FROM KNOWLEDGE.DOMAIN_VERSION WHERE DOMAIN_ID = %s ORDER BY VERSION DESC LIMIT 1",
                  (domain_id,))
    return _low(found[0]) if found else None


def snapshot(query: Query, execute: Execute, domain_id: str, kind: str, note: Optional[str] = None,
             by: Optional[str] = None) -> Optional[int]:
    """Write a version when the domain changed since the newest one. Returns the new version, or None."""
    snap = state(query, domain_id)
    if snap is None:
        return None
    digest = checksum(snap)
    last = latest(query, domain_id)
    if last and last.get("checksum") == digest:
        return None
    version = int(last["version"]) + 1 if last else 1
    execute("""INSERT INTO KNOWLEDGE.DOMAIN_VERSION (DOMAIN_ID, VERSION, DESCRIPTION, OWNER, ACTIVE_FLAG, CONFIG_JSON,
                     TARGETS_JSON, CHECKSUM, CHANGE_KIND, CHANGE_NOTE, CREATED_BY)
               SELECT %s, %s, %s, %s, %s, PARSE_JSON(%s), PARSE_JSON(%s), %s, %s, %s, COALESCE(%s, CURRENT_USER())""",
            (domain_id, version, snap["description"], snap["owner"], snap["active"], json.dumps(snap["config"], default=str),
             json.dumps(snap["targets"], default=str), digest, kind, (note or "")[:2000] or None, by))
    execute("UPDATE KNOWLEDGE.DOMAIN_REGISTRY SET VERSION = %s WHERE DOMAIN_ID = %s", (version, domain_id))
    return version


def from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """A DOMAIN_VERSION row back in snapshot form."""
    r = _low(row)
    return {"description": r.get("description"), "owner": r.get("owner"), "active": bool(r.get("active_flag")),
            "config": _json(r.get("config_json")) or {}, "targets": _json(r.get("targets_json")) or []}


def _section(snap: Dict[str, Any], name: str) -> Any:
    cfg = snap.get("config") or {}
    if name == "description":
        return {"description": snap.get("description"), "owner": snap.get("owner"), "active": snap.get("active")}
    if name == "contract":
        return {k: cfg.get(k) for k in ("contract", "silver_database", "silver_schema", "standard", "origin")}
    if name == "targets":
        return snap.get("targets") or []
    return cfg.get(name)


def _pretty(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str) if value not in (None, {}, []) else ""


def _ops(old: str, new: str) -> Dict[str, Any]:
    a, b = old.splitlines(), new.splitlines()
    ops: List[List[Any]] = []
    plus = minus = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            ops += [[" ", i1 + k + 1, j1 + k + 1, a[i1 + k]] for k in range(i2 - i1)]
            continue
        ops += [["-", k + 1, None, a[k]] for k in range(i1, i2)]
        ops += [["+", None, k + 1, b[k]] for k in range(j1, j2)]
        minus += i2 - i1
        plus += j2 - j1
    from services.knowledge.skills import compact

    return {"status": "same" if not plus and not minus else "changed", "added": plus, "removed": minus, "ops": compact(ops)}


def diff(base: Dict[str, Any], head: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per section: a line diff of its JSON (the DiffView format)."""
    return [{"path": SECTIONS[name], **_ops(_pretty(_section(base, name)), _pretty(_section(head, name)))} for name in SECTIONS]


def summary(base: Optional[Dict[str, Any]], head: Dict[str, Any]) -> List[str]:
    """Plain-language list of what changed between two snapshots."""
    if base is None:
        return ["First recorded version"]
    out: List[str] = []
    if base.get("description") != head.get("description"):
        out.append("Description changed")
    if base.get("owner") != head.get("owner"):
        out.append(f"Owner {base.get('owner') or 'none'} -> {head.get('owner') or 'none'}")
    if base.get("active") != head.get("active"):
        out.append("Restored" if head.get("active") else "Deleted")
    br, hr = (base.get("config") or {}).get("rules") or {}, (head.get("config") or {}).get("rules") or {}
    for key in sorted(set(br) | set(hr)):
        if br.get(key) != hr.get(key):
            out.append(f"Rule {key}: {br.get(key, 'default')} -> {hr.get(key, 'default')}")
    for name in ("signals", "source_systems"):
        if _section(base, name) != _section(head, name):
            out.append(f"{SECTIONS[name]} changed")
    if _section(base, "contract") != _section(head, "contract"):
        out.append("Contract or location changed")
    bt = {t["table"]: t for t in base.get("targets") or []}
    ht = {t["table"]: t for t in head.get("targets") or []}
    for t in sorted(set(ht) - set(bt)):
        out.append(f"Target added: {t.split('.')[-1]}")
    for t in sorted(set(bt) - set(ht)):
        out.append(f"Target removed: {t.split('.')[-1]}")
    for t in sorted(set(bt) & set(ht)):
        bc = {c["name"]: c for c in bt[t].get("columns") or []}
        hc = {c["name"]: c for c in ht[t].get("columns") or []}
        added, removed = set(hc) - set(bc), set(bc) - set(hc)
        changed = [c for c in set(bc) & set(hc) if bc[c] != hc[c]]
        if added or removed or changed or bt[t].get("active") != ht[t].get("active"):
            parts = [f"+{len(added)} columns" if added else "", f"-{len(removed)} columns" if removed else "",
                     f"{len(changed)} changed" if changed else "",
                     ("activated" if ht[t].get("active") else "deactivated") if bt[t].get("active") != ht[t].get("active") else ""]
            out.append(f"{t.split('.')[-1]}: " + ", ".join(p for p in parts if p))
    return out or ["No visible change"]


def rollback_config(current: Dict[str, Any], old: Dict[str, Any]) -> Dict[str, Any]:
    """Config after rolling back: the old rules, signals, sources and contract; deletion and origin stay as they are."""
    out = dict(current or {})
    for key in ROLLBACK_KEYS:
        if key in (old or {}):
            out[key] = old[key]
        else:
            out.pop(key, None)
    return out
