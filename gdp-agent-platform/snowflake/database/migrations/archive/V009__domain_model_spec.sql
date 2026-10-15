-- V009 domain model specs and detection config, seeded from domain/<domain>/domain_pack.json.
-- MODEL_SPEC: hub/spoke role, hub FK, HKEY column order, contract reference lookups, casts, minimum mapping.
-- CONFIG: domain detection signals, source systems, silver location and contract path.

ALTER TABLE {{database}}.KNOWLEDGE.TARGET_TABLE_REGISTRY ADD COLUMN IF NOT EXISTS MODEL_SPEC VARIANT;
ALTER TABLE {{database}}.KNOWLEDGE.DOMAIN_REGISTRY ADD COLUMN IF NOT EXISTS CONFIG VARIANT;
