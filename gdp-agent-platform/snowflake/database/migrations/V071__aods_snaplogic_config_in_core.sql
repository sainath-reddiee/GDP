-- V071: SnapLogic's config tables live in the platform's own schema, CORE (one database, one schema): onboarding writes
-- one row per source to CORE.AODS_SNAPLOGIC_CONFIG_MASTER and one row per table to CORE.AODS_SNAPLOGIC_CONFIG_DETAIL
-- (services/source/snaplogic_config), and SnapLogic reads them from here. Same columns, types and order as the
-- SnapLogic config DDL; the identity columns are autoincrement as there. V070 pointed the sync at another database;
-- nothing was ever written there, and the setting it seeded loses its table_prefix here (the tables are always these).
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

CREATE TABLE IF NOT EXISTS {{database}}.CORE.AODS_SNAPLOGIC_CONFIG_MASTER (
    MASTER_CONFIG_ID NUMBER(38,0) AUTOINCREMENT START 1 INCREMENT 1 NOORDER COMMENT 'Unique identifier for the source system (Primary Key)',
    SOURCE_SYSTEM VARCHAR COMMENT 'Name of the source (e.g., Lightbox, ZoomInfo)',
    SOURCE_TYPE VARCHAR COMMENT 'Source system  type (e.g., S3,Snowflake,Oracle,API)',
    ACTIVE_FLAG BOOLEAN COMMENT 'Boolean toggle to enable/disable the entire source system',
    CREATED_AT TIMESTAMP_NTZ(6) COMMENT 'Timestamp when the source system record was first added',
    SCHEDULE_CRON VARCHAR COMMENT 'Airflow 5-field cron expression for per-source ingest DAG',
    SOURCE_SYSTEM_DESC VARCHAR,
    DOMAIN VARCHAR,
    AIRFLOW_SOURCE_SYSTEM VARCHAR,
    SCHEDULE_FREQUENCY VARCHAR,
    SILVER_COMMAND_NAME VARCHAR
);

CREATE TABLE IF NOT EXISTS {{database}}.CORE.AODS_SNAPLOGIC_CONFIG_DETAIL (
    CHILD_CONFIG_ID NUMBER(38,0) AUTOINCREMENT START 1 INCREMENT 1 ORDER COMMENT 'Unique identifier for a specific table/job configuration',
    MASTER_CONFIG_ID NUMBER(38,0) COMMENT 'Link to the parent Source System in Config Master.',
    PARENT_PIPELINE_NAME VARCHAR COMMENT 'Functional name of the ingestion task.',
    CHILD_PIPELINE_NAME VARCHAR COMMENT 'Functional name of the ingestion task.',
    SNAPLOGIC_PROJECT_NAME VARCHAR COMMENT 'Name of the SnapLogic project associated with this job.',
    SOURCE_ACCOUNT VARCHAR COMMENT 'The SnapLogic Account name used for authentication.',
    SOURCE_DATABASE_PATH VARCHAR COMMENT 'Source connection string or folder path.',
    SOURCE_SCHEMA_FILE VARCHAR COMMENT 'Path to the metadata/schema definition (if applicable).',
    SOURCE_TABLE_SQL VARCHAR COMMENT 'The specific SQL query or Object name used to pull data.',
    SOURCE_FILE_TYPE VARCHAR COMMENT 'Format of the source data (e.g., JSON, CSV, Parquet).',
    SOURCE_DELTA_KEY VARCHAR COMMENT 'The column used for incremental pulls (e.g., UPDATED_AT).',
    SOURCE_PRIMARY_KEYS VARCHAR COMMENT 'Primary key(s) of the source table used for Merges.',
    SOURCE_WHERE_CLAUSE VARCHAR COMMENT 'Custom filter applied to the source extraction logic.',
    TARGET_ACCOUNT VARCHAR COMMENT 'Destination details for the landing/staging layer.',
    LANDING_DATABASE VARCHAR COMMENT 'Destination details for the landing/staging layer.',
    LANDING_SCHEMA VARCHAR COMMENT 'Destination details for the landing/staging layer.',
    LANDING_TABLE VARCHAR COMMENT 'Destination details for the landing/staging layer.',
    BRONZE_DATABASE VARCHAR COMMENT 'Destination details for the finalized Bronze layer.',
    BRONZE_SCHEMA VARCHAR COMMENT 'Destination details for the finalized Bronze layer.',
    BRONZE_TABLE VARCHAR COMMENT 'Destination details for the finalized Bronze layer.',
    TARGET_PREVIOUS_LOAD_TIME VARCHAR COMMENT 'Last load timestamp of the Target object',
    LOAD_TYPE VARCHAR COMMENT 'Strategy: Incremental_Merge, Full_Refresh, or Append.',
    LOAD_FREQUENCY VARCHAR COMMENT 'Schedule interval (e.g., Daily, Hourly).',
    JOB_TRIGGER_TIME VARCHAR COMMENT 'Time when snaplogic pipeline was last triggered',
    API_URL VARCHAR COMMENT 'URL when source-API',
    API_KEY VARCHAR COMMENT 'Key/authorization token for API URL',
    EXTERNAL_STAGE VARCHAR COMMENT 'the details of the external stage',
    LAST_RUN_TIMESTAMP TIMESTAMP_NTZ(6) COMMENT 'The high-water mark; tracks when the last successful delta pull occurred.',
    IS_ACTIVE BOOLEAN COMMENT 'Boolean flag indicating if the object is in scope for data load',
    COMPLETION_FLAG BOOLEAN COMMENT 'Boolean flag indicating if data load is completed successfully',
    LANDING_TO_BRONZE_FLAG BOOLEAN COMMENT 'Boolean flag indicating if data is loaded to the Bronze layer.',
    CREATED_BY VARCHAR COMMENT 'Object entry created by',
    CREATED_AT TIMESTAMP_NTZ(6) COMMENT 'Timestamp when the source system record was first added',
    UPDATED_AT TIMESTAMP_NTZ(6) COMMENT 'Object entry Updated by',
    COMMENTS VARCHAR COMMENT 'Timestamp when the source system record was first Updated',
    SOURCE_AUDIT_COLUMNS VARCHAR,
    BRONZE_LOAD_TYPE VARCHAR,
    SOURCE_DELETE_ACTION VARCHAR,
    SOURCE_DELETE_KEY VARCHAR,
    SOURCE_DELETE_KEY_VALUE VARCHAR,
    SOURCE_DELETE_QUERY VARCHAR,
    SOURCE_SHEET_NAME VARCHAR,
    CONFIG_TABLE VARCHAR,
    EXTERNAL_VOLUME VARCHAR,
    BASE_LOCATION VARCHAR
);

-- the setting V070's deploy seeded named the other database; the tables are always the two above now
UPDATE {{database}}.CORE.PLATFORM_CONFIG SET CONFIG_VALUE = OBJECT_DELETE(CONFIG_VALUE, 'table_prefix')
 WHERE CONFIG_KEY = 'SNAPLOGIC_SYNC' AND CONFIG_VALUE:table_prefix IS NOT NULL;

GRANT SELECT ON TABLE {{database}}.CORE.AODS_SNAPLOGIC_CONFIG_MASTER TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.CORE.AODS_SNAPLOGIC_CONFIG_DETAIL TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.AODS_SNAPLOGIC_CONFIG_MASTER TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.AODS_SNAPLOGIC_CONFIG_DETAIL TO DATABASE ROLE {{database}}.DATA_ENGINEER;
