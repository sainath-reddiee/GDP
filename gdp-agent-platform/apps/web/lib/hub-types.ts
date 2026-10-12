// Types of the lifecycle hub APIs (/api/hub/*, apps/api/app/hub_*.py).

// ---------------------------------------------------------------- sources

export type IntakeField = {
  key: string; label?: string; type: "text" | "textarea" | "number" | "select" | "password" | "boolean" | "cron";
  required?: boolean; secret?: boolean; options?: string[]; help?: string; placeholder?: string; pattern?: string;
  show_if?: { key: string; in: string[] };
};
export type IntakeForm = { version?: number; sections: { title: string; fields: IntakeField[] }[] };

export type Pipeline = {
  source_id: string; source_name: string; source_type: string; connection_type: string | null;
  connection_metadata: Record<string, unknown> | null; schedule: { frequency?: string; cron?: string; timezone?: string } | null;
  target_landing_table: string | null; snaplogic_pipeline_name: string | null; domain_id: string | null;
  status: "REGISTERED" | "ACTIVE" | "PAUSED" | "RETIRED"; notes: string | null; has_secret: boolean;
  created_by?: string; created_at?: string; updated_at?: string | null;
};

/** One table's SnapLogic ingestion control row (PLATFORM_CONFIG.INGESTION_CONTROL_CONFIG, V046). */
export type IngestionControl = {
  master_id: number; child_id: number; parent_pipeline_name: string; child_pipeline_name: string; snaplogic_project_name: string;
  source_id: string | null; domain_id: string | null; domain_name?: string | null; source_name?: string | null; source_type?: string | null;
  source_account: string | null; source_database_path: string | null; source_schema_file: string | null; source_table_sql: string;
  source_file_type: string | null; source_sheet_name: string | null; source_delta_key: string | null; source_primary_keys: string | null;
  source_where_clause: string | null; source_audit_columns: string | null; target_account: string | null;
  landing_database: string; landing_schema: string; landing_table: string; bronze_schema: string; bronze_table: string;
  external_stage: string | null; external_volume: string | null; base_location: string | null; config_table: string | null;
  load_type: string; bronze_load_type: string; load_frequency: string; job_trigger_time: string | null;
  target_previous_load_time: string | null; last_run_timestamp: string | null; landing_to_bronze_flag: boolean; is_active: boolean;
  completion_flag: boolean; source_delete_action: string; source_delete_key: string | null; source_delete_key_value: string | null;
  source_delete_query: string | null; api_url: string | null; api_key: string | null; created_by: string; created_at: string | null;
  updated_by: string | null; updated_at: string | null; comments: string | null;
};
export type IngestionHistory = { child_id: number; action: string; is_active: boolean | null; changed_by: string; changed_at: string };

export type CatalogDatabase = { database_name: string; type: "STANDARD" | "IMPORTED DATABASE" | string; comment?: string | null };
/** Where runs can land by default (GET /api/landing/targets). */
export type LandingDefaults = { default: { landing_database: string; landing_schema: string; storage_type: string }; database: string; schemas: string[] };

export type ShareList = { shares: { name: string; owner_account: string; database_name: string | null; comment?: string }[]; mounted: string[] };
export type ShareTable = { table_name: string; table_type?: string; row_count?: number | null; bytes?: number | null; last_altered?: string | null };
export type OnboardStep = { step: string; ok: boolean; detail: string };
export type OnboardResult = { run_id: string; ok: boolean; steps: OnboardStep[]; continue_at: string; tables?: unknown[] };

// ---------------------------------------------------------------- profiles

export type ProfileRow = {
  profile_id: string | null; source_name: string; database_name: string; schema_name: string; table_name: string;
  table_fqn: string; run_id: string | null; domain_id: string | null; domain_name: string | null;
  row_count: number | null; column_count: number | null; avg_null_pct: number | null; key_candidates: number | null;
  pii_columns: number | null; profiled_at: string | null; profiled_by: string | null; semantic_types: string[];
  passes: number; catalog: boolean; tags: string[]; bookmarked: boolean;
};
export type ProfileFacets = {
  domains: { domain_id: string; domain_name: string }[]; tags: string[];
  runs: { run_id: string; run_name: string | null; profiled_at: string }[];
};
export type ProfileSearch = { profiles: ProfileRow[]; total: number; deployed: boolean; facets: ProfileFacets };

export type ColumnProfile = {
  column_name: string; ordinal: number; data_type: string | null; family: string | null; semantic_type: string | null;
  pii_classification: string | null; row_count: number | null; null_count: number | null; null_pct: number | null;
  distinct_count: number | null; cardinality: string | null; cardinality_ratio: number | null; min_value: string | null;
  max_value: string | null; potential_key: boolean; top_values: { value: unknown; count: number }[];
  histogram: { low?: number; high?: number; lower?: number; upper?: number; count: number }[] | null; description: string | null;
};
export type ProfilePass = { profile_id: string; run_id: string | null; row_count: number | null; columns: number; profiled_at: string; profiled_by: string | null };
export type ColumnDetail = {
  fqn: string; profile_id?: string; passes: ProfilePass[]; columns: ColumnProfile[]; catalog: boolean;
  drift: { changed: boolean; columns: Record<string, string[]>; drifted_columns: number } | null;
  comparison?: PassComparison | null;
  annotations?: { approved: Record<string, ColumnAnnotation>; pending: Record<string, ColumnAnnotation> };
};
export type PassMetrics = {
  data_type: string | null; semantic_type: string | null; pii_classification: string | null; null_pct: number | null;
  distinct_count: number | null; cardinality: string | null; min_value: string | null; max_value: string | null; row_count: number | null;
};
export type PassComparison = {
  a: string; b: string; counts: Record<"added" | "removed" | "changed" | "same", number>;
  row_count: { a: number | null; b: number | null; delta: number | null };
  columns: { column_name: string; status: "added" | "removed" | "changed" | "same"; flags: string[]; a: PassMetrics | null; b: PassMetrics | null; null_delta: number | null }[];
};
/** A reviewed fact about a column (V041); an approved one overrides the profiler's guess. */
export type ColumnAnnotation = {
  annotation_id: string; table_fqn: string; column_name: string; description: string | null; business_definition: string | null;
  pii_classification: string | null; semantic_type: string | null; accepted_values: string[]; detected: Record<string, unknown>;
  note: string | null; status: "PROPOSED" | "APPROVED" | "REJECTED" | "SUPERSEDED" | "WITHDRAWN"; proposed_by: string | null;
  proposed_at: string | null; decided_by: string | null; decided_at: string | null; decision_note: string | null;
  model_id: string | null; model_change_id: string | null;
};
export type ProfileSchedule = {
  schedule_id: string; source_system_id: string; database_name: string; schema_name: string; tables: string[]; cron: string;
  force_refresh: boolean; task_name: string | null; enabled: boolean; last_run_at: string | null; last_status: "OK" | "PARTIAL" | "FAILED" | null;
  last_result: { profiled?: string[]; cached?: string[]; failed?: { table: string; error: string }[]; drifted?: { table: string; columns: Record<string, string[]> }[]; error?: string } | null;
  created_by: string | null; created_at: string | null;
};
export type Expectations = { table: string; checks: { check: string; column: string | null; reason: string }[]; soda_yaml: string; gx_suite: unknown };

// ---------------------------------------------------------------- STTM

export type SttmLine = {
  source_system?: string | null; source_table: string | null; source_column: string | null; source_datatype?: string | null;
  target_column: string; target_datatype: string | null; mapping_type: string | null; transformation: string | null;
  business_rule: string | null; business_definition: string | null; join_logic?: string | null; filter_rule?: string | null;
};
export type SttmSummary = {
  catalog_id: string; version: number; versions: number; sttm_id: string | null; run_id: string | null; domain_id: string | null;
  domain_name: string | null; source_system: string | null; source_entities: string[]; target_entity: string; status: string;
  origin: string; line_count: number; created_by: string; created_at: string;
};
export type SttmVersion = { version: number; origin: string; status: string; change_note: string | null; line_count: number; sttm_id: string | null; run_id: string | null; created_by: string; created_at: string };
export type SttmEntry = SttmSummary & { lines: SttmLine[]; table_design: Record<string, unknown>; change_note: string | null; target_table_id: string | null };
export type SttmDiff = {
  from: number; to: number; summary: string; added: SttmLine[]; removed: SttmLine[];
  changed: { target_column: string; fields: string[]; from: Record<string, unknown>; to: Record<string, unknown> }[];
};

// ---------------------------------------------------------------- modeling

export type ModelAttribute = {
  name: string; datatype: string; nullable: boolean; is_pk: boolean; source_columns: string[]; derived: boolean;
  transformation_hint?: string; rationale?: string;
};
export type ModelEntity = { entity_name: string; kind: string; purpose: string; grain: string; business_keys: string[]; attributes: ModelAttribute[] };
export type ModelRelationship = { from_entity: string; from_column: string; to_entity: string; to_column: string; cardinality: string };
export type ModelSpec = {
  domain: string; mode: "MAP" | "FORK" | "NEW"; parent_domain: string | null; description: string;
  target_platform: "SNOWFLAKE" | "ICEBERG"; target_database: string; target_schema: string; external_volume: string | null;
  source_tables: string[]; design: { entities: ModelEntity[]; decision?: string; primary_entity?: string }; relationships: ModelRelationship[];
};
export type ModelIssue = { path: string; severity: "ERROR" | "WARN"; message: string };
export type ModelDesignRow = {
  design_id: string; version: number; domain_name: string; mode: string; parent_domain: string | null; status: string;
  branch: string | null; pr_url: string | null; created_by: string; created_at: string;
};
export type ModelContext = {
  domains: { domain_id: string; domain_name: string; description?: string | null }[]; designs: ModelDesignRow[];
  repos: { repo_id: string; name: string; git_url: string; provider: string; branch: string; dbt_project_dir: string }[];
  deployed: boolean; publisher_ready: boolean;
};
export type ModelRender = {
  spec: ModelSpec; issues: ModelIssue[]; ddl: string; domain_pack: Record<string, unknown>; pack_problems: string[];
  dbt: Record<string, string>; branch: string;
};

// ---------------------------------------------------------------- quality

export type QualitySummary = {
  days: number;
  data_quality: { scans: number; avg_health: number | null; failed_checks: number; warned_checks: number; checks: number };
  qa: { runs: number; tests: number; failed: number; errors: number; pass_rate: number | null };
  incidents: { open: number }; suites: number;
};
export type Execution = {
  id: string; kind: "SODA" | "QA"; run_id: string | null; run_name: string | null; target: string | null; mode: string | null;
  total: number | null; passed: number | null; warned: number | null; failed: number | null; errors: number | null;
  health: number | null; triggered_by: string | null; started_at: string; duration_ms: number | null;
};
export type Gate = { gate: string; status: "PASS" | "FAIL" | "PENDING"; detail: string };
export type GateRun = { run_id: string; run_name: string; current_state: string; target_model: string | null; updated_at: string; status: "READY" | "BLOCKED" | "PENDING"; gates: Gate[]; score: number };
export type Incident = {
  link_id: string; issue_key: string; summary: string | null; status: string | null; status_category: string | null;
  origin: string | null; run_id: string | null; run_name: string | null; target_table: string | null; linked_at: string;
};
export type Suites = {
  qa_suites: { suite_id: string; name: string; description: string | null; is_default: boolean; target_table: string | null; tests: number }[];
  check_packs: { run_id: string; run_name: string | null; target_table: string | null; checks: number; approved: number; proposed: number }[];
};

export type OnboardingCheck = {
  ok: boolean;
  pack?: { ok: boolean; problems: string[]; domain: string; exists: boolean; summary: { targets: number; columns: number; knowledge: number } };
  contract?: { ok: boolean; kind: "yaml" | "markdown"; problems: string[]; summary: Record<string, unknown> };
};

export type SttmFilter = { table: string | null; condition: string; reason: string | null };
export type SttmContext = {
  run_id: string | null; tables: string[];
  columns: Record<string, { column: string; data_type: string | null }[]>;
  profiles: Record<string, { data_type: string | null; semantic_type: string | null; pii_classification: string | null; null_pct: number | null;
    distinct_count: number | null; cardinality: string | null; min_value: string | null; max_value: string | null; potential_key: boolean;
    top_values: { value: unknown; count: number }[] | null; row_count: number | null }>;
  candidates: { source_table: string; source_column: string; source_datatype: string | null; target_column: string; final_score: number;
    confidence: number | null; rank: number; recommendation: string; generated_reason: string | null; transformation: string | null }[];
  decisions: { target_column: string; decision: string; transformation: string | null; business_justification: string | null;
    reviewer: string | null; decided_at: string | null }[];
};
