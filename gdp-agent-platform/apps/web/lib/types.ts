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

export type RunStatusFilter = "all" | "active" | "draft" | "completed" | "failed" | "archived";

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
  tags?: string[];
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
  database_name?: string | null;
  schema_name?: string | null;
  key_candidates?: number | null;
  pii_columns?: number | null;
  avg_null_percentage?: number | null;
  quality?: Scorecard | null;
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
  location?: string | null;
  oracle?: {
    host: string; port: string; service: string; schema_owner: string; runtime: "snowflake" | "api_host";
    protocol?: string | null; ready: boolean; schedule?: string | null;
    health?: { status: "ok" | "warn" | "fail"; at: string; headline: string; version?: string | null } | null;
    last_job?: { kind: string; status: string; at: string; tables: number; failed: string[]; rows: number } | null;
    running?: { kind: string; tables: number; done: number } | null;
  } | null;
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

export type WhoAmI = {
  user: string; role: string; auth_mode: "dev" | "pat"; agent: string | null;
  governance?: boolean; roles?: string[]; granted_roles?: string[]; privileges?: string[]; pending_for_me?: number;
  read_only?: boolean;
};

/** Does the signed-in user hold a privilege? Everything is allowed until governance is deployed. */
export function can(me: WhoAmI | null | undefined, privilege: string): boolean {
  if (!me || !me.governance) return true;
  const p = me.privileges ?? [];
  return p.includes("*") || p.includes(privilege);
}

/** May act directly, or raise a change request routed to the approver role. */
export function canAct(me: WhoAmI | null | undefined, privilege: string): boolean {
  return can(me, privilege) || can(me, "REQUEST.CHANGES");
}

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
  tags?: string[];
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

export type ModelMatch = {
  kind: "existing" | "proposed"; target_table: string; fqn: string; domain_name?: string | null;
  score: number; overlap_columns: string[]; reason: string;
  coverage_target?: number; coverage_source?: number; matched?: [string, string, string][];
  missing_target_columns?: string[]; unmatched_source_columns?: string[]; evidence?: string[];
};
export type ModelRecommendation = {
  action: "MAP_EXISTING" | "EXTEND_EXISTING" | "REVIEW" | "NEW"; fqn: string | null; target_table: string;
  confidence: number; headline: string; why: string[];
};

export type AnalyzeResult = {
  tables: {
    table: string; staged: boolean; row_count: number | null; column_count: number | null;
    quality: Scorecard | null; key_candidates: string[]; pii_columns: string[];
  }[];
  relationships: Relationship[];
  graph: import("@/app/onboarding/intent-types").ModelGraph;
  domain?: { detected: (DomainCandidate & { inherited_from_schema?: boolean }) | null; candidates: DomainCandidate[];
             schema?: DomainCandidate | null };
  suggested_standard?: "GDP" | "GENERIC";
  bands?: UiBands;
  models: {
    related: boolean;
    suggestions: ModelMatch[];
    recommendation?: ModelRecommendation;
    proposed?: ModelMatch | null;
    targets: { target_table_id: string; domain_name: string; target_table: string; fqn: string }[];
  };
};

export type Connector = {
  id: string; label: string; kind: "FILE" | "DATABASE" | "SAAS" | "API"; landable: boolean;
  fields: string[]; guidance: string | null;
  /** set when the platform extracts and lands the source itself (e.g. "oracle") */
  extractor?: string | null;
};

export type CheckStatus = "ok" | "warn" | "fail" | "skip";
export type OracleCheck = {
  id: string; label: string; status: CheckStatus; detail: string; fix?: string | null; ms?: number | null;
  code?: string | null; error?: string | null;
};
export type OracleTest = {
  ok: boolean; status: "ok" | "warn" | "fail"; headline: string; checks: OracleCheck[]; elapsed_ms?: number;
  server: {
    version?: string; database?: string; service?: string; container?: string; db_timezone?: string; charset?: string;
    nchar_charset?: string; account_status?: string; password_expires_in_days?: number | null;
    visible_tables?: number; visible_views?: number; skipped_columns?: number; latency_ms?: number;
  };
};

export type OracleHealth = {
  status: "ok" | "warn" | "fail"; at: string; headline: string; version?: string | null; latency_ms?: number | null;
  password_expires_in_days?: number | null;
} | null;

export type OracleLoad = {
  landed_as: string; oracle_table?: string; batch_id?: string; at: string; rows: number; mode: string;
  watermark_column: string | null; watermark: string | null; verified?: boolean | null; merge_keys?: string[] | null;
  skipped?: { column: string; reason: string }[];
};

export type OracleLoadEntry = {
  batch_id: string; mode: string; at: string; status: string; rows: number; read: number | null;
  verified: boolean | null; duration_s: number; drift: boolean; error: string | null; scheduled: boolean;
};

export type OracleTable = {
  table: string; type: "TABLE" | "VIEW" | "MVIEW" | "EXTERNAL"; estimated_rows: number | null;
  estimated_bytes: number | null; last_analyzed: string | null; stale_stats: boolean; comment: string | null;
  partitioned: boolean; temporary: boolean; iot: boolean; primary_key: string[]; column_count: number;
  skipped_columns: { column: string; reason: string }[];
  watermark: { column: string; reason: string } | null; extractable: boolean; not_extractable_reason: string | null;
  profile?: { row_count: number | null; status: string; profiled_at: string | null; pii_columns: number | null;
              key_candidates: number | null } | null;
  load?: OracleLoad | null;
};

export type OracleCatalog = {
  tables: OracleTable[]; landing: { database: string; schema: string }; runtime: "snowflake" | "api_host"; ready: boolean;
};

export type OracleColumn = {
  column_name: string; ordinal: number; oracle_type: string; data_type: string; snowflake_type: string | null;
  family: string; lob: boolean; free_number?: boolean; expr: string | null; skip_reason: string | null;
  nullable: boolean; comment: string | null; constraints: string[];
};

export type OraclePreview = {
  table: string; columns: string[]; rows: (string | number | null)[][]; skipped: { column: string; reason: string }[];
};

export type OracleProfileDoc = {
  profile: {
    row_count: number; approximate: boolean;
    columns: { column_name: string; data_type: string; source_type?: string; semantic_type: string;
               pii_classification: string; potential_key: boolean; constraints?: string[];
               statistics: { null_percentage?: number; distinct_count?: number | null } }[];
  };
  scorecard: { overall: number | null; grade: string | null } | null;
};

export type OracleSchedule = {
  cron: string; tables: string[]; mode: "append" | "merge" | "replace"; warehouse: string;
  watermark_columns: Record<string, string>; merge_keys: Record<string, string[]>; lookback_minutes: number;
  created_at: string;
} | null;

export type OracleExplain = { code: string | null; title: string; fix: string | null; retryable: boolean; detail: string };

export type IngestJobTable = {
  phase: string; rows?: number; files?: number; rows_loaded?: number; rows_extracted?: number; row_count?: number;
  rows_per_s?: number; started?: number; finished?: number; duration_s?: number; error?: string | null;
  explain?: OracleExplain | null; note?: string | null; profiled?: boolean; profile_error?: string | null;
  verification?: { read_from_oracle: number; copied: number; in_table: number; verified: boolean } | null;
  drift?: { added: Record<string, string>; missing: string[]; retyped: Record<string, { was: string; now: string }>;
            changed: boolean } | null;
  merged?: { inserted: number; updated: number } | null;
  skipped?: { column: string; reason: string }[];
};

export type IngestJob = {
  job_id: string; source_id: string; kind: "profile" | "ingest";
  status: "RUNNING" | "DONE" | "PARTIAL" | "FAILED" | "CANCELLED";
  started_at: number; finished_at: number | null; cancel_requested?: boolean;
  options?: Record<string, unknown>;
  tables: Record<string, IngestJobTable>;
  result: Record<string, unknown>[] | null; error: string | null;
};

export type OracleOverview = {
  id: string; name: string;
  connection: { host: string; port: string; service_name?: string | null; sid?: string | null; user: string;
                schema_owner: string; protocol?: string; ssl_server_dn_match?: string; runtime: "snowflake" | "api_host";
                password_env?: string | null; wallet_dir?: string | null };
  access: { secret: string | null; integration: string | null; procedure: string | null; created: string[];
            ready: boolean; password_env_present: boolean | null };
  landing: { database: string; schema: string };
  health: OracleHealth; loads: Record<string, OracleLoad>; history: Record<string, OracleLoadEntry[]>;
  schedule: OracleSchedule;
  last_scheduled_run: { at: string; tables: number; failed: string[]; rows: number } | null;
  last_job: { job_id: string; kind: string; status: string; at: string; tables: number; failed: string[]; rows: number;
              duration_s: number } | null;
  running_job: IngestJob | null;
};

export type ExternalFile = { path: string; size: number | null; last_modified: string };

export type LandResult = {
  source_system_id: string; database: string; schema: string;
  tables: { table: string; files: string[]; status: "LOADED" | "FAILED"; rows_loaded: number; error: string | null }[];
};
