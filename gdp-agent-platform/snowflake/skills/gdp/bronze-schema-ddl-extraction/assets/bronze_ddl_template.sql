-- ============================================================================
-- ${ENV_NAME}_{PREFIX}_BRONZE_DB.{SCHEMA} - Complete Object DDLs
-- ============================================================================

-- ----------------------------------------------------------------------------
-- TABLE: {TABLE_NAME}
-- ----------------------------------------------------------------------------
CREATE ICEBERG TABLE IF NOT EXISTS ${ENV_NAME}_{PREFIX}_BRONZE_DB.{SCHEMA}.{TABLE_NAME} (

    -- Source columns (preserve source order, types, and nullability exactly)
    {COLUMN_1}               {TYPE},
    {COLUMN_2}               {TYPE},

    -- Audit columns (preserve exactly as they exist in source — do NOT alter nullability)
    {PREFIX}_INSERTED_TS     TIMESTAMP_NTZ(6),
    {PREFIX}_IS_ACTIVE       BOOLEAN,
    {PREFIX}_ROW_HASH        BINARY,
    {PREFIX}_UPDATED_TS      TIMESTAMP_NTZ(6),
    ETL_CREATED_TS           TIMESTAMP_NTZ(6)
)
    EXTERNAL_VOLUME = '{PREFIX}_SF_${ENV_NAME}_BRONZE'
    CATALOG = 'SNOWFLAKE'
    BASE_LOCATION = '{prefix_lower}-sf-${ENV_NAME}-bronze/{SCHEMA}/{TABLE_NAME}/';
