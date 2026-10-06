"""Source adapters. Phase 1 sources are Snowflake databases and Snowflake shares (mounted databases).

An adapter only reads metadata and builds SELECT statements; it never writes. All SQL runs through
`Runner`, a callable (sql, params) -> list of row dicts, so the logic is unit-testable without Snowflake.
Source onboarding procedures run as CALLER so the Snowflake role chosen in the UI governs
catalog access, registration, validation, and landing reads.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from services.source.identifiers import fqn, normalize, quote

Runner = Callable[[str, list], List[Dict]]

PASSED, FAILED, WARNING = "PASSED", "FAILED", "WARNING"
OBJECT_TYPES = {"BASE TABLE": "TABLE", "VIEW": "VIEW", "MATERIALIZED VIEW": "MATERIALIZED_VIEW"}
MAX_OBJECTS = 1000
MAX_SELECTED = 500
STORAGE_TYPES = ("IN_PLACE", "MANAGED", "ICEBERG")
DEFAULT_LANDING_SCHEMA = "LANDING"
VOLUME_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


@dataclass(frozen=True)
class SourceSpec:
    """What to read: a registered connection (SOURCE_REGISTRY id) and the objects selected in it."""

    connection_id: Optional[str]
    database: str
    schema: str
    tables: Tuple[str, ...] = ()

    @classmethod
    def parse(cls, raw: Dict[str, Any]) -> "SourceSpec":
        tables = raw.get("tables") or []
        assert isinstance(tables, (list, tuple)) and all(isinstance(t, str) for t in tables), \
            "source_spec.tables must be a list of object names"
        assert len(tables) <= MAX_SELECTED, f"select at most {MAX_SELECTED} objects per run"
        return cls(raw.get("connection_id") or None, normalize(raw.get("database") or ""),
                   normalize(raw.get("schema") or ""), tuple(sorted(set(tables))))


@dataclass(frozen=True)
class TargetSpec:
    """Where to land: decoupled from the source so one connection can feed several landing zones."""

    landing_database: str
    landing_schema: str = DEFAULT_LANDING_SCHEMA
    storage_type: str = "MANAGED"
    external_volume: Optional[str] = field(default=None, compare=False)

    @classmethod
    def parse(cls, raw: Optional[Dict[str, Any]], default_database: str,
              external_volume: Optional[str] = None) -> "TargetSpec":
        raw = raw or {}
        storage = str(raw.get("storage_type") or "MANAGED").upper()
        assert storage in STORAGE_TYPES, f"storage_type must be one of {STORAGE_TYPES}"
        spec = cls(normalize(raw.get("landing_database") or default_database),
                   normalize(raw.get("landing_schema") or DEFAULT_LANDING_SCHEMA), storage, external_volume)
        if storage == "ICEBERG":
            assert external_volume and VOLUME_NAME.match(external_volume), (
                "ICEBERG landing needs an external volume: set PLATFORM_CONFIG LANDING_EXTERNAL_VOLUME")
        return spec

    def as_dict(self) -> Dict[str, str]:
        return {"landing_database": self.landing_database, "landing_schema": self.landing_schema,
                "storage_type": self.storage_type}

    @property
    def copies(self) -> bool:
        """IN_PLACE reads the source table directly; nothing is copied or created."""
        return self.storage_type != "IN_PLACE"

    def create_sql(self, table: str, select_sql: str, comment: str) -> str:
        assert self.copies, "IN_PLACE targets never create tables"
        target = fqn(self.landing_database, self.landing_schema, table)
        safe_comment = comment.replace("\\", "\\\\").replace("'", "''")
        if self.storage_type == "ICEBERG":
            location = f"{self.landing_schema}/{table}".lower()
            return (f"CREATE OR REPLACE ICEBERG TABLE {target} EXTERNAL_VOLUME = '{self.external_volume}' "
                    f"CATALOG = 'SNOWFLAKE' BASE_LOCATION = '{location}' "
                    f"COMMENT = '{safe_comment}' AS {select_sql}")
        return f"CREATE OR REPLACE TABLE {target} COMMENT = '{safe_comment}' AS {select_sql}"


@dataclass(frozen=True)
class AccessCheck:
    name: str
    status: str
    detail: str
    remediation: Optional[str] = None

    def as_dict(self) -> Dict:
        return asdict(self)


@dataclass(frozen=True)
class SourceObjectInfo:
    database: str
    schema: str
    name: str
    object_type: str
    row_count: Optional[int]
    bytes: Optional[int]
    last_altered: Optional[str]


def checks_passed(checks: Sequence[AccessCheck]) -> bool:
    return bool(checks) and all(c.status != FAILED for c in checks)


class SourceAdapter(ABC):
    source_type: str = ""

    def __init__(self, database: str, schema: str):
        self.database = normalize(database)
        self.schema = normalize(schema)

    def object_fqn(self, name: str) -> str:
        return fqn(self.database, self.schema, name)

    def select_sql(self, name: str) -> str:
        return f"SELECT * FROM {self.object_fqn(name)}"

    def count_sql(self, name: str) -> str:
        return f"SELECT COUNT(*) AS N FROM {self.object_fqn(name)}"

    def columns_sql(self) -> str:
        return (
            "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, "
            "NUMERIC_SCALE, ORDINAL_POSITION, IS_NULLABLE, COMMENT "
            f"FROM {quote(self.database)}.INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION"
        )

    def database_type(self, run: Runner) -> Optional[str]:
        rows = run(f"SELECT TYPE FROM {quote(self.database)}.INFORMATION_SCHEMA.DATABASES "
                   "WHERE DATABASE_NAME = ?", [self.database])
        return rows[0]["TYPE"] if rows else None

    @abstractmethod
    def type_check(self, db_type: Optional[str]) -> AccessCheck: ...

    def discover_objects(self, run: Runner) -> List[SourceObjectInfo]:
        rows = run(
            "SELECT TABLE_NAME, TABLE_TYPE, ROW_COUNT, BYTES, LAST_ALTERED::VARCHAR AS LAST_ALTERED "
            f"FROM {quote(self.database)}.INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = ? AND TABLE_TYPE IN ('BASE TABLE', 'VIEW', 'MATERIALIZED VIEW') "
            f"ORDER BY TABLE_NAME LIMIT {MAX_OBJECTS + 1}",
            [self.schema],
        )
        assert len(rows) <= MAX_OBJECTS, f"schema has more than {MAX_OBJECTS} objects; narrow the source"
        return [
            SourceObjectInfo(self.database, self.schema, r["TABLE_NAME"], OBJECT_TYPES[r["TABLE_TYPE"]],
                             r["ROW_COUNT"], r["BYTES"], r["LAST_ALTERED"])
            for r in rows
        ]

    def fetch_schema_catalog(self, run: Runner) -> List[Dict[str, Any]]:
        """Inspection only: every object with its row count and column metadata, in two queries."""
        columns: Dict[str, List[Dict[str, Any]]] = {}
        for c in run(self.columns_sql(), [self.schema]):
            columns.setdefault(c["TABLE_NAME"], []).append({
                "column_name": c["COLUMN_NAME"], "data_type": c["DATA_TYPE"],
                "ordinal_position": c["ORDINAL_POSITION"], "nullable": c["IS_NULLABLE"] == "YES",
                "comment": c.get("COMMENT"),
            })
        return [{**asdict(o), "column_count": len(columns.get(o.name, [])), "columns": columns.get(o.name, [])}
                for o in self.discover_objects(run)]

    def validate_access(self, run: Runner, selected: Sequence[str]) -> List[AccessCheck]:
        """Ordered checks; stops at the first failure that makes later checks meaningless."""
        checks: List[AccessCheck] = []
        try:
            db_type = self.database_type(run)
        except Exception as exc:
            return [AccessCheck("DATABASE", FAILED, f"{self.database} is not accessible: {exc}",
                                f"Grant USAGE (or IMPORTED PRIVILEGES for a share) on {self.database} "
                                "to the platform owner role")]
        if db_type is None:
            return [AccessCheck("DATABASE", FAILED, f"{self.database} not found", "Check the database name")]
        checks.append(AccessCheck("DATABASE", PASSED, f"{self.database} ({db_type})"))
        checks.append(self.type_check(db_type))
        if checks[-1].status == FAILED:
            return checks

        schemas = run(f"SELECT SCHEMA_NAME FROM {quote(self.database)}.INFORMATION_SCHEMA.SCHEMATA "
                      "WHERE SCHEMA_NAME = ?", [self.schema])
        if not schemas:
            checks.append(AccessCheck("SCHEMA", FAILED, f"{self.schema} not found or not visible",
                                      f"Grant USAGE on schema {self.database}.{self.schema}"))
            return checks
        checks.append(AccessCheck("SCHEMA", PASSED, f"{self.database}.{self.schema}"))

        objects = {o.name for o in self.discover_objects(run)}
        if not objects:
            checks.append(AccessCheck("TABLES", FAILED, "no tables or views are visible in the schema",
                                      "Grant SELECT on the tables to be onboarded"))
            return checks
        checks.append(AccessCheck("TABLES", PASSED, f"{len(objects)} objects visible"))

        if not selected:
            checks.append(AccessCheck("SELECTED_OBJECTS", FAILED, "no objects selected", "Select at least one table"))
            return checks
        if len(selected) > MAX_SELECTED:
            checks.append(AccessCheck("SELECTED_OBJECTS", FAILED, f"{len(selected)} objects selected",
                                      f"Select at most {MAX_SELECTED} objects per run"))
            return checks
        for name in selected:
            if name not in objects:
                checks.append(AccessCheck("SELECTED_OBJECTS", FAILED, f"{name} is not in the discovered objects"))
                continue
            try:
                run(f"{self.select_sql(name)} LIMIT 0", [])
                checks.append(AccessCheck("SELECTED_OBJECTS", PASSED, f"{name} readable"))
            except Exception as exc:
                checks.append(AccessCheck("SELECTED_OBJECTS", FAILED, f"{name} not readable: {exc}",
                                          f"Grant SELECT on {self.database}.{self.schema}.{name}"))
        return checks


class SnowflakeDatabaseAdapter(SourceAdapter):
    source_type = "SNOWFLAKE_DATABASE"

    def type_check(self, db_type: Optional[str]) -> AccessCheck:
        if db_type == "IMPORTED DATABASE":
            return AccessCheck("SOURCE_TYPE", WARNING, "database is a mounted share",
                               "Register it as SNOWFLAKE_SHARE to record share lineage")
        return AccessCheck("SOURCE_TYPE", PASSED, f"database type {db_type}")


class SnowflakeShareAdapter(SourceAdapter):
    """A share is consumed as a database created FROM SHARE (an admin step); then it reads like a database."""

    source_type = "SNOWFLAKE_SHARE"

    def type_check(self, db_type: Optional[str]) -> AccessCheck:
        if db_type != "IMPORTED DATABASE":
            return AccessCheck("SHARE", FAILED, f"{self.database} is a {db_type} database, not a mounted share",
                               "An admin must run CREATE DATABASE ... FROM SHARE and grant IMPORTED PRIVILEGES")
        return AccessCheck("SHARE", PASSED, f"{self.database} is a mounted share")


class LandedExternalAdapter(SnowflakeDatabaseAdapter):
    """An external source (files, database, SaaS, API) after it has been landed into a Snowflake schema."""

    source_type = "EXTERNAL"

    def type_check(self, db_type: Optional[str]) -> AccessCheck:
        return AccessCheck("SOURCE_TYPE", PASSED, f"external source landed in {self.database}.{self.schema}")


ADAPTERS = {a.source_type: a for a in (SnowflakeDatabaseAdapter, SnowflakeShareAdapter)}


def adapter_for(source_type: str, database: str, schema: str) -> SourceAdapter:
    if str(source_type or "").upper().startswith("EXTERNAL_"):
        return LandedExternalAdapter(database, schema)
    assert source_type in ADAPTERS, f"SOURCE_TYPE must be one of {sorted(ADAPTERS)}"
    return ADAPTERS[source_type](database, schema)
