// QA Store (services/qa/packs.py) and Data Quality Store (services/catalog/dq.py) payloads.

export type PackStatus = "DRAFT" | "PENDING_APPROVAL" | "APPROVED" | "SUPERSEDED" | "RETIRED";
export type PackSeverity = "ERROR" | "WARN" | "INFO";

export type PackLastRun = { tests: number; passed: number; failed: number; errors: number; review: number; started_at: string } | null;

export type PackRow = {
  pack_id: string; version: number; domain_id: string; domain_name: string | null; name: string; description: string | null;
  tags: string[]; status: PackStatus; source: "UI" | "REPO"; approved_version: number | null; created_by: string;
  created_at: string; queries: number; last_run: PackLastRun;
};

export type PackQuery = {
  query_id?: string; query_name: string; description: string | null; sql_expression: string; severity: PackSeverity;
  expectation: string | null; tags: string[]; ordinal?: number; created_by?: string; updated_at?: string | null;
};

export type PackVersion = {
  version: number; status: PackStatus; change_note: string | null; created_by: string; created_at: string;
  approved_by: string | null; approved_at: string | null;
};

export type PackDetail = {
  pack: PackRow & { change_note: string | null; submitted_by: string | null; submitted_at: string | null; approved_by: string | null; approved_at: string | null };
  queries: PackQuery[]; approved_version: number | null; versions: PackVersion[];
};

export type PackSummary = {
  packs: number; approved_packs: number; rules: number; pending: number; domains: number; top_tags: string[];
  pass_rate: number | null; deployed?: boolean; run_tests?: number;
};

export type QaOutcome = "PASS" | "FAIL" | "REVIEW" | "NOT_RUN" | "ERROR";
export type QaResult = {
  test_id: string | null; title: string; severity: string; pack_severity?: PackSeverity; expected: string | null;
  outcome: QaOutcome; detail?: string | null; measured?: unknown; rows_returned?: number; columns?: string[];
  sample?: Record<string, unknown>[]; masked?: string[]; sql?: string | null; duration_ms?: number;
};
export type QaExecution = {
  stored: boolean; allowed: string[]; results: QaResult[]; qa_run_id?: string; tests?: number; passed?: number;
  failed?: number; errors?: number; review?: number; duration_ms?: number;
};
export type PackRun = {
  qa_run_id: string; version: number | null; tests: number; passed: number; failed: number; review: number; errors: number;
  not_run: number; duration_ms: number | null; triggered_by: string | null; created_by: string | null; started_at: string;
};

// ---------------------------------------------------------------- data quality store

export type DqOutcome = "PASS" | "WARN" | "FAIL" | "ERROR" | "NOT_EVALUATED";
export type DqCheck = {
  check_id: string; table_name: string; column_name: string | null; check_type: string;
  check_definition: Record<string, unknown>; soda_dsl: string; headline: string; severity: "WARN" | "FAIL";
  status: "ACTIVE" | "PAUSED" | "RETIRED"; origin: "PROFILE" | "RUN" | "USER"; domain_id: string | null;
  schedule_cron: string | null; last_run_status: DqOutcome | null; last_run_at: string | null; last_measured: number | null;
  created_by: string; created_at: string; updated_at: string | null;
  requirement?: string | null; source_run_id?: string | null;  // V038
};
export type DqTable = {
  table: string; domain_id: string | null; schedule_cron: string | null; checks: number; counts: Record<string, number>;
  pass_rate: number | null; columns: { column: string | null; checks: DqCheck[] }[];
  schedule: { cron: string; enabled: boolean } | null;
};
export type DqCatalog = { tables: DqTable[]; deployed: boolean; checks?: number };
export type DqPoint = { outcome: DqOutcome; measured: number | null; failed_rows: number | null; detail: string | null; at: string };
export type DqHistory = {
  scans: { scan_id: string; started_at: string; checks: number; passed: number; warned: number; failed: number; errors: number;
           not_evaluated: number; health: number | null; rows_scanned: number | null; triggered_by: string | null; duration_ms: number | null }[];
  checks: Record<string, DqPoint[]>;
};
export type DqSummary = {
  deployed: boolean; checks: number; tables: number; passed?: number; warned?: number; failed?: number; never_run?: number;
  scheduled_tables?: number; pass_rate: number | null; coverage: number | null; run_checks?: number;
};
export type DqScan = {
  scan_id: string; table: string; checks: number; passed: number; warned: number; failed: number; not_evaluated: number;
  errors: number; health: number | null; rows_scanned: number | null; duration_ms: number;
  results: { catalog_check_id: string; target_column: string | null; check_type: string | null; outcome: DqOutcome;
             measured: number | null; threshold: string | null; failed_rows: number | null; detail: string | null }[];
};

// ---------------------------------------------------------------- command center

export type Overview = {
  domain: { domain_id: string; domain_name: string } | null;
  ingested: {
    bytes: number; gb: number; tables: number; rows: number; in_place: number; measured: "landed" | "source" | "mixed";
    last_day: string | null; daily: { day: string; bytes: number; gb: number; tables: number }[];
  } | null;
  sources: { total: number; shares: number; databases: number; other: number; external: number; external_active: number } | null;
  profiling: {
    tables: number; columns: number; pii: number; catalog_tables: number; high_cardinality: number; drift: number;
    drift_tables: number; health: number | null;
  } | null;
  models: { designs: number; published: number; branches: number; in_design: number; failed: number; run_designs: number; hub_designs: number } | null;
  sttm: { mappings: number; verified: number; pending: number } | null;
  qa: (PackSummary & { run_tests: number; executed_tests: number; run_suites: number; run_pass_rate: number | null }) | null;
  dq: (DqSummary & { run_checks: number; store_checks: number }) | null;
};

// ---------------------------------------------------------------- run assets in the stores (hub_runs_api)

export type RunModel = {
  design_id: string; run_id: string; run_name: string | null; current_state: string | null; domain_id: string | null;
  domain_name: string | null; version: number; status: string; origin: string | null; decision: string | null;
  created_by: string | null; created_at: string; approved_by: string | null; publication_status: string | null;
  head_branch: string | null; pr_url: string | null; pr_number: number | null;
  entities: { name: string | null; kind: string | null; columns: number }[]; entity_count: number;
};
export type DbtGeneration = {
  generation_id: string; run_id: string; run_name: string | null; current_state: string | null; domain: string | null;
  target_model: string | null; generation_version: number; generation_status: string; files_generated: number | null;
  parent_generation_id: string | null; created_by: string | null; created_at: string; publication_status: string | null;
  published_generation_id: string | null; head_branch: string | null; pr_url: string | null; pr_number: number | null;
  sttm_changed: boolean;
};
export type DbtFile = { artifact_id: string; artifact_type: string; file_path: string; content_sha256: string; content: string };
export type DbtGenerationDetail = {
  generation: { generation_id: string; run_id: string; parent_generation_id: string | null; domain: string | null; target_model: string | null;
                sttm_version: number | null; generation_version: number; generation_status: string; created_by: string | null; created_at: string };
  run: { run_name: string | null; current_state: string | null; domain_id: string | null } | null;
  files: DbtFile[];
  versions: { generation_id: string; generation_version: number; generation_status: string; parent_generation_id: string | null; created_by: string | null; created_at: string }[];
  publication: { status: string; head_branch: string | null; base_branch: string | null; pr_url: string | null; pr_number: number | null; generation_id: string | null; created_at: string } | null;
  latest: boolean; published: boolean;
};
export type RunCheck = {
  expectation_id: string; run_id: string; run_name: string | null; current_state: string | null; domain_name: string | null;
  target_table: string; target_column: string | null; check_type: string; definition: Record<string, unknown>;
  evidence: unknown; backtest: { status?: string; detail?: string } | null; severity: "FAIL" | "WARN"; origin: string | null;
  client_requirement: string | null; status: "PROPOSED" | "APPROVED" | "REJECTED"; version: number; reviewed_by: string | null;
  sodacl: string | null; result_id: string | null; outcome: DqOutcome | null; measured: number | null; failed_rows: number | null;
  detail: string | null; sample_rows: Record<string, unknown>[] | null; sql_text: string | null; checked_at: string | null;
};
export type RunCheckRun = { run_id: string; run_name: string | null; domain_name: string | null; current_state: string | null;
  checks: number; proposed: number; approved: number; rejected: number; failing: number };
export type RunCheckCoverage = { run_id: string; columns: number; covered: number; coverage: number | null;
  missing: { target_column: string; mapping_type: string | null }[] };
export type RunTest = {
  test_id: string; run_id: string | null; run_name: string | null; domain_id: string | null; domain_name: string | null;
  target_table_id: string | null; scope: string | null; target_fqn: string | null; category: string | null; title: string;
  objective: string | null; sql_text: string; expected: string | null; severity: string | null; target_column: string | null;
  origin: string | null; created_by: string | null; created_at: string; history: { outcome: string; ran_at: string }[];
  result_id: string | null; outcome: string | null; rows_returned: number | null; detail: string | null;
  sample_rows: Record<string, unknown>[] | null; ran_at: string | null;
};
export type RunTestSummary = { tests: number; executed: number; passed: number; failed: number; pass_rate: number | null };
