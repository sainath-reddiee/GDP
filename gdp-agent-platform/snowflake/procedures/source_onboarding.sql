-- Source onboarding procedures. Re-applied on every deploy.

CREATE OR REPLACE PROCEDURE {{database}}.SOURCE.REGISTER_SOURCE(RUN_ID VARCHAR, PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.procedures.register_source'
  COMMENT = 'Register a Snowflake database or share as the run source and discover its objects'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.SOURCE.VALIDATE_SOURCE_ACCESS(RUN_ID VARCHAR, SELECTED_OBJECTS_JSON VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.procedures.validate_source_access'
  COMMENT = 'Record the object selection and check the platform can read every selected object'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.SOURCE.EXECUTE_LANDING(RUN_ID VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.procedures.execute_landing'
  COMMENT = 'Copy selected objects as-is into LANDING and reconcile row counts'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.SOURCE.FETCH_SCHEMA_CATALOG(CONNECTION_ID VARCHAR, DATABASE_NAME VARCHAR, SCHEMA_NAME VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.procedures.fetch_schema_catalog'
  COMMENT = 'Inspect a registered source connection: objects, row counts and columns; no run needed'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.SOURCE.SET_LANDING_TARGET(RUN_ID VARCHAR, PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.procedures.set_landing_target'
  COMMENT = 'Set the run landing database, schema and storage type (MANAGED or ICEBERG) before landing'
  EXECUTE AS CALLER;

-- Caller's rights: profiling reads the source in place, so the caller's role must be able to SELECT it.
CREATE OR REPLACE PROCEDURE {{database}}.SOURCE.PROFILE_SOURCE_TABLES(SOURCE_ID VARCHAR, PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.profiling.procedures.profile_source_tables'
  COMMENT = 'Profile selected source tables in place (no copy) into @METADATA.PROFILES_STAGE'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.SOURCE.REGISTER_CONNECTION(PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.procedures.register_connection'
  COMMENT = 'Register a reusable source connection without creating a run'
  EXECUTE AS CALLER;

GRANT USAGE ON PROCEDURE {{database}}.SOURCE.PROFILE_SOURCE_TABLES(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.SOURCE.REGISTER_CONNECTION(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.SOURCE.FETCH_SCHEMA_CATALOG(VARCHAR, VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.SOURCE.SET_LANDING_TARGET(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.SOURCE.REGISTER_SOURCE(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.SOURCE.VALIDATE_SOURCE_ACCESS(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.SOURCE.EXECUTE_LANDING(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
