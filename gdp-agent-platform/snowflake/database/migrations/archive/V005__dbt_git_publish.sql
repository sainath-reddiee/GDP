-- V005: read whole files from Snowflake git repository clones, and record GitHub branch/PR publications.

-- One record per file: no field or record splitting, so dbt SQL/YAML with commas reads back intact.
CREATE FILE FORMAT IF NOT EXISTS {{database}}.CODEGEN.RAW_TEXT_FORMAT
  TYPE = CSV
  FIELD_DELIMITER = NONE
  RECORD_DELIMITER = NONE
  ESCAPE_UNENCLOSED_FIELD = NONE
  FIELD_OPTIONALLY_ENCLOSED_BY = NONE
  COMMENT = 'Read a whole text file as one value (git skeleton fetch)';

CREATE TABLE IF NOT EXISTS {{database}}.CODEGEN.GIT_PUBLICATION (
    PUBLICATION_ID  VARCHAR(36)   NOT NULL,
    RUN_ID          VARCHAR(36)   NOT NULL,
    GENERATION_ID   VARCHAR(36),
    PROVIDER        VARCHAR(32)   NOT NULL,
    ORIGIN          VARCHAR(1024),
    BASE_BRANCH     VARCHAR(256),
    HEAD_BRANCH     VARCHAR(256),
    COMMIT_SHA      VARCHAR(64),
    FILES_PUSHED    NUMBER(6,0),
    PR_NUMBER       NUMBER(10,0),
    PR_URL          VARCHAR(1024),
    DBT_PROJECT     VARCHAR(512),
    STATUS          VARCHAR(32)   NOT NULL,
    DETAIL          VARCHAR(4000),
    CREATED_BY      VARCHAR(256)  NOT NULL,
    CREATED_AT      TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_GIT_PUBLICATION PRIMARY KEY (PUBLICATION_ID)
);
