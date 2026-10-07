export type StageStatusValue = "COMPLETE" | "ACTIVE" | "REVIEW_REQUIRED" | "LOCKED" | "FAILED" | "BLOCKED" | "CANCELLED";

export type StageStatus = { stage: string; status: StageStatusValue; note?: string };

export type LaneInfo = { status: StageStatusValue; note: string; done: boolean };
export type RunLanes = {
  SODA: LaneInfo; QA: LaneInfo; VALIDATION: { done: boolean };
  gate: { ready: boolean; waiting_on: string[] };
};

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

export type StorageType = "IN_PLACE" | "MANAGED" | "ICEBERG";

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
  /** Parallel lanes after the STTM, from the work actually done (absent before the STTM is approved). */
  lanes?: RunLanes;
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
  connection_type?: string | null; landed_tables?: number | null; last_landed_at?: string | null;
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

export type UiBands = { confident: number; weak: number; model_match_strong: number; join_strong: number };

export const DEFAULT_BANDS: UiBands = { confident: 0.8, weak: 0.45, model_match_strong: 0.5, join_strong: 0.85 };

export type MappingOverview = {
  bands?: UiBands;
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

export type ProfileStatus = "UNPROFILED" | "PROFILING" | "STAGED_READY_FOR_MODELING" | "STALE" | "FAILED";

export type SourceOverviewItem = SourceConnection & {
  health: "HEALTHY" | "UNREACHABLE" | "NOT_LANDED"; health_detail: string; table_count: number | null;
  staged_tables: number; profiling_tables: number; failed_tables: number;
  last_profiled_at: string | null; active_jobs: number;
};

export type SourcesOverview = {
  sources: SourceOverviewItem[];
  totals: { sources: number; tables: number; staged: number; profiling: number };
};

export type InventoryTable = {
  table_name: string; table_type: string; row_count: number | null; bytes: number | null; column_count: number;
  last_altered: string | null; status: ProfileStatus; domain_name: string | null; stage_path: string | null;
  profiled_at: string | null; profiled_by: string | null; avg_null_percentage: number | null;
  key_candidates: number | null; pii_columns: number | null; is_approximate: boolean | null; error_message: string | null;
  quality?: Scorecard | null;
  domain_inferred?: boolean; domain_confidence?: number | null;
};

export type DomainCandidate = {
  domain_id: string; domain_name: string; confidence: number; signals: string[]; matched_terms: string[];
};

export type SourceInventory = {
  source: { source_system_id: string; source_system_name: string; source_type: string; database_name: string; schema_name: string };
  tables: InventoryTable[];
  jobs: { job_id: string; tables: string[]; started_at: number }[];
  domain_candidates?: DomainCandidate[];
};

export type ProfileColumn = {
  column_name: string; data_type: string; family: string; cardinality: string | null; semantic_type: string;
  pii_classification: string; potential_key: boolean; description?: string | null;
  patterns: { pattern: string; count: number }[];
  sample_values: { value: string | null; count: number }[];
  statistics: {
    row_count: number; null_count: number; null_percentage: number; distinct_count: number | null;
    distinct_percentage?: number; min?: string | null; max?: string | null; min_length?: number; max_length?: number;
    avg_length?: number; average?: number; enum_values?: string[] | null; date_format?: string | null;
    frequency_distribution?: { value: string | null; count: number }[];
    histogram?: { lower: number; upper: number; count: number }[];
  };
};

export type TableProfileDoc = {
  entry: { profile_stage_path: string; profiled_at: string; profiled_by: string | null; is_approximate: boolean | null };
  profile: {
    profiler_version: string; row_count: number; column_count: number; approximate: boolean;
    model_version: string | null; profiled_at: string; columns: ProfileColumn[];
    source: { source_name: string; database: string; schema: string; table: string };
  };
};

export type CatalogInventory = {
  database: string;
  schema: string;
  source: { source_system_id: string; source_system_name: string } | null;
  tables: InventoryTable[];
  jobs: { job_id: string; tables: string[]; started_at: number }[];
  domain_candidates?: DomainCandidate[];
};

export type ProfileStoreRow = {
  source_name: string; database_name: string; schema_name: string; table_name: string;
  row_count: number | null; column_count: number | null; profile_stage_path: string;
  is_approximate: boolean | null; profiled_by: string | null; profiled_at: string;
  status: string | null; status_updated_at: string | null; error_message: string | null;
  avg_null_percentage: number | null; key_candidates: number | null; pii_columns: number | null;
};

export type Scorecard = {
  overall: number | null;
  grade: string;
  dimensions: { completeness: number | null; uniqueness: number | null; validity: number | null; freshness: number | null };
  age_days: number | null;
};

export type SuggestedCheck = {
  check: string; column: string | null; reason: string; valid_values?: string[]; valid_regex?: string;
};

export type Drift = {
  added: string[]; removed: string[]; retyped: { column: string; from: string; to: string }[];
  row_count: { before: number | null; after: number | null; delta: number | null; pct: number | null };
  profiled_at: string | null; schema_changed: boolean; changed: boolean;
};

export type TableInsights = TableProfileDoc & {
  scorecard: Scorecard; checks: SuggestedCheck[]; checks_yaml: string; drift: Drift | null;
};

export type Relationship = {
  left: string; right: string; keys: string[]; cardinality: string; confidence: number; evidence?: string[]; source?: string;
};

export type AnalyzeResult = {
  tables: {
    table: string; staged: boolean; row_count: number | null; column_count: number | null;
    quality: Scorecard | null; key_candidates: string[]; pii_columns: string[];
  }[];
  relationships: Relationship[];
  graph: import("@/app/onboarding/intent-types").ModelGraph;
  domain?: { detected: DomainCandidate | null; candidates: DomainCandidate[] };
  suggested_standard?: "GDP" | "GENERIC";
  bands?: UiBands;
  models: {
    related: boolean;
    suggestions: { kind: "existing" | "proposed"; target_table: string; fqn: string; domain_name?: string | null;
      score: number; overlap_columns: string[]; reason: string }[];
    targets: { target_table_id: string; domain_name: string; target_table: string; fqn: string }[];
  };
};

export type Connector = {
  id: string; label: string; kind: "FILE" | "DATABASE" | "SAAS" | "API"; landable: boolean;
  fields: string[]; guidance: string | null;
};

export type ExternalFile = { path: string; size: number | null; last_modified: string };

export type LandResult = {
  source_system_id: string; database: string; schema: string;
  tables: { table: string; files: string[]; status: "LOADED" | "FAILED"; rows_loaded: number; error: string | null }[];
};
