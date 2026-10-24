-- Stage procedures for profiling through dbt validation. Re-applied on every deploy.

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.RUN_PROFILING(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.profiling.procedures.run_profiling_basic'
  COMMENT = 'Profile landed tables and enrich descriptions; reuses persistent CORE.TABLE_PROFILES'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.RUN_PROFILING(RUN_ID VARCHAR, OPTIONS_JSON VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.profiling.procedures.run_profiling'
  COMMENT = 'Profile landed tables; OPTIONS_JSON {force_refresh, refresh_tables, concurrency_limit}'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.REFRESH_TABLE_PROFILE(RUN_ID VARCHAR, SOURCE_TABLE VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.profiling.procedures.refresh_table_profile'
  COMMENT = 'Bust the persistent profile of one table and re-profile it from the run landing'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.IDENTIFY_DOMAIN(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.knowledge.procedures.identify_domain'
  COMMENT = 'Score active domains from the profile and Cortex Search'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.SEARCH_KNOWLEDGE(QUERY VARCHAR, DOMAIN VARCHAR, KNOWLEDGE_TYPE VARCHAR, LIMIT FLOAT)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.knowledge.procedures.search_knowledge'
  COMMENT = 'Cortex Search over current domain knowledge'
  EXECUTE AS OWNER;

DROP PROCEDURE IF EXISTS {{database}}.{{schema}}.REGISTER_TARGET_TABLE(VARCHAR, VARCHAR, VARCHAR);

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.REGISTER_TARGET_TABLE(PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.knowledge.procedures.register_target_table'
  COMMENT = 'Store a caller-snapshotted existing table as the modeling target'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.APPLY_MODEL_DESIGN(RUN_ID VARCHAR, DESIGN_VERSION NUMBER)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.modeling.design.apply_design'
  COMMENT = 'Approve a model design: register its entities as targets, write MODEL_SPEC and MODEL_DEFINITION'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.APPLY_DOMAIN_MODEL(MODEL_ID VARCHAR, MODEL_VERSION NUMBER)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.modeling.registry_sync.apply_model_version'
  COMMENT = 'Put an approved domain model version into the target registry (Model Workbench)'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.PIN_RUN_MODEL(RUN_ID VARCHAR, MODEL_ID VARCHAR, MODEL_VERSION NUMBER,
                                                           ENTITY VARCHAR, DESIGN_VERSION NUMBER)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.modeling.run_link.pin_run'
  COMMENT = 'Point a run at an approved domain model entity (Model Workbench)'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.LOAD_SKILL(SKILL_NAME VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.knowledge.procedures.load_skill'
  COMMENT = 'Load the current version of a skill'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.GENERATE_MAPPING_CANDIDATES(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.mapping.procedures.generate_mapping_candidates'
  COMMENT = 'Hybrid mapping candidates for the run target model'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.SAVE_MAPPING_DECISIONS(RUN_ID VARCHAR, DECISIONS_JSON VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.mapping.procedures.save_mapping_decisions'
  COMMENT = 'Record reviewer mapping decisions while the run is in MAPPING_REVIEW'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.GENERATE_STTM(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.sttm.procedures.generate_sttm'
  COMMENT = 'Assemble the table-level STTM from approved mappings'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.UPDATE_JOIN_GRAPH(RUN_ID VARCHAR, PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.sttm.procedures.update_join_graph'
  COMMENT = 'Reviewer edits to the STTM join plan: driving table, join keys and LEFT or INNER joins'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.REFINE_TRANSFORMATION(RUN_ID VARCHAR, PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.sttm.procedures.refine_transformation'
  COMMENT = 'Natural-language to Snowflake transform SQL using profile and STTM context'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.APPLY_TRANSFORMATION(RUN_ID VARCHAR, PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.sttm.procedures.apply_transformation'
  COMMENT = 'Apply a refined transform to the STTM and store it as domain knowledge'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.EXPORT_STTM_CSV(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.sttm.procedures.export_sttm_csv'
  COMMENT = 'Write the STTM contract CSV to CORE.DBT_STAGE for Soda and dbt'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.GENERATE_SODA(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.soda.procedures.generate_soda'
  COMMENT = 'Propose official SodaCL from the approved STTM, client brief, and learned Soda patterns'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.IMPORT_CLIENT_EXPECTATIONS(RUN_ID VARCHAR, ROWS_JSON VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.soda.procedures.import_client_expectations'
  COMMENT = 'Import a client quality brief or structured Soda requirement rows'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.BACKTEST_SODA(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.soda.procedures.backtest_soda'
  COMMENT = 'Dry-run the current Soda checks against source data with the caller role'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.QA_SUITE(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.qa.procedures.qa_suite'
  COMMENT = 'Functional QA test SQL derived from the current STTM, plus saved tester queries'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.QA_ASK(RUN_ID VARCHAR, QUESTION VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.qa.procedures.qa_ask'
  COMMENT = 'Natural language to one guarded read-only test query, compiled with EXPLAIN under the caller role'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.QA_SAVE(RUN_ID VARCHAR, PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.qa.procedures.qa_save'
  COMMENT = 'Save a guarded tester or AI test query to the run QA suite'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.QA_TABLE_SUITE(TARGET_TABLE_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.qa.procedures.table_suite'
  COMMENT = 'Functional QA test SQL for a domain target table: generated from its latest STTM, plus saved table tests and suites'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.QA_TABLE_ASK(TARGET_TABLE_ID VARCHAR, QUESTION VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.qa.procedures.table_ask'
  COMMENT = 'Natural language to one guarded read-only test query on a target table, compiled with EXPLAIN under the caller role'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.QA_TABLE_SAVE(TARGET_TABLE_ID VARCHAR, PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.qa.procedures.table_save'
  COMMENT = 'Save a guarded tester or AI test query to a target table suite'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.SAVE_SODA_DECISIONS(RUN_ID VARCHAR, DECISIONS_JSON VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.soda.procedures.save_soda_decisions'
  COMMENT = 'Confirm, modify or reject proposed Soda checks and write the decision to knowledge'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.GENERATE_DBT(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.dbt.procedures.generate_dbt_simple'
  COMMENT = 'Generate a compile-only dbt project from the approved STTM, in parallel with Soda'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.GENERATE_DBT(RUN_ID VARCHAR, PAYLOAD_JSON VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.dbt.procedures.generate_dbt'
  COMMENT = 'Generate a compile-only dbt project from the approved STTM, in parallel with Soda'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.VALIDATE_DBT(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.validation.procedures.validate_dbt'
  COMMENT = 'Deterministic validation plus optional dbt compile WRITEBACK=FALSE'
  EXECUTE AS OWNER;

-- the code standards agent alone on a run's current generation: reports, records its tool call and cost, moves nothing
CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.REVIEW_STANDARDS(RUN_ID VARCHAR)
  RETURNS VARIANT LANGUAGE PYTHON RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.validation.procedures.review_standards'
  COMMENT = 'Code standards agent: review the generated dbt project against the written standards'
  EXECUTE AS OWNER;

GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.RUN_PROFILING(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.RUN_PROFILING(VARCHAR) TO DATABASE ROLE {{database}}.SERVICE_AGENT;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.RUN_PROFILING(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.RUN_PROFILING(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.SERVICE_AGENT;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.REFRESH_TABLE_PROFILE(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.IDENTIFY_DOMAIN(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.IDENTIFY_DOMAIN(VARCHAR) TO DATABASE ROLE {{database}}.SERVICE_AGENT;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.SEARCH_KNOWLEDGE(VARCHAR, VARCHAR, VARCHAR, FLOAT) TO DATABASE ROLE {{database}}.VIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.LOAD_SKILL(VARCHAR) TO DATABASE ROLE {{database}}.VIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.REGISTER_TARGET_TABLE(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.APPLY_MODEL_DESIGN(VARCHAR, NUMBER) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.APPLY_DOMAIN_MODEL(VARCHAR, NUMBER) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.PIN_RUN_MODEL(VARCHAR, VARCHAR, NUMBER, VARCHAR, NUMBER) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.PIN_RUN_MODEL(VARCHAR, VARCHAR, NUMBER, VARCHAR, NUMBER) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.GENERATE_MAPPING_CANDIDATES(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.GENERATE_MAPPING_CANDIDATES(VARCHAR) TO DATABASE ROLE {{database}}.SERVICE_AGENT;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.SAVE_MAPPING_DECISIONS(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.GENERATE_STTM(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.GENERATE_STTM(VARCHAR) TO DATABASE ROLE {{database}}.SERVICE_AGENT;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.UPDATE_JOIN_GRAPH(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.UPDATE_JOIN_GRAPH(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.REFINE_TRANSFORMATION(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.REFINE_TRANSFORMATION(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.APPLY_TRANSFORMATION(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.APPLY_TRANSFORMATION(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.EXPORT_STTM_CSV(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.EXPORT_STTM_CSV(VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.GENERATE_SODA(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.QA_SUITE(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.QA_SUITE(VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.QA_SUITE(VARCHAR) TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.QA_ASK(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.QA_ASK(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.QA_ASK(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.QA_SAVE(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.QA_SAVE(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.QA_SAVE(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.QA_TABLE_SUITE(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.QA_TABLE_SUITE(VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.QA_TABLE_SUITE(VARCHAR) TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.QA_TABLE_ASK(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.QA_TABLE_ASK(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.QA_TABLE_ASK(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.QA_TABLE_SAVE(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.QA_TABLE_SAVE(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.QA_TABLE_SAVE(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.QA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.BACKTEST_SODA(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.BACKTEST_SODA(VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.GENERATE_SODA(VARCHAR) TO DATABASE ROLE {{database}}.SERVICE_AGENT;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.IMPORT_CLIENT_EXPECTATIONS(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.IMPORT_CLIENT_EXPECTATIONS(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.SAVE_SODA_DECISIONS(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.REVIEWER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.SAVE_SODA_DECISIONS(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.GENERATE_DBT(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.GENERATE_DBT(VARCHAR) TO DATABASE ROLE {{database}}.SERVICE_AGENT;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.GENERATE_DBT(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.GENERATE_DBT(VARCHAR, VARCHAR) TO DATABASE ROLE {{database}}.SERVICE_AGENT;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.VALIDATE_DBT(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.VALIDATE_DBT(VARCHAR) TO DATABASE ROLE {{database}}.SERVICE_AGENT;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.REVIEW_STANDARDS(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.REVIEW_STANDARDS(VARCHAR) TO DATABASE ROLE {{database}}.SERVICE_AGENT;

-- Data Quality Store (V037): scan one table's ACTIVE catalog checks. Called by the per-table Snowflake tasks the DQ
-- Store schedules create (CORE.DQ_<hash>). Owner's rights: the owner role needs SELECT on scheduled tables.
CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.RUN_TABLE_SCAN(TABLE_FQN VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.catalog.dq.run_table_scan'
  COMMENT = 'Run the Data Quality Store checks of one table and record the scan'
  EXECUTE AS OWNER;

GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.RUN_TABLE_SCAN(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;

-- Profiling Store (V041): one scheduled profiling run (a database.schema, all of its tables or a list). Called by the
-- schedule's task (CORE.PROF_<hash>) and by Run now. Owner's rights: the owner role needs SELECT on the tables.
CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.RUN_SCHEDULED_PROFILE(SCHEDULE_ID VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.catalog.profile_ops.run_scheduled'
  COMMENT = 'Run one Profiling Store schedule and record its outcome and drift'
  EXECUTE AS OWNER;

GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.RUN_SCHEDULED_PROFILE(VARCHAR) TO DATABASE ROLE {{database}}.DATA_ENGINEER;

-- Data Quality suites (V040): put an approved suite version (one table's reviewed checks) into the catalog the scanner
-- and the schedules read. Owner's rights, so approvers need no write grant on the catalog.
CREATE OR REPLACE PROCEDURE {{database}}.{{schema}}.APPLY_DQ_SUITE(SUITE_ID VARCHAR, SUITE_VERSION NUMBER)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  IMPORTS = ('{{services_import}}')
  HANDLER = 'services.catalog.dq_suite.apply_suite_version'
  COMMENT = 'Put an approved data quality suite version into the checks catalog'
  EXECUTE AS OWNER;

GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.APPLY_DQ_SUITE(VARCHAR, NUMBER) TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON PROCEDURE {{database}}.{{schema}}.APPLY_DQ_SUITE(VARCHAR, NUMBER) TO DATABASE ROLE {{database}}.REVIEWER;
