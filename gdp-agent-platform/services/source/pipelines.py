"""External (non-Snowflake) sources in the Source Hub: the intake form and its validation (pure).

The platform does not run these pipelines. The Source Hub asks the intake questions, and the answers become one row
of CORE.INGESTION_PIPELINE_CATALOG (V035), which the ingestion tool (SnapLogic) reads to load the named
landing table. The questions are data: CORE.PLATFORM_CONFIG key INGESTION_INTAKE_FORM (seeded with DEFAULT_FORM,
editable in Admin), so a team can ask exactly what its pipelines need without a code change.

Form shape: {"version": 1, "sections": [{"title", "fields": [field]}]}, field = {"key", "label", "type", "required",
"secret", "options", "help", "placeholder", "pattern", "show_if": {"key": "...", "in": [...]}}. Types: text, textarea,
number, select, password, boolean, cron. A field marked secret (every password field is) is never stored in clear:
the API seals the secret answers together with secretbox (purpose INGESTION_SECRET, context the source id).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

FORM_KEY = "INGESTION_INTAKE_FORM"
TYPES = ("text", "textarea", "number", "select", "password", "boolean", "cron")
STATUSES = ("REGISTERED", "ACTIVE", "PAUSED", "RETIRED")
SOURCE_TYPES = ("ORACLE", "SQLSERVER", "POSTGRES", "MYSQL", "SALESFORCE", "REST_API", "S3", "SFTP", "OTHER")
CONNECTION_TYPES = ("JDBC", "S3", "SFTP", "REST")
FREQUENCIES = ("HOURLY", "DAILY", "WEEKLY", "MONTHLY", "ON_DEMAND", "CRON")
# Answers stored in their own catalog columns rather than in CONNECTION_METADATA.
COLUMN_KEYS = ("source_name", "source_type", "connection_type", "target_landing_table", "snaplogic_pipeline_name",
               "frequency", "cron", "timezone", "domain_id", "notes")
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
TABLE_FQN = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$]*){0,2}$")
CRON = re.compile(r"^\s*(\S+\s+){4}\S+\s*$")
MAX_TEXT = 4000

_JDBC = {"key": "connection_type", "in": ["JDBC"]}
DEFAULT_FORM: Dict[str, Any] = {
    "version": 1,
    "sections": [
        {"title": "Source", "fields": [
            {"key": "source_name", "label": "Source name", "type": "text", "required": True,
             "placeholder": "CRM_ORACLE", "pattern": NAME.pattern,
             "help": "Letters, digits and _; starts with a letter. Used to name the landing table."},
            {"key": "source_type", "label": "Source system", "type": "select", "required": True,
             "options": list(SOURCE_TYPES)},
            {"key": "connection_type", "label": "Connection type", "type": "select", "required": True,
             "options": list(CONNECTION_TYPES)},
            {"key": "description", "label": "What the source holds", "type": "textarea"},
            {"key": "owner", "label": "Business owner", "type": "text"},
        ]},
        {"title": "Connection", "fields": [
            {"key": "host", "label": "Host", "type": "text", "required": True, "show_if": _JDBC},
            {"key": "port", "label": "Port", "type": "number", "show_if": _JDBC},
            {"key": "database", "label": "Database or service name", "type": "text", "show_if": _JDBC},
            {"key": "source_schema", "label": "Source schema", "type": "text", "show_if": _JDBC},
            {"key": "base_url", "label": "Base URL", "type": "text", "required": True,
             "show_if": {"key": "connection_type", "in": ["REST"]}},
            {"key": "bucket_path", "label": "Bucket and prefix", "type": "text", "required": True,
             "placeholder": "s3://bucket/prefix/", "show_if": {"key": "connection_type", "in": ["S3"]}},
            {"key": "sftp_path", "label": "Server and folder", "type": "text", "required": True,
             "placeholder": "sftp.example.com:/outbound", "show_if": {"key": "connection_type", "in": ["SFTP"]}},
            {"key": "file_format", "label": "File format", "type": "select", "options": ["CSV", "PARQUET", "JSON", "AVRO"],
             "show_if": {"key": "connection_type", "in": ["S3", "SFTP"]}},
            {"key": "auth_user", "label": "User or client id", "type": "text"},
            {"key": "auth_secret", "label": "Password, key or token", "type": "password", "secret": True,
             "help": "Encrypted before it is stored; never shown again."},
        ]},
        {"title": "Extraction", "fields": [
            {"key": "objects", "label": "Tables, objects or endpoints", "type": "textarea", "required": True,
             "help": "One per line."},
            {"key": "extraction_query", "label": "Extraction query or filter", "type": "textarea"},
            {"key": "load_mode", "label": "Load mode", "type": "select", "options": ["FULL", "INCREMENTAL"]},
            {"key": "incremental_column", "label": "Incremental column", "type": "text",
             "show_if": {"key": "load_mode", "in": ["INCREMENTAL"]}},
        ]},
        {"title": "Schedule and landing", "fields": [
            {"key": "frequency", "label": "Frequency", "type": "select", "required": True, "options": list(FREQUENCIES)},
            {"key": "cron", "label": "Cron expression", "type": "cron", "required": True, "placeholder": "0 2 * * *",
             "show_if": {"key": "frequency", "in": ["CRON"]}},
            {"key": "timezone", "label": "Time zone", "type": "text", "placeholder": "UTC"},
            {"key": "target_landing_table", "label": "Target landing table", "type": "text", "required": True,
             "placeholder": "LANDING.CRM_ORACLE__ACCOUNTS", "pattern": TABLE_FQN.pattern},
            {"key": "snaplogic_pipeline_name", "label": "SnapLogic pipeline", "type": "text"},
            {"key": "notes", "label": "Notes", "type": "textarea"},
        ]},
    ],
}


def fields(form: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [f for s in (form or {}).get("sections") or [] for f in s.get("fields") or []]


def check_form(form: Dict[str, Any]) -> List[str]:
    """Problems with an intake form (a bad admin edit must not break the wizard)."""
    problems: List[str] = []
    if not isinstance(form, dict) or not isinstance(form.get("sections"), list):
        return ["the form needs a list of sections"]
    seen = set()
    for f in fields(form):
        key = str(f.get("key") or "")
        if not re.match(r"^[a-z][a-z0-9_]{0,63}$", key):
            problems.append(f"field key {key!r} must be lower case letters, digits and _")
        if key in seen:
            problems.append(f"field {key} appears twice")
        seen.add(key)
        if f.get("type") not in TYPES:
            problems.append(f"{key}: type must be one of {list(TYPES)}")
        if f.get("type") == "select" and not f.get("options"):
            problems.append(f"{key}: a select needs options")
        if f.get("pattern"):
            try:
                re.compile(f["pattern"])
            except re.error:
                problems.append(f"{key}: pattern is not a valid regular expression")
    for required in ("source_name", "source_type", "target_landing_table"):
        if required not in seen:
            problems.append(f"the form must ask for {required}")
    return problems


def form_or_default(stored: Any) -> Dict[str, Any]:
    return stored if isinstance(stored, dict) and not check_form(stored) else DEFAULT_FORM


def _visible(field: Dict[str, Any], answers: Dict[str, Any]) -> bool:
    cond = field.get("show_if")
    if not cond:
        return True
    return str(answers.get(cond.get("key")) or "") in [str(v) for v in cond.get("in") or []]


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def validate(form: Dict[str, Any], answers: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str], Dict[str, str]]:
    """(public answers, secret answers, errors by field key). Hidden fields (show_if not met) and keys the form does
    not ask are dropped, so the catalog holds exactly what was asked."""
    public: Dict[str, Any] = {}
    secrets: Dict[str, str] = {}
    errors: Dict[str, str] = {}
    answers = answers or {}
    for f in fields(form):
        key, kind = f["key"], f.get("type")
        if not _visible(f, answers):
            continue
        value = answers.get(key)
        if _blank(value):
            if f.get("required"):
                errors[key] = f"{f.get('label') or key} is required"
            continue
        if kind == "number":
            try:
                value = int(value) if float(value).is_integer() else float(value)
            except (TypeError, ValueError):
                errors[key] = f"{f.get('label') or key} must be a number"
                continue
        elif kind == "boolean":
            value = value if isinstance(value, bool) else str(value).lower() in ("true", "yes", "1", "on")
        else:
            value = str(value).strip()
            if len(value) > MAX_TEXT:
                errors[key] = f"{f.get('label') or key} is longer than {MAX_TEXT} characters"
                continue
            if kind == "select" and value not in [str(o) for o in f.get("options") or []]:
                errors[key] = f"{f.get('label') or key} must be one of {f.get('options')}"
                continue
            if kind == "cron" and not CRON.match(value):
                errors[key] = "a cron expression has five fields, e.g. 0 2 * * *"
                continue
            if f.get("pattern") and not re.fullmatch(f["pattern"], value):
                errors[key] = f"{f.get('label') or key} has an invalid format"
                continue
        if f.get("secret") or kind == "password":
            secrets[key] = str(value)
        else:
            public[key] = value
    if public.get("source_name") and not NAME.match(str(public["source_name"])):
        errors["source_name"] = "letters, digits and _, starting with a letter"
    if public.get("target_landing_table") and not TABLE_FQN.match(str(public["target_landing_table"])):
        errors["target_landing_table"] = "a table name, optionally SCHEMA.TABLE or DATABASE.SCHEMA.TABLE"
    return public, secrets, errors


def catalog_row(source_id: str, public: Dict[str, Any], status: str = "REGISTERED") -> Dict[str, Any]:
    """Catalog columns for validated public answers; everything not stored in its own column is CONNECTION_METADATA."""
    assert status in STATUSES, f"status must be one of {list(STATUSES)}"
    schedule = {k: public[k] for k in ("frequency", "cron", "timezone") if public.get(k) not in (None, "")}
    source_type = str(public.get("source_type") or "OTHER").upper()
    return {
        "source_id": source_id,
        "source_name": str(public.get("source_name") or "").upper(),
        "source_type": source_type[:32],
        "connection_type": (str(public.get("connection_type")).upper()[:16] if public.get("connection_type") else None),
        "connection_metadata": {k: v for k, v in public.items() if k not in COLUMN_KEYS},
        "schedule": schedule or None,
        "target_landing_table": str(public.get("target_landing_table") or "").upper() or None,
        "snaplogic_pipeline_name": public.get("snaplogic_pipeline_name") or None,
        "domain_id": public.get("domain_id") or None,
        "notes": public.get("notes") or None,
        "status": status,
    }


def mask(row: Dict[str, Any]) -> Dict[str, Any]:
    """A catalog row for the browser: never the sealed bytes, only whether a secret is on record."""
    out = {k: v for k, v in row.items() if k != "secret_sealed"}
    out["has_secret"] = bool(row.get("secret_sealed"))
    return out


def default_landing_table(source_name: str, obj: Optional[str] = None) -> str:
    from services.source.identifiers import landing_table_name

    return f"LANDING.{landing_table_name(source_name, obj or 'DATA')}"
