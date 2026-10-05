-- Stage procedures for profiling through dbt validation. Re-applied on every deploy.

CREATE OR REPLACE PROCEDURE {{database}}.PROFILE.RUN_PROFILING(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.profiling.procedures.run_profiling'
  COMMENT = 'Profile landed tables and enrich descriptions'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.KNOWLEDGE.IDENTIFY_DOMAIN(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.knowledge.procedures.identify_domain'
  COMMENT = 'Score active domains from the profile and Cortex Search'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.KNOWLEDGE.SEARCH_KNOWLEDGE(QUERY VARCHAR, DOMAIN VARCHAR, KNOWLEDGE_TYPE VARCHAR, LIMIT FLOAT)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.knowledge.procedures.search_knowledge'
  COMMENT = 'Cortex Search over current domain knowledge'
  EXECUTE AS OWNER;

DROP PROCEDURE IF EXISTS {{database}}.KNOWLEDGE.REGISTER_TARGET_TABLE(VARCHAR, VARCHAR, VARCHAR);

CREATE OR REPLACE PROCEDURE {{database}}.KNOWLEDGE.REGISTER_TARGET_TABLE(PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.knowledge.procedures.register_target_table'
  COMMENT = 'Store a caller-snapshotted existing table as the modeling target'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.KNOWLEDGE.LOAD_SKILL(SKILL_NAME VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.knowledge.procedures.load_skill'
  COMMENT = 'Load the current version of a skill'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.MAPPING.GENERATE_MAPPING_CANDIDATES(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.mapping.procedures.generate_mapping_candidates'
  COMMENT = 'Hybrid mapping candidates for the run target model'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.MAPPING.SAVE_MAPPING_DECISIONS(RUN_ID VARCHAR, DECISIONS_JSON VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.mapping.procedures.save_mapping_decisions'
  COMMENT = 'Record reviewer mapping decisions while the run is in MAPPING_REVIEW'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.CONTRACT.GENERATE_STTM(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.sttm.procedures.generate_sttm'
  COMMENT = 'Assemble the table-level STTM from approved mappings'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.CONTRACT.REFINE_TRANSFORMATION(RUN_ID VARCHAR, PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.sttm.procedures.refine_transformation'
  COMMENT = 'Natural-language to Snowflake transform SQL using profile and STTM context'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.CONTRACT.APPLY_TRANSFORMATION(RUN_ID VARCHAR, PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.sttm.procedures.apply_transformation'
  COMMENT = 'Apply a refined transform to the STTM and store it as domain knowledge'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.CONTRACT.EXPORT_STTM_CSV(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.sttm.procedures.export_sttm_csv'
  COMMENT = 'Write the STTM contract CSV to CODEGEN.DBT_STAGE for Soda and dbt'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.CONTRACT.GENERATE_SODA(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.soda.procedures.generate_soda'
  COMMENT = 'Propose official SodaCL from the approved STTM, client brief, and learned Soda patterns'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.CONTRACT.IMPORT_CLIENT_EXPECTATIONS(RUN_ID VARCHAR, ROWS_JSON VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.soda.procedures.import_client_expectations'
  COMMENT = 'Import a client quality brief or structured Soda requirement rows'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.CONTRACT.SAVE_SODA_DECISIONS(RUN_ID VARCHAR, DECISIONS_JSON VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.soda.procedures.save_soda_decisions'
  COMMENT = 'Confirm, modify or reject proposed Soda checks and write the decision to knowledge'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.CODEGEN.GENERATE_DBT(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.dbt.procedures.generate_dbt_simple'
  COMMENT = 'Generate a compile-only dbt project from the approved STTM, in parallel with Soda'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.CODEGEN.GENERATE_DBT(RUN_ID VARCHAR, PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.dbt.procedures.generate_dbt'
  COMMENT = 'Generate a compile-only dbt project from the approved STTM, in parallel with Soda'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.CODEGEN.VALIDATE_DBT(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.validation.procedures.validate_dbt'
  COMMENT = 'Deterministic validation plus optional dbt compile WRITEBACK=FALSE'
  EXECUTE AS OWNER;

GRANT USAGE ON PROCEDURE {{database}}.PROFILE.RUN_PROFILING(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.PROFILE.RUN_PROFILING(VARCHAR) TO DATABASE ROLE {{database}}.SERVICE_AGENT;
GRANT USAGE ON PROCEDURE {{database}}.KNOWLEDGE.IDENTIFY_DOMAIN(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.KNOWLEDGE.IDENTIFY_DOMAIN(VARCHAR) TO DATABASE ROLE {{database}}.SERVICE_AGENT;
GRANT USAGE ON PROCEDURE {{database}}.KNOWLEDGE.SEARCH_KNOWLEDGE(VARCHAR, VARCHAR, VARCHAR, FLOAT) TO DATABASE ROLE {{database}}.VIEWER;
GRANT USAGE ON PROCEDURE {{database}}.KNOWLEDGE.LOAD_SKILL(VARCHAR) TO DATABASE ROLE {{database}}.VIEWER;
GRANT USAGE ON PROCEDURE {{database}}.KNOWLEDGE.REGISTER_TARGET_TABLE(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.MAPPING.GENERATE_MAPPING_CANDIDATES(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.MAPPING.GENERATE_MAPPING_CANDIDATES(VARCHAR) TO DATABASE ROLE {{database}}.SERVICE_AGENT;
GRANT USAGE ON PROCEDURE {{database}}.MAPPING.SAVE_MAPPING_DECISIONS(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.CONTRACT.GENERATE_STTM(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.CONTRACT.GENERATE_STTM(VARCHAR) TO DATABASE ROLE {{database}}.SERVICE_AGENT;
GRANT USAGE ON PROCEDURE {{database}}.CONTRACT.REFINE_TRANSFORMATION(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.CONTRACT.REFINE_TRANSFORMATION(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.CONTRACT.APPLY_TRANSFORMATION(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.CONTRACT.APPLY_TRANSFORMATION(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.CONTRACT.EXPORT_STTM_CSV(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.CONTRACT.EXPORT_STTM_CSV(VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.CONTRACT.GENERATE_SODA(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.CONTRACT.GENERATE_SODA(VARCHAR) TO DATABASE ROLE {{database}}.SERVICE_AGENT;
GRANT USAGE ON PROCEDURE {{database}}.CONTRACT.IMPORT_CLIENT_EXPECTATIONS(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.CONTRACT.IMPORT_CLIENT_EXPECTATIONS(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.CONTRACT.SAVE_SODA_DECISIONS(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.CONTRACT.SAVE_SODA_DECISIONS(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.CODEGEN.GENERATE_DBT(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.CODEGEN.GENERATE_DBT(VARCHAR) TO DATABASE ROLE {{database}}.SERVICE_AGENT;
GRANT USAGE ON PROCEDURE {{database}}.CODEGEN.GENERATE_DBT(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.CODEGEN.GENERATE_DBT(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.SERVICE_AGENT;
GRANT USAGE ON PROCEDURE {{database}}.CODEGEN.VALIDATE_DBT(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.CODEGEN.VALIDATE_DBT(VARCHAR) TO DATABASE ROLE {{database}}.SERVICE_AGENT;
