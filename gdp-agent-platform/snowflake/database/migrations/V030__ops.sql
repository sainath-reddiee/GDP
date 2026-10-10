-- Ops capture for Airflow (Amazon MWAA): environments, DAGs, DAG runs, task runs, raw push and poll events, and the
-- leases that keep one worker per background job. Runs arrive from the poller (boto3 invoke_rest_api, the source of
-- truth) and, when MWAA has egress, from the listener plugin (signed push). Both write the same keys, so duplicates and
-- out-of-order events are harmless: the newest state by timestamp wins.
-- Times are stored in UTC. START and END are reserved words, so run and task times are STARTED_AT and ENDED_AT, and
-- the poll cursor column is CURSOR_VALUE.
-- Raw events are purged after 30 days by the worker's retention job (no Snowflake task).
-- Every statement is safe to run again (IF NOT EXISTS).
CREATE SCHEMA IF NOT EXISTS {{database}}.OPS COMMENT = 'Airflow (MWAA) runs captured for ops and support';

CREATE TABLE IF NOT EXISTS {{database}}.OPS.AIRFLOW_ENV (
    ENV_ID               VARCHAR(64)   NOT NULL,   -- slug, also the X-GDP-Env header value the plugin sends
    NAME                 VARCHAR(120)  NOT NULL,
    KIND                 VARCHAR(16)   DEFAULT 'MWAA',
    MWAA_ENV             VARCHAR(256)  NOT NULL,   -- the MWAA environment name
    REGION               VARCHAR(32)   NOT NULL,
    AIRFLOW_URL          VARCHAR(1024),            -- web UI base, for links only
    API_VERSION          VARCHAR(8),               -- v1 (Airflow 2) | v2 (Airflow 3), detected from /version
    AIRFLOW_VERSION      VARCHAR(32),
    ENABLED              BOOLEAN       DEFAULT TRUE,
    POLL_SECONDS         NUMBER(6,0)   DEFAULT 300,
    PUSH_ENABLED         BOOLEAN       DEFAULT FALSE,
    PUSH_SECRET          BINARY,                   -- ENCRYPT(push secret, key from the API host); never returned after creation
    WEBHOOK_SECRET_NAME  VARCHAR(512),             -- reserved for a Snowflake secret holding the push secret
    LAST_POLL_AT         TIMESTAMP_LTZ,            -- last successful poll
    LAST_ATTEMPT_AT      TIMESTAMP_LTZ,            -- last poll attempt, successful or not
    CURSOR_VALUE         VARCHAR(64),              -- ISO time the last complete poll covered up to
    LAST_ERROR           VARCHAR(2000),
    CREATED_BY           VARCHAR(256)  DEFAULT CURRENT_USER(),
    CREATED_AT           TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT           TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_OPS_AIRFLOW_ENV PRIMARY KEY (ENV_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.OPS.DAG (
    ENV_ID            VARCHAR(64)   NOT NULL,
    DAG_ID            VARCHAR(250)  NOT NULL,
    FILELOC           VARCHAR(2000),
    OWNERS            ARRAY,
    TAGS              ARRAY,
    SCHEDULE          VARCHAR(500),
    IS_PAUSED         BOOLEAN,
    IS_ACTIVE         BOOLEAN,
    DESCRIPTION       VARCHAR(4000),
    TEAM_ID           VARCHAR(64),               -- settings below are set in the platform, never by Airflow
    CRITICALITY       VARCHAR(16),               -- CRITICAL | HIGH | MEDIUM | LOW
    EXPECTED_BY_CRON  VARCHAR(120),
    MAX_DURATION_MIN  NUMBER(8,0),
    DOMAIN_ID         VARCHAR(36),
    REPO_ID           VARCHAR(36),
    REPO_PATH         VARCHAR(2000),
    LAST_SEEN_AT      TIMESTAMP_LTZ,
    UPDATED_AT        TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_OPS_DAG PRIMARY KEY (ENV_ID, DAG_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.OPS.DAG_RUN (
    ENV_ID            VARCHAR(64)   NOT NULL,
    DAG_ID            VARCHAR(250)  NOT NULL,
    RUN_ID            VARCHAR(250)  NOT NULL,
    RUN_TYPE          VARCHAR(32),
    STATE             VARCHAR(32),
    LOGICAL_DATE      TIMESTAMP_LTZ,
    STARTED_AT        TIMESTAMP_LTZ,
    ENDED_AT          TIMESTAMP_LTZ,
    DURATION_S        NUMBER(12,3),
    EXTERNAL_TRIGGER  BOOLEAN,
    NOTE              VARCHAR(4000),
    SOURCE            VARCHAR(8),                -- PUSH | POLL, whichever wrote the current state
    UPDATED_AT        TIMESTAMP_LTZ,             -- the state's own time in Airflow; the newer one wins
    LOADED_AT         TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_OPS_DAG_RUN PRIMARY KEY (ENV_ID, DAG_ID, RUN_ID)
) CLUSTER BY (ENV_ID, DAG_ID, TO_DATE(STARTED_AT));

CREATE TABLE IF NOT EXISTS {{database}}.OPS.TASK_RUN (
    ENV_ID         VARCHAR(64)   NOT NULL,
    DAG_ID         VARCHAR(250)  NOT NULL,
    RUN_ID         VARCHAR(250)  NOT NULL,
    TASK_ID        VARCHAR(250)  NOT NULL,
    MAP_INDEX      NUMBER(10,0)  NOT NULL DEFAULT -1,
    TRY_NUMBER     NUMBER(6,0)   NOT NULL DEFAULT 0,
    STATE          VARCHAR(32),
    OPERATOR       VARCHAR(250),
    STARTED_AT     TIMESTAMP_LTZ,
    ENDED_AT       TIMESTAMP_LTZ,
    DURATION_S     NUMBER(12,3),
    HOSTNAME       VARCHAR(500),
    LOG_REF        VARCHAR(2000),
    ERROR_EXCERPT  VARCHAR(4000),               -- redacted before it is stored
    SOURCE         VARCHAR(8),
    UPDATED_AT     TIMESTAMP_LTZ,
    LOADED_AT      TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_OPS_TASK_RUN PRIMARY KEY (ENV_ID, DAG_ID, RUN_ID, TASK_ID, MAP_INDEX, TRY_NUMBER)
) CLUSTER BY (ENV_ID, DAG_ID, TO_DATE(STARTED_AT));

CREATE TABLE IF NOT EXISTS {{database}}.OPS.EVENT (
    EVENT_ID      VARCHAR(128)  NOT NULL,       -- the plugin's X-GDP-Event-Id (replay guard), or a poll id
    ENV_ID        VARCHAR(64),
    SOURCE        VARCHAR(8)    NOT NULL,       -- PUSH | POLL
    KIND          VARCHAR(32),
    PAYLOAD       VARIANT,
    RECEIVED_AT   TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    PROCESSED_AT  TIMESTAMP_LTZ,
    ERROR         VARCHAR(2000),
    CONSTRAINT PK_OPS_EVENT PRIMARY KEY (EVENT_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.OPS.JOB_LEASE (
    JOB_NAME      VARCHAR(256)  NOT NULL,       -- poll:<env_id> | outbox | escalate | sla | retention | worker
    HOLDER        VARCHAR(256),
    LEASE_UNTIL   TIMESTAMP_LTZ,
    LAST_RUN_AT   TIMESTAMP_LTZ,
    CURSOR_VALUE  VARCHAR(256),
    CONSTRAINT PK_OPS_JOB_LEASE PRIMARY KEY (JOB_NAME)
);

-- OPS_SERVICE: the worker and the ingest endpoint (service user), and engineers who poll or change settings.
-- OPS_VIEWER: reads every OPS table; the push secret stays encrypted (DECRYPT needs the key held by the API host).
-- Grant OPS_SERVICE to the service user's account role: GRANT DATABASE ROLE {{database}}.OPS_SERVICE TO ROLE <role>
CREATE DATABASE ROLE IF NOT EXISTS {{database}}.OPS_VIEWER  COMMENT = 'Read Airflow runs captured for ops';
CREATE DATABASE ROLE IF NOT EXISTS {{database}}.OPS_SERVICE COMMENT = 'Write Airflow runs, events and job leases';
GRANT DATABASE ROLE {{database}}.OPS_VIEWER  TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT DATABASE ROLE {{database}}.OPS_VIEWER  TO DATABASE ROLE {{database}}.VIEWER;
GRANT DATABASE ROLE {{database}}.OPS_SERVICE TO DATABASE ROLE {{database}}.DATA_ENGINEER;

GRANT USAGE ON DATABASE {{database}} TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT USAGE ON SCHEMA {{database}}.OPS TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT SELECT ON ALL TABLES IN SCHEMA {{database}}.OPS TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT SELECT ON FUTURE TABLES IN SCHEMA {{database}}.OPS TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {{database}}.OPS TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT SELECT, INSERT, UPDATE, DELETE ON FUTURE TABLES IN SCHEMA {{database}}.OPS TO DATABASE ROLE {{database}}.OPS_SERVICE;
-- the service user also reads platform settings (LLM model, config) like any engineer
GRANT USAGE ON SCHEMA {{database}}.CORE TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT SELECT ON TABLE {{database}}.CORE.PLATFORM_CONFIG TO DATABASE ROLE {{database}}.OPS_SERVICE;
