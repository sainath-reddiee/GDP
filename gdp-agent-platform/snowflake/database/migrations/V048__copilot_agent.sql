-- V048 Copilot workspace (/dashboard/copilot): one conversational agent that drafts changes through the platform's
-- own API routes and keeps them for review. Applied once; never edit after it has been applied. Add a new V### instead.
--
-- CORE.COPILOT_SESSION    one conversation: its title, the Jira issue and domain it is about, its state.
-- CORE.COPILOT_EVENT      everything shown in the chat, in order: the user's messages and form answers, the agent's
--                         thoughts, tool calls (name, arguments, outcome), questions and replies.
-- CORE.COPILOT_PLAN_STEP  the eight steps of the plan rail and their state.
-- CORE.COPILOT_CHANGE     a drafted change: the API calls that would apply it (OPERATIONS, replayed on Approve and
--                         Publish under the user's own session, so every privilege and approval rule still applies),
--                         what it changes (PREVIEW: model, STTM, dbt and QA/DQ diffs) and, once published, the outcome.
--                         CHANGE_KEY identifies the asset, so a revision replaces the draft for that asset.
--
-- Rows belong to the person who created them: the row access policy CORE.COPILOT_OWN_ROWS limits every table to the
-- caller's own rows (platform admins see all), as JIRA.OWN_ROWS does for Jira tokens (V033).
-- Every statement is safe to run again (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS {{database}}.CORE.COPILOT_SESSION (
    SESSION_ID   VARCHAR(36)   NOT NULL,
    TITLE        VARCHAR(256),
    STATUS       VARCHAR(32)   DEFAULT 'OPEN',          -- OPEN | PUBLISHED | ARCHIVED
    JIRA_KEY     VARCHAR(64),
    DOMAIN_ID    VARCHAR(36),
    MODEL        VARCHAR(128),
    CREATED_BY   VARCHAR(256)  DEFAULT CURRENT_USER(),
    CREATED_AT   TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT   TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_COPILOT_SESSION PRIMARY KEY (SESSION_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.CORE.COPILOT_EVENT (
    EVENT_ID     VARCHAR(36)   NOT NULL,
    SESSION_ID   VARCHAR(36)   NOT NULL,
    SEQ          NUMBER(10,0)  NOT NULL,
    KIND         VARCHAR(16)   NOT NULL,                -- USER | ANSWER | THOUGHT | TOOL | CHANGE | ASK | REPLY | ERROR
    CONTENT      VARIANT,
    CREATED_BY   VARCHAR(256)  DEFAULT CURRENT_USER(),
    CREATED_AT   TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_COPILOT_EVENT PRIMARY KEY (EVENT_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.CORE.COPILOT_PLAN_STEP (
    SESSION_ID   VARCHAR(36)   NOT NULL,
    STEP_NO      NUMBER(2,0)   NOT NULL,
    STEP_KEY     VARCHAR(32)   NOT NULL,
    STATUS       VARCHAR(16)   DEFAULT 'PENDING',       -- PENDING | RUNNING | DONE | BLOCKED | SKIPPED
    NOTE         VARCHAR(2000),
    CREATED_BY   VARCHAR(256)  DEFAULT CURRENT_USER(),
    UPDATED_AT   TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_COPILOT_PLAN_STEP PRIMARY KEY (SESSION_ID, STEP_NO)
);

CREATE TABLE IF NOT EXISTS {{database}}.CORE.COPILOT_CHANGE (
    CHANGE_ID    VARCHAR(36)   NOT NULL,
    SESSION_ID   VARCHAR(36)   NOT NULL,
    SEQ          NUMBER(10,0)  NOT NULL,
    CHANGE_KEY   VARCHAR(512)  NOT NULL,                -- e.g. TABLE_MODEL:<id>, STTM:<catalog id>, JIRA:<key>
    KIND         VARCHAR(16)   NOT NULL,                -- INGESTION | MODEL | STTM | DBT | QA | DQ | GIT | JIRA
    TITLE        VARCHAR(512),
    OPERATIONS   VARIANT,                               -- [{method, path, body}] replayed in order on publish
    PREVIEW      VARIANT,                               -- {model?, sttm?, files?} for the diff workbench
    STATUS       VARCHAR(24)   DEFAULT 'PROPOSED',      -- PROPOSED | APPLIED | PENDING_APPROVAL | FAILED | DISCARDED
    RESULT       VARIANT,
    REQUEST_ID   VARCHAR(36),                           -- the governance change request when one was raised
    CREATED_BY   VARCHAR(256)  DEFAULT CURRENT_USER(),
    CREATED_AT   TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT   TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_COPILOT_CHANGE PRIMARY KEY (CHANGE_ID)
);

GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.COPILOT_SESSION TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT ON TABLE {{database}}.CORE.COPILOT_EVENT TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {{database}}.CORE.COPILOT_PLAN_STEP TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.COPILOT_CHANGE TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.COPILOT_SESSION TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT SELECT, INSERT ON TABLE {{database}}.CORE.COPILOT_EVENT TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {{database}}.CORE.COPILOT_PLAN_STEP TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.COPILOT_CHANGE TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT SELECT ON TABLE {{database}}.CORE.COPILOT_SESSION TO DATABASE ROLE {{database}}.REVIEWER;
GRANT SELECT ON TABLE {{database}}.CORE.COPILOT_EVENT TO DATABASE ROLE {{database}}.REVIEWER;
GRANT SELECT ON TABLE {{database}}.CORE.COPILOT_PLAN_STEP TO DATABASE ROLE {{database}}.REVIEWER;
GRANT SELECT ON TABLE {{database}}.CORE.COPILOT_CHANGE TO DATABASE ROLE {{database}}.REVIEWER;

CREATE ROW ACCESS POLICY IF NOT EXISTS {{database}}.CORE.COPILOT_OWN_ROWS AS (ROW_USER VARCHAR) RETURNS BOOLEAN ->
    ROW_USER = CURRENT_USER() OR IS_DATABASE_ROLE_IN_SESSION('PLATFORM_ADMIN')
  COMMENT = 'Copilot sessions, events, plan steps and drafted changes are visible only to the person who created them (and platform admins)';

ALTER TABLE {{database}}.CORE.COPILOT_SESSION DROP ALL ROW ACCESS POLICIES;
ALTER TABLE {{database}}.CORE.COPILOT_SESSION ADD ROW ACCESS POLICY {{database}}.CORE.COPILOT_OWN_ROWS ON (CREATED_BY);
ALTER TABLE {{database}}.CORE.COPILOT_EVENT DROP ALL ROW ACCESS POLICIES;
ALTER TABLE {{database}}.CORE.COPILOT_EVENT ADD ROW ACCESS POLICY {{database}}.CORE.COPILOT_OWN_ROWS ON (CREATED_BY);
ALTER TABLE {{database}}.CORE.COPILOT_PLAN_STEP DROP ALL ROW ACCESS POLICIES;
ALTER TABLE {{database}}.CORE.COPILOT_PLAN_STEP ADD ROW ACCESS POLICY {{database}}.CORE.COPILOT_OWN_ROWS ON (CREATED_BY);
ALTER TABLE {{database}}.CORE.COPILOT_CHANGE DROP ALL ROW ACCESS POLICIES;
ALTER TABLE {{database}}.CORE.COPILOT_CHANGE ADD ROW ACCESS POLICY {{database}}.CORE.COPILOT_OWN_ROWS ON (CREATED_BY);
