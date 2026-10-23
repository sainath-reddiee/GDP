-- V076: personas (apps/api/app/personas_api.py, services/governance/personas.py). A persona is a named bundle of the
-- governance roles (CORE.APP_ROLE) that one kind of person needs: a super admin assigns personas to people instead of
-- toggling roles one by one, and the platform grants the roles the bundles hold (CORE.USER_ROLE stays the place
-- privileges come from). "View as" offers a person the personas they were given.
--   CORE.PERSONA        one persona: its name, what it is for, its order in lists
--   CORE.PERSONA_ROLE   the roles a persona grants (a super admin can change them; holders follow)
--   CORE.USER_PERSONA   who holds which persona, and who gave it
-- Seeded with the default bundles; a seeded persona is never overwritten on a later deploy (edits are kept). Nobody is
-- given a persona here: existing users keep their roles, and Admin, People lists the personas their roles match.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

CREATE TABLE IF NOT EXISTS {{database}}.CORE.PERSONA (
    PERSONA_ID     VARCHAR(32)    NOT NULL,      -- product_owner | admin | designer | forward_lead | developer | qa | reliability | curator | super
    LABEL          VARCHAR(128)   NOT NULL,
    DESCRIPTION    VARCHAR(1000),
    POSITION       NUMBER(4,0)    DEFAULT 100,
    ACTIVE         BOOLEAN        DEFAULT TRUE,
    UPDATED_BY     VARCHAR(256)   DEFAULT CURRENT_USER(),
    UPDATED_AT     TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_PERSONA PRIMARY KEY (PERSONA_ID)
);

CREATE TABLE IF NOT EXISTS {{database}}.CORE.PERSONA_ROLE (
    PERSONA_ID     VARCHAR(32)    NOT NULL,
    ROLE_NAME      VARCHAR(64)    NOT NULL,
    CONSTRAINT PK_PERSONA_ROLE PRIMARY KEY (PERSONA_ID, ROLE_NAME)
);

CREATE TABLE IF NOT EXISTS {{database}}.CORE.USER_PERSONA (
    USER_NAME      VARCHAR(256)   NOT NULL,
    PERSONA_ID     VARCHAR(32)    NOT NULL,
    ASSIGNED_BY    VARCHAR(256)   DEFAULT CURRENT_USER(),
    ASSIGNED_AT    TIMESTAMP_LTZ  DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_USER_PERSONA PRIMARY KEY (USER_NAME, PERSONA_ID)
);

INSERT INTO {{database}}.CORE.PERSONA (PERSONA_ID, LABEL, DESCRIPTION, POSITION)
SELECT * FROM VALUES
    ('product_owner', 'GDP Product Owner', 'Data owner: files requests, owns domains and their knowledge', 10),
    ('admin', 'Data OS Administrator', 'Connects and lands sources, configures ingestion, approves Airflow fixes', 20),
    ('designer', 'Data Designer', 'Starts runs, profiles, maps and approves the design gates', 30),
    ('forward_lead', 'Forward Engineering Lead', 'Generates and validates code, versions and releases skills', 40),
    ('developer', 'Data Developer', 'Reviews and approves code, publishes pull requests', 50),
    ('qa', 'QA & UAT Lead', 'QA tests and their signoff', 60),
    ('reliability', 'Data Reliability Engineer', 'Operations, incidents and their resolution', 70),
    ('curator', 'Knowledge & Skills Curator', 'Verifies Enterprise Memory and releases skills', 80),
    ('super', 'Super admin', 'Everything, and the only one who sees Admin and governance', 90)
    AS V (PERSONA_ID, LABEL, DESCRIPTION, POSITION)
WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.PERSONA P WHERE P.PERSONA_ID = V.PERSONA_ID);

INSERT INTO {{database}}.CORE.PERSONA_ROLE (PERSONA_ID, ROLE_NAME)
SELECT V.PERSONA_ID, V.ROLE_NAME FROM VALUES
    ('product_owner', 'DATA_STEWARD'),
    ('admin', 'PLATFORM_ADMIN'),
    ('designer', 'DATA_ENGINEER'), ('designer', 'MODEL_APPROVER'), ('designer', 'STTM_APPROVER'), ('designer', 'DQ_APPROVER'),
    ('forward_lead', 'DATA_ENGINEER'), ('forward_lead', 'SKILL_OWNER'),
    ('developer', 'CODE_REVIEWER'),
    ('qa', 'QA_LEAD'),
    ('reliability', 'SUPPORT_ENGINEER'),
    ('curator', 'DATA_STEWARD'), ('curator', 'SKILL_OWNER'),
    ('super', 'SUPER_ADMIN')
    AS V (PERSONA_ID, ROLE_NAME)
-- only for a persona seeded just now (its bundle is empty): a bundle a super admin edited stays as they left it
WHERE NOT EXISTS (SELECT 1 FROM {{database}}.CORE.PERSONA_ROLE R WHERE R.PERSONA_ID = V.PERSONA_ID);

GRANT SELECT ON TABLE {{database}}.CORE.PERSONA TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.CORE.PERSONA_ROLE TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT ON TABLE {{database}}.CORE.USER_PERSONA TO DATABASE ROLE {{database}}.VIEWER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.PERSONA TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, DELETE ON TABLE {{database}}.CORE.PERSONA_ROLE TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, DELETE ON TABLE {{database}}.CORE.USER_PERSONA TO DATABASE ROLE {{database}}.DATA_ENGINEER;
