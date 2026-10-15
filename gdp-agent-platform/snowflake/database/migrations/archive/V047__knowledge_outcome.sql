-- V047 Knowledge outcomes: whether the knowledge put into a prompt led to output a reviewer accepted.
-- Applied once; never edit after it has been applied. Add a new V### instead.
--
-- KNOWLEDGE.KNOWLEDGE_OUTCOME  one row per knowledge lineage and stage, rebuilt by services/knowledge/outcomes.py from
--                              KNOWLEDGE.KNOWLEDGE_USAGE (what went into a prompt, per run) and the reviewers'
--                              decisions on what the stage proposed in that run:
--                                MAPPING  MAPPING.MAPPING_DECISION against the rank-1 candidate per target column
--                                SODA     CONTRACT.SODA_EXPECTATION_REGISTRY review status per target column
--                              An item written for one target column is judged on that column's decision; an item
--                              without one on the run's decisions for the stage as a whole (one observation per run).
--                              HELPFULNESS = (accepted + corrected / 2 + 1) / (observed + 2), so a handful of
--                              observations never swings it to 0 or 1. Ranking (services/knowledge/select.py) and
--                              mapping history weights read it.
-- Every statement is safe to run again (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS {{database}}.KNOWLEDGE.KNOWLEDGE_OUTCOME (
    LINEAGE_ID     VARCHAR(64)    NOT NULL,
    STAGE          VARCHAR(32)    NOT NULL,
    KNOWLEDGE_ID   VARCHAR(36),               -- the newest version that was used
    USES           NUMBER(10,0),              -- times put into a prompt
    RUNS           NUMBER(10,0),              -- distinct runs it was used in
    OBSERVED       NUMBER(12,2),              -- decisions attributed to it (a run-level observation counts once)
    ACCEPTED       NUMBER(12,2),
    CORRECTED      NUMBER(12,2),
    REJECTED       NUMBER(12,2),
    HELPFULNESS    FLOAT,
    LAST_USED_AT   TIMESTAMP_LTZ,
    COMPUTED_AT    TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_KNOWLEDGE_OUTCOME PRIMARY KEY (LINEAGE_ID, STAGE)
);

GRANT SELECT ON TABLE {{database}}.KNOWLEDGE.KNOWLEDGE_OUTCOME TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {{database}}.KNOWLEDGE.KNOWLEDGE_OUTCOME TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {{database}}.KNOWLEDGE.KNOWLEDGE_OUTCOME TO DATABASE ROLE {{database}}.REVIEWER;
