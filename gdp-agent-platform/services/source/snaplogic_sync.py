"""Writing SnapLogic's config tables from onboarding (services/source/snaplogic_config builds the rows).

Called by apps/api/app/hub_sources_api inside the transaction that writes the CORE rows, so CORE and SnapLogic's
tables never disagree: a failure here rolls both back. Only runs when the SNAPLOGIC_SYNC setting is on; off, nothing
here is called and onboarding is exactly as before.

ids: a source's master row is reused when one exists for the same SOURCE_SYSTEM (one master row per source); new master
and detail rows carry an exact creation time (microseconds) and their autoincrement ids are read back by it in the
same transaction. Every attempt is kept in CORE.SNAPLOGIC_SYNC_LOG (V070), with what to write again when a share's
sync fails after its data has landed.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from services.source import snaplogic_config as sc

LOG = "CORE.SNAPLOGIC_SYNC_LOG"


class Conflict(Exception):
    """SnapLogic already loads one of these landing tables (HTTP 409)."""


def setting(db: Any) -> Dict[str, Any]:
    """The SNAPLOGIC_SYNC setting, validated (the default, off, when it is missing or unreadable)."""
    try:
        found = db.query("SELECT CONFIG_VALUE FROM CORE.PLATFORM_CONFIG WHERE CONFIG_KEY = %s AND IS_CURRENT "
                         "ORDER BY VERSION DESC LIMIT 1", (sc.KEY,))
    except Exception:
        found = []
    raw = found[0].get("config_value") if found else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    value, problems = sc.validate_setting(raw if isinstance(raw, dict) else sc.DEFAULT_SETTING)
    if problems:
        value["enabled"] = False      # a broken setting never writes anywhere
    return value


def preview(header: Dict[str, Any], rows: List[Dict[str, Any]], setting_: Dict[str, Any], user: str) -> Dict[str, Any]:
    """The rows an onboarding would write, before it writes them (ids are assigned by SnapLogic's tables)."""
    stamp = sc.created_at()
    return {"enabled": bool(setting_.get("enabled")), "tables": sc.tables(setting_),
            "master": sc.to_master(header, setting_, stamp),
            "details": [sc.to_detail(r, header, 0, setting_, user, stamp) | {"MASTER_CONFIG_ID": None} for r in rows]}


def _landed(db: Any, table: str, rows: List[Dict[str, Any]]) -> List[str]:
    triples = [(r["landing_database"], r["landing_schema"], r["landing_table"]) for r in rows]
    where = " OR ".join(["(UPPER(LANDING_DATABASE) = %s AND UPPER(LANDING_SCHEMA) = %s AND UPPER(LANDING_TABLE) = %s)"] * len(triples))
    found = db.query(f"SELECT LANDING_DATABASE, LANDING_SCHEMA, LANDING_TABLE FROM {table} WHERE IS_ACTIVE AND ({where})",
                     tuple(v.upper() for t in triples for v in t))
    return sorted({f"{f['landing_database']}.{f['landing_schema']}.{f['landing_table']}" for f in found})


def _master_id(db: Any, table: str, header: Dict[str, Any], setting_: Dict[str, Any], stamp: str) -> Dict[str, Any]:
    """{id, created}: the source's master row, reused when it exists, else written now and read back by its stamp."""
    existing = db.query(f"""SELECT MASTER_CONFIG_ID FROM {table} WHERE UPPER(SOURCE_SYSTEM) = UPPER(%s)
                             ORDER BY ACTIVE_FLAG DESC, MASTER_CONFIG_ID LIMIT 1""", (header["source_name"],))
    if existing:
        return {"id": int(existing[0]["master_config_id"]), "created": False}
    master = sc.to_master(header, setting_, stamp)
    db.execute(sc.insert_master_sql(table), tuple(sc.master_params(master)))
    found = db.query(f"""SELECT MASTER_CONFIG_ID FROM {table}
                          WHERE SOURCE_SYSTEM = %s AND CREATED_AT = %s::TIMESTAMP_NTZ""", (master["SOURCE_SYSTEM"], stamp))
    assert len(found) == 1, f"could not read back the SnapLogic master row for {master['SOURCE_SYSTEM']} ({len(found)} found)"
    return {"id": int(found[0]["master_config_id"]), "created": True}


def write_new(db: Any, header: Dict[str, Any], rows: List[Dict[str, Any]], setting_: Dict[str, Any], user: str,
              source_id: str, master_id: int) -> Dict[str, Any]:
    """Inside the caller's transaction, after the CORE rows: the master row (new or reused), one detail row per table,
    and the links back on the CORE rows. Raises Conflict when SnapLogic already loads one of the landing tables."""
    t = sc.tables(setting_)
    clash = _landed(db, t["detail"], rows)
    if clash:
        raise Conflict(f"SnapLogic already loads {', '.join(clash)} (active in {t['detail']}); deactivate those rows first")
    stamp = sc.created_at()
    master = _master_id(db, t["master"], header, setting_, stamp)
    details = [sc.to_detail(r, header, master["id"], setting_, user, stamp) for r in rows]
    db.execute(sc.insert_detail_sql(t["detail"], len(details)), tuple(sc.detail_params(details)))
    found = db.query(f"""SELECT CHILD_CONFIG_ID, LANDING_TABLE FROM {t['detail']}
                          WHERE MASTER_CONFIG_ID = %s AND CREATED_AT = %s::TIMESTAMP_NTZ""", (master["id"], stamp))
    children = {str(f["landing_table"]).upper(): int(f["child_config_id"]) for f in found}
    assert len(children) == len(rows), f"wrote {len(rows)} SnapLogic detail rows but read back {len(children)}"
    # the links and the source-level answers on the CORE rows (columns from V070)
    db.execute("""UPDATE CORE.INGESTION_PIPELINE_CATALOG SET SCHEDULE_CRON = %s, SCHEDULE_FREQUENCY = %s,
                         SOURCE_SYSTEM_DESC = %s, DOMAIN = %s, AIRFLOW_SOURCE_SYSTEM = %s, SILVER_COMMAND_NAME = %s,
                         SNAPLOGIC_MASTER_CONFIG_ID = %s
                   WHERE SOURCE_ID = %s""",
               (header.get("schedule_cron"), header.get("schedule_frequency"), header.get("source_system_desc"),
                sc.domain_label(header.get("domain_name")), header.get("airflow_source_system"),
                header.get("silver_command_name"), master["id"], source_id))
    for r in rows:
        db.execute("""UPDATE CORE.INGESTION_CONTROL_CONFIG SET BRONZE_DATABASE = %s, SNAPLOGIC_CHILD_CONFIG_ID = %s
                       WHERE MASTER_ID = %s AND LANDING_TABLE = %s""",
                   (header.get("bronze_database") or setting_["defaults"].get("bronze_database"),
                    children[r["landing_table"].upper()], int(master_id), r["landing_table"]))
    return {"master_config_id": master["id"], "master_created": master["created"],
            "child_config_ids": [children[r["landing_table"].upper()] for r in rows], "tables": t}


def write_edit(db: Any, current: Dict[str, Any], row: Dict[str, Any], setting_: Dict[str, Any]) -> Dict[str, Any]:
    """Inside the caller's transaction: carry an edit of a CORE control row to its detail row. A row onboarded before
    the sync was on has no link and is skipped (not an error)."""
    child = current.get("snaplogic_child_config_id")
    if not child:
        return {"skipped": "this table was onboarded before the SnapLogic sync was on, so it has no SnapLogic row to follow"}
    t = sc.tables(setting_)
    values = sc.edit_values(row)
    cols = list(values)
    count = db.execute_count(sc.update_detail_sql(t["detail"], cols), (*[values[c] for c in cols], int(child))) \
        if hasattr(db, "execute_count") else db.execute(sc.update_detail_sql(t["detail"], cols), (*[values[c] for c in cols], int(child)))
    if count == 0:
        return {"skipped": f"SnapLogic row {child} is no longer in {t['detail']}"}
    return {"child_config_id": int(child), "table": t["detail"]}


def log(db: Any, kind: str, status: str, setting_: Dict[str, Any], *, source_id: Optional[str] = None,
        master_id: Optional[int] = None, run_id: Optional[str] = None, result: Optional[Dict[str, Any]] = None,
        request: Optional[Dict[str, Any]] = None, error: Optional[str] = None) -> Optional[str]:
    """One CORE.SNAPLOGIC_SYNC_LOG row; never fails the onboarding it describes (a missing V070 is only skipped)."""
    sync_id = str(uuid.uuid4())
    result = result or {}
    try:
        db.execute(f"""INSERT INTO {LOG} (SYNC_ID, KIND, STATUS, SOURCE_ID, MASTER_ID, RUN_ID, MASTER_CONFIG_ID, CHILD_CONFIG_IDS,
                                         TARGET_TABLES, REQUEST, ERROR)
                       SELECT %s, %s, %s, %s, %s, %s, %s, PARSE_JSON(%s), PARSE_JSON(%s), PARSE_JSON(%s), %s""",
                   (sync_id, kind, status, source_id, master_id, run_id, result.get("master_config_id"),
                    json.dumps(result.get("child_config_ids") or []), json.dumps(sc.tables(setting_)),
                    json.dumps(request) if request is not None else None, (error or "")[:4000] or None))
    except Exception:
        return None
    return sync_id
