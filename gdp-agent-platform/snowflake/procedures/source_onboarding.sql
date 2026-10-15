-- Source onboarding procedures. Re-applied on every deploy.

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.REGISTER_SOURCE(RUN_ID VARCHAR, PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.procedures.register_source'
  COMMENT = 'Register a Snowflake database or share as the run source and discover its objects'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.VALIDATE_SOURCE_ACCESS(RUN_ID VARCHAR, SELECTED_OBJECTS_JSON VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.procedures.validate_source_access'
  COMMENT = 'Record the object selection and check the platform can read every selected object'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.EXECUTE_LANDING(RUN_ID VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.procedures.execute_landing'
  COMMENT = 'Copy selected objects as-is into LANDING and reconcile row counts'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.FETCH_SCHEMA_CATALOG(CONNECTION_ID VARCHAR, DATABASE_NAME VARCHAR, SCHEMA_NAME VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.procedures.fetch_schema_catalog'
  COMMENT = 'Inspect a registered source connection: objects, row counts and columns; no run needed'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.SET_LANDING_TARGET(RUN_ID VARCHAR, PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.procedures.set_landing_target'
  COMMENT = 'Set the run landing database, schema and storage type (MANAGED or ICEBERG) before landing'
  EXECUTE AS CALLER;

-- Caller's rights: profiling reads the source in place, so the caller's role must be able to SELECT it.
CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.PROFILE_SOURCE_TABLES(SOURCE_ID VARCHAR, PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.profiling.procedures.profile_source_tables'
  COMMENT = 'Profile selected source tables in place (no copy) into @CORE.PROFILES_STAGE'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.REGISTER_CONNECTION(PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.procedures.register_connection'
  COMMENT = 'Register a reusable source connection without creating a run'
  EXECUTE AS CALLER;

GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.PROFILE_SOURCE_TABLES(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.REGISTER_CONNECTION(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.FETCH_SCHEMA_CATALOG(VARCHAR, VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.SET_LANDING_TARGET(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.REGISTER_SOURCE(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.VALIDATE_SOURCE_ACCESS(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.EXECUTE_LANDING(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;

-- External sources: registered once, landed into <platform db>.EXT_<SOURCE> (files via stage + COPY),
-- then profiled and modeled like any Snowflake schema. Caller's rights: the caller's role creates the
-- landing schema and must be allowed to use the named storage integration.
CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.REGISTER_EXTERNAL_SOURCE(PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.external.register_external_source'
  COMMENT = 'Register an external source (files, database, SaaS, API); no credentials are stored'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.LIST_EXTERNAL_FILES(SOURCE_ID VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.external.list_external_files'
  COMMENT = 'Files available to land for a file-based external source'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.LAND_EXTERNAL_FILES(SOURCE_ID VARCHAR, PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.external.land_external_files'
  COMMENT = 'Land staged files of an external source into tables (INFER_SCHEMA + COPY INTO)'
  EXECUTE AS CALLER;

GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.REGISTER_EXTERNAL_SOURCE(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.LIST_EXTERNAL_FILES(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.LAND_EXTERNAL_FILES(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;

-- Source Hub share onboarding (V035): land the selected objects through CORE.LANDING_STAGE as Snappy Parquet and
-- COPY INTO ... MATCH_BY_COLUMN_NAME, instead of CREATE TABLE AS SELECT. Caller's rights, like EXECUTE_LANDING.
CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.EXECUTE_STAGED_LANDING(RUN_ID VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.procedures.execute_staged_landing'
  COMMENT = 'Unload selected share objects to LANDING_STAGE as Parquet and COPY them into LANDING'
  EXECUTE AS CALLER;

GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.EXECUTE_STAGED_LANDING(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
