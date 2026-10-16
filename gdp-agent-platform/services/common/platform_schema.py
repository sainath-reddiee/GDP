"""The one schema that holds every platform object, and the map from the schemas used before V061.

Every platform table, view, stage, file format, sequence, policy, procedure, task, git repository, Cortex Search service
and the Cortex Agent lives in <database>.PLATFORM_SCHEMA. Static SQL names objects as `CORE.<NAME>`;
tests/unit/test_platform_schema.py fails the build if any SQL names a platform object in another schema, so changing
PLATFORM_SCHEMA is one constant plus one codemod pass. Names composed at runtime (tasks, dbt projects, git
repositories, stages, deploy rendering) use the helpers below.

MOVED is the single source of truth for the consolidation: V061 (the renames), the compatibility views, the codemod,
the verification and the rollback are all generated from it. It is data, never edited after V061 is applied.

Secrets, git repository clones and dbt projects are not renamed by V061 (Snowflake has no RENAME for the first two and
a secret value cannot be read back). New ones are created in PLATFORM_SCHEMA; existing ones are moved by
infrastructure/consolidation/cleanup.py, which also re-points the rows that name them. `resolve` therefore leaves
those kinds alone.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

PLATFORM_SCHEMA = "CORE"

# Schemas the platform used before V061. LANDING and GDP_SILVER hold landed and generated client data and stay as they
# are (data plane, user-configurable locations stored in registry rows).
LEGACY_SCHEMAS = ("SOURCE", "PROFILE", "METADATA", "KNOWLEDGE", "MAPPING", "CONTRACT", "CODEGEN", "MODELING",
                  "QUALITY", "AUDIT", "OPS", "CASES", "JIRA", "CODE", "GOVERNANCE", "PLATFORM_CONFIG", "CONFLUENCE")
DATA_PLANE_SCHEMAS = ("LANDING", "GDP_SILVER")

# Methods: RENAME moves the object in place (same object: data, files, Time Travel, grants); RECREATE means the deploy
# creates it in PLATFORM_SCHEMA from code (no state of its own); RUNTIME means infrastructure/consolidate_runtime.py
# handles it from the live account.
RENAME, RECREATE, RUNTIME = "RENAME", "RECREATE", "RUNTIME"

# (old schema, old name) -> (object type, new name, method). New name equals old name unless it would be ambiguous in
# one flat schema.
MOVED: Dict[Tuple[str, str], Tuple[str, str, str]] = {
    # SOURCE
    ("SOURCE", "SOURCE_REGISTRY"): ("TABLE", "SOURCE_REGISTRY", RENAME),
    ("SOURCE", "SOURCE_ACCESS_CHECK"): ("TABLE", "SOURCE_ACCESS_CHECK", RENAME),
    ("SOURCE", "SOURCE_OBJECT"): ("TABLE", "SOURCE_OBJECT", RENAME),
    ("SOURCE", "LANDING_TABLE_REGISTRY"): ("TABLE", "LANDING_TABLE_REGISTRY", RENAME),
    ("SOURCE", "LANDING_COLUMN_REGISTRY"): ("TABLE", "LANDING_COLUMN_REGISTRY", RENAME),
    ("SOURCE", "PARQUET_SNAPPY"): ("FILE FORMAT", "PARQUET_SNAPPY", RENAME),
    ("SOURCE", "LANDING_STAGE"): ("STAGE", "LANDING_STAGE", RENAME),
    ("SOURCE", "REGISTER_SOURCE"): ("PROCEDURE", "REGISTER_SOURCE", RECREATE),
    ("SOURCE", "VALIDATE_SOURCE_ACCESS"): ("PROCEDURE", "VALIDATE_SOURCE_ACCESS", RECREATE),
    ("SOURCE", "EXECUTE_LANDING"): ("PROCEDURE", "EXECUTE_LANDING", RECREATE),
    ("SOURCE", "FETCH_SCHEMA_CATALOG"): ("PROCEDURE", "FETCH_SCHEMA_CATALOG", RECREATE),
    ("SOURCE", "SET_LANDING_TARGET"): ("PROCEDURE", "SET_LANDING_TARGET", RECREATE),
    ("SOURCE", "PROFILE_SOURCE_TABLES"): ("PROCEDURE", "PROFILE_SOURCE_TABLES", RECREATE),
    ("SOURCE", "REGISTER_CONNECTION"): ("PROCEDURE", "REGISTER_CONNECTION", RECREATE),
    ("SOURCE", "REGISTER_EXTERNAL_SOURCE"): ("PROCEDURE", "REGISTER_EXTERNAL_SOURCE", RECREATE),
    ("SOURCE", "LIST_EXTERNAL_FILES"): ("PROCEDURE", "LIST_EXTERNAL_FILES", RECREATE),
    ("SOURCE", "LAND_EXTERNAL_FILES"): ("PROCEDURE", "LAND_EXTERNAL_FILES", RECREATE),
    ("SOURCE", "EXECUTE_STAGED_LANDING"): ("PROCEDURE", "EXECUTE_STAGED_LANDING", RECREATE),
    # PROFILE
    ("PROFILE", "PROFILE_REGISTRY"): ("TABLE", "PROFILE_REGISTRY", RENAME),
    ("PROFILE", "RUN_PROFILING"): ("PROCEDURE", "RUN_PROFILING", RECREATE),
    ("PROFILE", "REFRESH_TABLE_PROFILE"): ("PROCEDURE", "REFRESH_TABLE_PROFILE", RECREATE),
    ("PROFILE", "RUN_SCHEDULED_PROFILE"): ("PROCEDURE", "RUN_SCHEDULED_PROFILE", RECREATE),
    # METADATA
    ("METADATA", "TABLE_PROFILES"): ("TABLE", "TABLE_PROFILES", RENAME),
    ("METADATA", "PROFILE_JSON_FORMAT"): ("FILE FORMAT", "PROFILE_JSON_FORMAT", RENAME),
    ("METADATA", "PROFILES_STAGE"): ("STAGE", "PROFILES_STAGE", RENAME),
    # KNOWLEDGE
    ("KNOWLEDGE", "DOMAIN_REGISTRY"): ("TABLE", "DOMAIN_REGISTRY", RENAME),
    ("KNOWLEDGE", "DOMAIN_KNOWLEDGE"): ("TABLE", "DOMAIN_KNOWLEDGE", RENAME),
    ("KNOWLEDGE", "TARGET_TABLE_REGISTRY"): ("TABLE", "TARGET_TABLE_REGISTRY", RENAME),
    ("KNOWLEDGE", "TARGET_COLUMN_REGISTRY"): ("TABLE", "TARGET_COLUMN_REGISTRY", RENAME),
    ("KNOWLEDGE", "DOMAIN_RECOMMENDATION"): ("TABLE", "DOMAIN_RECOMMENDATION", RENAME),
    ("KNOWLEDGE", "SKILL_REGISTRY"): ("TABLE", "SKILL_REGISTRY", RENAME),
    ("KNOWLEDGE", "SKILL_CATEGORY"): ("TABLE", "SKILL_CATEGORY", RENAME),
    ("KNOWLEDGE", "SKILL_SETTING"): ("TABLE", "SKILL_SETTING", RENAME),
    ("KNOWLEDGE", "SKILL_LABEL"): ("TABLE", "SKILL_LABEL", RENAME),
    ("KNOWLEDGE", "SKILL_LABEL_HISTORY"): ("TABLE", "SKILL_LABEL_HISTORY", RENAME),
    ("KNOWLEDGE", "SKILL_STAGE_BINDING"): ("TABLE", "SKILL_STAGE_BINDING", RENAME),
    ("KNOWLEDGE", "KNOWLEDGE_USAGE"): ("TABLE", "KNOWLEDGE_USAGE", RENAME),
    ("KNOWLEDGE", "DOMAIN_VERSION"): ("TABLE", "DOMAIN_VERSION", RENAME),
    ("KNOWLEDGE", "DOMAIN_MEMBER"): ("TABLE", "DOMAIN_MEMBER", RENAME),
    ("KNOWLEDGE", "KNOWLEDGE_OUTCOME"): ("TABLE", "KNOWLEDGE_OUTCOME", RENAME),
    ("KNOWLEDGE", "DOMAIN_SKILL"): ("TABLE", "DOMAIN_SKILL", RENAME),
    ("KNOWLEDGE", "REFERENCE_INPUT"): ("TABLE", "REFERENCE_INPUT", RENAME),
    ("KNOWLEDGE", "REFERENCE_SCORE"): ("TABLE", "REFERENCE_SCORE", RENAME),
    ("KNOWLEDGE", "OPEN_QUESTION"): ("TABLE", "OPEN_QUESTION", RENAME),
    ("KNOWLEDGE", "CHANGE_EVAL"): ("TABLE", "CHANGE_EVAL", RENAME),
    ("KNOWLEDGE", "SKILL_STAGE"): ("STAGE", "SKILL_STAGE", RENAME),
    ("KNOWLEDGE", "KNOWLEDGE_SEARCH"): ("CORTEX SEARCH SERVICE", "KNOWLEDGE_SEARCH", RECREATE),
    ("KNOWLEDGE", "IDENTIFY_DOMAIN"): ("PROCEDURE", "IDENTIFY_DOMAIN", RECREATE),
    ("KNOWLEDGE", "SEARCH_KNOWLEDGE"): ("PROCEDURE", "SEARCH_KNOWLEDGE", RECREATE),
    ("KNOWLEDGE", "REGISTER_TARGET_TABLE"): ("PROCEDURE", "REGISTER_TARGET_TABLE", RECREATE),
    ("KNOWLEDGE", "LOAD_SKILL"): ("PROCEDURE", "LOAD_SKILL", RECREATE),
    # MAPPING
    ("MAPPING", "MAPPING_SCORING_CONFIG"): ("TABLE", "MAPPING_SCORING_CONFIG", RENAME),
    ("MAPPING", "COLUMN_EMBEDDING"): ("TABLE", "COLUMN_EMBEDDING", RENAME),
    ("MAPPING", "MAPPING_CANDIDATE"): ("TABLE", "MAPPING_CANDIDATE", RENAME),
    ("MAPPING", "MAPPING_DECISION"): ("TABLE", "MAPPING_DECISION", RENAME),
    ("MAPPING", "V_MAPPING_FEEDBACK"): ("VIEW", "V_MAPPING_FEEDBACK", RECREATE),
    ("MAPPING", "GENERATE_MAPPING_CANDIDATES"): ("PROCEDURE", "GENERATE_MAPPING_CANDIDATES", RECREATE),
    ("MAPPING", "SAVE_MAPPING_DECISIONS"): ("PROCEDURE", "SAVE_MAPPING_DECISIONS", RECREATE),
    # CONTRACT
    ("CONTRACT", "STTM_REGISTRY"): ("TABLE", "STTM_REGISTRY", RENAME),
    ("CONTRACT", "STTM_LINE"): ("TABLE", "STTM_LINE", RENAME),
    ("CONTRACT", "SODA_EXPECTATION_REGISTRY"): ("TABLE", "SODA_EXPECTATION_REGISTRY", RENAME),
    ("CONTRACT", "QA_TEST_CASE"): ("TABLE", "QA_TEST_CASE", RENAME),
    ("CONTRACT", "QA_SIGNOFF"): ("TABLE", "QA_SIGNOFF", RENAME),
    ("CONTRACT", "QA_TEST_SUITE"): ("TABLE", "QA_TEST_SUITE", RENAME),
    ("CONTRACT", "QA_TEST_VERSION"): ("TABLE", "QA_TEST_VERSION", RENAME),
    ("CONTRACT", "GENERATE_STTM"): ("PROCEDURE", "GENERATE_STTM", RECREATE),
    ("CONTRACT", "UPDATE_JOIN_GRAPH"): ("PROCEDURE", "UPDATE_JOIN_GRAPH", RECREATE),
    ("CONTRACT", "REFINE_TRANSFORMATION"): ("PROCEDURE", "REFINE_TRANSFORMATION", RECREATE),
    ("CONTRACT", "APPLY_TRANSFORMATION"): ("PROCEDURE", "APPLY_TRANSFORMATION", RECREATE),
    ("CONTRACT", "EXPORT_STTM_CSV"): ("PROCEDURE", "EXPORT_STTM_CSV", RECREATE),
    ("CONTRACT", "GENERATE_SODA"): ("PROCEDURE", "GENERATE_SODA", RECREATE),
    ("CONTRACT", "IMPORT_CLIENT_EXPECTATIONS"): ("PROCEDURE", "IMPORT_CLIENT_EXPECTATIONS", RECREATE),
    ("CONTRACT", "BACKTEST_SODA"): ("PROCEDURE", "BACKTEST_SODA", RECREATE),
    ("CONTRACT", "QA_SUITE"): ("PROCEDURE", "QA_SUITE", RECREATE),
    ("CONTRACT", "QA_ASK"): ("PROCEDURE", "QA_ASK", RECREATE),
    ("CONTRACT", "QA_SAVE"): ("PROCEDURE", "QA_SAVE", RECREATE),
    ("CONTRACT", "QA_TABLE_SUITE"): ("PROCEDURE", "QA_TABLE_SUITE", RECREATE),
    ("CONTRACT", "QA_TABLE_ASK"): ("PROCEDURE", "QA_TABLE_ASK", RECREATE),
    ("CONTRACT", "QA_TABLE_SAVE"): ("PROCEDURE", "QA_TABLE_SAVE", RECREATE),
    ("CONTRACT", "SAVE_SODA_DECISIONS"): ("PROCEDURE", "SAVE_SODA_DECISIONS", RECREATE),
    # CODEGEN
    ("CODEGEN", "DBT_GENERATION_REGISTRY"): ("TABLE", "DBT_GENERATION_REGISTRY", RENAME),
    ("CODEGEN", "GENERATED_ARTIFACT"): ("TABLE", "GENERATED_ARTIFACT", RENAME),
    ("CODEGEN", "VALIDATION_RUN"): ("TABLE", "VALIDATION_RUN", RENAME),
    ("CODEGEN", "GIT_PUBLICATION"): ("TABLE", "GIT_PUBLICATION", RENAME),
    ("CODEGEN", "DBT_STAGE"): ("STAGE", "DBT_STAGE", RENAME),
    ("CODEGEN", "RAW_TEXT_FORMAT"): ("FILE FORMAT", "RAW_TEXT_FORMAT", RENAME),
    ("CODEGEN", "GENERATE_DBT"): ("PROCEDURE", "GENERATE_DBT", RECREATE),
    ("CODEGEN", "VALIDATE_DBT"): ("PROCEDURE", "VALIDATE_DBT", RECREATE),
    ("CODEGEN", "PUBLISH_DBT_PR"): ("PROCEDURE", "PUBLISH_DBT_PR", RECREATE),
    ("CODEGEN", "GITHUB_API_EGRESS"): ("NETWORK RULE", "GITHUB_API_EGRESS", RUNTIME),
    ("CODEGEN", "SYNC_MODEL_PRS_TASK"): ("TASK", "SYNC_MODEL_PRS_TASK", RUNTIME),
    # MODELING
    ("MODELING", "MODEL_DESIGN"): ("TABLE", "MODEL_DESIGN", RENAME),
    ("MODELING", "APPLY_MODEL_DESIGN"): ("PROCEDURE", "APPLY_MODEL_DESIGN", RECREATE),
    ("MODELING", "APPLY_DOMAIN_MODEL"): ("PROCEDURE", "APPLY_DOMAIN_MODEL", RECREATE),
    ("MODELING", "PIN_RUN_MODEL"): ("PROCEDURE", "PIN_RUN_MODEL", RECREATE),
    # QUALITY
    ("QUALITY", "CHECK_RUN"): ("TABLE", "CHECK_RUN", RENAME),
    ("QUALITY", "CHECK_RESULT"): ("TABLE", "CHECK_RESULT", RENAME),
    ("QUALITY", "QA_RUN"): ("TABLE", "QA_RUN", RENAME),
    ("QUALITY", "QA_RESULT"): ("TABLE", "QA_RESULT", RENAME),
    ("QUALITY", "RUN_TABLE_SCAN"): ("PROCEDURE", "RUN_TABLE_SCAN", RECREATE),
    ("QUALITY", "APPLY_DQ_SUITE"): ("PROCEDURE", "APPLY_DQ_SUITE", RECREATE),
    # AUDIT
    ("AUDIT", "AGENT_RUN"): ("TABLE", "AGENT_RUN", RENAME),
    ("AUDIT", "AGENT_TOOL_CALL"): ("TABLE", "AGENT_TOOL_CALL", RENAME),
    ("AUDIT", "COST_USAGE"): ("TABLE", "COST_USAGE", RENAME),
    ("AUDIT", "V_RUN_COST"): ("VIEW", "V_RUN_COST", RECREATE),
    # OPS
    ("OPS", "AIRFLOW_ENV"): ("TABLE", "AIRFLOW_ENV", RENAME),
    ("OPS", "DAG"): ("TABLE", "DAG", RENAME),
    ("OPS", "DAG_RUN"): ("TABLE", "DAG_RUN", RENAME),
    ("OPS", "TASK_RUN"): ("TABLE", "TASK_RUN", RENAME),
    ("OPS", "EVENT"): ("TABLE", "EVENT", RENAME),
    ("OPS", "JOB_LEASE"): ("TABLE", "JOB_LEASE", RENAME),
    ("OPS", "TEAM"): ("TABLE", "TEAM", RENAME),
    ("OPS", "ROUTING_RULE"): ("TABLE", "ROUTING_RULE", RENAME),
    ("OPS", "INCIDENT"): ("TABLE", "INCIDENT", RENAME),
    ("OPS", "INCIDENT_EVENT"): ("TABLE", "INCIDENT_EVENT", RENAME),
    ("OPS", "NOTIFICATION"): ("TABLE", "NOTIFICATION", RENAME),
    ("OPS", "DAG_DEPENDENCY"): ("TABLE", "DAG_DEPENDENCY", RENAME),
    # CASES
    ("CASES", "CASE_RECORD"): ("TABLE", "CASE_RECORD", RENAME),
    ("CASES", "CASE_EVENT"): ("TABLE", "CASE_EVENT", RENAME),
    ("CASES", "CASE_LINK"): ("TABLE", "CASE_LINK", RENAME),
    ("CASES", "CASE_ARTIFACT"): ("TABLE", "CASE_ARTIFACT", RENAME),
    ("CASES", "CASE_SEQ"): ("SEQUENCE", "CASE_SEQ", RENAME),
    ("CASES", "DOMAIN_SCOPE"): ("ROW ACCESS POLICY", "DOMAIN_SCOPE", RENAME),
    # JIRA
    ("JIRA", "USER_TOKEN"): ("TABLE", "USER_TOKEN", RENAME),
    ("JIRA", "OAUTH_STATE"): ("TABLE", "OAUTH_STATE", RENAME),
    ("JIRA", "ISSUE_LINK"): ("TABLE", "ISSUE_LINK", RENAME),
    ("JIRA", "ACTION_LOG"): ("TABLE", "ACTION_LOG", RENAME),
    ("JIRA", "SAVED_FILTER"): ("TABLE", "SAVED_FILTER", RENAME),
    ("JIRA", "OWN_ROWS"): ("ROW ACCESS POLICY", "OWN_ROWS", RENAME),
    ("JIRA", "TRIAGE_ISSUE"): ("PROCEDURE", "TRIAGE_ISSUE", RECREATE),
    ("JIRA", "TRIAGE_TABLE"): ("PROCEDURE", "TRIAGE_TABLE", RECREATE),
    ("JIRA", "RESOLVE_TARGETS"): ("PROCEDURE", "RESOLVE_TARGETS", RECREATE),
    # CODE
    ("CODE", "REPO"): ("TABLE", "REPO", RENAME),
    ("CODE", "CODE_FILE"): ("TABLE", "CODE_FILE", RENAME),
    ("CODE", "CODE_CHUNK"): ("TABLE", "CODE_CHUNK", RENAME),
    ("CODE", "CODE_EDGE"): ("TABLE", "CODE_EDGE", RENAME),
    ("CODE", "REPO_SUMMARY"): ("TABLE", "REPO_SUMMARY", RENAME),
    ("CODE", "INDEX_RUN"): ("TABLE", "INDEX_RUN", RENAME),
    ("CODE", "CODE_USAGE"): ("TABLE", "CODE_USAGE", RENAME),
    ("CODE", "CODE_SEARCH"): ("CORTEX SEARCH SERVICE", "CODE_SEARCH", RECREATE),
    ("CODE", "INDEX_REPO"): ("PROCEDURE", "INDEX_REPO", RECREATE),
    # GOVERNANCE
    ("GOVERNANCE", "APP_ROLE"): ("TABLE", "APP_ROLE", RENAME),
    ("GOVERNANCE", "ROLE_PRIVILEGE"): ("TABLE", "ROLE_PRIVILEGE", RENAME),
    ("GOVERNANCE", "ROLE_GRANT"): ("TABLE", "ROLE_GRANT", RENAME),
    ("GOVERNANCE", "USER_ROLE"): ("TABLE", "USER_ROLE", RENAME),
    ("GOVERNANCE", "APPROVAL_POLICY"): ("TABLE", "APPROVAL_POLICY", RENAME),
    ("GOVERNANCE", "SETTING"): ("TABLE", "SETTING", RENAME),
    ("GOVERNANCE", "CHANGE_REQUEST"): ("TABLE", "CHANGE_REQUEST", RENAME),
    ("GOVERNANCE", "GOVERNANCE_EVENT"): ("TABLE", "GOVERNANCE_EVENT", RENAME),
    ("GOVERNANCE", "DATA_PROFILES"): ("TABLE", "DATA_PROFILES", RENAME),
    ("GOVERNANCE", "PERSISTENT_PROFILES"): ("VIEW", "PERSISTENT_PROFILES", RENAME),
    ("GOVERNANCE", "PROFILE_BOOKMARK"): ("TABLE", "PROFILE_BOOKMARK", RENAME),
    ("GOVERNANCE", "STTM_CATALOG"): ("TABLE", "STTM_CATALOG", RENAME),
    ("GOVERNANCE", "MODEL_DESIGNS"): ("TABLE", "MODEL_DESIGNS", RENAME),
    ("GOVERNANCE", "DOMAIN_QA_PACKS"): ("TABLE", "DOMAIN_QA_PACKS", RENAME),
    ("GOVERNANCE", "DOMAIN_QA_QUERIES"): ("TABLE", "DOMAIN_QA_QUERIES", RENAME),
    ("GOVERNANCE", "DQ_CHECKS_CATALOG"): ("TABLE", "DQ_CHECKS_CATALOG", RENAME),
    ("GOVERNANCE", "DQ_SCHEDULE"): ("TABLE", "DQ_SCHEDULE", RENAME),
    ("GOVERNANCE", "DOMAIN_MODEL"): ("TABLE", "DOMAIN_MODEL", RENAME),
    ("GOVERNANCE", "MODEL_REVIEW_COMMENT"): ("TABLE", "MODEL_REVIEW_COMMENT", RENAME),
    ("GOVERNANCE", "MODEL_CHANGE"): ("TABLE", "MODEL_CHANGE", RENAME),
    ("GOVERNANCE", "DQ_SUITE"): ("TABLE", "DQ_SUITE", RENAME),
    ("GOVERNANCE", "DQ_SUITE_VERSION"): ("TABLE", "DQ_SUITE_VERSION", RENAME),
    ("GOVERNANCE", "PROFILE_SCHEDULE"): ("TABLE", "PROFILE_SCHEDULE", RENAME),
    ("GOVERNANCE", "PROFILE_ANNOTATION"): ("TABLE", "PROFILE_ANNOTATION", RENAME),
    ("GOVERNANCE", "QA_PACK_SYNC"): ("TABLE", "QA_PACK_SYNC", RENAME),
    ("GOVERNANCE", "GIT_PR"): ("TABLE", "GIT_PR", RENAME),
    ("GOVERNANCE", "TABLE_MODEL"): ("TABLE", "TABLE_MODEL", RENAME),
    ("GOVERNANCE", "TABLE_MODEL_VERSION"): ("TABLE", "TABLE_MODEL_VERSION", RENAME),
    ("GOVERNANCE", "GOLD_MAPPING_LINE"): ("TABLE", "GOLD_MAPPING_LINE", RENAME),
    ("GOVERNANCE", "LINEAGE_EDGE"): ("TABLE", "LINEAGE_EDGE", RENAME),
    # PLATFORM_CONFIG
    ("PLATFORM_CONFIG", "INGESTION_PIPELINE_CATALOG"): ("TABLE", "INGESTION_PIPELINE_CATALOG", RENAME),
    ("PLATFORM_CONFIG", "INGESTION_CONTROL_CONFIG"): ("TABLE", "INGESTION_CONTROL_CONFIG", RENAME),
    ("PLATFORM_CONFIG", "INGESTION_CONTROL_HISTORY"): ("TABLE", "INGESTION_CONTROL_HISTORY", RENAME),
    ("PLATFORM_CONFIG", "SEQ_INGESTION_MASTER_ID"): ("SEQUENCE", "SEQ_INGESTION_MASTER_ID", RENAME),
    ("PLATFORM_CONFIG", "SEQ_INGESTION_CHILD_ID"): ("SEQUENCE", "SEQ_INGESTION_CHILD_ID", RENAME),
    # CONFLUENCE: bare SOURCE and PAGE would be ambiguous in one schema
    ("CONFLUENCE", "SOURCE"): ("TABLE", "CONFLUENCE_SOURCE", RENAME),
    ("CONFLUENCE", "PAGE"): ("TABLE", "CONFLUENCE_PAGE", RENAME),
    ("CONFLUENCE", "DOCUMENT_FILES"): ("STAGE", "DOCUMENT_FILES", RENAME),
}

# Name prefixes of objects the application creates at runtime in a legacy schema (tasks, git repositories, dbt
# projects). They keep their names in PLATFORM_SCHEMA.
RUNTIME_PREFIXES = {("QUALITY", "DQ_"): "TASK", ("PROFILE", "PROF_"): "TASK", ("CODE", "REFRESH_"): "TASK"}

# Kinds whose existing objects stay where they are (no RENAME in Snowflake, or versions that must not be reset).
KEPT_KINDS = ("secret", "git_repository", "dbt_project")

_IDENT = r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)'
_REF = re.compile(rf"^(@?)(?:({_IDENT})\.)?({_IDENT})\.({_IDENT})(/.*)?$", re.S)


def obj(name: str) -> str:
    """`CORE.<name>` for static references; the session database is the platform database."""
    return f"{PLATFORM_SCHEMA}.{name}"


def fqn(database: str, name: str) -> str:
    """`<database>.CORE.<name>` for names composed at runtime (tasks, dbt projects, git repositories)."""
    return f"{database}.{PLATFORM_SCHEMA}.{name}"


def stage(database: Optional[str], name: str) -> str:
    """`@<database>.CORE.<stage>` (or `@CORE.<stage>` without a database)."""
    return "@" + (fqn(database, name) if database else obj(name))


def new_name(schema: str, name: str) -> Optional[str]:
    """The object's name in PLATFORM_SCHEMA, or None when (schema, name) is not a platform object that moved."""
    schema, name = schema.strip('"').upper(), name.strip('"').upper()
    if (schema, name) in MOVED:
        return MOVED[(schema, name)][1]
    if schema in LEGACY_SCHEMAS and any(schema == s and name.startswith(p) for s, p in RUNTIME_PREFIXES):
        return name
    return None


def resolve(reference: Optional[str], kind: str = "object") -> Optional[str]:
    """Translate a name stored before V061 (in a row or a config value) to where the object lives now.

    Accepts `SCHEMA.OBJ`, `DB.SCHEMA.OBJ`, `@SCHEMA.STAGE/path` and `@DB.SCHEMA.STAGE/path`. Anything that is not a
    moved platform object is returned unchanged, so this is safe on new names, client tables and paths. Secrets, git
    repositories and dbt projects keep their location, so those kinds are never rewritten."""
    if not reference or kind in KEPT_KINDS:
        return reference
    m = _REF.match(reference.strip())
    if not m:
        return reference
    at, db, schema, name, rest = m.groups()
    target = new_name(schema, name)
    if target is None:
        return reference
    return f"{at}{db + '.' if db else ''}{PLATFORM_SCHEMA}.{target}{rest or ''}"


def moved(method: Optional[str] = None, types: Optional[Tuple[str, ...]] = None):
    """[(old_schema, old_name, type, new_name, method)] in a stable order, optionally filtered."""
    rows = [(s, n, t, nn, m) for (s, n), (t, nn, m) in MOVED.items()
            if (method is None or m == method) and (types is None or t in types)]
    return sorted(rows, key=lambda r: (LEGACY_SCHEMAS.index(r[0]), r[1]))
