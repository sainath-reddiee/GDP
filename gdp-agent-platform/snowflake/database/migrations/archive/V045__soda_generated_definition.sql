-- V045 Soda checks remember what the generator proposed, so regenerating keeps reviewed decisions.
-- Applied once; never edit after it has been applied. Add a new V### instead.
--
-- CONTRACT.SODA_EXPECTATION_REGISTRY.GENERATED_DEFINITION  the definition the generator proposed for the check, kept
--     next to CHECK_DEFINITION (which a reviewer may modify). On regeneration services/soda/decisions.carry_decisions
--     compares the new proposal with it: unchanged input keeps the reviewer's decision and definition; changed input
--     sends an approved check back for review (keeping a reviewer's modified definition) and keeps a rejection.
-- Safe to run again (IF NOT EXISTS).

ALTER TABLE {{database}}.CONTRACT.SODA_EXPECTATION_REGISTRY ADD COLUMN IF NOT EXISTS GENERATED_DEFINITION VARIANT;
