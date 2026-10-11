-- V041 Profiling Store governance: scheduled profiling and reviewed column annotations.
-- Applied once; never edit after it has been applied. Add a new V### instead.
--
-- GOVERNANCE.PROFILE_SCHEDULE    one row per scheduled set of tables (a database.schema, all of its tables or a list):
--                                a Snowflake task PROFILE.PROF_<hash> calls PROFILE.RUN_SCHEDULED_PROFILE on a cron
--                                and the schedule keeps the last run's outcome, including the tables that drifted.
-- GOVERNANCE.PROFILE_ANNOTATION  what people know about a column that the profiler cannot tell: description, business
--                                definition, PII class, semantic type, accepted values. A proposal is approved by
--                                another person (four eyes); the approved annotation overrides the profiler's guess in
--                                every profile view and, when the table is an entity of an approved domain model, is
--                                proposed to that model (GOVERNANCE.MODEL_CHANGE with SOURCE 'ANNOTATION').
-- GOVERNANCE.MODEL_CHANGE        gains SOURCE (RUN or ANNOTATION), ANNOTATION_ID and PATCH (the column fields an
--                                annotation sets, applied to the open draft when merged).
-- Every statement is safe to run again (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS {{database}}.GOVERNANCE.PROFILE_SCHEDULE (
    SCHEDULE_ID       VARCHAR(36)    NOT NULL,
    SOURCE_SYSTEM_ID  VARCHAR(36)    NOT NULL,
    DATABASE_NAME     VARCHAR(256)   NOT NULL,
    SCHEMA_NAME       VARCHAR(256)   NOT NULL,
    TABLES            ARRAY,                       -- NULL or empty: every table of the schema
    CRON              VARCHAR(64)    NOT NULL,
    FORCE_REFRESH     BOOLEAN        DEFAULT FALSE,
    TASK_NAME         VARCHAR(512),
    ENABLED           BOOLEAN        DEFAULT TRUE,
    LAST_RUN_AT       TIMESTAMP_LTZ,
    LAST_STATUS       VARCHAR(16),                 -- OK | PARTIAL | FAILED
    LAST_RESULT       VARIANT,                     -- profiled, cached, failed, drifted tables
    CREATED_BY        VARCHAR(256)   DEFAULT CURRENT_USER(),
    CREATED_AT        TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT        TIMESTAMP_LTZ,
    CONSTRAINT PK_PROFILE_SCHEDULE PRIMARY KEY (SCHEDULE_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.GOVERNANCE.PROFILE_ANNOTATION (
    ANNOTATION_ID        VARCHAR(36)    NOT NULL,
    TABLE_FQN            VARCHAR(800)   NOT NULL,
    COLUMN_NAME          VARCHAR(256)   NOT NULL,
    DESCRIPTION          VARCHAR(4000),
    BUSINESS_DEFINITION  VARCHAR(4000),
    PII_CLASSIFICATION   VARCHAR(64),
    SEMANTIC_TYPE        VARCHAR(64),
    ACCEPTED_VALUES      ARRAY,
    DETECTED             VARIANT,                  -- what the profiler said when the annotation was proposed
    NOTE                 VARCHAR(2000),
    STATUS               VARCHAR(16)    NOT NULL,  -- PROPOSED | APPROVED | REJECTED | SUPERSEDED | WITHDRAWN
    PROPOSED_BY          VARCHAR(256)   DEFAULT CURRENT_USER(),
    PROPOSED_AT          TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    DECIDED_BY           VARCHAR(256),
    DECIDED_AT           TIMESTAMP_LTZ,
    DECISION_NOTE        VARCHAR(2000),
    MODEL_ID             VARCHAR(36),
    MODEL_CHANGE_ID      VARCHAR(36),
    CONSTRAINT PK_PROFILE_ANNOTATION PRIMARY KEY (ANNOTATION_ID)
);

ALTER TABLE {{database}}.GOVERNANCE.MODEL_CHANGE ADD COLUMN IF NOT EXISTS SOURCE VARCHAR(16);
ALTER TABLE {{database}}.GOVERNANCE.MODEL_CHANGE ADD COLUMN IF NOT EXISTS ANNOTATION_ID VARCHAR(36);
ALTER TABLE {{database}}.GOVERNANCE.MODEL_CHANGE ADD COLUMN IF NOT EXISTS PATCH VARIANT;

GRANT SELECT ON TABLE {{database}}.GOVERNANCE.PROFILE_SCHEDULE TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.GOVERNANCE.PROFILE_ANNOTATION TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {{database}}.GOVERNANCE.PROFILE_SCHEDULE TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.GOVERNANCE.PROFILE_ANNOTATION TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.GOVERNANCE.PROFILE_ANNOTATION TO DATABASE ROLE {{database}}.REVIEWER;
GRANT INSERT ON TABLE {{database}}.GOVERNANCE.MODEL_CHANGE TO DATABASE ROLE {{database}}.REVIEWER;
