-- V074: a run maps to every active entity of its domain model (a hub and its spokes), not one table.
--   CORE.RUN_TARGET: the run's target set, written when the run is pinned to an approved domain model version
--     (services/modeling/run_link.py pin_run); hub first. An entity the model holds without columns, or that the
--     registry does not have active yet, is kept with INCLUDED = FALSE and the reason, so the page can say why.
--     A new pin marks the previous set IS_CURRENT = FALSE; nothing is deleted.
--   TARGET_TABLE_ID on CORE.MAPPING_CANDIDATE and CORE.MAPPING_DECISION: which table a candidate ranks in and a
--     decision loads into, so one source column can feed the hub and each spoke (ranks and decisions per table).
-- WORKFLOW_RUN.TARGET_MODEL keeps the hub, so single-target readers are unchanged. New columns are nullable with no
-- default: existing rows and insert statements are unchanged.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

CREATE TABLE IF NOT EXISTS {{database}}.CORE.RUN_TARGET (
    RUN_TARGET_ID        VARCHAR(36)    NOT NULL,
    RUN_ID               VARCHAR(36)    NOT NULL,
    MODEL_ID             VARCHAR(36)    NOT NULL,
    MODEL_VERSION        NUMBER(10,0)   NOT NULL,
    ENTITY_NAME          VARCHAR(256)   NOT NULL,
    TARGET_FQN           VARCHAR(512)   NOT NULL,       -- DB.SCHEMA.TABLE
    TARGET_TABLE_ID      VARCHAR(36),                   -- CORE.TARGET_TABLE_REGISTRY; NULL when not registered
    ROLE                 VARCHAR(16)    NOT NULL,       -- HUB | SPOKE | TABLE (the entity's table type)
    ORDINAL              NUMBER(4,0)    NOT NULL,       -- hub first, then the pinned entity, then by name
    INCLUDED             BOOLEAN        NOT NULL,
    EXCLUDED_REASON      VARCHAR(1000),
    IS_CURRENT           BOOLEAN        NOT NULL,
    CREATED_BY           VARCHAR(256)   DEFAULT CURRENT_USER(),
    CREATED_AT           TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_RUN_TARGET PRIMARY KEY (RUN_TARGET_ID)
);

ALTER TABLE {{database}}.CORE.MAPPING_CANDIDATE ADD COLUMN IF NOT EXISTS TARGET_TABLE_ID VARCHAR(36);
ALTER TABLE {{database}}.CORE.MAPPING_DECISION ADD COLUMN IF NOT EXISTS TARGET_TABLE_ID VARCHAR(36);

GRANT SELECT ON TABLE {{database}}.CORE.RUN_TARGET TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.RUN_TARGET TO DATABASE ROLE {{database}}.DATA_ENGINEER;
