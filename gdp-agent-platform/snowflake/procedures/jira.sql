-- Jira procedures. Re-applied on every deploy. Jira itself is called by the API with each engineer's own token;
-- these run inside Snowflake on data the API passes in.

CREATE OR REPLACE PROCEDURE {{database}}.JIRA.TRIAGE_ISSUE(RUN_ID VARCHAR, PAYLOAD VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.jira.triage.triage_entry'
  COMMENT = 'Triage a Jira issue against a run: diagnosis and guarded, compiled QA tests to reproduce it'
  EXECUTE AS CALLER;
