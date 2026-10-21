-- V070: SnapLogic config sync (services/source/snaplogic_config.py, behind the SNAPLOGIC_SYNC setting, off by default).
-- Onboarding writes one row per source to <prefix>SNAPLOGIC_CONFIG_MASTER and one row per table to
-- <prefix>SNAPLOGIC_CONFIG_DETAIL, the tables SnapLogic triggers from (DEV_GDP_UTIL_DB.CONFIG.AODS_* by default). Those
-- tables are owned outside the platform; this migration only adds what the platform keeps on its side:
--   the source-level answers SnapLogic's master row needs, on CORE.INGESTION_PIPELINE_CATALOG;
--   BRONZE_DATABASE on CORE.INGESTION_CONTROL_CONFIG;
--   the SnapLogic ids each CORE row was written as (MASTER_CONFIG_ID, CHILD_CONFIG_ID), so edits follow it;
--   CORE.SNAPLOGIC_SYNC_LOG: every sync attempt, so a share whose sync failed after landing can be retried.
-- Every new column is nullable with no default: existing rows and the existing insert statements are unchanged.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

ALTER TABLE {{database}}.CORE.INGESTION_PIPELINE_CATALOG ADD COLUMN IF NOT EXISTS SCHEDULE_CRON VARCHAR(64);
ALTER TABLE {{database}}.CORE.INGESTION_PIPELINE_CATALOG ADD COLUMN IF NOT EXISTS SCHEDULE_FREQUENCY VARCHAR(32);
ALTER TABLE {{database}}.CORE.INGESTION_PIPELINE_CATALOG ADD COLUMN IF NOT EXISTS SOURCE_SYSTEM_DESC VARCHAR(1000);
ALTER TABLE {{database}}.CORE.INGESTION_PIPELINE_CATALOG ADD COLUMN IF NOT EXISTS DOMAIN VARCHAR(128);
ALTER TABLE {{database}}.CORE.INGESTION_PIPELINE_CATALOG ADD COLUMN IF NOT EXISTS AIRFLOW_SOURCE_SYSTEM VARCHAR(128);
ALTER TABLE {{database}}.CORE.INGESTION_PIPELINE_CATALOG ADD COLUMN IF NOT EXISTS SILVER_COMMAND_NAME VARCHAR(256);
ALTER TABLE {{database}}.CORE.INGESTION_PIPELINE_CATALOG ADD COLUMN IF NOT EXISTS SNAPLOGIC_MASTER_CONFIG_ID NUMBER(38,0);

ALTER TABLE {{database}}.CORE.INGESTION_CONTROL_CONFIG ADD COLUMN IF NOT EXISTS BRONZE_DATABASE VARCHAR(128);
ALTER TABLE {{database}}.CORE.INGESTION_CONTROL_CONFIG ADD COLUMN IF NOT EXISTS SNAPLOGIC_CHILD_CONFIG_ID NUMBER(38,0);

CREATE TABLE IF NOT EXISTS {{database}}.CORE.SNAPLOGIC_SYNC_LOG (
    SYNC_ID              VARCHAR(36)    NOT NULL,
    KIND                 VARCHAR(16)    NOT NULL,       -- EXTERNAL (ingestion wizard), SHARE (share onboarding), EDIT
    STATUS               VARCHAR(16)    NOT NULL,       -- SYNCED | FAILED | SKIPPED (setting off, or no link yet)
    SOURCE_ID            VARCHAR(36),                   -- CORE.INGESTION_PIPELINE_CATALOG
    MASTER_ID            NUMBER(38,0),                  -- CORE.INGESTION_CONTROL_CONFIG
    RUN_ID               VARCHAR(36),                   -- the share onboarding's run
    MASTER_CONFIG_ID     NUMBER(38,0),                  -- the SnapLogic master row written or reused
    CHILD_CONFIG_IDS     ARRAY,
    TARGET_TABLES        VARIANT,                       -- {master, detail}: where it was written
    REQUEST              VARIANT,                       -- what to write again on a retry (never holds a secret)
    ERROR                VARCHAR(4000),
    CREATED_BY           VARCHAR(256)   DEFAULT CURRENT_USER(),
    CREATED_AT           TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_SNAPLOGIC_SYNC_LOG PRIMARY KEY (SYNC_ID)
);

GRANT SELECT ON TABLE {{database}}.CORE.SNAPLOGIC_SYNC_LOG TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.SNAPLOGIC_SYNC_LOG TO DATABASE ROLE {{database}}.DATA_ENGINEER;
