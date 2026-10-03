-- One supervisor agent. Tools recommend and call stage procedures; they never approve reviews
-- and never change production. Custom tools are generic procedures with VARCHAR/NUMBER only.

CREATE OR REPLACE AGENT {{database}}.CORE.DATA_ENGINEERING_SUPERVISOR
  COMMENT = 'Phase 1 supervisor: recommend, call tools, never approve or promote to production'
  PROFILE = '{"display_name": "Data Engineering Supervisor"}'
  FROM SPECIFICATION
  $$
models:
  orchestration: claude-sonnet-4-5
instructions:
  orchestration: >
    You are the data-engineering supervisor for a governed Snowflake onboarding factory.
    Workflow authority lives in the stored procedures and the state machine, never in this
    conversation. Call tools with the run_id from the user message. Recommend next actions;
    never claim a mapping, STTM, Soda check or dbt project is approved. Humans approve
    business decisions and production changes. Do not invent SQL, mappings, or table names.
    Before any stage, call load_skill for every skill that stage requires:
    landing: BRONZE-SCHEMA-DDL-EXTRACTION, SCHEMA-DDL-EXTRACTION.
    profiling: COLUMN-PROFILING, AI-COLUMN-DESCRIPTIONS, AI-DEEP-QUALITY-ANALYSIS.
    domain: AI-DATA-MODELING.
    mapping: AI-SCHEMA-MAPPING, MAPPING-VALIDATION, MAPPING-BUSINESS-RULES,
    MAPPING-APPROVAL-WORKFLOW, MAPPING-PATTERN-LIBRARY.
    sttm and dbt: DBT-ONBOARD-SOURCE, plus SILVER-MODEL and GDP_DOMAIN_SKILL for dbt.
    validation: MAPPING-VALIDATION, DEV-DATAREADINESS-CHECK, QA-DATAREADINESS-CHECK.
    Gold-model, Iceberg DDL, watermark inserts, and readiness jobs are guidance only.
    Do not execute them. Search domain knowledge before glossary or rule answers.
    If a tool rejects a transition, report the reason and stop.
    Use the attached stage skills. Read the skill whose description matches the stage
    before calling that stage's tool.
  response: >
    Be concise. State the current run state, what you did, and what a human must do next.
    Hide internal reasoning. Never paste secrets, raw PII samples, or connection strings.
{{agent_skills}}
tools:
  - tool_spec:
      type: cortex_search
      name: search_domain_knowledge
      description: Search glossary, rules, patterns and approved mappings
  - tool_spec:
      type: generic
      name: get_workflow_state
      description: Current state, stage rail and allowed transitions for a run
      input_schema:
        type: object
        properties:
          RUN_ID: {type: string, description: Workflow run id}
        required: [RUN_ID]
  - tool_spec:
      type: generic
      name: load_skill
      description: Load a GDP or modeling skill, including its references. Required before each stage.
      input_schema:
        type: object
        properties:
          SKILL_NAME: {type: string, description: Skill name such as AI-SCHEMA-MAPPING or SILVER-MODEL}
        required: [SKILL_NAME]
  - tool_spec:
      type: generic
      name: run_profiling
      description: Profile landed tables when the run is at LANDING_COMPLETE
      input_schema:
        type: object
        properties:
          RUN_ID: {type: string}
        required: [RUN_ID]
  - tool_spec:
      type: generic
      name: identify_domain
      description: Identify the domain after profiling completes
      input_schema:
        type: object
        properties:
          RUN_ID: {type: string}
        required: [RUN_ID]
  - tool_spec:
      type: generic
      name: generate_mapping_candidates
      description: Generate hybrid mapping candidates after the domain is identified
      input_schema:
        type: object
        properties:
          RUN_ID: {type: string}
        required: [RUN_ID]
  - tool_spec:
      type: generic
      name: generate_sttm
      description: Assemble the STTM after mappings are approved
      input_schema:
        type: object
        properties:
          RUN_ID: {type: string}
        required: [RUN_ID]
  - tool_spec:
      type: generic
      name: generate_soda
      description: Propose Soda expectations after the STTM is approved
      input_schema:
        type: object
        properties:
          RUN_ID: {type: string}
        required: [RUN_ID]
  - tool_spec:
      type: generic
      name: generate_dbt
      description: Generate the compile-only dbt project after Soda approval
      input_schema:
        type: object
        properties:
          RUN_ID: {type: string}
        required: [RUN_ID]
  - tool_spec:
      type: generic
      name: validate_dbt
      description: Validate generated dbt and compile with WRITEBACK=FALSE
      input_schema:
        type: object
        properties:
          RUN_ID: {type: string}
        required: [RUN_ID]
tool_resources:
  search_domain_knowledge:
    search_service: "{{database}}.KNOWLEDGE.KNOWLEDGE_SEARCH"
    max_results: "8"
    title_column: "TITLE"
    id_column: "KNOWLEDGE_ID"
  get_workflow_state:
    type: procedure
    identifier: "{{database}}.CORE.GET_WORKFLOW_STATE"
    execution_environment:
      type: warehouse
      warehouse: "{{warehouse}}"
  load_skill:
    type: procedure
    identifier: "{{database}}.KNOWLEDGE.LOAD_SKILL"
    execution_environment:
      type: warehouse
      warehouse: "{{warehouse}}"
  run_profiling:
    type: procedure
    identifier: "{{database}}.PROFILE.RUN_PROFILING"
    execution_environment:
      type: warehouse
      warehouse: "{{warehouse}}"
  identify_domain:
    type: procedure
    identifier: "{{database}}.KNOWLEDGE.IDENTIFY_DOMAIN"
    execution_environment:
      type: warehouse
      warehouse: "{{warehouse}}"
  generate_mapping_candidates:
    type: procedure
    identifier: "{{database}}.MAPPING.GENERATE_MAPPING_CANDIDATES"
    execution_environment:
      type: warehouse
      warehouse: "{{warehouse}}"
  generate_sttm:
    type: procedure
    identifier: "{{database}}.CONTRACT.GENERATE_STTM"
    execution_environment:
      type: warehouse
      warehouse: "{{warehouse}}"
  generate_soda:
    type: procedure
    identifier: "{{database}}.CONTRACT.GENERATE_SODA"
    execution_environment:
      type: warehouse
      warehouse: "{{warehouse}}"
  generate_dbt:
    type: procedure
    identifier: "{{database}}.CODEGEN.GENERATE_DBT"
    execution_environment:
      type: warehouse
      warehouse: "{{warehouse}}"
  validate_dbt:
    type: procedure
    identifier: "{{database}}.CODEGEN.VALIDATE_DBT"
    execution_environment:
      type: warehouse
      warehouse: "{{warehouse}}"
  $$;

GRANT READ ON STAGE {{database}}.KNOWLEDGE.SKILL_STAGE TO DATABASE ROLE {{database}}.SERVICE_AGENT;
GRANT READ ON STAGE {{database}}.KNOWLEDGE.SKILL_STAGE TO DATABASE ROLE {{database}}.DATA_ENGINEER;
GRANT USAGE ON AGENT {{database}}.CORE.DATA_ENGINEERING_SUPERVISOR TO DATABASE ROLE {{database}}.SERVICE_AGENT;
GRANT USAGE ON AGENT {{database}}.CORE.DATA_ENGINEERING_SUPERVISOR TO DATABASE ROLE {{database}}.DATA_ENGINEER;
