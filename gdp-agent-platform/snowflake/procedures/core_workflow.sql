-- Workflow procedures. Re-applied on every deploy; IMPORTS points at the
-- content-addressed services package uploaded by the deploy script.

CREATE OR REPLACE PROCEDURE {{database}}.CORE.CREATE_RUN(PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.workflow.procedures.create_run'
  COMMENT = 'Create an onboarding run in state CREATED'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.CORE.GET_WORKFLOW_STATE(RUN_ID VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.workflow.procedures.get_workflow_state'
  COMMENT = 'Current state, stage rail and allowed transitions for a run'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.CORE.TRANSITION_RUN(RUN_ID VARCHAR, TO_STATE VARCHAR, REASON VARCHAR, DETAILS_JSON VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.workflow.procedures.transition_run'
  COMMENT = 'SYSTEM transition; rejects HUMAN review gates'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.CORE.REVIEW_TRANSITION(RUN_ID VARCHAR, TO_STATE VARCHAR, DECISION VARCHAR, BUSINESS_JUSTIFICATION VARCHAR, COMMENTS VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.workflow.procedures.review_transition'
  COMMENT = 'HUMAN review gate transition; records the reviewer and justification'
  EXECUTE AS OWNER;

GRANT USAGE ON PROCEDURE {{database}}.CORE.GET_WORKFLOW_STATE(VARCHAR) TO DATABASE ROLE {{database}}.VIEWER;
GRANT USAGE ON PROCEDURE {{database}}.CORE.CREATE_RUN(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.CORE.REVIEW_TRANSITION(VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.CORE.TRANSITION_RUN(VARCHAR, VARCHAR, VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.PLATFORM_ADMIN;
