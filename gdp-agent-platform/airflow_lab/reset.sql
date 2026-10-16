-- Airflow failure-resolution lab: put every failure back, so the lab DAGs fail the same way again. Touches only the
-- CORE.LAB_* lab objects of {{database}}; run by run_loop.py before each pass.

CREATE OR REPLACE TABLE {{database}}.CORE.LAB_ORDERS_RAW COPY GRANTS (ORDER_ID NUMBER, CUSTOMER_ID NUMBER, AMOUNT NUMBER(12,2), CURRENCY VARCHAR(3));
INSERT INTO {{database}}.CORE.LAB_ORDERS_RAW VALUES (1, 10, 120.00, 'USD'), (2, 11, 80.50, 'EUR'), (3, 10, 42.00, 'XXX');

DROP TABLE IF EXISTS {{database}}.CORE.LAB_ORDERS_STG;                        -- lab_invalid_identifier
CREATE OR REPLACE TABLE {{database}}.CORE.LAB_ORDERS_CLEAN                     -- lab_invalid_column: no AMOUNT_USD
  COMMENT = 'Cleaned orders, USD only (CURRENCY is always USD). The revenue report reads AMOUNT_USD NUMBER(12,2), the order amount in US dollars: for these rows it equals AMOUNT.'
  AS SELECT ORDER_ID, CUSTOMER_ID, AMOUNT, CURRENCY FROM {{database}}.CORE.LAB_ORDERS_RAW WHERE CURRENCY = 'USD';
GRANT SELECT ON TABLE {{database}}.CORE.LAB_ORDERS_CLEAN TO DATABASE ROLE {{database}}.DATA_ENGINEER;
CREATE OR REPLACE TABLE {{database}}.CORE.LAB_ORDERS_IN AS                     -- lab_duplicate_keys: order 2 twice
  SELECT * FROM {{database}}.CORE.LAB_ORDERS_RAW UNION ALL SELECT * FROM {{database}}.CORE.LAB_ORDERS_RAW WHERE ORDER_ID = 2;
GRANT SELECT ON TABLE {{database}}.CORE.LAB_ORDERS_IN TO DATABASE ROLE {{database}}.DATA_ENGINEER;
DROP STAGE IF EXISTS {{database}}.CORE.LAB_EXPORT_STAGE;                       -- lab_multi_step
DROP TABLE IF EXISTS {{database}}.CORE.LAB_ORDERS_EXPORT;
DROP TABLE IF EXISTS {{database}}.CORE.LAB_CUSTOMER_DIM;                       -- lab_fix_breaks
CREATE OR REPLACE TABLE {{database}}.CORE.LAB_FLAKY_STATE COPY GRANTS (TRIED_AT TIMESTAMP_LTZ);   -- lab_flaky
