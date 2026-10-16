-- Airflow failure resolution (services/resolve). Re-applied on every deploy.
-- The ops worker calls SP_CLAIM_AND_RESOLVE and SP_RESUME_APPROVED (no Snowflake tasks); the decisions run here.
-- SP_RUN_FIX_STEP, SP_RCA_CONTEXT and SP_WALK_GRAPH are the agents' tools (snowflake/agents/rca_agent.sql,
-- resolution_agent.sql). Fix procedures themselves are named CORE.FIX_<NAME> and reviewed like code.

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.SP_CLAIM_AND_RESOLVE(HOLDER VARCHAR, MAX_FAILURES NUMBER)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.resolve.procedures.claim_and_resolve_entry'
  COMMENT = 'Claim pending Airflow failures, read the guardrails, search the published fixes, then fix, ask for approval, or find the cause and ticket'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.SP_RESUME_APPROVED(HOLDER VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.resolve.procedures.resume_approved_entry'
  COMMENT = 'Run the fixes a person approved; close the failures whose fix was rejected'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.SP_PUBLISH_KNOWN_ISSUE(ISSUE_ID VARCHAR, REVIEWER VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.resolve.procedures.publish_entry'
  COMMENT = 'Publish a reviewed known fix (never by its author) and refresh the known issue search'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.SP_RUN_FIX_STEP(FAILURE_ID VARCHAR, RESOLUTION_ID VARCHAR, STEP_NO NUMBER)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.resolve.fixes.run_step_entry'
  COMMENT = 'Run the next step of a published multi-step fix; refuses repeats and steps out of order'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.SP_RCA_CONTEXT(FAILURE_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.resolve.rca.failure_context_entry'
  COMMENT = 'Read only: an Airflow failure, its incident and the redacted log excerpt'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.SP_WALK_GRAPH(FAILURE_ID VARCHAR, HOPS NUMBER)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.resolve.rca.walk_graph_entry'
  COMMENT = 'Read only: DAGs, code, tables and documents upstream of a failed Airflow task'
  EXECUTE AS CALLER;

GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.SP_CLAIM_AND_RESOLVE(VARCHAR, NUMBER) TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.SP_RESUME_APPROVED(VARCHAR) TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.SP_PUBLISH_KNOWN_ISSUE(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.SP_RUN_FIX_STEP(VARCHAR, VARCHAR, NUMBER) TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.SP_RCA_CONTEXT(VARCHAR) TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.SP_WALK_GRAPH(VARCHAR, NUMBER) TO DATABASE ROLE {{database}}.OPS_VIEWER;

-- The safe-action catalogue (services/resolve/actions.py): the only steps an agent-proposed fix may use. Each
-- checks its input and the agent fix scope again when it runs.
CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.FIX_ADD_COLUMN(FAILURE_ID VARCHAR, INPUT VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.resolve.actions.add_column_entry'
  COMMENT = 'Safe action: add a missing column (in the agent fix scope only), optionally filled from another column'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.FIX_CREATE_TABLE_FROM(FAILURE_ID VARCHAR, INPUT VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.resolve.actions.create_table_from_entry'
  COMMENT = 'Safe action: create a missing table as a copy of an existing one (fix scope only)'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.FIX_CREATE_STAGE(FAILURE_ID VARCHAR, INPUT VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.resolve.actions.create_stage_entry'
  COMMENT = 'Safe action: create a missing internal stage (fix scope only)'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.FIX_DEDUP_ON_KEY(FAILURE_ID VARCHAR, INPUT VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.resolve.actions.dedup_on_key_entry'
  COMMENT = 'Safe action: keep one row per key (changes data; fix scope only)'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.FIX_RERUN_ONLY(FAILURE_ID VARCHAR, INPUT VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.resolve.actions.rerun_only_entry'
  COMMENT = 'Safe action: change nothing; the task is cleared to run again'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.SP_DESCRIBE_OBJECT(NAME VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.resolve.actions.describe_entry'
  COMMENT = 'Read only: whether a table or stage in the agent fix scope exists, and its columns'
  EXECUTE AS CALLER;

GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.FIX_ADD_COLUMN(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.FIX_CREATE_TABLE_FROM(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.FIX_CREATE_STAGE(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.FIX_DEDUP_ON_KEY(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.FIX_RERUN_ONLY(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.SP_DESCRIBE_OBJECT(VARCHAR) TO DATABASE ROLE {{database}}.OPS_VIEWER;
