-- V050 Saved views of the Tickets page (/tickets): a named set of filters over Jira issues and cases, private to its
-- owner or shared with everyone. The filters are checked by services/tickets.py before they are stored.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

CREATE TABLE IF NOT EXISTS {{database}}.CORE.TICKET_VIEW (
    VIEW_ID     VARCHAR(36)   NOT NULL,
    NAME        VARCHAR(120)  NOT NULL,
    FILTERS     VARIANT       NOT NULL,
    SHARED      BOOLEAN       DEFAULT FALSE,
    CREATED_BY  VARCHAR(256)  DEFAULT CURRENT_USER(),
    CREATED_AT  TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_TICKET_VIEW PRIMARY KEY (VIEW_ID)
);

GRANT SELECT ON TABLE {{database}}.CORE.TICKET_VIEW TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, DELETE ON TABLE {{database}}.CORE.TICKET_VIEW TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, DELETE ON TABLE {{database}}.CORE.TICKET_VIEW TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT SELECT, INSERT, DELETE ON TABLE {{database}}.CORE.TICKET_VIEW TO DATABASE ROLE {{database}}.REVIEWER;
GRANT SELECT, INSERT, DELETE ON TABLE {{database}}.CORE.TICKET_VIEW TO DATABASE ROLE {{database}}.DATA_STEWARD;
GRANT SELECT, INSERT, DELETE ON TABLE {{database}}.CORE.TICKET_VIEW TO DATABASE ROLE {{database}}.DOMAIN_OWNER;
