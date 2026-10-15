-- V054 Gold runs: the Gold Pipeline runs on the same run engine as the Silver Pipeline.
-- CORE.WORKFLOW_RUN.PIPELINE: SILVER (source to silver, pre-MDM; every run before this) or GOLD (silver, read in
--   place, to gold). Same states, stages, gates and approvals; the handlers branch on it where gold differs.
-- KNOWLEDGE.TARGET_TABLE_REGISTRY.LAYER: SILVER (the silver targets runs map into, as before) or GOLD (the facts,
--   dimensions and bridges of an approved GOLD domain model, registered so a Gold run can map into them).
-- Applied once; never edit after it has been applied. Add a new V### instead.
ALTER TABLE {{database}}.CORE.WORKFLOW_RUN ADD COLUMN IF NOT EXISTS PIPELINE VARCHAR(8) DEFAULT 'SILVER';
UPDATE {{database}}.CORE.WORKFLOW_RUN SET PIPELINE = 'SILVER' WHERE PIPELINE IS NULL;
ALTER TABLE {{database}}.KNOWLEDGE.TARGET_TABLE_REGISTRY ADD COLUMN IF NOT EXISTS LAYER VARCHAR(8) DEFAULT 'SILVER';
UPDATE {{database}}.KNOWLEDGE.TARGET_TABLE_REGISTRY SET LAYER = 'SILVER' WHERE LAYER IS NULL;
