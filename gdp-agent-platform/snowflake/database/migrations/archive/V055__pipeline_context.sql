-- V055 Each pipeline has its own context, and a Gold run builds a domain's star schema.
-- PIPELINE / LAYER = ANY | SILVER | GOLD on the skill bindings, a domain's own skills and knowledge items: a Silver run
--   loads what is ANY or SILVER, a Gold run what is ANY or GOLD (rows before this are ANY, except the tags below).
-- CORE.WORKFLOW_RUN.PARENT_RUN_ID: a Gold run's table runs (one per fact or bridge of the approved gold model) point at
--   the Gold run that selected and profiled the silver tables and proposed the model.
-- Applied once; never edit after it has been applied. Add a new V### instead. Safe to run again.
ALTER TABLE {{database}}.KNOWLEDGE.SKILL_STAGE_BINDING ADD COLUMN IF NOT EXISTS PIPELINE VARCHAR(8) DEFAULT 'ANY';
ALTER TABLE {{database}}.KNOWLEDGE.DOMAIN_SKILL ADD COLUMN IF NOT EXISTS PIPELINE VARCHAR(8) DEFAULT 'ANY';
ALTER TABLE {{database}}.KNOWLEDGE.DOMAIN_KNOWLEDGE ADD COLUMN IF NOT EXISTS LAYER VARCHAR(8) DEFAULT 'ANY';
ALTER TABLE {{database}}.CORE.WORKFLOW_RUN ADD COLUMN IF NOT EXISTS PARENT_RUN_ID VARCHAR(36);

UPDATE {{database}}.KNOWLEDGE.SKILL_STAGE_BINDING SET PIPELINE = 'ANY' WHERE PIPELINE IS NULL;
UPDATE {{database}}.KNOWLEDGE.DOMAIN_SKILL SET PIPELINE = 'ANY' WHERE PIPELINE IS NULL;
UPDATE {{database}}.KNOWLEDGE.DOMAIN_KNOWLEDGE SET LAYER = 'ANY' WHERE LAYER IS NULL;

-- silver-only skills: bronze to silver hub code and the silver model
UPDATE {{database}}.KNOWLEDGE.SKILL_STAGE_BINDING SET PIPELINE = 'SILVER'
 WHERE SKILL_NAME IN ('GDP-DBT-ONBOARD-SOURCE', 'SILVER-MODEL', 'BRONZE-SCHEMA-DDL-EXTRACTION', 'SCHEMA-DDL-EXTRACTION');

-- the gold skills: the GDP gold model at every gold modeling and code stage, the post-MDM data model for gold
MERGE INTO {{database}}.KNOWLEDGE.SKILL_STAGE_BINDING t
USING (SELECT * FROM VALUES ('MAPPING', 'GOLD-MODEL', 40, 'GDP', 'GOLD'), ('STTM', 'GOLD-MODEL', 40, 'GDP', 'GOLD'),
                            ('DBT', 'GOLD-MODEL', 10, 'GDP', 'GOLD'), ('VALIDATION', 'GOLD-MODEL', 40, 'GDP', 'GOLD'),
                            ('STTM', 'GDP-POST-MDM-DATA-MODEL', 45, 'GDP', 'GOLD'), ('DBT', 'GDP-POST-MDM-DATA-MODEL', 15, 'GDP', 'GOLD')
              AS v(STAGE, SKILL_NAME, POSITION, STANDARD, PIPELINE)) s
   ON t.STAGE = s.STAGE AND t.SKILL_NAME = s.SKILL_NAME
WHEN MATCHED AND t.UPDATED_BY = 'SEED' THEN UPDATE SET PIPELINE = s.PIPELINE
WHEN NOT MATCHED THEN INSERT (STAGE, SKILL_NAME, ENABLED, POSITION, STANDARD, PIPELINE, UPDATED_BY)
                      VALUES (s.STAGE, s.SKILL_NAME, TRUE, s.POSITION, s.STANDARD, s.PIPELINE, 'SEED');
