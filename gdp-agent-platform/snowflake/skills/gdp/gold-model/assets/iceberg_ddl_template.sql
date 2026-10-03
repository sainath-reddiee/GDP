-- =============================================================================
-- GOLD ICEBERG TABLE — DDL TEMPLATE
-- =============================================================================
-- Replace all {PLACEHOLDER} tokens before use.
-- Tokens:
--   {DATABASE}          e.g. DEV_GDP_GOLD_DB      (GDP is an example {PREFIX})
--   {SCHEMA}            e.g. OPPORTUNITY
--   {TABLE_NAME}        e.g. FACT_OPP_PROJECT      (UPPER_CASE)
--   {table_name_lower}  e.g. fact_opp_project      (lower_case, for sequence)
--   {schema_lower}      e.g. opportunity           (lower_case, for BASE_LOCATION)
--   {ENV}               e.g. DEV                   (for EXTERNAL_VOLUME)
--   {env_lower}         e.g. dev                   (for BASE_LOCATION)
--   {PREFIX}            e.g. GDP, DW, EDW           (project's audit-column namespace; may be blank)
--   {OWN_SKEY_COLUMN}   e.g. DIM_CLIENT_SKEY       (actual PK column name)
--   {LAYER_DESC}        e.g. dimension / fact
--   {ENTITY_DESC}       e.g. clients, opportunity projects
--   {SILVER_SOURCE}     e.g. Silver OPPORTUNITY_PROJECT
-- =============================================================================
-- KEY RULES FOR GOLD:
--   1. ALWAYS include `{TABLE_NAME}_HKEY STRING NOT NULL` directly above audit columns; rename source `MD5_HASH` to `{TABLE_NAME}_HKEY` if present
--   2. NO SOURCE_UNIQUE_ID or REF_{PREFIX}_SOURCE_SYSTEM_SKEY
--   3. Column order preserved exactly from spec / Excel
--   4. {PREFIX}_ audit columns always last, always NOT NULL
--   5. BIGINT / LONG → LONG; DATE → DATE; TIMESTAMP_NTZ(6) → TIMESTAMP_NTZ; CHAR(1) → STRING
--   6. Column names ending in `_F` → rename suffix to `_FLAG` (excludes ACTIVE_F and {PREFIX}_* columns)
--   7. FK constraint naming: FK_{TABLE_NAME}_{FK_COLUMN_NAME} (full SKEY column name)
-- =============================================================================

CREATE OR REPLACE SEQUENCE {DATABASE}.{SCHEMA}.SEQ_{TABLE_NAME}_SKEY
    START WITH 1
    INCREMENT BY 1
    NOORDER;

CREATE OR REPLACE ICEBERG TABLE {DATABASE}.{SCHEMA}.{TABLE_NAME} (

    -- -------------------------------------------------------------------------
    -- SURROGATE KEY — own PK, always first
    -- -------------------------------------------------------------------------
    {OWN_SKEY_COLUMN}               LONG                NOT NULL  COMMENT 'Surrogate key — auto-generated sequence',

    -- -------------------------------------------------------------------------
    -- FK SKEY COLUMNS  (LONG; nullable per spec)
    -- -------------------------------------------------------------------------
    -- {FK_SKEY_COLUMN}             LONG                         COMMENT 'FK → {TARGET_TABLE} — {description}',

    -- -------------------------------------------------------------------------
    -- BUSINESS COLUMNS (preserve spec order)
    -- -------------------------------------------------------------------------
    -- {COLUMN_NAME}                STRING                       COMMENT '{description}',

    -- -------------------------------------------------------------------------
    -- HASH KEY — always inject directly above audit; rename source MD5_HASH if present
    -- -------------------------------------------------------------------------
    {TABLE_NAME}_HKEY                STRING              NOT NULL  COMMENT 'Hash key — MD5 of business columns',

    -- -------------------------------------------------------------------------
    -- AUDIT COLUMNS — always last, always NOT NULL, always this exact order
    -- -------------------------------------------------------------------------
    {PREFIX}_IS_ACTIVE               BOOLEAN             NOT NULL  COMMENT 'Active flag for SCD2 — TRUE indicates current record',
    {PREFIX}_INSERTED_TS             TIMESTAMP_NTZ       NOT NULL  COMMENT 'Timestamp when record was inserted',
    {PREFIX}_INSERTED_BY             STRING              NOT NULL  COMMENT 'User/process that inserted the record',
    {PREFIX}_UPDATED_TS              TIMESTAMP_NTZ       NOT NULL  COMMENT 'Timestamp when record was last updated',
    {PREFIX}_UPDATED_BY              STRING              NOT NULL  COMMENT 'User/process that last updated the record',

    CONSTRAINT PK_{TABLE_NAME} PRIMARY KEY ({OWN_SKEY_COLUMN})

) COMMENT = 'Gold layer {LAYER_DESC} table for {ENTITY_DESC} — sourced from {SILVER_SOURCE}. Supports SCD Type 2 history.'
  EXTERNAL_VOLUME = '{PREFIX}_SF_{ENV}_GOLD'
  ICEBERG_VERSION = 2
  CATALOG = 'SNOWFLAKE'
  BASE_LOCATION = '{prefix_lower}-sf-{env_lower}-gold/{schema_lower}/{TABLE_NAME}/';
