-- V081: normalize GDP audit column datatypes across target, STTM, and Soda registries, remove placeholder pack owners
-- from CORE.DOMAIN_MEMBER so non-admin personas can view seeded domains, and clean test suffixes from workflow titles.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

-- 1. Remove placeholder pack owner strings from CORE.DOMAIN_MEMBER
DELETE FROM {{database}}.CORE.DOMAIN_MEMBER
WHERE UPPER(TRIM(USER_NAME)) IN ('GDP DATA OFFICE', 'SEED', 'GDP', 'PLATFORM', 'IMPORTED', 'DRAFTED FROM A CONTRACT');

-- 2. Normalize GDP_IS_ACTIVE -> BOOLEAN and GDP_INSERTED_TS / GDP_UPDATED_TS -> TIMESTAMP_NTZ in TARGET_COLUMN_REGISTRY
UPDATE {{database}}.CORE.TARGET_COLUMN_REGISTRY
SET DATA_TYPE = 'BOOLEAN'
WHERE COLUMN_NAME = 'GDP_IS_ACTIVE'
  AND DATA_TYPE IN ('TEXT', 'VARCHAR');

UPDATE {{database}}.CORE.TARGET_COLUMN_REGISTRY
SET DATA_TYPE = 'TIMESTAMP_NTZ'
WHERE COLUMN_NAME IN ('GDP_INSERTED_TS', 'GDP_UPDATED_TS')
  AND DATA_TYPE IN ('TEXT', 'VARCHAR');

-- 3. Normalize GDP audit column target datatypes in existing STTM lines
UPDATE {{database}}.CORE.STTM_LINE
SET TARGET_DATATYPE = 'BOOLEAN'
WHERE TARGET_COLUMN = 'GDP_IS_ACTIVE'
  AND TARGET_DATATYPE IN ('TEXT', 'VARCHAR');

UPDATE {{database}}.CORE.STTM_LINE
SET TARGET_DATATYPE = 'TIMESTAMP_NTZ'
WHERE TARGET_COLUMN IN ('GDP_INSERTED_TS', 'GDP_UPDATED_TS')
  AND TARGET_DATATYPE IN ('TEXT', 'VARCHAR');

-- 4. Normalize GDP audit column expected_type in existing Soda schema expectations
UPDATE {{database}}.CORE.SODA_EXPECTATION_REGISTRY
SET CHECK_DEFINITION = OBJECT_INSERT(CHECK_DEFINITION, 'expected_type', 'BOOLEAN', TRUE)
WHERE CHECK_TYPE = 'SCHEMA'
  AND TARGET_COLUMN = 'GDP_IS_ACTIVE'
  AND CHECK_DEFINITION:expected_type::STRING IN ('TEXT', 'VARCHAR');

UPDATE {{database}}.CORE.SODA_EXPECTATION_REGISTRY
SET CHECK_DEFINITION = OBJECT_INSERT(CHECK_DEFINITION, 'expected_type', 'TIMESTAMP_NTZ', TRUE)
WHERE CHECK_TYPE = 'SCHEMA'
  AND TARGET_COLUMN IN ('GDP_INSERTED_TS', 'GDP_UPDATED_TS')
  AND CHECK_DEFINITION:expected_type::STRING IN ('TEXT', 'VARCHAR');

-- 5. Strip (E2E test) suffix from seeded onboarding request titles
UPDATE {{database}}.CORE.ONBOARDING_REQUEST
SET TITLE = TRIM(REGEXP_REPLACE(TITLE, '\\s*\\(E2E test\\)\\s*$', '', 1, 0, 'i'))
WHERE TITLE ILIKE '%(E2E test)%';
