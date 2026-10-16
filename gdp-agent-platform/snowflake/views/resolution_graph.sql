-- The knowledge graph the root-cause analysis walks (airflow-failure-resolution-architecture, slide 5: GRAPH_NODE and
-- GRAPH_EDGE). Views over what the platform already loads, so the graph is always current and nothing is copied:
--   Airflow DAGs and their DAG-to-DAG dependencies (DAG, DAG_DEPENDENCY), the file each DAG is defined in,
--   code symbols and their references from indexed Airflow, dbt and SnapLogic code (CODE_EDGE, CODE_FILE),
--   table and column lineage (LINEAGE_EDGE), and Confluence pages (CONFLUENCE_PAGE, nodes only: read as documents).
-- An edge always points the way data flows: FROM_NODE_ID is upstream of TO_NODE_ID. Re-applied on every deploy.

CREATE OR REPLACE VIEW {{database}}.{{schema}}.GRAPH_NODE AS
SELECT DISTINCT NODE_ID, NODE_TYPE, NAME, DATABASE_NAME, SCHEMA_NAME, OBJECT_NAME, BODY, SOURCE_URI FROM (
    SELECT 'dag:' || D.ENV_ID || ':' || D.DAG_ID AS NODE_ID, 'AIRFLOW_DAG' AS NODE_TYPE, D.DAG_ID AS NAME,
           NULL AS DATABASE_NAME, NULL AS SCHEMA_NAME, D.DAG_ID AS OBJECT_NAME, D.DESCRIPTION AS BODY, D.FILELOC AS SOURCE_URI
      FROM {{database}}.{{schema}}.DAG D
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
    -- the file that defines a DAG
    SELECT 'file:' || D.REPO_ID || ':' || D.REPO_PATH, 'dag:' || D.ENV_ID || ':' || D.DAG_ID, 'DEFINES'
      FROM {{database}}.{{schema}}.DAG D WHERE D.REPO_ID IS NOT NULL AND D.REPO_PATH IS NOT NULL
    UNION ALL
    -- a symbol a model or function uses (ref, source, macro, call, import) is upstream of it
    SELECT 'code:' || E.REPO_ID || ':' || E.TO_NAME, 'code:' || E.REPO_ID || ':' || E.FROM_NAME, E.KIND
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
