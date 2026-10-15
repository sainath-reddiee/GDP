-- V056 GDP_DOMAIN_SKILL (the Silver dbt layering: staging, intermediate, marts) is a Silver Pipeline skill: Gold runs
-- follow the gold model skill instead. Seeded bindings only; a binding a person changed keeps its scope.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.
UPDATE {{database}}.KNOWLEDGE.SKILL_STAGE_BINDING SET PIPELINE = 'SILVER'
 WHERE SKILL_NAME = 'GDP_DOMAIN_SKILL' AND UPDATED_BY = 'SEED';
