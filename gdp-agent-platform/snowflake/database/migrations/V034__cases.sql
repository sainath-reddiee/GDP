-- Cases (PR Q1): one record per reported problem, data or code, from any source (Jira, an Airflow incident, a failing
-- QA test or data quality check, or "Report a problem" in the app), tracked from report to verified fix.
-- CASE is a reserved word in Snowflake, so the case table is CASES.CASE_RECORD (no quoted identifier needed), and its human
-- number is CASE_NUMBER (shown as CASE-<n>), drawn from the sequence CASES.CASE_SEQ.
-- DESCRIPTION and PAGE_CONTEXT are redacted by the API (services/ops/redact.py) before they are stored.
-- CASE_EVENT is the timeline and also holds the idempotency claims that stop two callers opening the same case twice
-- (the OPS.INCIDENT_EVENT pattern): a claim row has CASE_ID NULL until it is bound to the case it opened.
-- Visibility: the row access policy CASES.DOMAIN_SCOPE on CASE_RECORD.DOMAIN_ID shows a case when its domain is
-- GENERAL, the user is a member of the domain (KNOWLEDGE.DOMAIN_MEMBER), the domain has no members at all, the case has
-- no domain, or PLATFORM_ADMIN is in the session. Like V033 the role name is not qualified: it resolves against the
-- database the policy lives in. The same rule is applied by the API (services/governance/domains.py), which answers 404
-- for a case the caller cannot see; the policy is defense in depth for direct SQL access.
-- The child tables (CASE_EVENT, CASE_LINK, CASE_ARTIFACT) carry no policy of their own: a policy that looks up a table
-- protected by another policy is not something this migration relies on, so their rows are protected by the API, which
-- reads them only for a case it has already checked. A row access policy does not filter INSERT, so the API checks
-- every write too.
-- Times are stored as TIMESTAMP_LTZ. Every statement is safe to run again (IF NOT EXISTS). Needs a deploy role that may
-- create and apply row access policies.

CREATE SCHEMA IF NOT EXISTS {{database}}.CASES COMMENT = 'Cases: reported problems tracked from report to verified fix';

CREATE SEQUENCE IF NOT EXISTS {{database}}.CASES.CASE_SEQ START = 1 INCREMENT = 1 COMMENT = 'Human case numbers (CASE-<n>)';

CREATE TABLE IF NOT EXISTS {{database}}.CASES.CASE_RECORD (
    CASE_ID          VARCHAR(36)    NOT NULL,
    CASE_NUMBER      NUMBER(12,0)   DEFAULT {{database}}.CASES.CASE_SEQ.NEXTVAL,   -- shown as CASE-<n>
    DOMAIN_ID        VARCHAR(36),                -- NULL: not scoped to a domain (visible to everyone)
    TITLE            VARCHAR(300)   NOT NULL,
    DESCRIPTION      VARCHAR(20000),             -- redacted
    KIND             VARCHAR(16)    NOT NULL,    -- DATA_BUG | CODE_BUG | DATA_QUALITY | PIPELINE | QUESTION
    SOURCE           VARCHAR(16)    NOT NULL,    -- APP_REPORT | JIRA | INCIDENT | QA_FAILURE | DQ_FAILURE
    SOURCE_REF       VARCHAR(500),               -- Jira key, incident id, QA or DQ result id
    PAGE_CONTEXT     VARIANT,                    -- where "Report a problem" was pressed: path, run, table, check, test, model (redacted)
    STATUS           VARCHAR(16)    NOT NULL,    -- NEW | TRIAGED | IN_PROGRESS | FIX_PROPOSED | FIX_APPLIED | VERIFIED | RESOLVED | CLOSED | DUPLICATE
    SEVERITY         VARCHAR(2)     NOT NULL,    -- P1..P4
    ASSIGNEE         VARCHAR(256),
    TEAM_ID          VARCHAR(64),                -- OPS.TEAM
    SLA_DUE_AT       TIMESTAMP_LTZ,
    TARGET_TABLE_ID  VARCHAR(36),                -- KNOWLEDGE.TARGET_TABLE_REGISTRY
    RUN_ID           VARCHAR(36),                -- CORE.WORKFLOW_RUN
    REPO_ID          VARCHAR(64),                -- CODE.REPO, for code bugs
    MODELS           ARRAY,                      -- dbt models in scope
    FINGERPRINT      VARCHAR(40),                -- sha1 of the normalized title and target, for duplicates
    AI               VARIANT,                    -- the triage (PR Q2)
    AI_SUMMARY       VARCHAR(2000),
    DUPLICATE_OF     VARCHAR(36),
    RESOLUTION       VARCHAR(4000),
    RESOLVED_BY      VARCHAR(256),               -- four-eyes on the case's resolution knowledge
    RESOLVED_AT      TIMESTAMP_LTZ,
    CLOSED_AT        TIMESTAMP_LTZ,
    REOPENED         NUMBER(6,0)    DEFAULT 0,
    OPENED_BY        VARCHAR(256)   DEFAULT CURRENT_USER(),
    OPENED_AT        TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT       TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_CASE_RECORD PRIMARY KEY (CASE_ID)
) CLUSTER BY (STATUS, DOMAIN_ID);

CREATE TABLE IF NOT EXISTS {{database}}.CASES.CASE_EVENT (
    EVENT_ID         VARCHAR(36)    NOT NULL,
    CASE_ID          VARCHAR(36),                -- NULL only on an idempotency claim not yet bound to a case
    KIND             VARCHAR(32)    NOT NULL,    -- claim | opened | reported_again | updated | assigned | status | comment | link_added | link_removed | merged | merged_into
    ACTOR            VARCHAR(256),               -- a user, or 'system'
    DETAIL           VARIANT,
    IDEMPOTENCY_KEY  VARCHAR(500),               -- claims on case creation from each source
    CREATED_AT       TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_CASE_EVENT PRIMARY KEY (EVENT_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.CASES.CASE_LINK (
    LINK_ID          VARCHAR(36)    NOT NULL,
    CASE_ID          VARCHAR(36)    NOT NULL,
    KIND             VARCHAR(16)    NOT NULL,    -- JIRA | INCIDENT | QA_RESULT | DQ_RESULT | RUN | PR | KNOWLEDGE | CASE
    REF              VARCHAR(1000)  NOT NULL,
    LABEL            VARCHAR(500),
    URL              VARCHAR(2000),
    STATE            VARCHAR(16)    DEFAULT 'OK',   -- OK | MISSING | HIDDEN (a Jira issue deleted, moved or out of sight)
    CREATED_BY       VARCHAR(256)   DEFAULT CURRENT_USER(),
    CREATED_AT       TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_CASE_LINK PRIMARY KEY (LINK_ID),
    CONSTRAINT UQ_CASE_LINK UNIQUE (CASE_ID, KIND, REF)   -- not enforced by Snowflake: the API writes links with MERGE
);

CREATE TABLE IF NOT EXISTS {{database}}.CASES.CASE_ARTIFACT (
    ARTIFACT_ID      VARCHAR(36)    NOT NULL,
    CASE_ID          VARCHAR(36)    NOT NULL,
    TYPE             VARCHAR(24)    NOT NULL,    -- REPRO_TEST | STTM_CHANGE | CORRECTION_SQL | DBT_PATCH | KNOWLEDGE_DRAFT
    TITLE            VARCHAR(500),
    CONTENT          VARIANT,
    DIFF             VARCHAR(100000),
    STATUS           VARCHAR(16)    NOT NULL DEFAULT 'PROPOSED',   -- PROPOSED | ACCEPTED | APPLIED | REJECTED
    PROPOSED_BY      VARCHAR(256)   DEFAULT CURRENT_USER(),        -- a user, or 'ai'
    DECIDED_BY       VARCHAR(256),
    DECIDED_AT       TIMESTAMP_LTZ,
    CREATED_AT       TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT       TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_CASE_ARTIFACT PRIMARY KEY (ARTIFACT_ID)
);

-- domain visibility (the same rule as services/governance/domains.py); uncorrelated IN lists keep the policy simple
CREATE ROW ACCESS POLICY IF NOT EXISTS {{database}}.CASES.DOMAIN_SCOPE AS (ROW_DOMAIN VARCHAR) RETURNS BOOLEAN ->
    ROW_DOMAIN IS NULL
    OR IS_DATABASE_ROLE_IN_SESSION('PLATFORM_ADMIN')
    OR ROW_DOMAIN IN (SELECT DOMAIN_ID FROM {{database}}.KNOWLEDGE.DOMAIN_REGISTRY WHERE UPPER(DOMAIN_NAME) = 'GENERAL')
    OR ROW_DOMAIN IN (SELECT DOMAIN_ID FROM {{database}}.KNOWLEDGE.DOMAIN_MEMBER WHERE UPPER(USER_NAME) = UPPER(CURRENT_USER()))
    OR ROW_DOMAIN NOT IN (SELECT DOMAIN_ID FROM {{database}}.KNOWLEDGE.DOMAIN_MEMBER)
  COMMENT = 'A case is visible in GENERAL, to members of its domain, in a domain without members, and to platform admins';

ALTER TABLE {{database}}.CASES.CASE_RECORD DROP ALL ROW ACCESS POLICIES;
ALTER TABLE {{database}}.CASES.CASE_RECORD ADD ROW ACCESS POLICY {{database}}.CASES.DOMAIN_SCOPE ON (DOMAIN_ID);

-- grants: QA and data engineers work cases (DATA_ENGINEER also inherits QA_ENGINEER), viewers read them (still
-- filtered by the policy), and the ops worker (OPS_SERVICE) opens cases from incidents. Links can be removed; case,
-- event and artifact rows are never deleted.
GRANT USAGE ON SCHEMA {{database}}.CASES TO DATABASE ROLE {{database}}.VIEWER;
GRANT USAGE ON SCHEMA {{database}}.CASES TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT USAGE ON SCHEMA {{database}}.CASES TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON SCHEMA {{database}}.CASES TO DATABASE ROLE {{database}}.OPS_SERVICE;

GRANT SELECT ON ALL TABLES IN SCHEMA {{database}}.CASES TO DATABASE ROLE {{database}}.VIEWER;

GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CASES.CASE_RECORD   TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CASES.CASE_EVENT    TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {{database}}.CASES.CASE_LINK TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CASES.CASE_ARTIFACT TO DATABASE ROLE {{database}}.QA_ENGINEER;

GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CASES.CASE_RECORD   TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CASES.CASE_EVENT    TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {{database}}.CASES.CASE_LINK TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CASES.CASE_ARTIFACT TO DATABASE ROLE {{database}}.DATA_ENGINEER;

GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CASES.CASE_RECORD   TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CASES.CASE_EVENT    TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {{database}}.CASES.CASE_LINK TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CASES.CASE_ARTIFACT TO DATABASE ROLE {{database}}.OPS_SERVICE;

-- the column default draws the next case number with the inserting role's rights
GRANT USAGE ON SEQUENCE {{database}}.CASES.CASE_SEQ TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT USAGE ON SEQUENCE {{database}}.CASES.CASE_SEQ TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON SEQUENCE {{database}}.CASES.CASE_SEQ TO DATABASE ROLE {{database}}.OPS_SERVICE;

-- the case pages read incidents, teams and quality results for links; QA engineers may open a case from an incident
-- (the incident timeline gets a 'case_opened' event)
GRANT USAGE ON SCHEMA {{database}}.OPS TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT SELECT ON TABLE {{database}}.OPS.INCIDENT TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT SELECT ON TABLE {{database}}.OPS.TEAM TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT SELECT, INSERT ON TABLE {{database}}.OPS.INCIDENT_EVENT TO DATABASE ROLE {{database}}.QA_ENGINEER;
