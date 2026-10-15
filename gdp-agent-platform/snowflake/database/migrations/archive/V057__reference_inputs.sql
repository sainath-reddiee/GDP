-- V057 Reference inputs: anything a person gives the platform about a domain and a layer, so the engine decides better.
-- KNOWLEDGE.REFERENCE_INPUT: one input per row: its layer (SILVER, MDM, GOLD or ANY), what it is (DATA_MODEL, MDM_SCHEMA,
--   STTM, DBT_CODE, SAMPLE_ROWS, DOCUMENT), where it came from (an upload kept on @CONFLUENCE.DOCUMENT_FILES, a live
--   schema, a Git repository, a Confluence page), what it was read as (PARSED) and what it changed (APPLIED).
-- KNOWLEDGE.REFERENCE_SCORE: how close the platform's output (a proposed model, a mapping, an STTM, code) came to the
--   layer's reference, each time it was measured: the trend shows whether changes to skills, knowledge, rules and
--   conventions made decisions better.
-- CONFLUENCE.SOURCE.LAYER: a document read for knowledge proposes it for that layer.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.
CREATE TABLE IF NOT EXISTS {{database}}.KNOWLEDGE.REFERENCE_INPUT (
    REFERENCE_ID    VARCHAR(36)    NOT NULL,
    DOMAIN_ID       VARCHAR(36)    NOT NULL,
    LAYER           VARCHAR(8)     DEFAULT 'ANY',
    KIND            VARCHAR(16)    NOT NULL,
    TITLE           VARCHAR(1024),
    ORIGIN          VARCHAR(16)    NOT NULL,      -- UPLOAD | SCHEMA | GIT | CONFLUENCE
    SOURCE_POINTER  VARCHAR(2048),                -- the stage path, DATABASE.SCHEMA, the repository or the document id
    FILE_SHA256     VARCHAR(64),
    STATUS          VARCHAR(16)    DEFAULT 'QUEUED',   -- QUEUED | READING | READY | FAILED | WITHDRAWN
    NOTE            VARCHAR(4000),
    PARSED          VARIANT,
    APPLIED         VARIANT,
    CREATED_BY      VARCHAR(256)   DEFAULT CURRENT_USER(),
    CREATED_AT      TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT      TIMESTAMP_LTZ,
    CONSTRAINT PK_REFERENCE_INPUT PRIMARY KEY (REFERENCE_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.KNOWLEDGE.REFERENCE_SCORE (
    SCORE_ID        VARCHAR(36)    NOT NULL,
    DOMAIN_ID       VARCHAR(36)    NOT NULL,
    LAYER           VARCHAR(8)     NOT NULL,
    KIND            VARCHAR(16)    NOT NULL,      -- what was compared: DATA_MODEL, STTM, DBT_CODE
    REFERENCE_ID    VARCHAR(36),
    RUN_ID          VARCHAR(36),
    STAGE           VARCHAR(32),
    SCORE           VARIANT,                      -- {pct, matched, missing, extra, details}
    SCORED_BY       VARCHAR(256)   DEFAULT CURRENT_USER(),
    SCORED_AT       TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_REFERENCE_SCORE PRIMARY KEY (SCORE_ID)
);

ALTER TABLE {{database}}.CONFLUENCE.SOURCE ADD COLUMN IF NOT EXISTS LAYER VARCHAR(8) DEFAULT 'ANY';

GRANT SELECT ON TABLE {{database}}.KNOWLEDGE.REFERENCE_INPUT TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.KNOWLEDGE.REFERENCE_SCORE TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.KNOWLEDGE.REFERENCE_INPUT TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.KNOWLEDGE.REFERENCE_INPUT TO DATABASE ROLE {{database}}.DATA_STEWARD;
GRANT SELECT, INSERT ON TABLE {{database}}.KNOWLEDGE.REFERENCE_SCORE TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT ON TABLE {{database}}.KNOWLEDGE.REFERENCE_SCORE TO DATABASE ROLE {{database}}.DATA_STEWARD;
