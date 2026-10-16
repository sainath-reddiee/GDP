-- Airflow failure-resolution lab: put every failure back, so the lab DAGs fail the same way again. Test copy only
-- ({{database}}); run by run_loop.py before each pass.

CREATE OR REPLACE TABLE {{database}}.LAB.ORDERS_RAW COPY GRANTS (ORDER_ID NUMBER, CUSTOMER_ID NUMBER, AMOUNT NUMBER(12,2), CURRENCY VARCHAR(3));
INSERT INTO {{database}}.LAB.ORDERS_RAW VALUES (1, 10, 120.00, 'USD'), (2, 11, 80.50, 'EUR'), (3, 10, 42.00, 'XXX');

DROP TABLE IF EXISTS {{database}}.LAB.ORDERS_STG;                       -- lab_invalid_identifier
CREATE OR REPLACE TABLE {{database}}.LAB.ORDERS_CLEAN COPY GRANTS AS     -- lab_invalid_column: no AMOUNT_USD
  SELECT ORDER_ID, CUSTOMER_ID, AMOUNT, CURRENCY FROM {{database}}.LAB.ORDERS_RAW;
CREATE OR REPLACE TABLE {{database}}.LAB.ORDERS_IN COPY GRANTS AS        -- lab_duplicate_keys: order 2 twice
  SELECT * FROM {{database}}.LAB.ORDERS_RAW UNION ALL SELECT * FROM {{database}}.LAB.ORDERS_RAW WHERE ORDER_ID = 2;
DROP STAGE IF EXISTS {{database}}.LAB.EXPORT_STAGE;                      -- lab_multi_step
DROP TABLE IF EXISTS {{database}}.LAB.ORDERS_EXPORT;
DROP TABLE IF EXISTS {{database}}.LAB.CUSTOMER_DIM;                      -- lab_fix_breaks
CREATE OR REPLACE TABLE {{database}}.LAB.FLAKY_STATE COPY GRANTS (TRIED_AT TIMESTAMP_LTZ);   -- lab_flaky
