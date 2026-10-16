-- Search over the known fixes (airflow-failure-resolution-architecture, slide 5: KNOWN_ISSUE_SEARCH). The resolver
-- searches MATCH_TEXT filtered on the failure's DAG, task and exception class and keeps STATUS = 'PUBLISHED' only, so a
-- draft is never run. Publishing a fix refreshes the service (services.resolve.publish); TARGET_LAG is the fallback.

CREATE CORTEX SEARCH SERVICE IF NOT EXISTS {{database}}.{{schema}}.KNOWN_ISSUE_SEARCH
  ON MATCH_TEXT
  ATTRIBUTES DAG_ID, TASK_ID, EXCEPTION_CLASS, STATUS, FIX_TYPE
  WAREHOUSE = {{warehouse}}
  TARGET_LAG = '5 minutes'
  EMBEDDING_MODEL = 'snowflake-arctic-embed-l-v2.0'
  COMMENT = 'Published Airflow known fixes, matched to new failures'
AS (
  SELECT ISSUE_ID,
         TITLE,
         MATCH_TEXT,
         COALESCE(DAG_ID, '') AS DAG_ID,
         COALESCE(TASK_ID, '') AS TASK_ID,
         COALESCE(EXCEPTION_CLASS, '') AS EXCEPTION_CLASS,
         STATUS,
         FIX_TYPE,
         ERROR_SIGNATURE,
         VERSION_NO::VARCHAR AS VERSION_NO
    FROM {{database}}.{{schema}}.KNOWN_ISSUE
   WHERE STATUS IN ('PUBLISHED', 'RETIRED')
);

GRANT USAGE ON CORTEX SEARCH SERVICE {{database}}.{{schema}}.KNOWN_ISSUE_SEARCH TO DATABASE ROLE {{database}}.OPS_VIEWER;
