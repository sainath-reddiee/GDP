-- Code context procedures. Re-applied on every deploy.

CREATE OR REPLACE PROCEDURE {{database}}.CODE.INDEX_REPO(REPO_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.code.indexer.index_repo_entry'
  COMMENT = 'Refresh the code index of one repository from its Snowflake Git clone (changed files only)'
  EXECUTE AS CALLER;
