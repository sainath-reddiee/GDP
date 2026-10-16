-- V038 Data Quality Store: why a check exists and where it came from.
-- Applied once; never edit after it has been applied. Add a new V### instead.
--
-- GOVERNANCE.DQ_CHECKS_CATALOG gains
--   REQUIREMENT            the client requirement or profile evidence behind the check (a promoted run check keeps
--                          its CLIENT_REQUIREMENT, a profile-seeded check its evidence)
--   SOURCE_RUN_ID          the run a promoted check came from
-- Both are nullable; the API reads and writes them only when present, so the store works before this is applied.
-- Every statement is safe to run again (IF NOT EXISTS).

ALTER TABLE {{database}}.GOVERNANCE.DQ_CHECKS_CATALOG ADD COLUMN IF NOT EXISTS REQUIREMENT VARCHAR(4000);
ALTER TABLE {{database}}.GOVERNANCE.DQ_CHECKS_CATALOG ADD COLUMN IF NOT EXISTS SOURCE_RUN_ID VARCHAR(36);
