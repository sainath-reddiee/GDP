-- Source onboarding procedures. Re-applied on every deploy.

CREATE OR REPLACE PROCEDURE {{database}}.SOURCE.REGISTER_SOURCE(RUN_ID VARCHAR, PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.procedures.register_source'
  COMMENT = 'Register a Snowflake database or share as the run source and discover its objects'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.SOURCE.VALIDATE_SOURCE_ACCESS(RUN_ID VARCHAR, SELECTED_OBJECTS_JSON VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.procedures.validate_source_access'
  COMMENT = 'Record the object selection and check the platform can read every selected object'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.SOURCE.EXECUTE_LANDING(RUN_ID VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.source.procedures.execute_landing'
  COMMENT = 'Copy selected objects as-is into LANDING and reconcile row counts'
  EXECUTE AS OWNER;

GRANT USAGE ON PROCEDURE {{database}}.SOURCE.REGISTER_SOURCE(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.SOURCE.VALIDATE_SOURCE_ACCESS(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.SOURCE.EXECUTE_LANDING(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
