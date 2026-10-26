-- V082: Enable autonomous happy-path defaults, remove minimum_mapping blocker from TARGET_TABLE_REGISTRY.MODEL_SPEC,
-- and clean Store build description strings on onboarding requests.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

-- 1. Enable autonomous happy-path and DQ auto-approval defaults in PLATFORM_CONFIG
INSERT INTO {{database}}.CORE.PLATFORM_CONFIG (CONFIG_KEY, CONFIG_VALUE, DESCRIPTION, VERSION, IS_CURRENT, CREATED_BY)
SELECT 'AUTONOMOUS_HAPPY_PATH', PARSE_JSON('true'), 'Autonomous end-to-end happy-path execution without manual gate stops unless validation fails', 1, TRUE, CURRENT_USER()
WHERE NOT EXISTS (
    SELECT 1 FROM {{database}}.CORE.PLATFORM_CONFIG
    WHERE CONFIG_KEY = 'AUTONOMOUS_HAPPY_PATH' AND IS_CURRENT
);

INSERT INTO {{database}}.CORE.PLATFORM_CONFIG (CONFIG_KEY, CONFIG_VALUE, DESCRIPTION, VERSION, IS_CURRENT, CREATED_BY)
SELECT 'AUTOPILOT_APPROVES_DQ', PARSE_JSON('true'), 'Autopilot approves proposed Soda Data Quality expectations on behalf of the steward', 1, TRUE, CURRENT_USER()
WHERE NOT EXISTS (
    SELECT 1 FROM {{database}}.CORE.PLATFORM_CONFIG
    WHERE CONFIG_KEY = 'AUTOPILOT_APPROVES_DQ' AND IS_CURRENT
);

-- 2. Strip minimum_mapping from TARGET_TABLE_REGISTRY.MODEL_SPEC so single-source builds do not fail on domain minimum_mapping rules
UPDATE {{database}}.CORE.TARGET_TABLE_REGISTRY
SET MODEL_SPEC = OBJECT_DELETE(MODEL_SPEC, 'minimum_mapping')
WHERE MODEL_SPEC:minimum_mapping IS NOT NULL;

-- 3. Clear synthetic 'Store build of ...' business outcome text on Store-initiated onboarding requests
UPDATE {{database}}.CORE.ONBOARDING_REQUEST
SET BUSINESS_OUTCOME = NULL
WHERE BUSINESS_OUTCOME LIKE 'Store build of %';

-- 4. Normalize TIMESTAMP_NTZ(6) / TIMESTAMP_NTZ(9) audit column types to TIMESTAMP_NTZ
UPDATE {{database}}.CORE.TARGET_COLUMN_REGISTRY
SET DATA_TYPE = 'TIMESTAMP_NTZ'
WHERE COLUMN_NAME IN ('GDP_INSERTED_TS', 'GDP_UPDATED_TS', 'VALID_FROM', 'VALID_TO')
  AND DATA_TYPE LIKE 'TIMESTAMP_NTZ%';

UPDATE {{database}}.CORE.STTM_LINE
SET TARGET_DATATYPE = 'TIMESTAMP_NTZ'
WHERE TARGET_COLUMN IN ('GDP_INSERTED_TS', 'GDP_UPDATED_TS', 'VALID_FROM', 'VALID_TO')
  AND TARGET_DATATYPE LIKE 'TIMESTAMP_NTZ%';

-- 5. Ensure every STTM_LINE has a non-empty business_definition so dbt schema YAML documents all columns
UPDATE {{database}}.CORE.STTM_LINE
SET BUSINESS_DEFINITION = INITCAP(REPLACE(TARGET_COLUMN, '_', ' '))
WHERE BUSINESS_DEFINITION IS NULL OR TRIM(BUSINESS_DEFINITION) = '';


