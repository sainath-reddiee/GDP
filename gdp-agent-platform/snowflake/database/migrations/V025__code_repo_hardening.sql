-- Code repositories, hardening: one index run at a time per repository (a lock that expires), the Snowflake objects
-- the platform created for a repository (so disconnect can drop only those), and files that could not be read are
-- remembered so they are not retried on every refresh.
ALTER TABLE {{database}}.CODE.REPO ADD COLUMN IF NOT EXISTS LOCK_RUN_ID VARCHAR(36);
ALTER TABLE {{database}}.CODE.REPO ADD COLUMN IF NOT EXISTS LOCKED_AT TIMESTAMP_LTZ;
ALTER TABLE {{database}}.CODE.REPO ADD COLUMN IF NOT EXISTS CREATED_OBJECTS ARRAY;   -- {kind, name} the platform created
ALTER TABLE {{database}}.CODE.REPO ADD COLUMN IF NOT EXISTS DEFAULT_BRANCH VARCHAR(256); -- the remote's default, when known

ALTER TABLE {{database}}.CODE.CODE_FILE ADD COLUMN IF NOT EXISTS SKIPPED_REASON VARCHAR(512); -- set when the file could not be read

-- non UTF-8 bytes no longer fail a read (the file format is shared with dbt skeleton reads, where this is also wanted)
ALTER FILE FORMAT IF EXISTS {{database}}.CODEGEN.RAW_TEXT_FORMAT SET REPLACE_INVALID_CHARACTERS = TRUE;
