-- Airflow failure-resolution lab: the one-time admin part, run by a person in Snowsight (SECURITYADMIN, then SYSADMIN).
-- It creates the lab schema in the TEST COPY only, a role that can touch nothing else, and the service user the lab DAGs
-- run as. Never run it against DEV_AI_PLATFORM.

USE ROLE SYSADMIN;
CREATE SCHEMA IF NOT EXISTS DEV_AI_PLATFORM_APP_REHEARSAL.LAB COMMENT = 'Airflow resolution lab (test data only)';

USE ROLE SECURITYADMIN;
CREATE ROLE IF NOT EXISTS LAB_RUNNER COMMENT = 'Airflow resolution lab DAGs: the LAB schema of the test copy only';
GRANT ROLE LAB_RUNNER TO ROLE SYSADMIN;
GRANT USAGE ON WAREHOUSE DBT_WH TO ROLE LAB_RUNNER;
GRANT USAGE ON DATABASE DEV_AI_PLATFORM_APP_REHEARSAL TO ROLE LAB_RUNNER;
GRANT USAGE ON SCHEMA DEV_AI_PLATFORM_APP_REHEARSAL.LAB TO ROLE LAB_RUNNER;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA DEV_AI_PLATFORM_APP_REHEARSAL.LAB TO ROLE LAB_RUNNER;
GRANT SELECT, INSERT, UPDATE, DELETE ON FUTURE TABLES IN SCHEMA DEV_AI_PLATFORM_APP_REHEARSAL.LAB TO ROLE LAB_RUNNER;
GRANT READ, WRITE ON FUTURE STAGES IN SCHEMA DEV_AI_PLATFORM_APP_REHEARSAL.LAB TO ROLE LAB_RUNNER;

CREATE USER IF NOT EXISTS LAB_AIRFLOW TYPE = SERVICE DEFAULT_ROLE = LAB_RUNNER DEFAULT_WAREHOUSE = DBT_WH
  COMMENT = 'Airflow resolution lab DAGs (WSL); test copy only';
GRANT ROLE LAB_RUNNER TO USER LAB_AIRFLOW;

-- Credential (pick one; you keep it, it never goes into chat or the repository):
-- A) Key pair (recommended: no network policy needed). In WSL:
--      openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out ~/airflow/lab_key.p8
--      openssl rsa -in ~/airflow/lab_key.p8 -pubout | grep -v -- '-----' | tr -d '\n'
--    then: ALTER USER LAB_AIRFLOW SET RSA_PUBLIC_KEY = '<the single line printed above>';
-- B) Programmatic access token (Snowflake requires a network policy on the user for a service user's token):
--      ALTER USER LAB_AIRFLOW ADD PROGRAMMATIC ACCESS TOKEN LAB_PAT ROLE_RESTRICTION = 'LAB_RUNNER' DAYS_TO_EXPIRY = 7;
--
-- Then create the Airflow connection in WSL (see airflow_lab/README.md): connection id lab_snowflake, login LAB_AIRFLOW,
-- extra {"account": "ANBLICKSORG-ANBLICKSORG_AWS", "warehouse": "DBT_WH", "database": "DEV_AI_PLATFORM_APP_REHEARSAL",
--        "role": "LAB_RUNNER", "private_key_file": "/home/<you>/airflow/lab_key.p8"}   (A)  or password = the token (B).
--
-- When the lab is done: DROP USER LAB_AIRFLOW; DROP ROLE LAB_RUNNER; (the test copy itself is disposable).
