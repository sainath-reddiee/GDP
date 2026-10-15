-- Per-user Jira rows (QA and Jira hardening): JIRA.USER_TOKEN and JIRA.OAUTH_STATE hold one engineer's sealed tokens and
-- sign-in state, yet every engineer role has SELECT, UPDATE and DELETE on the whole JIRA schema (V027, V028). A row
-- access policy limits reads, updates, merges and deletes to the caller's own rows (USER_NAME = CURRENT_USER()).
-- The API writes USER_NAME from db.user, which is CURRENT_USER() of the caller's own session in both AIP_AUTH=dev
-- and AIP_AUTH=pat (apps/api/app/db.py), so the casing always matches. PLATFORM_ADMIN still sees every row (the
-- "N engineers connected" count in Admin); everyone else counts only their own connection.
-- A row access policy does not filter INSERT: a forged row for another user cannot be used (tokens are sealed by the
-- API host and bound to user and site), it only makes that user connect again.
-- QUALITY.QA_RUN and QA_RESULT keep SELECT, INSERT for DATA_ENGINEER and QA_ENGINEER (no UPDATE or DELETE was ever
-- granted): QA runs are stored by the API with the caller's role (services.qa.run.run_scope), not by a procedure.
-- Needs a deploy role that owns the JIRA schema and may apply row access policies. Safe to run again.

CREATE ROW ACCESS POLICY IF NOT EXISTS {{database}}.JIRA.OWN_ROWS AS (ROW_USER VARCHAR) RETURNS BOOLEAN ->
    ROW_USER = CURRENT_USER() OR IS_DATABASE_ROLE_IN_SESSION('PLATFORM_ADMIN')
  COMMENT = 'Jira token and sign-in rows are visible and writable only by their own user (and platform admins)';

ALTER TABLE {{database}}.JIRA.USER_TOKEN DROP ALL ROW ACCESS POLICIES;
ALTER TABLE {{database}}.JIRA.USER_TOKEN ADD ROW ACCESS POLICY {{database}}.JIRA.OWN_ROWS ON (USER_NAME);
ALTER TABLE {{database}}.JIRA.OAUTH_STATE DROP ALL ROW ACCESS POLICIES;
ALTER TABLE {{database}}.JIRA.OAUTH_STATE ADD ROW ACCESS POLICY {{database}}.JIRA.OWN_ROWS ON (USER_NAME);
