-- V068: governed removal of a registered target table or a source system. A removal always goes through another
-- person's approval (services/governance/policy.py STORE_ROUTES); once approved, the row is deactivated, never
-- deleted, with who removed it, when and why, and it can be restored. A retired target stays retired when the
-- knowledge seed runs again (services/knowledge/packs.py keeps RETIRED_AT rows inactive).
-- The approvers (DATA_STEWARD, and PLATFORM_ADMIN which inherits it) need UPDATE on the registries they change; before
-- this only the owner could, so domain delete and restore also failed for a steward who is not the owner.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.

ALTER TABLE {{database}}.CORE.TARGET_TABLE_REGISTRY ADD COLUMN IF NOT EXISTS RETIRED_AT TIMESTAMP_LTZ;
ALTER TABLE {{database}}.CORE.TARGET_TABLE_REGISTRY ADD COLUMN IF NOT EXISTS RETIRED_BY VARCHAR(256);
ALTER TABLE {{database}}.CORE.TARGET_TABLE_REGISTRY ADD COLUMN IF NOT EXISTS RETIRE_REASON VARCHAR(2000);

ALTER TABLE {{database}}.CORE.SOURCE_REGISTRY ADD COLUMN IF NOT EXISTS RETIRED_AT TIMESTAMP_LTZ;
ALTER TABLE {{database}}.CORE.SOURCE_REGISTRY ADD COLUMN IF NOT EXISTS RETIRED_BY VARCHAR(256);
ALTER TABLE {{database}}.CORE.SOURCE_REGISTRY ADD COLUMN IF NOT EXISTS RETIRE_REASON VARCHAR(2000);

GRANT SELECT, UPDATE ON TABLE {{database}}.CORE.TARGET_TABLE_REGISTRY TO DATABASE ROLE {{database}}.DATA_STEWARD;
GRANT SELECT, UPDATE ON TABLE {{database}}.CORE.SOURCE_REGISTRY TO DATABASE ROLE {{database}}.DATA_STEWARD;
GRANT SELECT, UPDATE ON TABLE {{database}}.CORE.DOMAIN_REGISTRY TO DATABASE ROLE {{database}}.DATA_STEWARD;
