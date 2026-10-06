-- V008 quality dimensions per staged profile (completeness, uniqueness, validity), written at profile time.
-- Freshness is computed at read time from the live source LAST_ALTERED.

ALTER TABLE {{database}}.METADATA.TABLE_PROFILES ADD COLUMN IF NOT EXISTS QUALITY_JSON VARIANT;
