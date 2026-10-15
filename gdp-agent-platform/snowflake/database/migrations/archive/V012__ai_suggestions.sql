-- AI suggestions shown next to rule results. One batched call per table and stage, cached by the fingerprint of
-- what the model was shown, so re-opening a page or re-running a stage never pays twice for the same answer.
CREATE TABLE IF NOT EXISTS {{database}}.CORE.AI_SUGGESTION (
    SUGGESTION_ID   VARCHAR(36)   NOT NULL,
    STAGE           VARCHAR(32)   NOT NULL,
    SCOPE_KEY       VARCHAR(1024) NOT NULL,
    FINGERPRINT     VARCHAR(64)   NOT NULL,
    MODEL           VARCHAR(128),
    PROMPT_VERSION  NUMBER(6,0)   NOT NULL,
    ITEMS           VARIANT,
    RUN_ID          VARCHAR(36),
    CREATED_BY      VARCHAR(256)  DEFAULT CURRENT_USER(),
    CREATED_AT      TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_AI_SUGGESTION PRIMARY KEY (SUGGESTION_ID)
);

-- Reviewer verdict per suggested item. Accepted items also become domain knowledge rules; rejected items are kept
-- as negative evidence so the same suggestion is not offered again for that scope.
CREATE TABLE IF NOT EXISTS {{database}}.CORE.AI_SUGGESTION_DECISION (
    DECISION_ID     VARCHAR(36)   NOT NULL,
    SUGGESTION_ID   VARCHAR(36)   NOT NULL,
    STAGE           VARCHAR(32)   NOT NULL,
    SCOPE_KEY       VARCHAR(1024) NOT NULL,
    ITEM_KEY        VARCHAR(1024) NOT NULL,
    DECISION        VARCHAR(16)   NOT NULL,
    ITEM            VARIANT,
    RUN_ID          VARCHAR(36),
    DOMAIN_ID       VARCHAR(64),
    NOTE            VARCHAR(2000),
    DECIDED_BY      VARCHAR(256)  DEFAULT CURRENT_USER(),
    DECIDED_AT      TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_AI_SUGGESTION_DECISION PRIMARY KEY (DECISION_ID)
);

GRANT SELECT, INSERT ON TABLE {{database}}.CORE.AI_SUGGESTION TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT ON TABLE {{database}}.CORE.AI_SUGGESTION_DECISION TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT ON TABLE {{database}}.CORE.AI_SUGGESTION TO DATABASE ROLE {{database}}.REVIEWER;
GRANT SELECT ON TABLE {{database}}.CORE.AI_SUGGESTION_DECISION TO DATABASE ROLE {{database}}.REVIEWER;
