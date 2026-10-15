-- V053 Pipeline 2 (post-MDM silver to gold) and end-to-end lineage.
-- GOVERNANCE.DOMAIN_MODEL.LAYER: a domain has a SILVER model (the runs' targets, as before) and may have a GOLD model
--   (facts, dimensions, bridges), each versioned and approved in the Model Workbench. Unique per (DOMAIN_ID, LAYER).
-- CORE.GOLD_BUILD: one gold build of a domain: its inputs (post-MDM silver or consumer silver hubs), the proposed gold
--   model, the review state and the generated dbt models. Its own small step state, apart from the run workflow.
-- GOVERNANCE.GOLD_MAPPING_LINE: where every gold column of a build comes from (a silver column, a resolution through
--   the MDM cross-reference, a derivation, or an audit column).
-- GOVERNANCE.LINEAGE_EDGE: column- and entity-level lineage across layers (source, bronze, silver pre-MDM, MDM,
--   silver post-MDM, gold), written by Silver and Gold code generation and by the mastered-domain contracts.
-- Applied once; never edit after it has been applied. Add a new V### instead.
ALTER TABLE {{database}}.GOVERNANCE.DOMAIN_MODEL ADD COLUMN IF NOT EXISTS LAYER VARCHAR(16) DEFAULT 'SILVER';
UPDATE {{database}}.GOVERNANCE.DOMAIN_MODEL SET LAYER = 'SILVER' WHERE LAYER IS NULL;
ALTER TABLE {{database}}.GOVERNANCE.DOMAIN_MODEL DROP CONSTRAINT UQ_DOMAIN_MODEL_DOMAIN;
ALTER TABLE {{database}}.GOVERNANCE.DOMAIN_MODEL ADD CONSTRAINT UQ_DOMAIN_MODEL_DOMAIN_LAYER UNIQUE (DOMAIN_ID, LAYER);

CREATE TABLE IF NOT EXISTS {{database}}.CORE.GOLD_BUILD (
    BUILD_ID        VARCHAR(36)    NOT NULL,
    DOMAIN_ID       VARCHAR(36)    NOT NULL,
    DOMAIN_NAME     VARCHAR(128)   NOT NULL,
    NAME            VARCHAR(256)   NOT NULL,
    STEP            VARCHAR(16)    DEFAULT 'INPUTS',     -- INPUTS | MODELING | REVIEW | CODE | DONE
    STATUS          VARCHAR(16)    DEFAULT 'DRAFT',      -- DRAFT | PROPOSED | IN_REVIEW | APPROVED | GENERATED | CANCELLED
    INPUTS          VARIANT,                             -- [{database, schema, table, layer, role}]
    PROPOSAL        VARIANT,                             -- the proposed gold specification (spec v2) and its notes
    MODEL_ID        VARCHAR(36),                         -- the domain's GOLD model the proposal was put into
    MODEL_VERSION   NUMBER(6,0),
    GENERATION      VARIANT,                             -- {generation_version, files: [{path, kind, lines}], stage_path}
    NOTE            VARCHAR(4000),
    CREATED_BY      VARCHAR(256)   DEFAULT CURRENT_USER(),
    CREATED_AT      TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_BY      VARCHAR(256),
    UPDATED_AT      TIMESTAMP_LTZ,
    CONSTRAINT PK_GOLD_BUILD PRIMARY KEY (BUILD_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.GOVERNANCE.GOLD_MAPPING_LINE (
    BUILD_ID        VARCHAR(36)    NOT NULL,
    POSITION        NUMBER(6,0)    NOT NULL,
    GOLD_TABLE      VARCHAR(256)   NOT NULL,
    GOLD_COLUMN     VARCHAR(256)   NOT NULL,
    KIND            VARCHAR(16)    NOT NULL,             -- COLUMN | RESOLVE | DERIVED | KEY | AUDIT
    SOURCE_LAYER    VARCHAR(32),                         -- SILVER_PRE_MDM | SILVER_POST_MDM | MDM | GOLD
    SOURCE_TABLE    VARCHAR(512),
    SOURCE_COLUMN   VARCHAR(256),
    EXPRESSION      VARCHAR(8000),
    NOTE            VARCHAR(2000),
    CONSTRAINT PK_GOLD_MAPPING_LINE PRIMARY KEY (BUILD_ID, GOLD_TABLE, GOLD_COLUMN)
);

CREATE TABLE IF NOT EXISTS {{database}}.GOVERNANCE.LINEAGE_EDGE (
    EDGE_ID         VARCHAR(64)    NOT NULL,             -- stable hash of the endpoints and the producer
    FROM_LAYER      VARCHAR(32)    NOT NULL,             -- SOURCE | BRONZE | SILVER_PRE_MDM | MDM | SILVER_POST_MDM | GOLD
    FROM_OBJECT     VARCHAR(512)   NOT NULL,             -- DATABASE.SCHEMA.TABLE (or SCHEMA.TABLE for env-independent hops)
    FROM_COLUMN     VARCHAR(256),                        -- NULL for an entity-level edge
    TO_LAYER        VARCHAR(32)    NOT NULL,
    TO_OBJECT       VARCHAR(512)   NOT NULL,
    TO_COLUMN       VARCHAR(256),
    KIND            VARCHAR(16)    NOT NULL,             -- INGEST | MAP | MDM_MATCH | MDM_GOLDEN | RESOLVE | BUILD
    EXPRESSION      VARCHAR(8000),
    PRODUCER        VARCHAR(16)    NOT NULL,             -- SILVER_RUN | GOLD_BUILD | MDM_CONTRACT
    PRODUCER_ID     VARCHAR(64),                         -- run id, build id or domain name
    GENERATION      NUMBER(6,0),
    DOMAIN_ID       VARCHAR(36),
    IS_CURRENT      BOOLEAN        DEFAULT TRUE,
    CREATED_AT      TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_LINEAGE_EDGE PRIMARY KEY (EDGE_ID)
);

GRANT SELECT ON TABLE {{database}}.CORE.GOLD_BUILD TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.GOVERNANCE.GOLD_MAPPING_LINE TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.GOVERNANCE.LINEAGE_EDGE TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.GOLD_BUILD TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {{database}}.GOVERNANCE.GOLD_MAPPING_LINE TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {{database}}.GOVERNANCE.LINEAGE_EDGE TO DATABASE ROLE {{database}}.DATA_ENGINEER;
