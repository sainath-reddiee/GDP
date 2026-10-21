-- V069: data quality alerting (services/quality/alerts.py, run by the ops worker). A table scan with failing checks,
-- a check that keeps warning, a failed freshness check or a scheduled scan that did not run opens or updates one
-- incident per table in CORE.INCIDENT (ENV_ID 'dq', DAG_ID the table), routed to the domain's support team, ticketed
-- through the Jira outbox and resolved automatically by a later clean scan. A daily digest per domain is kept here
-- and delivered through the outbox.
-- DQ_ALERT_ROUTE: which support team (CORE.TEAM) owns a domain's data quality alerts, and how loud they are.
-- DQ_DIGEST: one digest per domain and day (pass rate, newly failing checks, trend against last week).
-- The worker (OPS_SERVICE) already reads the scan tables through VIEWER; it writes these two.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

CREATE TABLE IF NOT EXISTS {{database}}.CORE.DQ_ALERT_ROUTE (
    DOMAIN_ID            VARCHAR(36)    NOT NULL,
    TEAM_ID              VARCHAR(64),                   -- CORE.TEAM; NULL leaves the domain's incidents unrouted
    FAIL_SEVERITY        VARCHAR(2)     DEFAULT 'P2',   -- incident severity for FAIL checks (WARN and stale: one lower)
    WARN_AFTER_RUNS      NUMBER(3,0)    DEFAULT 3,      -- a check warning this many scans in a row raises; 0 never
    STALE_GRACE_MINUTES  NUMBER(6,0)    DEFAULT 120,    -- a scheduled scan this late counts as missed
    DIGEST_ENABLED       BOOLEAN        DEFAULT TRUE,
    DIGEST_JIRA          BOOLEAN        DEFAULT FALSE,  -- also file the daily digest as a Jira issue in the team's project
    UPDATED_BY           VARCHAR(256)   DEFAULT CURRENT_USER(),
    UPDATED_AT           TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_DQ_ALERT_ROUTE PRIMARY KEY (DOMAIN_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.CORE.DQ_DIGEST (
    DOMAIN_ID            VARCHAR(36)    NOT NULL,
    DIGEST_DATE          DATE           NOT NULL,
    TEAM_ID              VARCHAR(64),
    OWNER                VARCHAR(256),                  -- the domain owner it is addressed to
    PASS_RATE            NUMBER(5,1),
    NEW_FAILING          NUMBER(6,0),
    CONTENT              VARIANT,                       -- services.quality.alerts.digest()
    CREATED_AT           TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_DQ_DIGEST PRIMARY KEY (DOMAIN_ID, DIGEST_DATE)
);

GRANT SELECT ON TABLE {{database}}.CORE.DQ_ALERT_ROUTE TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.DQ_ALERT_ROUTE TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT SELECT ON TABLE {{database}}.CORE.DQ_DIGEST TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.DQ_DIGEST TO DATABASE ROLE {{database}}.OPS_SERVICE;
