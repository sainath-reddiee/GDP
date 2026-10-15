-- V058: the platform learns from its own use.
-- KNOWLEDGE.OPEN_QUESTION: what the engine asks a domain's owner where it is unsure (a low-confidence mapping, an
--   assumption a proposal states); the answer becomes knowledge every later run uses. One question per subject.
-- KNOWLEDGE.CHANGE_EVAL: a knowledge change (a learned rule, or any proposal in the inbox) replayed on the domain's
--   past runs against their approved mappings: accuracy before and after, the verdict and the columns that changed.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.
CREATE TABLE IF NOT EXISTS {{database}}.KNOWLEDGE.OPEN_QUESTION (
    QUESTION_ID     VARCHAR(36)    NOT NULL,
    DOMAIN_ID       VARCHAR(36)    NOT NULL,
    LAYER           VARCHAR(8)     DEFAULT 'SILVER',
    RUN_ID          VARCHAR(36),
    STAGE           VARCHAR(32),
    SUBJECT_KEY     VARCHAR(1024)  NOT NULL,      -- mapping.<TARGET>.<SOURCE TABLE>.<COLUMN> or note.<stage>.<hash>
    KIND            VARCHAR(16)    NOT NULL,      -- MAPPING | NOTE
    QUESTION        VARCHAR(4000)  NOT NULL,
    CONTEXT         VARIANT,
    OPTIONS         VARIANT,
    STATUS          VARCHAR(16)    DEFAULT 'OPEN', -- OPEN | ANSWERED | DISMISSED
    ANSWER          VARIANT,
    ANSWERED_BY     VARCHAR(256),
    ANSWERED_AT     TIMESTAMP_LTZ,
    KNOWLEDGE_ID    VARCHAR(36),
    CREATED_AT      TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_OPEN_QUESTION PRIMARY KEY (QUESTION_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.KNOWLEDGE.CHANGE_EVAL (
    EVAL_ID         VARCHAR(36)    NOT NULL,
    DOMAIN_ID       VARCHAR(36)    NOT NULL,
    LAYER           VARCHAR(8)     NOT NULL,
    SUBJECT_KIND    VARCHAR(16)    NOT NULL,      -- LEARNED (a learned rule's key) | KNOWLEDGE (a knowledge id)
    SUBJECT_ID      VARCHAR(1024)  NOT NULL,
    VERDICT         VARCHAR(16),                  -- BETTER | SAME | WORSE | NO_EVIDENCE
    RESULT          VARIANT,
    CREATED_BY      VARCHAR(256)   DEFAULT CURRENT_USER(),
    CREATED_AT      TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_CHANGE_EVAL PRIMARY KEY (EVAL_ID)
);

GRANT SELECT ON TABLE {{database}}.KNOWLEDGE.OPEN_QUESTION TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.KNOWLEDGE.CHANGE_EVAL TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.KNOWLEDGE.OPEN_QUESTION TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.KNOWLEDGE.OPEN_QUESTION TO DATABASE ROLE {{database}}.DATA_STEWARD;
GRANT SELECT, INSERT ON TABLE {{database}}.KNOWLEDGE.CHANGE_EVAL TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT ON TABLE {{database}}.KNOWLEDGE.CHANGE_EVAL TO DATABASE ROLE {{database}}.DATA_STEWARD;
