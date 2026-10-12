-- V044 Table Model Store: one persistent, versioned model per modeled table, created from a run's model design or
-- ad hoc from a landed source table, edited in the Modeling Store at any time.
-- Applied once; never edit after it has been applied. Add a new V### instead.
--
-- GOVERNANCE.TABLE_MODEL          one row per (source system, table): where it came from (the run that designed it,
--                                 or ADHOC), its layer and table type, the latest version, the diagram layout and the
--                                 domain model entity it feeds (LINKED_MODEL_ID, LINKED_ENTITY).
--                                 TABLE_MODEL_ID = md5('table:' || SOURCE_NAME || '|' || TABLE_NAME) as a UUID.
-- GOVERNANCE.TABLE_MODEL_VERSION  every save is a new, immutable version: a single-entity v2 specification
--                                 (services/modeling/spec_v2.py), its issues, the change summary against the previous
--                                 version, the DDL and dbt files rendered from it, and the pull request it was
--                                 published in (GOVERNANCE.GIT_PR kind TABLE_MODEL; merged marks it PUBLISHED).
-- GOVERNANCE.MODEL_CHANGE         gains TABLE_MODEL_ID and TABLE_MODEL_VERSION: a proposal to the domain model can come
--                                 from a table model as well as from a run.
--
-- A table model never changes the governed domain model directly: the domain's entity changes only through a
-- MODEL_CHANGE proposal merged and approved in the Model Workbench. Every statement is safe to run again.

CREATE TABLE IF NOT EXISTS {{database}}.GOVERNANCE.TABLE_MODEL (
    TABLE_MODEL_ID     VARCHAR(36)    NOT NULL,
    TABLE_NAME         VARCHAR(256)   NOT NULL,   -- the modeled table (the specification's single entity)
    SOURCE_ID          VARCHAR(36),               -- SOURCE.SOURCE_REGISTRY.SOURCE_SYSTEM_ID when known
    SOURCE_NAME        VARCHAR(256)   NOT NULL,   -- source system name, e.g. SALESFORCE_CRM
    SOURCE_TABLES      ARRAY,                     -- source tables the columns come from (DATABASE.SCHEMA.TABLE or TABLE)
    DOMAIN_ID          VARCHAR(36),
    ORIGIN             VARCHAR(16)    NOT NULL,   -- RUN | ADHOC | IMPORT
    ORIGIN_RUN_ID      VARCHAR(36),               -- the run whose design created it (NULL when ad hoc)
    LAST_RUN_ID        VARCHAR(36),               -- the last run that registered a version
    MODEL_LAYER        VARCHAR(16)    DEFAULT 'SILVER',    -- LANDING | BRONZE | SILVER | GOLD
    TABLE_TYPE         VARCHAR(16)    DEFAULT 'STANDARD',   -- ICEBERG | STANDARD | DYNAMIC_TABLE
    CURRENT_VERSION    NUMBER(6,0),
    ETAG               VARCHAR(64),
    DIAGRAM_LAYOUT     VARIANT,
    LINKED_MODEL_ID    VARCHAR(36),               -- GOVERNANCE.DOMAIN_MODEL it feeds
    LINKED_ENTITY      VARCHAR(256),
    LAST_CHANGE_ID     VARCHAR(36),               -- the latest MODEL_CHANGE proposed from it
    CREATED_BY         VARCHAR(256)   DEFAULT CURRENT_USER(),
    CREATED_AT         TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_BY         VARCHAR(256),
    UPDATED_AT         TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_TABLE_MODEL PRIMARY KEY (TABLE_MODEL_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.GOVERNANCE.TABLE_MODEL_VERSION (
    TABLE_MODEL_ID      VARCHAR(36)    NOT NULL,
    VERSION             NUMBER(6,0)    NOT NULL,
    STATUS              VARCHAR(16)    NOT NULL,   -- DRAFT | PUBLISHED
    SPEC                VARIANT        NOT NULL,   -- single-entity v2 specification
    CHECKSUM            VARCHAR(64),
    ISSUES              VARIANT,
    CHANGE_NOTE         VARCHAR(2000),
    CHANGE_SUMMARY      VARIANT,                   -- services/modeling/model_diff.diff against the previous version
    GENERATED_DDL       VARCHAR,
    GENERATED_DBT       VARIANT,                   -- {path: content}
    ORIGIN              VARCHAR(16),               -- RUN | USER | ADHOC | IMPORT
    ORIGIN_RUN_ID       VARCHAR(36),
    RUN_DESIGN_VERSION  NUMBER(6,0),
    REPO_ID             VARCHAR(36),
    BRANCH              VARCHAR(256),
    PR_URL              VARCHAR(1000),
    PR_NUMBER           NUMBER(10,0),
    PR_STATE            VARCHAR(16),
    MERGED_SHA          VARCHAR(64),
    PR_SYNCED_AT        TIMESTAMP_LTZ,
    CREATED_BY          VARCHAR(256)   DEFAULT CURRENT_USER(),
    CREATED_AT          TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_TABLE_MODEL_VERSION PRIMARY KEY (TABLE_MODEL_ID, VERSION),
    CONSTRAINT FK_TABLE_MODEL_VERSION FOREIGN KEY (TABLE_MODEL_ID) REFERENCES {{database}}.GOVERNANCE.TABLE_MODEL (TABLE_MODEL_ID)
);

ALTER TABLE {{database}}.GOVERNANCE.MODEL_CHANGE ADD COLUMN IF NOT EXISTS TABLE_MODEL_ID VARCHAR(36);
ALTER TABLE {{database}}.GOVERNANCE.MODEL_CHANGE ADD COLUMN IF NOT EXISTS TABLE_MODEL_VERSION NUMBER(6,0);

GRANT SELECT ON TABLE {{database}}.GOVERNANCE.TABLE_MODEL TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.GOVERNANCE.TABLE_MODEL_VERSION TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.GOVERNANCE.TABLE_MODEL TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.GOVERNANCE.TABLE_MODEL_VERSION TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, UPDATE ON TABLE {{database}}.GOVERNANCE.TABLE_MODEL TO DATABASE ROLE {{database}}.REVIEWER;
GRANT SELECT, UPDATE ON TABLE {{database}}.GOVERNANCE.TABLE_MODEL_VERSION TO DATABASE ROLE {{database}}.REVIEWER;
