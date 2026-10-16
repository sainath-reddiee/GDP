-- V064: the Airflow task catalogue, part of the Airflow knowledge pack (services/ops/catalog.py). One row per task of
-- every captured DAG: its operator, its neighbours, the SQL it runs and the tables it reads and writes, read from the
-- Airflow REST API (the DAG source and its task list), so the root-cause analysis and the agent's proposals know what
-- a failed task does without the log having to say it. Rebuilt only for DAGs whose source changed (SOURCE_HASH).
-- Push-first capture: TASK_RUN.EXCERPT_SOURCE says whether an error excerpt came from a push (short) or the log.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

CREATE TABLE IF NOT EXISTS {{database}}.CORE.DAG_TASK (
    ENV_ID              VARCHAR(64)    NOT NULL,
    DAG_ID              VARCHAR(250)   NOT NULL,
    TASK_ID             VARCHAR(250)   NOT NULL,
    OPERATOR            VARCHAR(250),
    UPSTREAM            ARRAY,                       -- task ids this task waits for
    DOWNSTREAM          ARRAY,
    SQL_TEXT            VARCHAR(8000),               -- the statements the task runs, as written in the DAG source
    READS               ARRAY,                       -- tables and stages read, as named in the SQL (DB.SCHEMA.NAME or SCHEMA.NAME)
    WRITES              ARRAY,                       -- tables written (INSERT, MERGE, CREATE, UPDATE, DELETE, COPY INTO)
    DBT_SELECTORS       ARRAY,
    SNAPLOGIC_PIPELINE  VARCHAR(1000),
    SOURCE_PATH         VARCHAR(2000),
    SOURCE_HASH         VARCHAR(64),
    UPDATED_AT          TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_DAG_TASK PRIMARY KEY (ENV_ID, DAG_ID, TASK_ID)
);

ALTER TABLE {{database}}.CORE.DAG ADD COLUMN IF NOT EXISTS SOURCE_HASH VARCHAR(64);
ALTER TABLE {{database}}.CORE.DAG ADD COLUMN IF NOT EXISTS CATALOGED_AT TIMESTAMP_LTZ;
ALTER TABLE {{database}}.CORE.TASK_RUN ADD COLUMN IF NOT EXISTS EXCERPT_SOURCE VARCHAR(8);   -- PUSH | LOG

GRANT SELECT ON TABLE {{database}}.CORE.DAG_TASK TO DATABASE ROLE {{database}}.OPS_VIEWER;
GRANT INSERT, UPDATE, DELETE ON TABLE {{database}}.CORE.DAG_TASK TO DATABASE ROLE {{database}}.OPS_SERVICE;
