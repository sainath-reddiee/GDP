-- Hybrid (keyword + vector) search over indexed client code. Created once; the service refreshes itself from
-- CODE.CODE_CHUNK within TARGET_LAG (no tasks involved). Filters: REPO_ID, KIND, PATH.

CREATE CORTEX SEARCH SERVICE IF NOT EXISTS {{database}}.CODE.CODE_SEARCH
  ON SEARCH_TEXT
  ATTRIBUTES REPO_ID, KIND, PATH
  WAREHOUSE = {{warehouse}}
  TARGET_LAG = '10 minutes'
  EMBEDDING_MODEL = 'snowflake-arctic-embed-l-v2.0'
  COMMENT = 'Client code: dbt models, macros, tests, schema files, SQL, Python and docs'
AS (
  SELECT C.CHUNK_ID,
         C.REPO_ID,
         R.NAME AS REPO_NAME,
         C.PATH,
         C.START_LINE,
         C.END_LINE,
         C.KIND,
         COALESCE(C.NAME, '') AS NAME,
         C.TEXT,
         C.COMMIT_SHA,
         C.PATH || '\n' || COALESCE(C.NAME, '') || '\n' || LEFT(C.TEXT, 8000) AS SEARCH_TEXT
    FROM {{database}}.CODE.CODE_CHUNK C
    JOIN {{database}}.CODE.REPO R ON R.REPO_ID = C.REPO_ID
   WHERE R.ENABLED
);

GRANT USAGE ON CORTEX SEARCH SERVICE {{database}}.CODE.CODE_SEARCH TO DATABASE ROLE {{database}}.VIEWER;
