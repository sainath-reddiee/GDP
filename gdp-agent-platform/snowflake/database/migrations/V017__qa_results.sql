-- QA test runs: the run's functional tests executed inside Snowflake, one row per run of the suite and one row per
-- test, with the outcome, what was measured, masked sample rows and the SQL that ran.
CREATE SCHEMA IF NOT EXISTS {{database}}.QUALITY COMMENT = 'Data quality scan runs and check results';

CREATE TABLE IF NOT EXISTS {{database}}.QUALITY.QA_RUN (
    QA_RUN_ID      VARCHAR(36)   NOT NULL,
    RUN_ID         VARCHAR(36)   NOT NULL,
    STTM_ID        VARCHAR(36),
    TARGET         VARCHAR(1024),
    TARGET_BUILT   BOOLEAN,
    STARTED_AT     TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    DURATION_MS    NUMBER(12,0),
    TESTS          NUMBER(6,0),
    PASSED         NUMBER(6,0),
    FAILED         NUMBER(6,0),
    REVIEW         NUMBER(6,0),              -- ran, but a tester has to judge the result
    NOT_RUN        NUMBER(6,0),              -- reads the target model, which is not built yet
    ERRORS         NUMBER(6,0),
    TRIGGERED_BY   VARCHAR(32),
    CREATED_BY     VARCHAR(256)  DEFAULT CURRENT_USER(),
    CONSTRAINT PK_QA_RUN PRIMARY KEY (QA_RUN_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.QUALITY.QA_RESULT (
    RESULT_ID      VARCHAR(36)   NOT NULL,
    QA_RUN_ID      VARCHAR(36)   NOT NULL,
    RUN_ID         VARCHAR(36)   NOT NULL,
    TEST_ID        VARCHAR(64)   NOT NULL,
    CATEGORY       VARCHAR(32),
    TITLE          VARCHAR(500),
    SEVERITY       VARCHAR(16),
    ORIGIN         VARCHAR(16),
    OUTCOME        VARCHAR(16)   NOT NULL,   -- PASS | FAIL | REVIEW | NOT_RUN | ERROR
    ROWS_RETURNED  NUMBER(12,0),
    MEASURED       VARCHAR(200),
    EXPECTED       VARCHAR(1000),
    DETAIL         VARCHAR(4000),
    COLUMNS        VARIANT,
    SAMPLE_ROWS    VARIANT,                  -- up to 20 result rows, PII masked
    SQL_TEXT       VARCHAR(16000),
    DURATION_MS    NUMBER(12,0),
    CREATED_AT     TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_QA_RESULT PRIMARY KEY (RESULT_ID)
);

GRANT USAGE ON SCHEMA {{database}}.QUALITY TO DATABASE ROLE {{database}}.VIEWER;
GRANT USAGE ON SCHEMA {{database}}.QUALITY TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON SCHEMA {{database}}.QUALITY TO DATABASE ROLE {{database}}.REVIEWER;
GRANT SELECT, INSERT ON TABLE {{database}}.QUALITY.QA_RUN TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT ON TABLE {{database}}.QUALITY.QA_RESULT TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT ON TABLE {{database}}.QUALITY.QA_RUN TO DATABASE ROLE {{database}}.REVIEWER;
GRANT SELECT ON TABLE {{database}}.QUALITY.QA_RESULT TO DATABASE ROLE {{database}}.REVIEWER;
GRANT SELECT ON TABLE {{database}}.QUALITY.QA_RUN TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.QUALITY.QA_RESULT TO DATABASE ROLE {{database}}.VIEWER;
