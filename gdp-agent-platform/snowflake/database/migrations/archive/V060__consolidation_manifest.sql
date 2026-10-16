-- V060 consolidation manifest: the record of moving every platform object into CORE (V061).
-- infrastructure/consolidation/baseline.py writes one row per object before the move (row count, full-row hash,
-- object id, grants, policies, stage files, sequence position, task state); verify.py writes the checks after it;
-- rollback.py reads the rows to rename everything back. Additive only; safe to apply long before V061.
-- Applied once; never edit after it has been applied to an environment - add a new V### instead.

CREATE TABLE IF NOT EXISTS {{database}}.CORE.CONSOLIDATION_RUN (
    RUN_ID         VARCHAR(36)   NOT NULL,
    DATABASE_NAME  VARCHAR(256)  NOT NULL,
    PHASE          VARCHAR(16)   NOT NULL,   -- BASELINE | MOVE | VERIFY | ROLLBACK
    STATUS         VARCHAR(16)   NOT NULL,   -- RUNNING | COMPLETED | FAILED
    REHEARSAL      BOOLEAN       DEFAULT FALSE,
    DETAILS        VARIANT,
    CREATED_BY     VARCHAR(256)  DEFAULT CURRENT_USER(),
    STARTED_AT     TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    FINISHED_AT    TIMESTAMP_LTZ,
    CONSTRAINT PK_CONSOLIDATION_RUN PRIMARY KEY (RUN_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.CORE.CONSOLIDATION_OBJECT (
    RUN_ID       VARCHAR(36)   NOT NULL,     -- the BASELINE run
    OBJECT_TYPE  VARCHAR(32)   NOT NULL,     -- TABLE | VIEW | STAGE | FILE FORMAT | SEQUENCE | ROW ACCESS POLICY | ...
    OLD_FQN      VARCHAR(800)  NOT NULL,
    NEW_FQN      VARCHAR(800)  NOT NULL,
    METHOD       VARCHAR(16)   NOT NULL,     -- RENAME | RECREATE | RUNTIME | KEEP
    BASELINE     VARIANT,                    -- captured before the move
    AFTER        VARIANT,                    -- captured after the move
    STATUS       VARCHAR(16)   NOT NULL,     -- BASELINED | MOVED | VERIFIED | FAILED | ROLLED_BACK
    UPDATED_AT   TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_CONSOLIDATION_OBJECT PRIMARY KEY (RUN_ID, OBJECT_TYPE, OLD_FQN)
);

CREATE TABLE IF NOT EXISTS {{database}}.CORE.CONSOLIDATION_CHECK (
    CHECK_RUN_ID  VARCHAR(36)   NOT NULL,    -- the VERIFY run
    OLD_FQN       VARCHAR(800)  NOT NULL,
    CHECK_NAME    VARCHAR(64)   NOT NULL,    -- ROW_COUNT | HASH | OBJECT_ID | TIME_TRAVEL | FILES | GRANTS | ...
    EXPECTED      VARIANT,
    ACTUAL        VARIANT,
    PASSED        BOOLEAN       NOT NULL,
    CHECKED_AT    TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_CONSOLIDATION_CHECK PRIMARY KEY (CHECK_RUN_ID, OLD_FQN, CHECK_NAME)
);

GRANT SELECT ON TABLE {{database}}.CORE.CONSOLIDATION_RUN TO DATABASE ROLE {{database}}.PLATFORM_ADMIN;
GRANT SELECT ON TABLE {{database}}.CORE.CONSOLIDATION_OBJECT TO DATABASE ROLE {{database}}.PLATFORM_ADMIN;
GRANT SELECT ON TABLE {{database}}.CORE.CONSOLIDATION_CHECK TO DATABASE ROLE {{database}}.PLATFORM_ADMIN;
