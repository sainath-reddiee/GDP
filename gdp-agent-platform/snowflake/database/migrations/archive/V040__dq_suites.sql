-- V040 Data Quality suites: the checks of each table are versioned, reviewed and approved in the app.
-- Applied once; never edit after it has been applied. Add a new V### instead.
--
-- GOVERNANCE.DQ_SUITE          one row per checked table (SUITE_ID = md5('dq:' || TABLE_NAME) as a UUID): the approved
--                              version, the open version (at most one DRAFT or IN_REVIEW), the domain and the domain
--                              model the table belongs to.
-- GOVERNANCE.DQ_SUITE_VERSION  the versions. CHECKS holds the table's checks in the platform's neutral form, each with
--                              a stable CHECK_ID. Statuses: DRAFT, IN_REVIEW, APPROVED, PUBLISHED, SUPERSEDED,
--                              REJECTED. The draft is edited in place guarded by ETAG. MODEL_ID and MODEL_VERSION
--                              record the approved domain model the checks were reviewed against.
--
-- GOVERNANCE.DQ_CHECKS_CATALOG stays the runtime copy the scanner and the schedules read; only an approved version
-- writes it (QUALITY.APPLY_DQ_SUITE, owner's rights). The deploy writes an APPROVED version 1 per table from the
-- catalog (services/catalog/dq_suite.backfill). Every statement is safe to run again (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS {{database}}.GOVERNANCE.DQ_SUITE (
    SUITE_ID          VARCHAR(36)    NOT NULL,
    TABLE_NAME        VARCHAR(800)   NOT NULL,
    DOMAIN_ID         VARCHAR(36),
    MODEL_ID          VARCHAR(36),
    APPROVED_VERSION  NUMBER(6,0),
    OPEN_VERSION      NUMBER(6,0),
    DEFAULT_REPO_ID   VARCHAR(36),
    CREATED_BY        VARCHAR(256)   DEFAULT CURRENT_USER(),
    CREATED_AT        TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_BY        VARCHAR(256),
    UPDATED_AT        TIMESTAMP_LTZ,
    CONSTRAINT PK_DQ_SUITE PRIMARY KEY (SUITE_ID),
    CONSTRAINT UQ_DQ_SUITE_TABLE UNIQUE (TABLE_NAME)
);

CREATE TABLE IF NOT EXISTS {{database}}.GOVERNANCE.DQ_SUITE_VERSION (
    SUITE_ID           VARCHAR(36)    NOT NULL,
    VERSION            NUMBER(6,0)    NOT NULL,
    STATUS             VARCHAR(16)    NOT NULL,
    BASE_VERSION       NUMBER(6,0),
    CHECKS             VARIANT,
    ETAG               VARCHAR(64),
    AUTHORS            ARRAY,
    ORIGIN             VARCHAR(16),
    CHANGE_NOTE        VARCHAR(2000),
    MODEL_ID           VARCHAR(36),
    MODEL_VERSION      NUMBER(6,0),
    SUBMITTED_BY       VARCHAR(256),
    SUBMITTED_AT       TIMESTAMP_LTZ,
    APPROVED_BY        VARCHAR(256),
    APPROVED_AT        TIMESTAMP_LTZ,
    REJECTED_BY        VARCHAR(256),
    REJECTED_AT        TIMESTAMP_LTZ,
    REJECT_REASON      VARCHAR(2000),
    REVIEW_SUMMARY     VARIANT,
    CATALOG_SYNCED_AT  TIMESTAMP_LTZ,
    REPO_ID            VARCHAR(36),
    BRANCH             VARCHAR(256),
    PR_URL             VARCHAR(1000),
    PR_NUMBER          NUMBER(10,0),
    PR_STATE           VARCHAR(16),
    MERGED_SHA         VARCHAR(64),
    PR_SYNCED_AT       TIMESTAMP_LTZ,
    PUBLISH_RESULT     VARIANT,
    CREATED_BY         VARCHAR(256)   DEFAULT CURRENT_USER(),
    CREATED_AT         TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT         TIMESTAMP_LTZ,
    CONSTRAINT PK_DQ_SUITE_VERSION PRIMARY KEY (SUITE_ID, VERSION)
);

GRANT SELECT ON TABLE {{database}}.GOVERNANCE.DQ_SUITE TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.GOVERNANCE.DQ_SUITE_VERSION TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.GOVERNANCE.DQ_SUITE TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {{database}}.GOVERNANCE.DQ_SUITE_VERSION TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.GOVERNANCE.DQ_SUITE TO DATABASE ROLE {{database}}.REVIEWER;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {{database}}.GOVERNANCE.DQ_SUITE_VERSION TO DATABASE ROLE {{database}}.REVIEWER;
