-- V046 SnapLogic ingestion control: one row per table an external source loads, read by the SnapLogic parent and
-- child pipelines. Applied once; never edit after it has been applied. Add a new V### instead.
--
-- PLATFORM_CONFIG.INGESTION_CONTROL_CONFIG   written by the Source Store's external onboarding wizard
--                                            (services/source/ingestion_control.py). One onboarding is one source:
--                                            every table gets its own CHILD_ID under one MASTER_ID. The platform
--                                            only writes the configuration; SnapLogic reads it and updates
--                                            TARGET_PREVIOUS_LOAD_TIME, LAST_RUN_TIMESTAMP and COMPLETION_FLAG.
--                                            API_KEY holds a secret reference (a name in the vault), never a token.
-- PLATFORM_CONFIG.INGESTION_CONTROL_HISTORY  every create, edit, activation and deactivation: who, when, the active
--                                            state and the row as it was after the change. Append only.
-- Every statement is safe to run again (IF NOT EXISTS).

CREATE SCHEMA IF NOT EXISTS {{database}}.PLATFORM_CONFIG;

CREATE SEQUENCE IF NOT EXISTS {{database}}.PLATFORM_CONFIG.SEQ_INGESTION_MASTER_ID START = 1 INCREMENT = 1;
CREATE SEQUENCE IF NOT EXISTS {{database}}.PLATFORM_CONFIG.SEQ_INGESTION_CHILD_ID START = 1 INCREMENT = 1;

CREATE TABLE IF NOT EXISTS {{database}}.PLATFORM_CONFIG.INGESTION_CONTROL_CONFIG (
    -- identification and orchestration
    MASTER_ID                    NUMBER         DEFAULT {{database}}.PLATFORM_CONFIG.SEQ_INGESTION_MASTER_ID.NEXTVAL,
    CHILD_ID                     NUMBER         DEFAULT {{database}}.PLATFORM_CONFIG.SEQ_INGESTION_CHILD_ID.NEXTVAL,
    PARENT_PIPELINE_NAME         VARCHAR(256)   NOT NULL,
    CHILD_PIPELINE_NAME          VARCHAR(256)   NOT NULL,
    SNAPLOGIC_PROJECT_NAME       VARCHAR(256)   NOT NULL,
    SOURCE_ID                    VARCHAR(64),               -- PLATFORM_CONFIG.INGESTION_PIPELINE_CATALOG row of the source
    DOMAIN_ID                    VARCHAR(36),

    -- source system
    SOURCE_ACCOUNT               VARCHAR(256),
    SOURCE_DATABASE_PATH         VARCHAR(512),
    SOURCE_SCHEMA_FILE           VARCHAR(256),
    SOURCE_TABLE_SQL             VARCHAR(16777216) NOT NULL,
    SOURCE_FILE_TYPE             VARCHAR(64),               -- TABLE | CSV | JSON | PARQUET | XLSX
    SOURCE_SHEET_NAME            VARCHAR(128),              -- XLSX only
    SOURCE_DELTA_KEY             VARCHAR(128),
    SOURCE_PRIMARY_KEYS          VARCHAR(512),              -- comma-separated
    SOURCE_WHERE_CLAUSE          VARCHAR(16777216),
    SOURCE_AUDIT_COLUMNS         VARCHAR(512),

    -- target and Snowflake layers
    TARGET_ACCOUNT               VARCHAR(256),
    LANDING_DATABASE             VARCHAR(128)   NOT NULL,
    LANDING_SCHEMA               VARCHAR(128)   NOT NULL,
    LANDING_TABLE                VARCHAR(128)   NOT NULL,
    BRONZE_SCHEMA                VARCHAR(128)   NOT NULL,
    BRONZE_TABLE                 VARCHAR(128)   NOT NULL,
    EXTERNAL_STAGE               VARCHAR(256),
    EXTERNAL_VOLUME              VARCHAR(256),              -- Iceberg tables
    BASE_LOCATION                VARCHAR(512),              -- Iceberg root or storage path
    CONFIG_TABLE                 VARCHAR(256),

    -- load strategy, frequency and lifecycle
    LOAD_TYPE                    VARCHAR(32)    NOT NULL,   -- APPEND | TRUNCATE | SCD2
    BRONZE_LOAD_TYPE             VARCHAR(32)    NOT NULL,   -- TRUNCATE | SCD2 | MERGE
    LOAD_FREQUENCY               VARCHAR(64)    NOT NULL,   -- HOURLY | DAILY | WEEKLY | MONTHLY | ON_DEMAND
    JOB_TRIGGER_TIME             VARCHAR(64),               -- cron expression
    TARGET_PREVIOUS_LOAD_TIME    TIMESTAMP_NTZ,             -- set by SnapLogic
    LAST_RUN_TIMESTAMP           TIMESTAMP_NTZ,             -- set by SnapLogic
    LANDING_TO_BRONZE_FLAG       BOOLEAN        DEFAULT TRUE,
    IS_ACTIVE                    BOOLEAN        DEFAULT TRUE,
    COMPLETION_FLAG              BOOLEAN        DEFAULT FALSE,  -- set by SnapLogic

    -- source deletes
    SOURCE_DELETE_ACTION         VARCHAR(32)    DEFAULT 'NOT_REQUIRED',  -- REQUIRED | NOT_REQUIRED
    SOURCE_DELETE_KEY            VARCHAR(128),
    SOURCE_DELETE_KEY_VALUE      VARCHAR(256),
    SOURCE_DELETE_QUERY          VARCHAR(16777216),

    -- SnapLogic API
    API_URL                      VARCHAR(1024),
    API_KEY                      VARCHAR(512)   COMMENT 'Secret reference identifier (a name in the vault or a Snowflake SECRET), never a token',

    -- audit
    CREATED_BY                   VARCHAR(128)   NOT NULL,
    CREATED_AT                   TIMESTAMP_NTZ  DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_BY                   VARCHAR(128),
    UPDATED_AT                   TIMESTAMP_NTZ  DEFAULT CURRENT_TIMESTAMP(),
    COMMENTS                     VARCHAR(16777216),

    CONSTRAINT PK_INGESTION_CONTROL PRIMARY KEY (MASTER_ID, CHILD_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.PLATFORM_CONFIG.INGESTION_CONTROL_HISTORY (
    MASTER_ID    NUMBER         NOT NULL,
    CHILD_ID     NUMBER         NOT NULL,
    ACTION       VARCHAR(16)    NOT NULL,   -- CREATED | UPDATED | ACTIVATED | DEACTIVATED
    IS_ACTIVE    BOOLEAN,
    CHANGED_BY   VARCHAR(256)   DEFAULT CURRENT_USER(),
    CHANGED_AT   TIMESTAMP_NTZ  DEFAULT CURRENT_TIMESTAMP(),
    SNAPSHOT     VARIANT
);

GRANT USAGE ON SCHEMA {{database}}.PLATFORM_CONFIG TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.PLATFORM_CONFIG.INGESTION_CONTROL_CONFIG TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.PLATFORM_CONFIG.INGESTION_CONTROL_HISTORY TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.PLATFORM_CONFIG.INGESTION_CONTROL_CONFIG TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT ON TABLE {{database}}.PLATFORM_CONFIG.INGESTION_CONTROL_HISTORY TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON SEQUENCE {{database}}.PLATFORM_CONFIG.SEQ_INGESTION_MASTER_ID TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON SEQUENCE {{database}}.PLATFORM_CONFIG.SEQ_INGESTION_CHILD_ID TO DATABASE ROLE {{database}}.DATA_ENGINEER;
