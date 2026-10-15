-- Ops incidents (PR O2): support teams, routing rules, incidents with their timeline, and the notification outbox
-- (Teams cards and Jira bot calls) that the worker delivers with retries.
-- Teams webhook URLs are stored ENCRYPTed with the API host's key (AIP_SECRET_KEY, else JIRA_TOKEN_KEY), exactly like the
-- Airflow push secret: they are decrypted only at send time and never returned by any endpoint. Outbox rows name the
-- team and which of its webhooks to use, never the URL.
-- Ops settings (reopen window, rate limit, public URL, Jira transition) live in CORE.PLATFORM_CONFIG under key OPS.
-- Times are stored in UTC. Every statement is safe to run again (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS {{database}}.OPS.TEAM (
    TEAM_ID                    VARCHAR(64)   NOT NULL,   -- slug of the name
    NAME                       VARCHAR(120)  NOT NULL,
    JIRA_PROJECT               VARCHAR(32),              -- tickets for this team go here (else the Jira default project)
    JIRA_COMPONENT             VARCHAR(255),
    JIRA_ASSIGNEE_ACCOUNT_ID   VARCHAR(128),
    TEAMS_WEBHOOK_SECRET       BINARY,                   -- ENCRYPT(alerts webhook URL, host key); never returned
    ESCALATION_MINUTES         NUMBER(6,0)   DEFAULT 30,
    ESCALATION_WEBHOOK_SECRET  BINARY,                   -- ENCRYPT(escalation webhook URL, host key); never returned
    MEMBERS                    ARRAY,                    -- platform user names, for "my incidents"
    CREATED_BY                 VARCHAR(256)  DEFAULT CURRENT_USER(),
    CREATED_AT                 TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT                 TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_OPS_TEAM PRIMARY KEY (TEAM_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.OPS.ROUTING_RULE (
    RULE_ID            VARCHAR(36)   NOT NULL,
    PRIORITY           NUMBER(6,0)   NOT NULL DEFAULT 100,   -- lowest first; the first match wins
    DAG_PATTERN        VARCHAR(250),                         -- glob on DAG_ID (orders_*); NULL matches any
    TAG                VARCHAR(250),                         -- the DAG has this tag; NULL matches any
    OWNER              VARCHAR(250),                         -- the DAG has this owner; NULL matches any
    ENV_ID             VARCHAR(64),                          -- NULL matches any environment
    TEAM_ID            VARCHAR(64),
    SEVERITY_OVERRIDE  VARCHAR(2),                           -- P1..P4, wins over the computed severity
    MUTE_UNTIL         TIMESTAMP_LTZ,                        -- maintenance: matching incidents open MUTED until then
    MUTE_REASON        VARCHAR(500),
    ENABLED            BOOLEAN       DEFAULT TRUE,
    CREATED_BY         VARCHAR(256)  DEFAULT CURRENT_USER(),
    CREATED_AT         TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT         TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_OPS_ROUTING_RULE PRIMARY KEY (RULE_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.OPS.INCIDENT (
    INCIDENT_ID         VARCHAR(36)   NOT NULL,
    FINGERPRINT         VARCHAR(40)   NOT NULL,   -- sha1(env, dag, task, normalized error signature)
    ENV_ID              VARCHAR(64)   NOT NULL,
    DAG_ID              VARCHAR(250)  NOT NULL,
    TASK_ID             VARCHAR(250),             -- NULL for DAG-level kinds
    MAP_INDEX           NUMBER(10,0),
    RUN_ID              VARCHAR(250),             -- the latest occurrence's run
    KIND                VARCHAR(24)   NOT NULL,   -- FAILED | RETRIES_EXHAUSTED | LATE | LONG_RUNNING | UPSTREAM
    STATUS              VARCHAR(16)   NOT NULL,   -- OPEN | ACK | MITIGATED | RESOLVED | MUTED
    SEVERITY            VARCHAR(2)    NOT NULL,   -- P1..P4
    TEAM_ID             VARCHAR(64),              -- NULL: unrouted
    ASSIGNEE            VARCHAR(256),
    TITLE               VARCHAR(500),
    ERROR_EXCERPT       VARCHAR(4000),            -- redacted
    FIRST_SEEN          TIMESTAMP_LTZ,
    LAST_SEEN           TIMESTAMP_LTZ,
    OPENED_AT           TIMESTAMP_LTZ,            -- first open or last reopen: the escalation clock starts here
    OCCURRENCES         NUMBER(10,0)  DEFAULT 1,
    PARENT_INCIDENT_ID  VARCHAR(36),              -- storm control: a child of an upstream incident
    JIRA_KEY            VARCHAR(40),
    JIRA_STATE          VARCHAR(16),              -- PENDING | OPEN | DONE | NOT_RAISED | FAILED | SKIPPED
    JIRA_SYNCED_OCCURRENCES NUMBER(10,0) DEFAULT 1,
    JIRA_COMMENTED_AT   TIMESTAMP_LTZ,
    AI                  VARIANT,                  -- the diagnosis (PR O3)
    AI_SUMMARY          VARCHAR(2000),
    RESOLUTION          VARCHAR(4000),
    RESOLVED_BY         VARCHAR(256),
    RESOLVED_AT         TIMESTAMP_LTZ,
    ACKED_BY            VARCHAR(256),
    ACKED_AT            TIMESTAMP_LTZ,
    MUTED_UNTIL         TIMESTAMP_LTZ,
    MUTE_REASON         VARCHAR(500),
    ESCALATIONS         NUMBER(4,0)   DEFAULT 0,
    LAST_ESCALATED_AT   TIMESTAMP_LTZ,
    CREATED_AT          TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT          TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_OPS_INCIDENT PRIMARY KEY (INCIDENT_ID)
) CLUSTER BY (STATUS, ENV_ID, DAG_ID);

CREATE TABLE IF NOT EXISTS {{database}}.OPS.INCIDENT_EVENT (
    EVENT_ID         VARCHAR(36)   NOT NULL,
    INCIDENT_ID      VARCHAR(36),               -- NULL only on an idempotency claim not yet bound to an incident
    KIND             VARCHAR(32)   NOT NULL,    -- opened | occurrence | reopened | ack | assigned | resolved | muted | ...
    ACTOR            VARCHAR(256),              -- a user, or 'system'
    DETAIL           VARIANT,
    IDEMPOTENCY_KEY  VARCHAR(500),              -- occurrence keys and Jira create claims
    CREATED_AT       TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_OPS_INCIDENT_EVENT PRIMARY KEY (EVENT_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.OPS.NOTIFICATION (
    NOTIFICATION_ID  VARCHAR(36)   NOT NULL,
    INCIDENT_ID      VARCHAR(36),
    TEAM_ID          VARCHAR(64),
    CHANNEL          VARCHAR(16)   NOT NULL,    -- TEAMS | JIRA
    KIND             VARCHAR(32)   NOT NULL,    -- opened | escalated | reoccurred | resolved | storm_summary | create | recur | resolve | reopen
    TARGET_SECRET    VARCHAR(16),               -- which team webhook: alerts | escalation (never the URL)
    PAYLOAD          VARIANT,                   -- the redacted card, or the Jira action's parameters
    STATUS           VARCHAR(16)   NOT NULL,    -- PENDING | SENT | FAILED (retrying) | DEAD | SUPPRESSED | SKIPPED
    ATTEMPTS         NUMBER(4,0)   DEFAULT 0,
    NEXT_AT          TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    ERROR            VARCHAR(2000),
    DEDUPE_KEY       VARCHAR(500),
    CREATED_AT       TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    SENT_AT          TIMESTAMP_LTZ,
    CONSTRAINT PK_OPS_NOTIFICATION PRIMARY KEY (NOTIFICATION_ID)
);

-- Upstream links between DAGs (Airflow datasets, ExternalTaskSensor, or set by hand) for storm grouping. Empty is fine:
-- incidents are then never grouped.
CREATE TABLE IF NOT EXISTS {{database}}.OPS.DAG_DEPENDENCY (
    ENV_ID             VARCHAR(64)   NOT NULL,
    UPSTREAM_DAG_ID    VARCHAR(250)  NOT NULL,
    DOWNSTREAM_DAG_ID  VARCHAR(250)  NOT NULL,
    KIND               VARCHAR(16),               -- DATASET | SENSOR | MANUAL
    UPDATED_AT         TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_OPS_DAG_DEPENDENCY PRIMARY KEY (ENV_ID, UPSTREAM_DAG_ID, DOWNSTREAM_DAG_ID)
);

-- per-DAG mute (maintenance) and the timezone its expected-by cron is read in (UTC when NULL)
ALTER TABLE {{database}}.OPS.DAG ADD COLUMN IF NOT EXISTS MUTE_UNTIL TIMESTAMP_LTZ;
ALTER TABLE {{database}}.OPS.DAG ADD COLUMN IF NOT EXISTS MUTE_REASON VARCHAR(500);
ALTER TABLE {{database}}.OPS.DAG ADD COLUMN IF NOT EXISTS TIMEZONE VARCHAR(64);

-- same grants as V030 (the FUTURE grants already cover these tables; repeated so a re-run on its own is complete)
GRANT SELECT ON ALL TABLES IN SCHEMA {{database}}.OPS TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {{database}}.OPS TO DATABASE ROLE {{database}}.OPS_SERVICE;
