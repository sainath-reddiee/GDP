export type StageStatusValue = "COMPLETE" | "ACTIVE" | "REVIEW_REQUIRED" | "LOCKED" | "FAILED" | "BLOCKED" | "CANCELLED";

export type StageStatus = { stage: string; status: StageStatusValue };

export type AllowedTransition = { to_state: string; actor: "SYSTEM" | "HUMAN" | "ANY" };

export type RunInfo = {
  run_name: string;
  target_model: string | null;
  source_system_id: string | null;
  source_database: string | null;
  source_schema: string | null;
  environment: string;
  created_by: string;
  created_at: string;
  domain_name?: string | null;
  domain_id?: string | null;
  landing_database?: string | null;
  landing_schema?: string | null;
  storage_type?: StorageType | null;
  archived_at?: string | null;
  archived_by?: string | null;
};

export type Lifecycle = "DRAFT" | "RUNNING" | "COMPLETED" | "FAILED" | "ARCHIVED";

export type RunStatusFilter = "all" | "active" | "completed" | "failed" | "archived";

export type StorageType = "MANAGED" | "ICEBERG";

export type RunState = {
  run_id: string;
  current_state: string;
  current_stage: string | null;
  status: string;
  lifecycle?: Lifecycle;
  is_archived?: boolean;
  state_version: number;
  failed_from_state: string | null;
  failure_reason: string | null;
  stages: StageStatus[];
  allowed_transitions: AllowedTransition[];
  run: RunInfo;
};

export type RunSummary = {
  run_id: string;
  run_name: string;
  current_state: string;
  current_stage: string | null;
  status: string;
  lifecycle: Lifecycle;
  is_archived: boolean;
  target_model: string | null;
  created_by: string;
  created_at: string;
  updated_at?: string | null;
  source_system_name?: string | null;
  source_database?: string | null;
  source_schema?: string | null;
  domain_name?: string | null;
  age_minutes?: number | null;
  table_count: number;
};

export type CleanupResult = {
  requested: number;
  not_found: string[];
  deleted: string[];
  skipped: { run_id: string; reason: string }[];
  landing: { dropped: string[]; kept: { table: string; reason: string; runs?: string[] }[]; errors: { table: string; error: string }[] };
  workspaces: { run_id: string; files_removed: number; projects_dropped: string[]; errors: string[] }[];
  profiles_preserved: boolean;
};

export type ArchiveResult = { archived: boolean; changed: string[]; skipped: { run_id: string; reason: string }[] };

export type ProfileCacheTable = {
  table_name: string;
  status: "CACHED" | "UNPROFILED";
  profiled_at: string | null;
  row_count: number | null;
  column_count: number | null;
  is_approximate: boolean | null;
  profiled_in_run: string | null;
};

export type CachedProfile = {
  source_name: string; database_name: string; schema_name: string; table_name: string;
  row_count: number | null; column_count: number | null; is_approximate: boolean | null;
  profiled_in_run: string | null; profiled_by: string | null; profiled_at: string;
};

export type SourceConnection = {
  source_system_id: string; source_system_name: string; source_type: string;
  owner: string | null; security_classification: string | null;
  database_name: string; schema_name: string; created_at: string; runs: number; last_run_at: string | null;
};

export type LandingTargets = {
  default: { landing_database: string; landing_schema: string; storage_type: StorageType };
  database: string;
  schemas: string[];
  iceberg_available: boolean;
};

export type SourceOverview = {
  source: {
    source_system_id: string; source_system_name: string; source_type: string; owner: string | null;
    security_classification: string | null; source_database: string; source_schema: string;
  } | null;
  objects: {
    object_name: string; object_type: string; row_count_estimate: number | null; bytes: number | null;
    last_altered: string | null; selected_flag: boolean;
  }[];
  checks: { check_name: string; status: "PASSED" | "FAILED" | "WARNING"; detail: string | null; remediation: string | null; checked_at: string }[];
  landing: {
    landing_id: string; source_table: string; landing_table: string; ingestion_method: string;
    ingestion_status: string; source_row_count: number | null; row_count: number | null; query_id: string | null;
    error_message: string | null; created_at: string; columns: number;
  }[];
};

export type MappingOverview = {
  candidates: {
    candidate_id: string; source_column_id: string; source_column: string; source_datatype: string;
    source_table: string; target_column_id: string; target_column: string; target_datatype: string;
    nullable: boolean; is_business_key: boolean; final_score: number; rank: number; confidence: number;
    recommendation: string; generated_reason: string | null; transformation: string | null;
    semantic_score: number; keyword_score: number; datatype_score: number; statistical_score: number;
    domain_score: number; context_score: number; historical_score: number;
    llm_preferred?: string | null; llm_agrees?: boolean | null;
  }[];
  decisions: {
    decision_id: string; source_column_id: string; target_column_id: string | null; candidate_id: string | null;
    decision: string; transformation: string | null; business_justification: string | null; reviewer: string;
    reviewed_at: string;
  }[];
  targets: {
    target_column_id: string; column_name: string; data_type: string; nullable: boolean;
    is_business_key: boolean; semantic_type: string; is_pii?: boolean | null; definition?: string | null;
  }[];
  target_table?: { target_table_id: string; target_table: string; target_database: string; target_schema: string; grain?: string | null } | null;
  profile?: Record<string, MappingProfile>;
  status: { source_columns: number; decided: number; undecided: string[]; missing_required_targets: string[]; complete: boolean };
};

export type MappingProfile = {
  source_column_id: string; source_table: string; column_name: string; data_type: string;
  semantic_type: string | null; null_percentage: number | null; distinct_percentage: number | null;
  cardinality: number | null; description: string | null; pii: string | null; values: unknown[] | null;
};

export type MappingSuggestion = {
  source_column_id: string;
  action: "APPROVE" | "MODIFY" | "ALTERNATIVE" | "NULL";
  target_column_id: string | null;
  target_column: string | null;
  candidate_id: string | null;
  transformation: string | null;
  confidence: number;
  reason: string;
  note: string | null;
  decision: Record<string, unknown>;
};

export type WhoAmI = { user: string; role: string; auth_mode: "dev" | "pat"; agent: string | null };

export type AuditEvent = {
  event_id: string;
  run_id?: string;
  run_name?: string;
  from_state: string | null;
  to_state: string;
  actor_type: string;
  actor: string;
  reason: string | null;
  created_at: string;
};
