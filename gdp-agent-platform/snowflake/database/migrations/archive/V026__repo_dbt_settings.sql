-- dbt settings per code repository, configured once in Admin, Integrations: the dbt workspace of every run uses the
-- repository serving its domain (clone, origin, base branch, project folder) instead of a per-run setup.
ALTER TABLE {{database}}.CODE.REPO ADD COLUMN IF NOT EXISTS USE_FOR_DBT BOOLEAN DEFAULT TRUE;   -- offered to the dbt workspace
ALTER TABLE {{database}}.CODE.REPO ADD COLUMN IF NOT EXISTS DBT_PROJECT_DIR VARCHAR(512) DEFAULT ''; -- folder with dbt_project.yml; '' is the root
ALTER TABLE {{database}}.CODE.REPO ADD COLUMN IF NOT EXISTS OPEN_PR BOOLEAN DEFAULT TRUE;       -- run default: open a PR after generating
ALTER TABLE {{database}}.CODE.REPO ADD COLUMN IF NOT EXISTS DRAFT_PR BOOLEAN DEFAULT FALSE;     -- run default: open it as a draft
