-- Hybrid (keyword + vector) search over the platform's own resources, for the Home search (apps/api/app/search_api.py,
-- services/search/resources.py): onboarding requests (workflows), domains, sources, runs and target tables, so a
-- question in plain words ("client accounts", "why did the yardi load break") finds them by meaning. Domain knowledge
-- has its own service (KNOWLEDGE_SEARCH). Created once; the service refreshes itself within TARGET_LAG.
-- Filters: KIND, DOMAIN_ID, STATUS. Who may see a result (domain membership) is checked by the API.

CREATE CORTEX SEARCH SERVICE IF NOT EXISTS {{database}}.{{schema}}.RESOURCE_SEARCH
  ON SEARCH_TEXT
  ATTRIBUTES KIND, DOMAIN_ID, DOMAIN_NAME, STATUS
  WAREHOUSE = {{warehouse}}
  TARGET_LAG = '10 minutes'
  EMBEDDING_MODEL = 'snowflake-arctic-embed-l-v2.0'
  COMMENT = 'Workflows, domains, sources, runs and target tables, searched by meaning on Home'
AS (
  SELECT R.REQUEST_ID AS RESOURCE_ID, 'WORKFLOW' AS KIND, R.TITLE,
         COALESCE(R.WAITING_LABEL, R.STAGE) AS SUBTITLE, R.DOMAIN_ID, D.DOMAIN_NAME, R.STATUS,
         'Onboarding request (' || R.REQUEST_TYPE || ') ' || R.TITLE || '\n' || COALESCE(R.BUSINESS_OUTCOME, '') || '\n'
           || COALESCE(R.PROPOSED_DOMAIN, D.DOMAIN_NAME, '') || '\n' || COALESCE(R.WAITING_LABEL, '') AS SEARCH_TEXT
    FROM {{database}}.{{schema}}.ONBOARDING_REQUEST R
    LEFT JOIN {{database}}.{{schema}}.DOMAIN_REGISTRY D ON D.DOMAIN_ID = R.DOMAIN_ID
  UNION ALL
  SELECT D.DOMAIN_ID, 'DOMAIN', D.DOMAIN_NAME, D.DESCRIPTION, D.DOMAIN_ID, D.DOMAIN_NAME,
         IFF(COALESCE(D.ACTIVE_FLAG, TRUE), 'ACTIVE', 'RETIRED'),
         'Data domain ' || D.DOMAIN_NAME || '\n' || COALESCE(D.DESCRIPTION, '') || '\nOwner: ' || COALESCE(D.OWNER, '')
    FROM {{database}}.{{schema}}.DOMAIN_REGISTRY D
  UNION ALL
  SELECT S.SOURCE_SYSTEM_ID, 'SOURCE', S.SOURCE_SYSTEM_NAME, S.SOURCE_TYPE || COALESCE(' · ' || D.DOMAIN_NAME, ''),
         S.DOMAIN_ID, D.DOMAIN_NAME, IFF(COALESCE(S.ACTIVE_FLAG, TRUE), 'ACTIVE', 'RETIRED'),
         'Source system ' || S.SOURCE_SYSTEM_NAME || ' (' || S.SOURCE_TYPE || ')\n' || COALESCE(D.DOMAIN_NAME, '')
           || '\nOwner: ' || COALESCE(S.OWNER, '')
    FROM {{database}}.{{schema}}.SOURCE_REGISTRY S
    LEFT JOIN {{database}}.{{schema}}.DOMAIN_REGISTRY D ON D.DOMAIN_ID = S.DOMAIN_ID
  UNION ALL
  SELECT W.RUN_ID, 'RUN', W.RUN_NAME, W.CURRENT_STATE || COALESCE(' · ' || D.DOMAIN_NAME, ''), W.DOMAIN_ID, D.DOMAIN_NAME,
         W.CURRENT_STATE,
         'Pipeline run ' || W.RUN_NAME || '\nState: ' || W.CURRENT_STATE || '\n' || COALESCE(W.FAILURE_REASON, '') || '\n'
           || COALESCE(W.TARGET_MODEL, '') || '\n' || COALESCE(W.SOURCE_DATABASE || '.' || W.SOURCE_SCHEMA, '')
    FROM {{database}}.{{schema}}.WORKFLOW_RUN W
    LEFT JOIN {{database}}.{{schema}}.DOMAIN_REGISTRY D ON D.DOMAIN_ID = W.DOMAIN_ID
   WHERE W.DELETED_AT IS NULL AND NOT COALESCE(W.IS_ARCHIVED, FALSE)
  UNION ALL
  SELECT T.TARGET_TABLE_ID, 'TABLE', T.TARGET_TABLE, T.TARGET_SCHEMA || COALESCE(' · ' || D.DOMAIN_NAME, ''),
         T.DOMAIN_ID, D.DOMAIN_NAME, IFF(COALESCE(T.ACTIVE_FLAG, TRUE), 'ACTIVE', 'INACTIVE'),
         'Target table ' || T.TARGET_TABLE || ' (' || COALESCE(T.TABLE_TYPE, 'TABLE') || ')\n' || COALESCE(T.DESCRIPTION, '')
           || '\nGrain: ' || COALESCE(T.GRAIN, '') || '\n' || COALESCE(D.DOMAIN_NAME, '')
    FROM {{database}}.{{schema}}.TARGET_TABLE_REGISTRY T
    LEFT JOIN {{database}}.{{schema}}.DOMAIN_REGISTRY D ON D.DOMAIN_ID = T.DOMAIN_ID
);

GRANT USAGE ON CORTEX SEARCH SERVICE {{database}}.{{schema}}.RESOURCE_SEARCH TO DATABASE ROLE {{database}}.VIEWER;
