-- Target model designs per run: AI-proposed or edited by the developer, one row per version. Approving a version
-- registers its entities as targets (KNOWLEDGE.TARGET_TABLE_REGISTRY + MODEL_SPEC) and stores MODEL_DEFINITION.
CREATE SCHEMA IF NOT EXISTS {{database}}.MODELING COMMENT = 'Target model designs';

CREATE TABLE IF NOT EXISTS {{database}}.MODELING.MODEL_DESIGN (
    DESIGN_ID     VARCHAR(36)   NOT NULL,
    RUN_ID        VARCHAR(36)   NOT NULL,
    VERSION       NUMBER(6,0)   NOT NULL,
    STATUS        VARCHAR(16)   NOT NULL,   -- DRAFT | APPROVED | SUPERSEDED | REJECTED
    ORIGIN        VARCHAR(16),              -- REGISTERED (1:1 baseline) | AI | USER
    DECISION      VARCHAR(32),              -- REUSE_EXISTING | EXTEND_EXISTING | NEW
    DESIGN_JSON   VARIANT,
    CONVENTIONS   VARIANT,                  -- the conventions this version follows (GDP, COMPANY, CUSTOM, NONE)
    ISSUES        VARIANT,                  -- grounding / convention problems at save time
    INSTRUCTIONS  VARCHAR(4000),
    MODEL         VARCHAR(100),             -- the Cortex model that wrote it (AI versions)
    NOTE          VARCHAR(2000),
    CREATED_BY    VARCHAR(256)  DEFAULT CURRENT_USER(),
    CREATED_AT    TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    APPROVED_BY   VARCHAR(256),
    APPROVED_AT   TIMESTAMP_LTZ,
    CONSTRAINT PK_MODEL_DESIGN PRIMARY KEY (DESIGN_ID)
);

GRANT USAGE ON SCHEMA {{database}}.MODELING TO DATABASE ROLE {{database}}.VIEWER;
GRANT USAGE ON SCHEMA {{database}}.MODELING TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON SCHEMA {{database}}.MODELING TO DATABASE ROLE {{database}}.REVIEWER;
GRANT SELECT, INSERT ON TABLE {{database}}.MODELING.MODEL_DESIGN TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT ON TABLE {{database}}.MODELING.MODEL_DESIGN TO DATABASE ROLE {{database}}.REVIEWER;
GRANT SELECT ON TABLE {{database}}.MODELING.MODEL_DESIGN TO DATABASE ROLE {{database}}.VIEWER;
