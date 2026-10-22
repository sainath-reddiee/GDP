-- V073: source onboarding runs with the caller's rights. REGISTER_SOURCE, VALIDATE_SOURCE_ACCESS, EXECUTE_LANDING,
-- EXECUTE_STAGED_LANDING, SET_LANDING_TARGET, REGISTER_CONNECTION, PROFILE_SOURCE_TABLES and the external source
-- procedures are EXECUTE AS CALLER (snowflake/procedures/source_onboarding.sql), so they read the source with the
-- signed-in person's role. Their own writes then need that role's grants too, and these tables had SELECT grants only:
-- registering a source failed at its first write (the run's state, CORE.WORKFLOW_RUN) with "insufficient privileges"
-- for every role except the tables' owner, as on a client account where people work as AODS_DATA_ENGINEER.
-- DATA_ENGINEER (and PLATFORM_ADMIN, which holds it) may now write what onboarding writes; nothing is deleted.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.WORKFLOW_RUN TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT ON TABLE {{database}}.CORE.WORKFLOW_EVENT TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.SOURCE_REGISTRY TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.SOURCE_OBJECT TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT ON TABLE {{database}}.CORE.SOURCE_ACCESS_CHECK TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.LANDING_TABLE_REGISTRY TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT ON TABLE {{database}}.CORE.LANDING_COLUMN_REGISTRY TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.PROFILE_REGISTRY TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT INSERT ON TABLE {{database}}.CORE.AGENT_TOOL_CALL TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT INSERT ON TABLE {{database}}.CORE.COST_USAGE TO DATABASE ROLE {{database}}.DATA_ENGINEER;
