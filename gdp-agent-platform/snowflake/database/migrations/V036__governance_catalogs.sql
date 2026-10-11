-- V036 Tenant-wide catalogs for the lifecycle hubs (/dashboard/profiling, /dashboard/sttm, /dashboard/modeling).
-- Applied once; never edit after it has been applied. Add a new V### instead.
--
-- These tables are additive: the run pipeline keeps its own stores (METADATA.TABLE_PROFILES and the staged profile
-- documents, CONTRACT.STTM_REGISTRY / STTM_LINE, MODELING.MODEL_DESIGN) as the source of truth for runs. The services
-- also write here (services/catalog/*), and a write that fails here never fails the run step.
--
-- GOVERNANCE.DATA_PROFILES: column-level profile history across every run: one row per profiled column per profiling
--   pass (PROFILE_ID groups the columns of one pass). Searchable by domain, table, run and tags (CORE.TAG_ASSIGNMENT,
--   entity PROFILE, key DATABASE.SCHEMA.TABLE). GOVERNANCE.PERSISTENT_PROFILES is the same data as a view.
-- GOVERNANCE.PROFILE_BOOKMARK: a user's bookmarked tables.
-- GOVERNANCE.STTM_CATALOG: every version of every source-to-target mapping. CATALOG_ID names one mapping (source system
--   to target table); each STTM generation, transformation edit, join edit or catalog edit adds a version with the full
--   line set, so any two versions can be compared.
-- GOVERNANCE.MODEL_DESIGNS: models finalized in the Modeling hub: the specification, the rendered domain pack and dbt
--   files, and the Git branch and pull request they were published to.
-- Backfill: STTM versions already on record are copied once (INSERT ... WHERE NOT EXISTS, safe to run again). Column
-- profiles live in staged documents, so DATA_PROFILES fills from the next profiling pass of each table; the hub shows
-- METADATA.TABLE_PROFILES for tables profiled before this migration.

CREATE TABLE IF NOT EXISTS {{database}}.GOVERNANCE.DATA_PROFILES (
    PROFILE_ID         VARCHAR(36)    NOT NULL,   -- one profiling pass of one table
    SOURCE_NAME        VARCHAR(256)   NOT NULL,
    DATABASE_NAME      VARCHAR(256)   NOT NULL,
    SCHEMA_NAME        VARCHAR(256)   NOT NULL,
    TABLE_NAME         VARCHAR(256)   NOT NULL,
    COLUMN_NAME        VARCHAR(256)   NOT NULL,
    ORDINAL            NUMBER(6,0),
    RUN_ID             VARCHAR(36),
    DOMAIN_ID          VARCHAR(36),
    DATA_TYPE          VARCHAR(256),
    FAMILY             VARCHAR(32),
    SEMANTIC_TYPE      VARCHAR(64),
    PII_CLASSIFICATION VARCHAR(64),
    ROW_COUNT          NUMBER(38,0),
    NULL_COUNT         NUMBER(38,0),
    NULL_PCT           FLOAT,
    DISTINCT_COUNT     NUMBER(38,0),
    CARDINALITY        VARCHAR(16),                -- UNIQUE | HIGH | MEDIUM | LOW | CONSTANT
    CARDINALITY_RATIO  FLOAT,                      -- distinct / non-null
    MIN_VALUE          VARCHAR(1024),              -- NULL for PII columns
    MAX_VALUE          VARCHAR(1024),
    POTENTIAL_KEY      BOOLEAN        DEFAULT FALSE,
    TOP_VALUES         VARIANT,                    -- masked frequency distribution (top 10)
    HISTOGRAM          VARIANT,                    -- numeric buckets, non-PII only
    DESCRIPTION        VARCHAR(4000),
    IS_APPROXIMATE     BOOLEAN        DEFAULT FALSE,
    PROFILER_VERSION   VARCHAR(16),
    PROFILED_BY        VARCHAR(256)   DEFAULT CURRENT_USER(),
    PROFILED_AT        TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_DATA_PROFILES PRIMARY KEY (PROFILE_ID, COLUMN_NAME)
);

CREATE OR REPLACE VIEW {{database}}.GOVERNANCE.PERSISTENT_PROFILES
  COMMENT = 'Alias of GOVERNANCE.DATA_PROFILES'
AS SELECT * FROM {{database}}.GOVERNANCE.DATA_PROFILES;

CREATE TABLE IF NOT EXISTS {{database}}.GOVERNANCE.PROFILE_BOOKMARK (
    USER_NAME      VARCHAR(256)   NOT NULL,
    TABLE_FQN      VARCHAR(800)   NOT NULL,       -- DATABASE.SCHEMA.TABLE, upper case
    NOTE           VARCHAR(500),
    CREATED_AT     TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_PROFILE_BOOKMARK PRIMARY KEY (USER_NAME, TABLE_FQN)
);

CREATE TABLE IF NOT EXISTS {{database}}.GOVERNANCE.STTM_CATALOG (
    CATALOG_ID       VARCHAR(36)    NOT NULL,     -- one mapping: source system -> target table
    VERSION          NUMBER(6,0)    NOT NULL,
    STTM_ID          VARCHAR(36),                 -- CONTRACT.STTM_REGISTRY row this version came from (NULL for catalog edits)
    RUN_ID           VARCHAR(36),
    DOMAIN_ID        VARCHAR(36),
    DOMAIN_NAME      VARCHAR(128),
    SOURCE_SYSTEM    VARCHAR(256),
    SOURCE_ENTITIES  ARRAY,                       -- source tables feeding the target
    TARGET_TABLE_ID  VARCHAR(36),
    TARGET_ENTITY    VARCHAR(800)   NOT NULL,     -- DATABASE.SCHEMA.TABLE
    STATUS           VARCHAR(16)    NOT NULL,     -- DRAFT | REVIEW | APPROVED | SUPERSEDED
    ORIGIN           VARCHAR(16)    NOT NULL,     -- GENERATED | TRANSFORM_EDIT | JOIN_EDIT | CATALOG_EDIT | BACKFILL
    CHANGE_NOTE      VARCHAR(2000),
    TABLE_DESIGN     VARIANT,
    LINES            ARRAY          NOT NULL,     -- the full line set of this version
    LINE_COUNT       NUMBER(6,0),
    CREATED_BY       VARCHAR(256)   DEFAULT CURRENT_USER(),
    CREATED_AT       TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_STTM_CATALOG PRIMARY KEY (CATALOG_ID, VERSION)
);

CREATE TABLE IF NOT EXISTS {{database}}.GOVERNANCE.MODEL_DESIGNS (
    DESIGN_ID        VARCHAR(36)    NOT NULL,
    VERSION          NUMBER(6,0)    NOT NULL,
    DOMAIN_NAME      VARCHAR(128)   NOT NULL,
    MODE             VARCHAR(8)     NOT NULL,     -- MAP | FORK | NEW
    PARENT_DOMAIN    VARCHAR(128),                -- MAP and FORK: the existing domain
    STATUS           VARCHAR(16)    NOT NULL,     -- DRAFT | FINALIZED | PUBLISHED | PUBLISH_FAILED
    SPEC             VARIANT        NOT NULL,     -- entities, attributes, relationships, target platform
    ISSUES           VARIANT,
    DOMAIN_PACK      VARIANT,                     -- rendered domain_pack.json
    ARTIFACTS        VARIANT,                     -- {path: content} committed on publish
    REPO_ID          VARCHAR(36),                 -- CODE.REPO the branch was pushed to
    BRANCH           VARCHAR(200),
    PR_URL           VARCHAR(1000),
    PUBLISH_RESULT   VARIANT,
    SOURCE_TABLES    ARRAY,
    CREATED_BY       VARCHAR(256)   DEFAULT CURRENT_USER(),
    CREATED_AT       TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT       TIMESTAMP_LTZ,
    CONSTRAINT PK_MODEL_DESIGNS PRIMARY KEY (DESIGN_ID, VERSION)
);

-- Backfill STTM versions on record (catalog id: md5 of source system and target table id, the same rule as
-- services/catalog/sttm.py catalog_id).
INSERT INTO {{database}}.GOVERNANCE.STTM_CATALOG
    (CATALOG_ID, VERSION, STTM_ID, RUN_ID, DOMAIN_ID, DOMAIN_NAME, SOURCE_SYSTEM, SOURCE_ENTITIES, TARGET_TABLE_ID,
     TARGET_ENTITY, STATUS, ORIGIN, CHANGE_NOTE, TABLE_DESIGN, LINES, LINE_COUNT, CREATED_BY, CREATED_AT)
WITH L AS (
    SELECT STTM_ID,
           MAX(SOURCE_SYSTEM) AS SOURCE_SYSTEM,
           ARRAY_AGG(DISTINCT SOURCE_TABLE) AS SOURCE_ENTITIES,
           MAX(TARGET_DOMAIN) AS DOMAIN_NAME,
           MAX(TARGET_DATABASE || '.' || TARGET_SCHEMA || '.' || TARGET_TABLE) AS TARGET_ENTITY,
           ARRAY_AGG(OBJECT_CONSTRUCT_KEEP_NULL(
               'source_system', SOURCE_SYSTEM, 'source_table', SOURCE_TABLE, 'source_column', SOURCE_COLUMN,
               'source_datatype', SOURCE_DATATYPE, 'target_column', TARGET_COLUMN, 'target_datatype', TARGET_DATATYPE,
               'mapping_type', MAPPING_TYPE, 'transformation', TRANSFORMATION, 'business_rule', BUSINESS_RULE,
               'business_definition', BUSINESS_DEFINITION, 'join_logic', JOIN_LOGIC))
             WITHIN GROUP (ORDER BY TARGET_COLUMN) AS LINES,
           COUNT(*) AS LINE_COUNT
      FROM {{database}}.CONTRACT.STTM_LINE
     GROUP BY STTM_ID
), R AS (
    SELECT S.*, L.SOURCE_SYSTEM, L.SOURCE_ENTITIES, L.DOMAIN_NAME, L.TARGET_ENTITY, L.LINES, L.LINE_COUNT,
           MD5(COALESCE(L.SOURCE_SYSTEM, '') || '|' || S.TARGET_TABLE_ID) AS CATALOG_KEY
      FROM {{database}}.CONTRACT.STTM_REGISTRY S
      JOIN L ON L.STTM_ID = S.STTM_ID
)
SELECT SUBSTR(CATALOG_KEY, 1, 8) || '-' || SUBSTR(CATALOG_KEY, 9, 4) || '-' || SUBSTR(CATALOG_KEY, 13, 4) || '-'
           || SUBSTR(CATALOG_KEY, 17, 4) || '-' || SUBSTR(CATALOG_KEY, 21, 12),
       ROW_NUMBER() OVER (PARTITION BY CATALOG_KEY ORDER BY CREATED_AT, STTM_VERSION),
       STTM_ID, RUN_ID, DOMAIN_ID, DOMAIN_NAME, SOURCE_SYSTEM, SOURCE_ENTITIES, TARGET_TABLE_ID, TARGET_ENTITY,
       IFF(STATUS IN ('DRAFT', 'REVIEW', 'APPROVED'), STATUS, 'SUPERSEDED'), 'BACKFILL',
       'Copied from STTM v' || STTM_VERSION || ' of run ' || RUN_ID, TABLE_DESIGN, LINES, LINE_COUNT, CREATED_BY, CREATED_AT
  FROM R
 WHERE NOT EXISTS (SELECT 1 FROM {{database}}.GOVERNANCE.STTM_CATALOG C WHERE C.STTM_ID = R.STTM_ID);

GRANT SELECT ON TABLE {{database}}.GOVERNANCE.DATA_PROFILES TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON VIEW {{database}}.GOVERNANCE.PERSISTENT_PROFILES TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.GOVERNANCE.PROFILE_BOOKMARK TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.GOVERNANCE.STTM_CATALOG TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.GOVERNANCE.MODEL_DESIGNS TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT ON TABLE {{database}}.GOVERNANCE.DATA_PROFILES TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT ON VIEW {{database}}.GOVERNANCE.PERSISTENT_PROFILES TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, DELETE ON TABLE {{database}}.GOVERNANCE.PROFILE_BOOKMARK TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.GOVERNANCE.STTM_CATALOG TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.GOVERNANCE.MODEL_DESIGNS TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT ON TABLE {{database}}.GOVERNANCE.PROFILE_BOOKMARK TO DATABASE ROLE {{database}}.REVIEWER;
GRANT SELECT ON TABLE {{database}}.GOVERNANCE.STTM_CATALOG TO DATABASE ROLE {{database}}.REVIEWER;
GRANT SELECT ON TABLE {{database}}.GOVERNANCE.MODEL_DESIGNS TO DATABASE ROLE {{database}}.REVIEWER;
