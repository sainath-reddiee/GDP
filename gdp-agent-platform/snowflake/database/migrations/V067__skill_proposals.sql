-- V067: the skill scout (services/knowledge/scout.py, scout_run.py). Each week it looks for knowledge that was approved
-- and used again and again, generalises it (client names replaced by placeholders), drafts a reusable skill from it,
-- tests the draft against what the stage uses today, and files it here: PROPOSED with a DRAFT version in
-- CORE.SKILL_REGISTRY (no label, so nothing loads it until a person promotes it), or DISCARDED with the reason.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

CREATE TABLE IF NOT EXISTS {{database}}.CORE.SKILL_PROPOSAL (
    PROPOSAL_ID      VARCHAR(36)    NOT NULL,
    CLUSTER_KEY      VARCHAR(256)   NOT NULL,       -- type and theme of the knowledge it came from (dedupe key)
    SOURCE           VARCHAR(16)    DEFAULT 'KNOWLEDGE',
    STATUS           VARCHAR(16)    NOT NULL,       -- PROPOSED | DISCARDED | DISMISSED
    SKILL_ID         VARCHAR(36),                   -- the DRAFT version filed in CORE.SKILL_REGISTRY
    SKILL_NAME       VARCHAR(128),
    TITLE            VARCHAR(512),
    STAGE            VARCHAR(32),                   -- the pipeline stage it would serve
    EVIDENCE_COUNT   NUMBER(6,0),
    DOMAIN_COUNT     NUMBER(4,0),
    EVIDENCE         VARIANT,                       -- [{knowledge_id, lineage_id, title, domain, uses, helpfulness}]
    REPLACEMENTS     VARIANT,                       -- [{from_kind, placeholder, count}] (names never stored)
    TRANSFER_SCORE   NUMBER(5,1),                   -- 0..100: how well it carries to another client
    TRANSFER_LABEL   VARCHAR(32),
    EVAL             VARIANT,                       -- candidate versus baseline, from the evaluation
    ISSUES           VARIANT,                       -- the deterministic checks
    DISCARD_REASON   VARCHAR(2000),
    MODEL            VARCHAR(128),
    DISMISSED_BY     VARCHAR(256),
    DISMISSED_AT     TIMESTAMP_LTZ,
    DISMISS_NOTE     VARCHAR(2000),
    CREATED_BY       VARCHAR(256)   DEFAULT CURRENT_USER(),
    CREATED_AT       TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT       TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_SKILL_PROPOSAL PRIMARY KEY (PROPOSAL_ID)
);

GRANT SELECT ON TABLE {{database}}.CORE.SKILL_PROPOSAL TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.CORE.SKILL_PROPOSAL TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT INSERT, UPDATE ON TABLE {{database}}.CORE.SKILL_PROPOSAL TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT ON TABLE {{database}}.CORE.SKILL_PROPOSAL TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT INSERT, UPDATE ON TABLE {{database}}.CORE.SKILL_PROPOSAL TO DATABASE ROLE {{database}}.OPS_SERVICE;
-- the worker files DRAFT versions only (it never moves a label), and reads the knowledge and skills it compares with
GRANT SELECT, INSERT ON TABLE {{database}}.CORE.SKILL_REGISTRY TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT SELECT ON TABLE {{database}}.CORE.SKILL_LABEL TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT SELECT ON TABLE {{database}}.CORE.SKILL_CATEGORY TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT SELECT ON TABLE {{database}}.CORE.SKILL_STAGE_BINDING TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT SELECT ON TABLE {{database}}.CORE.KNOWLEDGE_OUTCOME TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT SELECT ON TABLE {{database}}.CORE.KNOWLEDGE_USAGE TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT SELECT ON TABLE {{database}}.CORE.TARGET_TABLE_REGISTRY TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT SELECT ON TABLE {{database}}.CORE.SOURCE_REGISTRY TO DATABASE ROLE {{database}}.OPS_SERVICE;
GRANT SELECT ON TABLE {{database}}.CORE.DOMAIN_REGISTRY TO DATABASE ROLE {{database}}.OPS_SERVICE;
