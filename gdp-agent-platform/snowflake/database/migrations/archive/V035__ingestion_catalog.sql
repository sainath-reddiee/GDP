-- V035 Source Hub: the catalog of external ingestion pipelines and the stage that staged share landing goes through.
-- Applied once; never edit after it has been applied. Add a new V### instead.
--
-- PLATFORM_CONFIG.INGESTION_PIPELINE_CATALOG: one row per external (non-Snowflake) source registered in the Source
-- Hub. The platform only records the intake answers here; the ingestion tool (SnapLogic) reads the catalog and loads
-- the named landing table on its own. CONNECTION_METADATA holds the non-secret answers; anything the intake form marks
-- secret is sealed by the API (services/common/secretbox.py, purpose INGESTION_SECRET, row context SOURCE_ID) into
-- SECRET_SEALED and never stored in clear text. The questions themselves are the CORE.PLATFORM_CONFIG key
-- INGESTION_INTAKE_FORM (seeded by infrastructure/seed_knowledge.py, editable in Admin).
--
-- SOURCE.LANDING_STAGE: internal stage for share landing by staged Parquet: the shared table is unloaded as compressed
-- Parquet under runs/<run_id>/<table>/ (so the share is read once, by one statement) and loaded into the landing table
-- with COPY INTO ... MATCH_BY_COLUMN_NAME. Every statement is safe to run again (IF NOT EXISTS).

CREATE SCHEMA IF NOT EXISTS {{database}}.PLATFORM_CONFIG
  COMMENT = 'Platform catalogs maintained by the app and read by external tools (ingestion pipelines)';

CREATE TABLE IF NOT EXISTS {{database}}.PLATFORM_CONFIG.INGESTION_PIPELINE_CATALOG (
    SOURCE_ID                VARCHAR(64)   NOT NULL,
    SOURCE_NAME              VARCHAR(128)  NOT NULL,
    SOURCE_TYPE              VARCHAR(32)   NOT NULL,   -- ORACLE | SQLSERVER | POSTGRES | SALESFORCE | REST_API | S3 | SFTP | OTHER
    CONNECTION_TYPE          VARCHAR(16),               -- JDBC | S3 | SFTP | REST
    CONNECTION_METADATA      VARIANT,                   -- intake answers without secrets
    SECRET_SEALED            BINARY,                    -- secretbox-sealed JSON of the secret answers
    SCHEDULE                 VARIANT,                   -- {"frequency": "DAILY", "cron": "0 2 * * *", "timezone": "UTC"}
    TARGET_LANDING_TABLE     VARCHAR(256),
    SNAPLOGIC_PIPELINE_NAME  VARCHAR(128),
    DOMAIN_ID                VARCHAR(36),
    STATUS                   VARCHAR(32)   NOT NULL,   -- REGISTERED | ACTIVE | PAUSED | RETIRED
    NOTES                    VARCHAR(2000),
    CREATED_BY               VARCHAR(256)  DEFAULT CURRENT_USER(),
    CREATED_AT               TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_BY               VARCHAR(256),
    UPDATED_AT               TIMESTAMP_NTZ,
    CONSTRAINT PK_INGESTION_PIPELINE_CATALOG PRIMARY KEY (SOURCE_ID)
);

CREATE FILE FORMAT IF NOT EXISTS {{database}}.SOURCE.PARQUET_SNAPPY
  TYPE = 'PARQUET'
  COMPRESSION = 'SNAPPY'
  COMMENT = 'Staged share landing: Snappy-compressed Parquet';

CREATE STAGE IF NOT EXISTS {{database}}.SOURCE.LANDING_STAGE
  ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')
  FILE_FORMAT = {{database}}.SOURCE.PARQUET_SNAPPY
  COMMENT = 'Staged share landing: runs/<run_id>/<table>/ Parquet parts';

GRANT USAGE ON SCHEMA {{database}}.PLATFORM_CONFIG TO DATABASE ROLE {{database}}.VIEWER;
GRANT USAGE ON SCHEMA {{database}}.PLATFORM_CONFIG TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT ON TABLE {{database}}.PLATFORM_CONFIG.INGESTION_PIPELINE_CATALOG TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.PLATFORM_CONFIG.INGESTION_PIPELINE_CATALOG TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON FILE FORMAT {{database}}.SOURCE.PARQUET_SNAPPY TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT READ, WRITE ON STAGE {{database}}.SOURCE.LANDING_STAGE TO DATABASE ROLE {{database}}.DATA_ENGINEER;
