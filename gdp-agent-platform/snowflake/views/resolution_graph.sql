-- The knowledge graph the root-cause analysis walks (airflow-failure-resolution-architecture, slide 5: GRAPH_NODE and
-- GRAPH_EDGE). Views over what the platform already loads, so the graph is always current and nothing is copied:
--   Airflow DAGs and their DAG-to-DAG dependencies (DAG, DAG_DEPENDENCY), the file each DAG is defined in,
--   the tasks of each DAG with the tables and stages they read and write (DAG_TASK, the Airflow task catalogue), so a
--   failed task leads to the tables it reads and on to the tasks of other DAGs that write them,
--   code symbols and their references from indexed Airflow, dbt and SnapLogic code (CODE_EDGE, CODE_FILE),
--   table and column lineage (LINEAGE_EDGE), and Confluence pages (CONFLUENCE_PAGE, nodes only: read as documents).
-- An edge always points the way data flows: FROM_NODE_ID is upstream of TO_NODE_ID. Re-applied on every deploy.

CREATE OR REPLACE VIEW {{database}}.{{schema}}.GRAPH_NODE AS
SELECT DISTINCT NODE_ID, NODE_TYPE, NAME, DATABASE_NAME, SCHEMA_NAME, OBJECT_NAME, BODY, SOURCE_URI FROM (
    SELECT 'dag:' || D.ENV_ID || ':' || D.DAG_ID AS NODE_ID, 'AIRFLOW_DAG' AS NODE_TYPE, D.DAG_ID AS NAME,
           NULL AS DATABASE_NAME, NULL AS SCHEMA_NAME, D.DAG_ID AS OBJECT_NAME, D.DESCRIPTION AS BODY, D.FILELOC AS SOURCE_URI
      FROM {{database}}.{{schema}}.DAG D
    UNION ALL
    SELECT 'task:' || T.ENV_ID || ':' || T.DAG_ID || ':' || T.TASK_ID, 'AIRFLOW_TASK', T.DAG_ID || '.' || T.TASK_ID,
           NULL, NULL, T.TASK_ID, LEFT(T.SQL_TEXT, 4000), T.SOURCE_PATH
      FROM {{database}}.{{schema}}.DAG_TASK T
    UNION ALL
    SELECT 'obj:' || UPPER(O.VALUE::VARCHAR), IFF(STARTSWITH(O.VALUE::VARCHAR, '@'), 'STAGE', 'TABLE'), O.VALUE::VARCHAR,
           IFF(ARRAY_SIZE(SPLIT(O.VALUE::VARCHAR, '.')) = 3, LTRIM(SPLIT_PART(O.VALUE::VARCHAR, '.', 1), '@'), NULL),
           IFF(ARRAY_SIZE(SPLIT(O.VALUE::VARCHAR, '.')) >= 2, LTRIM(SPLIT_PART(O.VALUE::VARCHAR, '.', -2), '@'), NULL),
           LTRIM(SPLIT_PART(O.VALUE::VARCHAR, '.', -1), '@'), NULL, NULL
      FROM {{database}}.{{schema}}.DAG_TASK T,
           LATERAL FLATTEN(INPUT => ARRAY_CAT(COALESCE(T.READS, ARRAY_CONSTRUCT()), COALESCE(T.WRITES, ARRAY_CONSTRUCT()))) O
    UNION ALL
    SELECT 'file:' || F.REPO_ID || ':' || F.PATH, 'CODE_FILE', F.PATH, NULL, NULL, F.PATH, NULL, F.PATH
      FROM {{database}}.{{schema}}.CODE_FILE F
    UNION ALL
    SELECT 'code:' || E.REPO_ID || ':' || E.FROM_NAME, 'CODE_SYMBOL', E.FROM_NAME, NULL, NULL, E.FROM_NAME, NULL, E.PATH
      FROM {{database}}.{{schema}}.CODE_EDGE E
    UNION ALL
    SELECT 'code:' || E.REPO_ID || ':' || E.TO_NAME, 'CODE_SYMBOL', E.TO_NAME, NULL, NULL, E.TO_NAME, NULL, NULL
      FROM {{database}}.{{schema}}.CODE_EDGE E
    UNION ALL
    SELECT 'obj:' || UPPER(L.FROM_OBJECT), 'TABLE', L.FROM_OBJECT,
           SPLIT_PART(L.FROM_OBJECT, '.', -3), SPLIT_PART(L.FROM_OBJECT, '.', -2), SPLIT_PART(L.FROM_OBJECT, '.', -1), NULL, NULL
      FROM {{database}}.{{schema}}.LINEAGE_EDGE L WHERE L.IS_CURRENT
    UNION ALL
    SELECT 'obj:' || UPPER(L.TO_OBJECT), 'TABLE', L.TO_OBJECT,
           SPLIT_PART(L.TO_OBJECT, '.', -3), SPLIT_PART(L.TO_OBJECT, '.', -2), SPLIT_PART(L.TO_OBJECT, '.', -1), NULL, NULL
      FROM {{database}}.{{schema}}.LINEAGE_EDGE L WHERE L.IS_CURRENT
    UNION ALL
    SELECT 'doc:' || P.SOURCE_ID || ':' || P.PAGE_ID, 'DOC', P.TITLE, NULL, NULL, P.TITLE, LEFT(P.TEXT, 4000), P.URL
      FROM {{database}}.{{schema}}.CONFLUENCE_PAGE P
);

CREATE OR REPLACE VIEW {{database}}.{{schema}}.GRAPH_EDGE AS
SELECT DISTINCT SHA1(FROM_NODE_ID || '>' || TO_NODE_ID || '>' || EDGE_TYPE) AS EDGE_ID, FROM_NODE_ID, TO_NODE_ID, EDGE_TYPE FROM (
    -- an upstream DAG feeds the DAG it triggers or waits for
    SELECT 'dag:' || G.ENV_ID || ':' || G.UPSTREAM_DAG_ID AS FROM_NODE_ID, 'dag:' || G.ENV_ID || ':' || G.DOWNSTREAM_DAG_ID AS TO_NODE_ID,
           'DAG_DEPENDS' AS EDGE_TYPE
      FROM {{database}}.{{schema}}.DAG_DEPENDENCY G
    UNION ALL
    -- the file that defines a DAG: the path the catalogue resolved inside the repository, else the DAG's mapping
    SELECT 'file:' || D.REPO_ID || ':' || COALESCE(P.SOURCE_PATH, D.REPO_PATH), 'dag:' || D.ENV_ID || ':' || D.DAG_ID, 'DEFINES'
      FROM {{database}}.{{schema}}.DAG D
      LEFT JOIN (SELECT ENV_ID, DAG_ID, MAX(SOURCE_PATH) AS SOURCE_PATH FROM {{database}}.{{schema}}.DAG_TASK
                  GROUP BY ENV_ID, DAG_ID) P ON P.ENV_ID = D.ENV_ID AND P.DAG_ID = D.DAG_ID
     WHERE D.REPO_ID IS NOT NULL AND COALESCE(P.SOURCE_PATH, D.REPO_PATH) IS NOT NULL
    UNION ALL
    -- a DAG's tasks, and the order they run in
    SELECT 'dag:' || T.ENV_ID || ':' || T.DAG_ID, 'task:' || T.ENV_ID || ':' || T.DAG_ID || ':' || T.TASK_ID, 'HAS_TASK'
      FROM {{database}}.{{schema}}.DAG_TASK T
    UNION ALL
    SELECT 'task:' || T.ENV_ID || ':' || T.DAG_ID || ':' || U.VALUE::VARCHAR, 'task:' || T.ENV_ID || ':' || T.DAG_ID || ':' || T.TASK_ID,
           'TASK_FLOW'
      FROM {{database}}.{{schema}}.DAG_TASK T, LATERAL FLATTEN(INPUT => T.UPSTREAM) U
    UNION ALL
    -- a table or stage a task reads is upstream of it; a table it writes is downstream
    SELECT 'obj:' || UPPER(R.VALUE::VARCHAR), 'task:' || T.ENV_ID || ':' || T.DAG_ID || ':' || T.TASK_ID, 'READS'
      FROM {{database}}.{{schema}}.DAG_TASK T, LATERAL FLATTEN(INPUT => T.READS) R
    UNION ALL
    SELECT 'task:' || T.ENV_ID || ':' || T.DAG_ID || ':' || T.TASK_ID, 'obj:' || UPPER(W.VALUE::VARCHAR), 'WRITES'
      FROM {{database}}.{{schema}}.DAG_TASK T, LATERAL FLATTEN(INPUT => T.WRITES) W
    UNION ALL
    -- a symbol a model or function uses (ref, source, macro, call, import) is upstream of it; what it writes is downstream
    SELECT IFF(E.KIND = 'WRITES', 'code:' || E.REPO_ID || ':' || E.FROM_NAME, 'code:' || E.REPO_ID || ':' || E.TO_NAME),
           IFF(E.KIND = 'WRITES', 'code:' || E.REPO_ID || ':' || E.TO_NAME, 'code:' || E.REPO_ID || ':' || E.FROM_NAME), E.KIND
      FROM {{database}}.{{schema}}.CODE_EDGE E
    UNION ALL
    -- the file a symbol is declared in
    SELECT 'file:' || E.REPO_ID || ':' || E.PATH, 'code:' || E.REPO_ID || ':' || E.FROM_NAME, 'DECLARES'
      FROM {{database}}.{{schema}}.CODE_EDGE E WHERE E.PATH IS NOT NULL
    UNION ALL
    -- table lineage
    SELECT 'obj:' || UPPER(L.FROM_OBJECT), 'obj:' || UPPER(L.TO_OBJECT), 'LINEAGE'
      FROM {{database}}.{{schema}}.LINEAGE_EDGE L WHERE L.IS_CURRENT AND UPPER(L.FROM_OBJECT) <> UPPER(L.TO_OBJECT)
);

GRANT SELECT ON VIEW {{database}}.{{schema}}.GRAPH_NODE TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT SELECT ON VIEW {{database}}.{{schema}}.GRAPH_EDGE TO DATABASE ROLE {{database}}.OPS_VIEWER;
