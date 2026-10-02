-- Migration script to rename columns in MAPPING_ROWS table
-- Goal: 
-- 1. Merge source_short_desc and source_long_desc into source_description
-- 2. Rename target_desc to target_description
-- 3. Rename transformation_rule to transformation_logic

USE SCHEMA DBT_GENERATOR.APP;

-- 1. Add new column source_description
ALTER TABLE MAPPING_ROWS ADD COLUMN SOURCE_DESCRIPTION TEXT;

-- 2. Populate source_description from existing short/long descriptions
UPDATE MAPPING_ROWS
SET SOURCE_DESCRIPTION = COALESCE(SOURCE_SHORT_DESC, '') || 
                         CASE WHEN SOURCE_LONG_DESC IS NOT NULL AND SOURCE_SHORT_DESC IS NOT NULL THEN ' - ' ELSE '' END || 
                         COALESCE(SOURCE_LONG_DESC, '');

-- 3. Drop old short/long description columns
ALTER TABLE MAPPING_ROWS DROP COLUMN SOURCE_SHORT_DESC;
ALTER TABLE MAPPING_ROWS DROP COLUMN SOURCE_LONG_DESC;

-- 4. Rename target_desc to target_description
ALTER TABLE MAPPING_ROWS RENAME COLUMN TARGET_DESC TO TARGET_DESCRIPTION;

-- 5. Rename transformation_rule to transformation_logic
ALTER TABLE MAPPING_ROWS RENAME COLUMN TRANSFORMATION_RULE TO TRANSFORMATION_LOGIC;

-- Verify the changes
DESC TABLE MAPPING_ROWS;
