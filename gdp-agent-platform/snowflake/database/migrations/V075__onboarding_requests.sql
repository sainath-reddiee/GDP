-- V075: onboarding requests (apps/api/app/requests_api.py, services/onboarding/requests.py). A data owner asks for a
-- source, a domain or a data product; the platform checks what already exists and routes the request to the role
-- that has to act next: the Data OS Administrator while a source is not registered or landed, the Data Designer
-- while the domain has no approved model or a run is being profiled and mapped, the Forward Engineering Lead while the
-- code is generated and validated, the Data Developer at code review. The stage is worked out from the linked sources
-- and runs every time the request is read (never typed in by hand), and kept on the row so queues can be listed fast.
--   CORE.ONBOARDING_REQUEST        one request: what, for which domain, by whom, its stage and who it waits on
--   CORE.ONBOARDING_REQUEST_ITEM   a source the request needs (registered, or only named until the admin registers
--                                   it) and the run that onboards it
--   CORE.ONBOARDING_REQUEST_EVENT  everything that happened to it, with the actor (submitted, stage changes, links,
--                                   comments, cancel, reject, reopen); nothing is deleted
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

CREATE TABLE IF NOT EXISTS {{database}}.CORE.ONBOARDING_REQUEST (
    REQUEST_ID           VARCHAR(36)    NOT NULL,
    REQUEST_TYPE         VARCHAR(16)    NOT NULL,       -- SOURCE | DOMAIN | PRODUCT
    TITLE                VARCHAR(256)   NOT NULL,
    BUSINESS_OUTCOME     VARCHAR(4000),
    DOMAIN_ID            VARCHAR(36),                   -- NULL until a requested new domain exists
    PROPOSED_DOMAIN      VARCHAR(128),                  -- the name asked for, when the domain does not exist yet
    PIPELINE             VARCHAR(8)     DEFAULT 'SILVER', -- SILVER | GOLD (a data product is built on Gold)
    PRIORITY             VARCHAR(8)     DEFAULT 'MEDIUM', -- LOW | MEDIUM | HIGH
    NEEDED_BY            DATE,
    STATUS               VARCHAR(16)    NOT NULL,       -- OPEN | DONE | CANCELLED | REJECTED
    STAGE                VARCHAR(16)    NOT NULL,       -- NEW (not routed yet) | DOMAIN | ENABLE | DESIGN | BUILD | REVIEW | DONE | CLOSED
    WAITING_ON           VARCHAR(64),                   -- the privilege that acts next (e.g. SOURCE.CONNECT)
    WAITING_LABEL        VARCHAR(256),                  -- what is awaited, in words
    ATTENTION            VARCHAR(1000),                 -- a blocker someone must look at (a failed run, a gone source)
    REQUESTED_BY         VARCHAR(256)   DEFAULT CURRENT_USER(),
    CREATED_AT           TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT           TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    STAGE_CHANGED_AT     TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CLOSED_BY            VARCHAR(256),
    CLOSED_AT            TIMESTAMP_LTZ,
    CLOSE_REASON         VARCHAR(2000),
    CONSTRAINT PK_ONBOARDING_REQUEST PRIMARY KEY (REQUEST_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.CORE.ONBOARDING_REQUEST_ITEM (
    ITEM_ID              VARCHAR(36)    NOT NULL,
    REQUEST_ID           VARCHAR(36)    NOT NULL,
    SOURCE_SYSTEM_ID     VARCHAR(36),                   -- CORE.SOURCE_REGISTRY; NULL while the source is only named
    SOURCE_NAME          VARCHAR(256)   NOT NULL,       -- the registered name, or the name the requester gave
    SOURCE_NOTE          VARCHAR(2000),                 -- what is needed from it (objects, tables, frequency)
    RUN_ID               VARCHAR(36),                   -- CORE.WORKFLOW_RUN that onboards it; NULL until one exists
    STATUS               VARCHAR(16)    NOT NULL,       -- MISSING | UNREACHABLE | NOT_LANDED | NO_RUN | DESIGN | BUILD | REVIEW | DONE | ATTENTION | REMOVED
    STATUS_DETAIL        VARCHAR(1000),
    ADDED_BY             VARCHAR(256)   DEFAULT CURRENT_USER(),
    ADDED_AT             TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT           TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    REMOVED_BY           VARCHAR(256),
    REMOVED_AT           TIMESTAMP_LTZ,
    CONSTRAINT PK_ONBOARDING_REQUEST_ITEM PRIMARY KEY (ITEM_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.CORE.ONBOARDING_REQUEST_EVENT (
    EVENT_ID             VARCHAR(36)    NOT NULL,
    REQUEST_ID           VARCHAR(36)    NOT NULL,
    ITEM_ID              VARCHAR(36),
    KIND                 VARCHAR(32)    NOT NULL,       -- SUBMITTED | ROUTED | STAGE | SOURCE_ADDED | SOURCE_LINKED | RUN_LINKED | RUN_UNLINKED | SOURCE_REMOVED | DOMAIN_LINKED | COMMENT | CANCELLED | REJECTED | REOPENED | DONE
    FROM_VALUE           VARCHAR(256),
    TO_VALUE             VARCHAR(256),
    NOTE                 VARCHAR(4000),
    ACTOR                VARCHAR(256)   DEFAULT CURRENT_USER(),
    AUTOMATIC            BOOLEAN        DEFAULT FALSE,  -- the platform did it (a stage worked out, a run found)
    CREATED_AT           TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_ONBOARDING_REQUEST_EVENT PRIMARY KEY (EVENT_ID)
);

GRANT SELECT ON TABLE {{database}}.CORE.ONBOARDING_REQUEST TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.CORE.ONBOARDING_REQUEST_ITEM TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.CORE.ONBOARDING_REQUEST_EVENT TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.ONBOARDING_REQUEST TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.ONBOARDING_REQUEST_ITEM TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT ON TABLE {{database}}.CORE.ONBOARDING_REQUEST_EVENT TO DATABASE ROLE {{database}}.DATA_ENGINEER;
