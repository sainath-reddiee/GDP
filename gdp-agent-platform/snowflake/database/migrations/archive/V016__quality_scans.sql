-- Data quality scans: the run's checks executed inside Snowflake, one row per scan and one row per check result,
-- with the measured value, threshold, failing-row count, masked sample rows and the SQL that ran.
CREATE SCHEMA IF NOT EXISTS {{database}}.QUALITY COMMENT = 'Data quality scan runs and check results';

CREATE TABLE IF NOT EXISTS {{database}}.QUALITY.CHECK_RUN (
    SCAN_ID        VARCHAR(36)   NOT NULL,
    RUN_ID         VARCHAR(36)   NOT NULL,
    STTM_ID        VARCHAR(36),
    TARGET         VARCHAR(1024),            -- the built model, or 'source tables' for a source backtest
    MODE           VARCHAR(16)   NOT NULL,   -- MODEL | SOURCE
    STARTED_AT     TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    DURATION_MS    NUMBER(12,0),
    CHECKS         NUMBER(6,0),
    PASSED         NUMBER(6,0),
    WARNED         NUMBER(6,0),
    FAILED         NUMBER(6,0),
    NOT_EVALUATED  NUMBER(6,0),
    ERRORS         NUMBER(6,0),
    HEALTH         NUMBER(5,0),              -- 0-100: passes, half for warnings; unevaluated checks excluded
    ROWS_SCANNED   NUMBER(18,0),
    TRIGGERED_BY   VARCHAR(32),              -- UI | COPILOT | SCHEDULE
    CREATED_BY     VARCHAR(256)  DEFAULT CURRENT_USER(),
    CONSTRAINT PK_CHECK_RUN PRIMARY KEY (SCAN_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.QUALITY.CHECK_RESULT (
    RESULT_ID       VARCHAR(36)   NOT NULL,
    SCAN_ID         VARCHAR(36)   NOT NULL,
    RUN_ID          VARCHAR(36)   NOT NULL,
    EXPECTATION_ID  VARCHAR(36),
    TARGET_TABLE    VARCHAR(256),
    TARGET_COLUMN   VARCHAR(256),
    CHECK_TYPE      VARCHAR(32),
    KIND            VARCHAR(32),
    DIMENSION       VARCHAR(32),              -- completeness | uniqueness | validity | timeliness | schema | consistency | accuracy
    SEVERITY        VARCHAR(16),
    OUTCOME         VARCHAR(16)   NOT NULL,   -- PASS | WARN | FAIL | NOT_EVALUATED | ERROR
    MEASURED        FLOAT,
    THRESHOLD       VARCHAR(200),
    FAILED_ROWS     NUMBER(18,0),
    DETAIL          VARCHAR(4000),
    SAMPLE_ROWS     VARIANT,                  -- up to 20 failing rows, PII masked
    SQL_TEXT        VARCHAR(16000),
    DURATION_MS     NUMBER(12,0),
    CREATED_AT      TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_CHECK_RESULT PRIMARY KEY (RESULT_ID)
);

GRANT USAGE ON SCHEMA {{database}}.QUALITY TO DATABASE ROLE {{database}}.VIEWER;
GRANT USAGE ON SCHEMA {{database}}.QUALITY TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON SCHEMA {{database}}.QUALITY TO DATABASE ROLE {{database}}.REVIEWER;
GRANT SELECT, INSERT ON TABLE {{database}}.QUALITY.CHECK_RUN TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT ON TABLE {{database}}.QUALITY.CHECK_RESULT TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT ON TABLE {{database}}.QUALITY.CHECK_RUN TO DATABASE ROLE {{database}}.REVIEWER;
GRANT SELECT ON TABLE {{database}}.QUALITY.CHECK_RESULT TO DATABASE ROLE {{database}}.REVIEWER;
GRANT SELECT ON TABLE {{database}}.QUALITY.CHECK_RUN TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.QUALITY.CHECK_RESULT TO DATABASE ROLE {{database}}.VIEWER;
