-- V062: Airflow failure resolution (docs: airflow-failure-resolution-architecture, slide 5 "Data model").
-- A failed Airflow task becomes a FAILURE_EVENT (the work queue). The worker calls CORE.SP_CLAIM_AND_RESOLVE, which reads
-- the guardrails (CONTROL_PREFERENCE, PIPELINE_SCOPE) first, searches the published fixes (KNOWN_ISSUE through
-- CORE.KNOWN_ISSUE_SEARCH) and then runs the fix, waits for a person's approval (APPROVAL_REQUEST, routed by
-- APPROVAL_ROUTE) or finds the cause and opens a ticket. Every fix or ticket is audited in RESOLUTION_RUN; the steps of a
-- multi-step fix (FIX_SKILL) in RESOLUTION_STEP. Only a reviewer publishes a KNOWN_ISSUE, so the automated path only
-- ever runs fixes a person approved once.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

-- ---------------------------------------------------------------- work queue
CREATE TABLE IF NOT EXISTS {{database}}.CORE.FAILURE_EVENT (
    FAILURE_ID       VARCHAR(36)    NOT NULL,
    DEDUP_KEY        VARCHAR(600)   NOT NULL,      -- env, dag, task, run, map index, try: the same try is ingested once
    ENV_ID           VARCHAR(64)    NOT NULL,
    INCIDENT_ID      VARCHAR(36),                  -- the incident the failure belongs to (one Jira ticket for both)
    DAG_ID           VARCHAR(250)   NOT NULL,
    TASK_ID          VARCHAR(250),
    RUN_ID           VARCHAR(250),
    MAP_INDEX        NUMBER(10,0)   DEFAULT -1,
    TRY_NUMBER       NUMBER(6,0),
    EXCEPTION_CLASS  VARCHAR(250),
    EXCEPTION_TEXT   VARCHAR(4000),
    ERROR_SIGNATURE  VARCHAR(64)    NOT NULL,      -- sha1 of dag, task, class and the normalised text; KNOWN_ISSUE has the same
    LOG_URL          VARCHAR(2048),
    QUERY_ID         VARCHAR(64),                  -- from the log, when present
    DBT_NODE_ID      VARCHAR(512),                 -- from the log, when present
    STATUS           VARCHAR(24)    DEFAULT 'PENDING',  -- PENDING | IN_PROGRESS | OUT_OF_SCOPE | AWAITING_APPROVAL | REJECTED | RESOLVED | TICKETED
    FLOW_MODE        VARCHAR(24),                  -- the scope the claim found: AUTO | APPROVAL_ALWAYS | TICKET_ONLY | DISABLED
    ISSUE_ID         VARCHAR(36),                  -- the published fix the search matched
    SEARCH_SCORE     FLOAT,
    TICKET_ID        VARCHAR(64),                  -- the Jira key, when TICKETED
    CLEAR_STATE      VARCHAR(16),                  -- REQUESTED | DONE | FAILED | SKIPPED: the Airflow clear after a fix
    CLEAR_DETAIL     VARCHAR(2000),
    RCA              VARIANT,                      -- cause, evidence and the graph path, on a miss
    DETAIL           VARCHAR(4000),                -- why the failure is where it is, in one line
    CLAIMED_BY       VARCHAR(256),
    CLAIMED_AT       TIMESTAMP_LTZ,
    CREATED_AT       TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT       TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_FAILURE_EVENT PRIMARY KEY (FAILURE_ID)
);

-- ---------------------------------------------------------------- published fixes
CREATE TABLE IF NOT EXISTS {{database}}.CORE.FIX_SKILL (
    SKILL_ID         VARCHAR(36)    NOT NULL,
    SKILL_NAME       VARCHAR(250)   NOT NULL,      -- unique
    DESCRIPTION      VARCHAR(4000),
    STEPS            VARIANT,                      -- [{step_no, action (a fix procedure's name), input, note}] in order
    VERSION_NO       NUMBER(6,0)    DEFAULT 1,
    OWNER            VARCHAR(256),
    CREATED_BY       VARCHAR(256),
    CREATED_AT       TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT       TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_FIX_SKILL PRIMARY KEY (SKILL_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.CORE.KNOWN_ISSUE (
    ISSUE_ID           VARCHAR(36)    NOT NULL,
    TITLE              VARCHAR(500)   NOT NULL,
    ERROR_SIGNATURE    VARCHAR(64),
    DAG_ID             VARCHAR(250),                -- null: any DAG
    TASK_ID            VARCHAR(250),                -- null: any task of the DAG
    EXCEPTION_CLASS    VARCHAR(250),
    MATCH_TEXT         VARCHAR(8000)  NOT NULL,     -- what the search compares a failure with
    FIX_TYPE           VARCHAR(16)    NOT NULL,     -- PROCEDURE | SKILL
    PROCEDURE_FQN      VARCHAR(512),                -- PROCEDURE only: called with the failure id
    SKILL_ID           VARCHAR(36),                 -- SKILL only
    APPROVAL_REQUIRED  BOOLEAN        DEFAULT TRUE,
    STATUS             VARCHAR(16)    DEFAULT 'DRAFT',   -- DRAFT | PUBLISHED | RETIRED
    VERSION_NO         NUMBER(6,0)    DEFAULT 1,
    OWNER              VARCHAR(256),
    SOURCE_INCIDENT_ID VARCHAR(36),                 -- the solved incident it was written from
    SOURCE_FAILURE_ID  VARCHAR(36),
    NOTES              VARCHAR(4000),
    CREATED_BY         VARCHAR(256),
    CREATED_AT         TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    PUBLISHED_BY       VARCHAR(256),
    PUBLISHED_AT       TIMESTAMP_LTZ,
    UPDATED_AT         TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_KNOWN_ISSUE PRIMARY KEY (ISSUE_ID)
) CHANGE_TRACKING = TRUE;   -- CORE.KNOWN_ISSUE_SEARCH reads it

-- ---------------------------------------------------------------- approval and audit
CREATE TABLE IF NOT EXISTS {{database}}.CORE.APPROVAL_REQUEST (
    APPROVAL_ID      VARCHAR(36)    NOT NULL,
    FAILURE_ID       VARCHAR(36)    NOT NULL,
    ISSUE_ID         VARCHAR(36)    NOT NULL,
    FIX_TYPE         VARCHAR(16),
    FIX_NAME         VARCHAR(512),
    STATUS           VARCHAR(16)    DEFAULT 'OPEN',  -- OPEN | APPROVED | REJECTED | DONE (the approved fix ran)
    APPROVER         VARCHAR(256),                   -- from APPROVAL_ROUTE; null: any holder of OPS.FIX_APPROVE
    DECIDED_BY       VARCHAR(256),
    DECISION_NOTE    VARCHAR(2000),
    REQUESTED_AT     TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    DECIDED_AT       TIMESTAMP_LTZ,
    CONSTRAINT PK_APPROVAL_REQUEST PRIMARY KEY (APPROVAL_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.CORE.RESOLUTION_RUN (
    RESOLUTION_ID    VARCHAR(36)    NOT NULL,
    FAILURE_ID       VARCHAR(36)    NOT NULL,
    ISSUE_ID         VARCHAR(36),                    -- null on a miss
    FIX_TYPE         VARCHAR(16),
    FIX_NAME         VARCHAR(512),
    OUTCOME          VARCHAR(16),                    -- SUCCESS | FAILED | TICKETED
    DETAIL           VARIANT,                        -- the result, or the cause and why a ticket was opened
    ACTOR            VARCHAR(256),
    STARTED_AT       TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    FINISHED_AT      TIMESTAMP_LTZ,
    CONSTRAINT PK_RESOLUTION_RUN PRIMARY KEY (RESOLUTION_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.CORE.RESOLUTION_STEP (
    RESOLUTION_ID    VARCHAR(36)    NOT NULL,
    STEP_NO          NUMBER(4,0)    NOT NULL,
    ACTION           VARCHAR(512),
    STATUS           VARCHAR(16),                    -- SUCCESS | FAILED
    OUTPUT           VARIANT,
    STARTED_AT       TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    FINISHED_AT      TIMESTAMP_LTZ,
    CONSTRAINT PK_RESOLUTION_STEP PRIMARY KEY (RESOLUTION_ID, STEP_NO)
);

-- ---------------------------------------------------------------- guardrails, read before anything else
CREATE TABLE IF NOT EXISTS {{database}}.CORE.CONTROL_PREFERENCE (
    PREFERENCE_ID              VARCHAR(36)    NOT NULL,
    IS_ACTIVE                  BOOLEAN        DEFAULT TRUE,     -- one active row
    AUTO_RESOLVE_ENABLED       BOOLEAN        DEFAULT FALSE,    -- false: failures stay PENDING, nothing is claimed
    SEARCH_SCORE_MIN           FLOAT          DEFAULT 0.75,     -- below it a search hit is a miss
    MAX_PARALLEL_TASKS         NUMBER(4,0)    DEFAULT 5,        -- failures one claim takes at most
    AIRFLOW_CLEAR_ENABLED      BOOLEAN        DEFAULT TRUE,     -- clear the Airflow task after a successful fix
    DEFAULT_APPROVAL_REQUIRED  BOOLEAN        DEFAULT TRUE,     -- for a fix whose own flag is not set
    GRAPH_HOPS                 NUMBER(2,0)    DEFAULT 3,        -- how far upstream the root-cause analysis walks
    NOTIFY_TARGET              VARCHAR(256),
    RCA_MODE                   VARCHAR(16)    DEFAULT 'AGENT',  -- AGENT (the RCA agent, grounded diagnosis if it fails) | GROUNDED
    UPDATED_BY                 VARCHAR(256),
    UPDATED_AT                 TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_CONTROL_PREFERENCE PRIMARY KEY (PREFERENCE_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.CORE.PIPELINE_SCOPE (
    SCOPE_ID         VARCHAR(36)    NOT NULL,
    DAG_ID           VARCHAR(250)   NOT NULL,
    TASK_ID          VARCHAR(250),                   -- null: the whole DAG; a task row wins over the DAG row
    FLOW_MODE        VARCHAR(24)    DEFAULT 'AUTO',  -- AUTO | APPROVAL_ALWAYS | TICKET_ONLY | DISABLED
    ENABLED          BOOLEAN        DEFAULT TRUE,    -- false = DISABLED
    OWNER            VARCHAR(256),
    UPDATED_BY       VARCHAR(256),
    UPDATED_AT       TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_PIPELINE_SCOPE PRIMARY KEY (SCOPE_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.CORE.APPROVAL_ROUTE (
    ROUTE_ID         VARCHAR(36)    NOT NULL,
    DAG_ID           VARCHAR(250)   NOT NULL,
    TASK_ID          VARCHAR(250),                   -- null: the whole DAG; a task row wins over the DAG row
    APPROVER         VARCHAR(256)   NOT NULL,        -- a user name, written onto APPROVAL_REQUEST
    NOTIFY_TARGET    VARCHAR(256),                   -- overrides CONTROL_PREFERENCE.NOTIFY_TARGET
    UPDATED_BY       VARCHAR(256),
    UPDATED_AT       TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_APPROVAL_ROUTE PRIMARY KEY (ROUTE_ID)
);

-- the one active preference row, off until an admin turns auto-resolution on
INSERT INTO {{database}}.CORE.CONTROL_PREFERENCE (PREFERENCE_ID, IS_ACTIVE, AUTO_RESOLVE_ENABLED, UPDATED_BY)
SELECT 'default', TRUE, FALSE, 'V062'
 WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.CONTROL_PREFERENCE WHERE PREFERENCE_ID = 'default');

-- ---------------------------------------------------------------- grants (no future grants in CORE since V061)
GRANT SELECT ON TABLE {{database}}.CORE.FAILURE_EVENT TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT SELECT ON TABLE {{database}}.CORE.FIX_SKILL TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT SELECT ON TABLE {{database}}.CORE.KNOWN_ISSUE TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT SELECT ON TABLE {{database}}.CORE.APPROVAL_REQUEST TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT SELECT ON TABLE {{database}}.CORE.RESOLUTION_RUN TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT SELECT ON TABLE {{database}}.CORE.RESOLUTION_STEP TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT SELECT ON TABLE {{database}}.CORE.CONTROL_PREFERENCE TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT SELECT ON TABLE {{database}}.CORE.PIPELINE_SCOPE TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT SELECT ON TABLE {{database}}.CORE.APPROVAL_ROUTE TO DATABASE ROLE {{database}}.OPS_VIEWER;

GRANT INSERT, UPDATE ON TABLE {{database}}.CORE.FAILURE_EVENT TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT INSERT, UPDATE ON TABLE {{database}}.CORE.FIX_SKILL TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT INSERT, UPDATE ON TABLE {{database}}.CORE.KNOWN_ISSUE TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT INSERT, UPDATE ON TABLE {{database}}.CORE.APPROVAL_REQUEST TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT INSERT, UPDATE ON TABLE {{database}}.CORE.RESOLUTION_RUN TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT INSERT, UPDATE ON TABLE {{database}}.CORE.RESOLUTION_STEP TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT INSERT, UPDATE ON TABLE {{database}}.CORE.CONTROL_PREFERENCE TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT INSERT, UPDATE, DELETE ON TABLE {{database}}.CORE.PIPELINE_SCOPE TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT INSERT, UPDATE, DELETE ON TABLE {{database}}.CORE.APPROVAL_ROUTE TO DATABASE ROLE {{database}}.OPS_SERVICE;
