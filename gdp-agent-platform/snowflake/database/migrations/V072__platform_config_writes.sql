-- V072: Admin settings are saved by the signed-in person's role. CORE.PLATFORM_CONFIG had SELECT grants only, so a save
-- (Admin, Integrations, Datadog; AI models; rules; SnapLogic sync; ...) worked only for the role that owns the table and
-- failed with "insufficient privileges" for everyone else, as on a client account where people use the AODS_* roles.
-- DATA_ENGINEER (and PLATFORM_ADMIN, which holds it) may now add and retire setting versions; who may change a setting
-- is still the app's CONFIG.EDIT privilege (services.governance.policy). Rows are never deleted: a change adds a version.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

GRANT SELECT, INSERT, UPDATE ON TABLE {{database}}.CORE.PLATFORM_CONFIG TO DATABASE ROLE {{database}}.DATA_ENGINEER;
