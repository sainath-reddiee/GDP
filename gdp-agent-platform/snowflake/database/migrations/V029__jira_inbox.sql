-- Jira inbox for the QA workspace: saved JQL filters per user, optionally shared with everyone who can read Jira
-- issues here. A filter is only JQL: running it still goes through each engineer's own Jira token and permissions.
CREATE TABLE IF NOT EXISTS {{database}}.JIRA.SAVED_FILTER (
    FILTER_ID   VARCHAR(36)   NOT NULL,
    USER_NAME   VARCHAR(256)  NOT NULL,   -- the owner: only they change or delete it
    NAME        VARCHAR(120)  NOT NULL,   -- unique per owner
    JQL         VARCHAR(4000) NOT NULL,
    SHARED      BOOLEAN       DEFAULT FALSE,
    CREATED_AT  TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT  TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_JIRA_SAVED_FILTER PRIMARY KEY (FILTER_ID)
);

GRANT SELECT ON TABLE {{database}}.JIRA.SAVED_FILTER TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {{database}}.JIRA.SAVED_FILTER TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {{database}}.JIRA.SAVED_FILTER TO DATABASE ROLE {{database}}.REVIEWER;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {{database}}.JIRA.SAVED_FILTER TO DATABASE ROLE {{database}}.QA_ENGINEER;
