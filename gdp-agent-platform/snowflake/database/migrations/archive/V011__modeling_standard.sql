-- Each run answers "is this GDP or not?": GDP (hub/spoke with GDP conventions) or GENERIC (any company).
-- NULL on runs created before the choice existed; they keep GDP behaviour.
ALTER TABLE {{database}}.CORE.WORKFLOW_RUN ADD COLUMN IF NOT EXISTS MODELING_STANDARD VARCHAR(16);
