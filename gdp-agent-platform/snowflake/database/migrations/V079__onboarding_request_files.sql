-- V079: files on onboarding requests (apps/api/app/requests_api.py). A person asking for a domain brings what
-- describes it, in any format (a data dictionary, a model export, a glossary, a sample domain JSON, a PDF). The
-- original is kept in @CORE.DOCUMENT_FILES under requests/<request id>/; once the request's domain exists each file is
-- handed to the domain's documents (CORE.CONFLUENCE_SOURCE, KIND = 'UPLOAD'), read there, and its knowledge proposed
-- for the steward's review.
--   DOMAIN_SOURCE_ID   the domain document the file became; NULL until it is handed over
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

CREATE TABLE IF NOT EXISTS {{database}}.CORE.ONBOARDING_REQUEST_FILE (
    FILE_ID              VARCHAR(36)    NOT NULL,
    REQUEST_ID           VARCHAR(36)    NOT NULL,
    FILE_NAME            VARCHAR(512)   NOT NULL,
    CONTENT_TYPE         VARCHAR(256),
    BYTES                NUMBER(18,0),
    SHA256               VARCHAR(64),
    STAGE_PATH           VARCHAR(1024)  NOT NULL,
    ADDED_BY             VARCHAR(256)   DEFAULT CURRENT_USER(),
    ADDED_AT             TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    DOMAIN_SOURCE_ID     VARCHAR(36),
    HANDED_OVER_AT       TIMESTAMP_LTZ,
    REMOVED_BY           VARCHAR(256),
    REMOVED_AT           TIMESTAMP_LTZ,
    CONSTRAINT PK_ONBOARDING_REQUEST_FILE PRIMARY KEY (FILE_ID)
);

GRANT SELECT ON TABLE {{database}}.CORE.ONBOARDING_REQUEST_FILE TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.ONBOARDING_REQUEST_FILE TO DATABASE ROLE {{database}}.DATA_ENGINEER;
