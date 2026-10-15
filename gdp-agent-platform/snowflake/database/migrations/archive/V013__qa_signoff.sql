-- QA sign-off: a tester approves or rejects the run's QA tests for one STTM version. QA is a parallel lane next to
-- Data Quality and dbt; code review opens only when it is signed off. A new STTM version needs a new sign-off.
CREATE TABLE IF NOT EXISTS {{database}}.CONTRACT.QA_SIGNOFF (
    SIGNOFF_ID   VARCHAR(36)   NOT NULL,
    RUN_ID       VARCHAR(36)   NOT NULL,
    STTM_ID      VARCHAR(36),
    DECISION     VARCHAR(16)   NOT NULL,   -- APPROVED | REJECTED
    NOTE         VARCHAR(4000),
    DECIDED_BY   VARCHAR(256)  DEFAULT CURRENT_USER(),
    DECIDED_AT   TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_QA_SIGNOFF PRIMARY KEY (SIGNOFF_ID)
);

GRANT SELECT, INSERT ON TABLE {{database}}.CONTRACT.QA_SIGNOFF TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT ON TABLE {{database}}.CONTRACT.QA_SIGNOFF TO DATABASE ROLE {{database}}.REVIEWER;
