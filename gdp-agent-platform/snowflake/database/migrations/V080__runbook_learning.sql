-- V080: runbook learning from pulled logs (services/ops/runbook_learning.py). Every time the logs of a failed task are
-- pulled into the UI (Airflow task log, incident Datadog logs) the error lines are fingerprinted and matched against
-- the AIRFLOW_RUNBOOK knowledge. One row per error fingerprint:
--   RUNBOOK_KEY        the runbook that matched (SOURCE_REFERENCE in CORE.DOMAIN_KNOWLEDGE); for an error no runbook
--                      matched, the key of the runbook proposed for it (PROPOSED in the Knowledge inbox until approved)
--   SEEN_COUNT         how many distinct task tries showed it (LAST_TRY_KEY keeps a refresh from counting twice)
--   FIX_SUCCESS_COUNT  fixes that resolved a failure with this fingerprint (services/resolve/engine.run_fix); LAST_FIX names it
-- The runbook context the RCA agent reads carries these counts, so a fix that worked is suggested first next time.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

CREATE TABLE IF NOT EXISTS {{database}}.CORE.RUNBOOK_SIGHTING (
    FINGERPRINT          VARCHAR(32)    NOT NULL,
    RUNBOOK_KEY          VARCHAR(256),
    MATCHED              BOOLEAN        DEFAULT FALSE,   -- an ACTIVE runbook matched (FALSE: proposed or none)
    SIGNATURE            VARCHAR(1000),                  -- the normalised error line
    SAMPLE               VARCHAR(4000),                  -- a redacted example of the error
    SOURCE               VARCHAR(16),                    -- AIRFLOW | DATADOG
    LAST_DAG_ID          VARCHAR(256),
    LAST_TASK_ID         VARCHAR(256),
    LAST_TRY_KEY         VARCHAR(1024),
    SEEN_COUNT           NUMBER(18,0)   DEFAULT 1,
    FIX_SUCCESS_COUNT    NUMBER(18,0)   DEFAULT 0,
    LAST_FIX             VARCHAR(512),
    FIRST_SEEN_AT        TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    LAST_SEEN_AT         TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    LAST_FIXED_AT        TIMESTAMP_LTZ,
    CONSTRAINT PK_RUNBOOK_SIGHTING PRIMARY KEY (FINGERPRINT)
);

GRANT SELECT ON TABLE {{database}}.CORE.RUNBOOK_SIGHTING TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.RUNBOOK_SIGHTING TO DATABASE ROLE {{database}}.DATA_ENGINEER;
