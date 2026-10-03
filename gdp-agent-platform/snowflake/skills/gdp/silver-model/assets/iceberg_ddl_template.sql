-- =============================================================================
-- SILVER ICEBERG TABLE — DDL TEMPLATE
-- =============================================================================
-- Replace all {PLACEHOLDER} tokens before use.
-- Tokens:
--   {DATABASE}         e.g. DEV_GDP_SILVER_DB     (GDP is an example {PREFIX})
--   {SCHEMA}           e.g. OPPORTUNITY
--   {TABLE_NAME}       e.g. OPPORTUNITY_CORE       (UPPER_CASE)
--   {table_name_lower} e.g. opportunity_core       (lower_case, for sequence)
--   {schema_lower}     e.g. opportunity            (lower_case, for BASE_LOCATION)
--   {ENV}              e.g. DEV                    (for EXTERNAL_VOLUME)
--   {env_lower}        e.g. dev                    (for BASE_LOCATION)
--   {PREFIX}           e.g. GDP, DW, EDW            (project's audit-column namespace; may be blank)
-- =============================================================================
-- KEY RULES FOR SILVER:
--   1. ALWAYS include `{TABLE_NAME}_HKEY STRING NOT NULL` directly above audit columns
--   2. Column order by zone: Zone 1 (keys) → Zone 2 (business) → Zone 3 (HKEY) → Zone 4 (audit)
--   3. Zone 1 sub-order: own SKEY → SOURCE_UNIQUE_ID → REF_{PREFIX}_SOURCE_SYSTEM_SKEY → parent SKEYs
--   4. {PREFIX}_ audit columns always last, always NOT NULL
--   5. IS_* flags always STRING DEFAULT 'N'
--   6. Column names ending in `_F` → rename suffix to `_FLAG` (excludes ACTIVE_F and {PREFIX}_* columns)
--   7. Column names with spaces → replace with `_`
--   8. FK constraint naming: FK_{TABLE_NAME}_{FK_COLUMN_NAME} (full SKEY column name)
--   9. All _SKEY columns use LONG data type (not DECIMAL)
-- =============================================================================

-- Sequence must be created before the table. NEXTVAL is used in the dbt model SELECT.
CREATE OR REPLACE SEQUENCE {DATABASE}.{SCHEMA}.SEQ_{TABLE_NAME}_SKEY
    START WITH 1
    INCREMENT BY 1
    NOORDER;

CREATE OR REPLACE ICEBERG TABLE {DATABASE}.{SCHEMA}.{TABLE_NAME} (

    {TABLE_NAME}_SKEY               LONG                NOT NULL  COMMENT 'Surrogate key — auto-generated sequence',
    SOURCE_UNIQUE_ID                STRING              NOT NULL  COMMENT 'Unique identifier from the source system for traceability',
    REF_{PREFIX}_SOURCE_SYSTEM_SKEY LONG                NOT NULL  COMMENT 'FK to REF_{PREFIX}_SOURCE_SYSTEM — identifies the source system',
    -- [OPTIONAL — child tables only] Uncomment and add parent FK(s) here:
    -- {PARENT_TABLE}_SKEY          LONG                NOT NULL  COMMENT 'FK to {PARENT_TABLE}.{PARENT_TABLE}_SKEY',

    -- [REFERENCE FKs — REF_*_SKEY pattern, LONG nullable]
    -- REF_{LOOKUP_TABLE}_SKEY      LONG                COMMENT 'FK to REF_{LOOKUP_TABLE} — {description}',

    -- [DOMAIN ATTRIBUTES — STRING for text/IDs, FLOAT for geo/scores, LONG for counts]
    -- {COLUMN_NAME}                STRING              COMMENT '{description}',

    -- [FLAGS — STRING DEFAULT 'N']
    -- IS_{FLAG_NAME}               STRING              DEFAULT 'N'  COMMENT 'Y/N flag — {what it indicates}',

    -- [BUSINESS DATES — TIMESTAMP_NTZ(6) nullable]
    -- VALID_FROM                   TIMESTAMP_NTZ(6)    COMMENT 'Start of the validity period for this record',
    -- VALID_TO                     TIMESTAMP_NTZ(6)    COMMENT 'End of the validity period for this record',

    {TABLE_NAME}_HKEY               STRING              NOT NULL  COMMENT 'MD5/SHA2 hash of business attributes for change detection',

    {PREFIX}_IS_ACTIVE              BOOLEAN             NOT NULL  COMMENT 'Active flag for SCD2 — TRUE indicates current record',
    {PREFIX}_INSERTED_TS            TIMESTAMP_NTZ(6)    NOT NULL  COMMENT 'Timestamp when record was inserted',
    {PREFIX}_INSERTED_BY            STRING              NOT NULL  COMMENT 'User/process that inserted the record',
    {PREFIX}_UPDATED_TS             TIMESTAMP_NTZ(6)    NOT NULL  COMMENT 'Timestamp when record was last updated',
    {PREFIX}_UPDATED_BY             STRING              NOT NULL  COMMENT 'User/process that last updated the record',

    CONSTRAINT PK_{TABLE_NAME} PRIMARY KEY ({TABLE_NAME}_SKEY)

) COMMENT = '{Entity description} — {primary source} as primary source. Supports SCD Type 2 history.'
  EXTERNAL_VOLUME = '{PREFIX}_SF_{ENV}_SILVER'
  ICEBERG_VERSION = 2
  CATALOG = 'SNOWFLAKE'
  BASE_LOCATION = '{prefix_lower}-sf-{env_lower}-silver/{schema_lower}/{TABLE_NAME}/';
