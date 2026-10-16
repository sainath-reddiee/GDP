-- Airflow failure-resolution lab: seed tables, the reviewed fix procedures and the guardrails, all in the platform's
-- one schema ({{database}}.CORE; lab tables are named LAB_*). Run by run_loop.py as the platform's role. Safe to run
-- again. Every LAB_* object is granted to the platform's DATA_ENGINEER database role when it is created.
-- Known fixes are not seeded here: run_loop.py publishes them from the first real failures, the way a team learns them.

CREATE TABLE IF NOT EXISTS {{database}}.CORE.LAB_ORDERS_RAW (ORDER_ID NUMBER, CUSTOMER_ID NUMBER, AMOUNT NUMBER(12,2), CURRENCY VARCHAR(3));
CREATE TABLE IF NOT EXISTS {{database}}.CORE.LAB_FLAKY_STATE (TRIED_AT TIMESTAMP_LTZ);
GRANT SELECT ON TABLE {{database}}.CORE.LAB_ORDERS_RAW TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT ON TABLE {{database}}.CORE.LAB_FLAKY_STATE TO DATABASE ROLE {{database}}.DATA_ENGINEER;

-- ---------------------------------------------------------------- fixes: CORE.FIX_<NAME>(FAILURE_ID), reviewed like code
CREATE OR REPLACE PROCEDURE {{database}}.CORE.FIX_LAB_CREATE_ORDERS_STG(FAILURE_ID VARCHAR)
  RETURNS VARIANT LANGUAGE SQL COMMENT = 'Lab: create the missing staging table from the raw orders' AS
$$
BEGIN
  CREATE TABLE IF NOT EXISTS {{database}}.CORE.LAB_ORDERS_STG AS SELECT * FROM {{database}}.CORE.LAB_ORDERS_RAW;
  GRANT SELECT ON TABLE {{database}}.CORE.LAB_ORDERS_STG TO DATABASE ROLE {{database}}.DATA_ENGINEER;
  RETURN OBJECT_CONSTRUCT('created', 'CORE.LAB_ORDERS_STG', 'failure', :FAILURE_ID);
END;
$$;

CREATE OR REPLACE PROCEDURE {{database}}.CORE.FIX_LAB_ADD_AMOUNT_USD(FAILURE_ID VARCHAR)
  RETURNS VARIANT LANGUAGE SQL COMMENT = 'Lab: add the AMOUNT_USD column the revenue task expects, filled from AMOUNT' AS
$$
BEGIN
  ALTER TABLE {{database}}.CORE.LAB_ORDERS_CLEAN ADD COLUMN IF NOT EXISTS AMOUNT_USD NUMBER(12,2);
  UPDATE {{database}}.CORE.LAB_ORDERS_CLEAN SET AMOUNT_USD = AMOUNT WHERE AMOUNT_USD IS NULL;
  RETURN OBJECT_CONSTRUCT('added', 'CORE.LAB_ORDERS_CLEAN.AMOUNT_USD', 'failure', :FAILURE_ID);
END;
$$;

CREATE OR REPLACE PROCEDURE {{database}}.CORE.FIX_LAB_DEDUP_ORDERS_IN(FAILURE_ID VARCHAR)
  RETURNS VARIANT LANGUAGE SQL COMMENT = 'Lab: keep one row per ORDER_ID in CORE.LAB_ORDERS_IN' AS
$$
BEGIN
  CREATE OR REPLACE TABLE {{database}}.CORE.LAB_ORDERS_IN COPY GRANTS AS
    SELECT * FROM {{database}}.CORE.LAB_ORDERS_IN QUALIFY ROW_NUMBER() OVER (PARTITION BY ORDER_ID ORDER BY ORDER_ID) = 1;
  RETURN OBJECT_CONSTRUCT('deduplicated', 'CORE.LAB_ORDERS_IN', 'failure', :FAILURE_ID);
END;
$$;

CREATE OR REPLACE PROCEDURE {{database}}.CORE.FIX_LAB_CREATE_EXPORT_STAGE(FAILURE_ID VARCHAR)
  RETURNS VARIANT LANGUAGE SQL COMMENT = 'Lab skill step 1: create the export stage' AS
$$
BEGIN
  CREATE STAGE IF NOT EXISTS {{database}}.CORE.LAB_EXPORT_STAGE;
  GRANT READ ON STAGE {{database}}.CORE.LAB_EXPORT_STAGE TO DATABASE ROLE {{database}}.DATA_ENGINEER;
  RETURN OBJECT_CONSTRUCT('created', 'CORE.LAB_EXPORT_STAGE');
END;
$$;

CREATE OR REPLACE PROCEDURE {{database}}.CORE.FIX_LAB_CREATE_EXPORT_TABLE(FAILURE_ID VARCHAR)
  RETURNS VARIANT LANGUAGE SQL COMMENT = 'Lab skill step 2: create the export table from the raw orders' AS
$$
BEGIN
  CREATE TABLE IF NOT EXISTS {{database}}.CORE.LAB_ORDERS_EXPORT AS SELECT ORDER_ID, AMOUNT FROM {{database}}.CORE.LAB_ORDERS_RAW;
  GRANT SELECT ON TABLE {{database}}.CORE.LAB_ORDERS_EXPORT TO DATABASE ROLE {{database}}.DATA_ENGINEER;
  RETURN OBJECT_CONSTRUCT('created', 'CORE.LAB_ORDERS_EXPORT');
END;
$$;

CREATE OR REPLACE PROCEDURE {{database}}.CORE.FIX_LAB_BROKEN(FAILURE_ID VARCHAR)
  RETURNS VARIANT LANGUAGE SQL COMMENT = 'Lab: a fix that fails on purpose (the customer dimension source is gone)' AS
$$
DECLARE
  gone EXCEPTION (-20010, 'customer dimension source CUSTOMER_MASTER is not available');
BEGIN
  RAISE gone;
END;
$$;

-- ---------------------------------------------------------------- guardrails for the lab DAGs (lab_unscoped stays out)
MERGE INTO {{database}}.CORE.PIPELINE_SCOPE T USING (
  SELECT * FROM VALUES ('lab_invalid_identifier', 'AUTO'), ('lab_invalid_column', 'AUTO'),
                       ('lab_duplicate_keys', 'APPROVAL_ALWAYS'), ('lab_multi_step', 'AUTO'), ('lab_flaky', 'AUTO'),
                       ('lab_fix_breaks', 'AUTO'), ('lab_ticket_only', 'TICKET_ONLY') AS V(DAG_ID, FLOW_MODE)) S
  ON T.DAG_ID = S.DAG_ID AND T.TASK_ID IS NULL
WHEN MATCHED THEN UPDATE SET FLOW_MODE = S.FLOW_MODE, ENABLED = TRUE, UPDATED_BY = 'airflow_lab', UPDATED_AT = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (SCOPE_ID, DAG_ID, FLOW_MODE, ENABLED, OWNER, UPDATED_BY)
  VALUES (UUID_STRING(), S.DAG_ID, S.FLOW_MODE, TRUE, 'airflow_lab', 'airflow_lab');

MERGE INTO {{database}}.CORE.APPROVAL_ROUTE T USING (SELECT 'lab_duplicate_keys' AS DAG_ID, 'LAB_APPROVER' AS APPROVER) S
  ON T.DAG_ID = S.DAG_ID AND T.TASK_ID IS NULL
WHEN MATCHED THEN UPDATE SET APPROVER = S.APPROVER, UPDATED_BY = 'airflow_lab', UPDATED_AT = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (ROUTE_ID, DAG_ID, APPROVER, UPDATED_BY) VALUES (UUID_STRING(), S.DAG_ID, S.APPROVER, 'airflow_lab');
