-- ============================================================
-- Data Readiness Check — SQL Query Templates
-- ============================================================
-- These queries are templates. Replace placeholders:
--   {DATABASE}  = your Gold database, e.g. DEV_ACME_GOLD_COMPANY_DB
--   {SCHEMA}    = e.g. COMPANY
--   {TABLE}     = e.g. DIM_COMPANY
--   {PREFIX}    = your project's audit-column namespace, e.g. GDP, DW (may be blank)
-- ============================================================

-- ============================================================
-- QUERY 1: Get table list for a schema (with exclusions)
-- ============================================================
SHOW TABLES IN SCHEMA {DATABASE}.{SCHEMA};

-- Then filter results in-memory:
-- Exclude: name LIKE '%_HIST' OR name LIKE '%_TEMP' OR name LIKE '%_BKP'
--          OR name LIKE 'REF_%' OR name LIKE '%XREF%' OR name = 'flyway_schema_history'


-- ============================================================
-- QUERY 2: Row count + last refresh for a single table
-- ============================================================
SELECT 
  '{TABLE}' AS TABLE_NAME,
  COUNT(*) AS ROW_CNT,
  TO_VARCHAR(MAX({PREFIX}_UPDATED_TS), 'YYYY-MM-DD HH24:MI:SS') AS LAST_REFRESHED
FROM {DATABASE}.{SCHEMA}.{TABLE};


-- ============================================================
-- QUERY 3: Batch row count + refresh for multiple tables (UNION ALL)
-- Use this pattern for efficiency — combine up to 10 tables per query
-- ============================================================
SELECT '{TABLE_1}' AS TBL, COUNT(*) AS ROW_CNT, 
  TO_VARCHAR(MAX({PREFIX}_UPDATED_TS), 'YYYY-MM-DD HH24:MI:SS') AS LAST_REFRESHED 
FROM {DATABASE}.{SCHEMA}.{TABLE_1}
UNION ALL 
SELECT '{TABLE_2}', COUNT(*), 
  TO_VARCHAR(MAX({PREFIX}_UPDATED_TS), 'YYYY-MM-DD HH24:MI:SS') 
FROM {DATABASE}.{SCHEMA}.{TABLE_2}
-- ... continue for each table


-- ============================================================
-- QUERY 4: Get all columns for a table (to identify nulls and SKEYs)
-- ============================================================
SELECT COLUMN_NAME, DATA_TYPE 
FROM {DATABASE}.INFORMATION_SCHEMA.COLUMNS 
WHERE TABLE_SCHEMA = '{SCHEMA}' 
  AND TABLE_NAME = '{TABLE}' 
ORDER BY ORDINAL_POSITION;


-- ============================================================
-- QUERY 5: Check 100% null columns (batch all columns at once)
-- Generate one CASE per column. A column is 100% null if count = total rows.
-- ============================================================
SELECT 
  COUNT(*) AS TOTAL_ROWS,
  SUM(CASE WHEN {COLUMN_1} IS NULL THEN 1 ELSE 0 END) AS {COLUMN_1}_NULL,
  SUM(CASE WHEN {COLUMN_2} IS NULL THEN 1 ELSE 0 END) AS {COLUMN_2}_NULL
  -- ... one line per column
FROM {DATABASE}.{SCHEMA}.{TABLE};

-- A column is 100% null when {COLUMN}_NULL = TOTAL_ROWS


-- ============================================================
-- QUERY 6: Check SKEY columns for -1 values (batch all SKEYs)
-- Only report if NEG1_COUNT = TOTAL_ROWS (i.e., 100% = -1)
-- ============================================================
SELECT 
  COUNT(*) AS TOTAL_ROWS,
  SUM(CASE WHEN {SKEY_1} = -1 THEN 1 ELSE 0 END) AS {SKEY_1}_NEG1,
  SUM(CASE WHEN {SKEY_2} = -1 THEN 1 ELSE 0 END) AS {SKEY_2}_NEG1
  -- ... one line per SKEY column
FROM {DATABASE}.{SCHEMA}.{TABLE};

-- Only include in report if {SKEY}_NEG1 = TOTAL_ROWS


-- ============================================================
-- QUERY 7: Get SKEY columns for all tables in a schema (batch)
-- ============================================================
SELECT TABLE_NAME, COLUMN_NAME 
FROM {DATABASE}.INFORMATION_SCHEMA.COLUMNS 
WHERE TABLE_SCHEMA = '{SCHEMA}' 
  AND COLUMN_NAME LIKE '%_SKEY%'
  AND TABLE_NAME NOT LIKE '%_HIST'
  AND TABLE_NAME NOT LIKE '%_TEMP'
  AND TABLE_NAME NOT LIKE '%_BKP'
  AND TABLE_NAME NOT LIKE 'REF_%'
  AND TABLE_NAME NOT LIKE '%XREF%'
  AND TABLE_NAME != 'flyway_schema_history'
ORDER BY TABLE_NAME, ORDINAL_POSITION;


-- ============================================================
-- QUERY 8: Efficient null check — existence test (fast for large tables)
-- Returns empty result if column is NOT 100% null
-- ============================================================
SELECT 1 FROM {DATABASE}.{SCHEMA}.{TABLE} 
WHERE {COLUMN} IS NOT NULL 
LIMIT 1;

-- If this returns 0 rows → column is 100% null
-- Use this for large tables instead of full COUNT to save compute


-- ============================================================
-- EXAMPLE: Full SKEY check for a fact table (all SKEYs at once)
-- Replace with the actual FK SKEY columns discovered via QUERY 7 for your table.
-- ============================================================
-- SELECT 
--   COUNT(*) AS TOTAL_ROWS,
--   SUM(CASE WHEN DIM_EXAMPLE_A_SKEY = -1 THEN 1 ELSE 0 END) AS DIM_EXAMPLE_A_SKEY_NEG1,
--   SUM(CASE WHEN DIM_EXAMPLE_B_SKEY = -1 THEN 1 ELSE 0 END) AS DIM_EXAMPLE_B_SKEY_NEG1
--   -- ... one line per FK SKEY column discovered for this table
-- FROM {DATABASE}.{SCHEMA}.FACT_EXAMPLE;


-- ============================================================
-- QUERY 9: Schema-level batch column discovery for null checks
-- Returns ALL non-audit, non-HKEY columns for ALL tables in a schema.
-- Use this to guarantee no table/column is missed during null analysis.
-- ============================================================
SELECT TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION, DATA_TYPE
FROM {DATABASE}.INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = '{SCHEMA}'
  AND TABLE_NAME NOT LIKE '%_HIST'
  AND TABLE_NAME NOT LIKE '%_TEMP'
  AND TABLE_NAME NOT LIKE '%_BKP'
  AND TABLE_NAME NOT LIKE 'REF_%'
  AND TABLE_NAME NOT LIKE '%XREF%'
  AND TABLE_NAME != 'flyway_schema_history'
  AND COLUMN_NAME NOT IN ('{PREFIX}_IS_ACTIVE','{PREFIX}_INSERTED_TS','{PREFIX}_INSERTED_BY','{PREFIX}_UPDATED_TS','{PREFIX}_UPDATED_BY')
  AND COLUMN_NAME NOT LIKE '%_HKEY'
ORDER BY TABLE_NAME, ORDINAL_POSITION;

-- Use the results to generate null-check queries for EVERY populated table.
-- Completeness rule: if a table has rows, ALL its columns from this result 
-- MUST appear in a null-check query. No exceptions.


-- ============================================================
-- QUERY 10: Combined null + SKEY check (single-pass for small tables)
-- For tables with <100K rows, combine null AND SKEY checks in one query.
-- ============================================================
SELECT 
  COUNT(*) AS TOTAL_ROWS,
  -- Null checks (one per non-audit column)
  SUM(CASE WHEN {COL_1} IS NULL THEN 1 ELSE 0 END) AS {COL_1}_NULL,
  SUM(CASE WHEN {COL_2} IS NULL THEN 1 ELSE 0 END) AS {COL_2}_NULL,
  -- SKEY checks (one per foreign-key SKEY)
  SUM(CASE WHEN {FK_SKEY_1} = -1 THEN 1 ELSE 0 END) AS {FK_SKEY_1}_NEG1,
  SUM(CASE WHEN {FK_SKEY_2} = -1 THEN 1 ELSE 0 END) AS {FK_SKEY_2}_NEG1
FROM {DATABASE}.{SCHEMA}.{TABLE};

-- Interpretation:
--   Column is 100% null when: {COL}_NULL = TOTAL_ROWS
--   SKEY is 100% = -1 when: {FK_SKEY}_NEG1 = TOTAL_ROWS
