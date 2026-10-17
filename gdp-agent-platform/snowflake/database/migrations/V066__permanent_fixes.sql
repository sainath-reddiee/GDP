-- V066: permanent fixes for recurring known issues. When a published known fix keeps firing (PERMANENT_FIX_MIN_RUNS
-- times in 30 days), the agent drafts the code change that removes the cause (services/resolve/permanent.py); anyone
-- with the privilege can also ask for one. A second person accepts it, and only then a draft pull request opens. The
-- known fix stays until a person retires it after the pull request merged.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

CREATE SEQUENCE IF NOT EXISTS {{database}}.CORE.PERMANENT_FIX_SEQ START = 1 INCREMENT = 1;

CREATE TABLE IF NOT EXISTS {{database}}.CORE.PERMANENT_FIX (
    FIX_ID             VARCHAR(36)    NOT NULL,
    FIX_NUMBER         NUMBER(10,0)   DEFAULT {{database}}.CORE.PERMANENT_FIX_SEQ.NEXTVAL,  -- the branch fix/known-issue-<n>
    VERSION            NUMBER(6,0)    DEFAULT 1,       -- the CORE.GIT_PR key; a new draft is a new FIX_ID
    ISSUE_ID           VARCHAR(36)    NOT NULL,
    STATUS             VARCHAR(16)    DEFAULT 'DRAFTED',  -- UNAVAILABLE | DRAFTED | ACCEPTED | REJECTED | PUBLISHED | MERGED | CLOSED | SUPERSEDED
    TRIGGER_KIND       VARCHAR(8)     NOT NULL,        -- AUTO (the worker) | MANUAL (a person asked)
    DRAFTED_BY         VARCHAR(256)   NOT NULL,        -- PLATFORM_AGENT or the person who asked
    INSTRUCTIONS       VARCHAR(2000),                  -- what the person asked for, when MANUAL
    ENV_ID             VARCHAR(64),
    DAG_ID             VARCHAR(250),
    TASK_ID            VARCHAR(250),
    DOMAIN_ID          VARCHAR(36),
    REPO_ID            VARCHAR(36),
    BASE_BRANCH        VARCHAR(256),
    INDEXED_COMMIT     VARCHAR(40),
    FILES              VARIANT,                        -- [{path, role, base_sha256, new_content, change}]
    DIFF               VARCHAR(16777216),
    TITLE              VARCHAR(400),
    SUMMARY            VARCHAR(2000),
    RATIONALE          VARCHAR(4000),
    CONFIDENCE         FLOAT,
    CONTEXT            VARIANT,                        -- recurring stats, cause, models, files considered and skipped
    UNAVAILABLE_REASON VARCHAR(2000),
    PUBLISHABLE        BOOLEAN        DEFAULT FALSE,
    MODEL              VARCHAR(128),
    DECIDED_BY         VARCHAR(256),
    DECIDED_AT         TIMESTAMP_LTZ,
    DECISION_NOTE      VARCHAR(2000),
    PUBLISHED_BY       VARCHAR(256),
    PUBLISHED_AT       TIMESTAMP_LTZ,
    BRANCH             VARCHAR(256),
    PR_NUMBER          NUMBER(10,0),
    PR_URL             VARCHAR(1000),
    PR_STATE           VARCHAR(16),
    MERGED_SHA         VARCHAR(64),
    PR_SYNCED_AT       TIMESTAMP_LTZ,
    PUBLISH_RESULT     VARIANT,
    RETIRE_DECISION    VARCHAR(16),                    -- after a merge: RETIRED | KEPT (a person decides)
    RETIRE_DECIDED_BY  VARCHAR(256),
    RETIRE_DECIDED_AT  TIMESTAMP_LTZ,
    CREATED_AT         TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT         TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_PERMANENT_FIX PRIMARY KEY (FIX_ID)
);

ALTER TABLE {{database}}.CORE.CONTROL_PREFERENCE ADD COLUMN IF NOT EXISTS PERMANENT_FIX_ENABLED BOOLEAN DEFAULT TRUE;
ALTER TABLE {{database}}.CORE.CONTROL_PREFERENCE ADD COLUMN IF NOT EXISTS PERMANENT_FIX_MIN_RUNS NUMBER(4,0) DEFAULT 3;

GRANT SELECT ON TABLE {{database}}.CORE.PERMANENT_FIX TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT INSERT, UPDATE ON TABLE {{database}}.CORE.PERMANENT_FIX TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT USAGE ON SEQUENCE {{database}}.CORE.PERMANENT_FIX_SEQ TO DATABASE ROLE {{database}}.OPS_SERVICE;
