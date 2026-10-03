-- Hybrid (keyword + vector) search over current domain knowledge. Created once; the service refreshes
-- itself from the base tables within TARGET_LAG (no tasks involved).

CREATE CORTEX SEARCH SERVICE IF NOT EXISTS {{database}}.KNOWLEDGE.KNOWLEDGE_SEARCH
  ON SEARCH_TEXT
  ATTRIBUTES DOMAIN_NAME, KNOWLEDGE_TYPE, STATUS, VERSION
  WAREHOUSE = {{warehouse}}
  TARGET_LAG = '10 minutes'
  EMBEDDING_MODEL = 'snowflake-arctic-embed-l-v2.0'
  COMMENT = 'Domain knowledge: glossary, rules, patterns, approved mappings, templates'
AS (
  SELECT K.KNOWLEDGE_ID,
         K.TITLE,
         K.CONTENT,
         K.TITLE || '\n' || K.CONTENT AS SEARCH_TEXT,
         D.DOMAIN_NAME,
         K.KNOWLEDGE_TYPE,
         K.STATUS,
         K.VERSION::VARCHAR AS VERSION,
         K.SOURCE_REFERENCE
    FROM {{database}}.KNOWLEDGE.DOMAIN_KNOWLEDGE K
    JOIN {{database}}.KNOWLEDGE.DOMAIN_REGISTRY D ON D.DOMAIN_ID = K.DOMAIN_ID
   WHERE K.IS_CURRENT
);

GRANT USAGE ON CORTEX SEARCH SERVICE {{database}}.KNOWLEDGE.KNOWLEDGE_SEARCH TO DATABASE ROLE {{database}}.VIEWER;
