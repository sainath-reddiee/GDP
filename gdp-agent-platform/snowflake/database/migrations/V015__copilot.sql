-- Copilot conversations: every question and answer with the page it was asked from, the model and the knowledge
-- it cited. Kept for audit and so a conversation can continue where it left off.
CREATE TABLE IF NOT EXISTS {{database}}.CORE.COPILOT_MESSAGE (
    MESSAGE_ID       VARCHAR(36)   NOT NULL,
    CONVERSATION_ID  VARCHAR(36)   NOT NULL,
    ROLE             VARCHAR(16)   NOT NULL,   -- USER | ASSISTANT
    CONTENT          VARCHAR(16000) NOT NULL,
    PAGE             VARIANT,                  -- route, run, stage, database, schema, table
    RUN_ID           VARCHAR(36),
    MODEL            VARCHAR(128),
    CITATIONS        VARIANT,
    CREATED_BY       VARCHAR(256)  DEFAULT CURRENT_USER(),
    CREATED_AT       TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_COPILOT_MESSAGE PRIMARY KEY (MESSAGE_ID)
);

GRANT SELECT, INSERT ON TABLE {{database}}.CORE.COPILOT_MESSAGE TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT ON TABLE {{database}}.CORE.COPILOT_MESSAGE TO DATABASE ROLE {{database}}.REVIEWER;
