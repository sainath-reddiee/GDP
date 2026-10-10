-- Jira procedures. Re-applied on every deploy. Jira itself is called by the API with each engineer's own token;
-- these run inside Snowflake on data the API passes in.

CREATE OR REPLACE PROCEDURE {{database}}.JIRA.TRIAGE_ISSUE(RUN_ID VARCHAR, PAYLOAD VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.jira.triage.triage_entry'
  COMMENT = 'Triage a Jira issue against a run: diagnosis and guarded, compiled QA tests to reproduce it'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.JIRA.TRIAGE_TABLE(TARGET_TABLE_ID VARCHAR, PAYLOAD VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.jira.triage.triage_table_entry'
  COMMENT = 'Triage a Jira issue against a domain target table: diagnosis and guarded, compiled QA tests to reproduce it'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.JIRA.RESOLVE_TARGETS(PAYLOAD VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.jira.triage.resolve_entry'
  COMMENT = 'Rank the target tables a Jira issue is about: existing links and names first, then an AI ranking of candidates'
  EXECUTE AS CALLER;

GRANT USAGE ON PROCEDURE {{database}}.JIRA.TRIAGE_ISSUE(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.JIRA.TRIAGE_ISSUE(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.JIRA.TRIAGE_ISSUE(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.JIRA.TRIAGE_TABLE(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.JIRA.TRIAGE_TABLE(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.JIRA.TRIAGE_TABLE(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.JIRA.RESOLVE_TARGETS(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.JIRA.RESOLVE_TARGETS(VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.JIRA.RESOLVE_TARGETS(VARCHAR) TO DATABASE ROLE {{database}}.QA_ENGINEER;
