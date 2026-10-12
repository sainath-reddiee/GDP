// QA Store (services/qa/packs.py) and Data Quality Store (services/catalog/dq.py) payloads.

export type PackStatus = "DRAFT" | "PENDING_APPROVAL" | "APPROVED" | "SUPERSEDED" | "RETIRED";
export type PackSeverity = "ERROR" | "WARN" | "INFO";

export type PackLastRun = { tests: number; passed: number; failed: number; errors: number; review: number; started_at: string } | null;

export type PackRow = {
  pack_id: string; version: number; domain_id: string; domain_name: string | null; name: string; description: string | null;
  tags: string[]; status: PackStatus; source: "UI" | "REPO"; approved_version: number | null; created_by: string;
  created_at: string; queries: number; last_run: PackLastRun;
  repo_conflict?: boolean; repo_path?: string | null; repo_sha?: string | null;  // V042
};

export type PackQuery = {
  query_id?: string; query_name: string; description: string | null; sql_expression: string; severity: PackSeverity;
  expectation: string | null; tags: string[]; ordinal?: number; created_by?: string; updated_at?: string | null;
};

export type PackVersion = {
  version: number; status: PackStatus; change_note: string | null; created_by: string; created_at: string;
  approved_by: string | null; approved_at: string | null;
  source?: "UI" | "REPO"; repo_path?: string | null; pr_state?: string | null; pr_url?: string | null; pr_number?: number | null; branch?: string | null;
};
/** A repository change the deploy could not apply because the pack was edited in the app since (V042). */
export type PackConflict = {
  sync: { sync_id: string; pack_id: string; repo_path: string | null; repo_sha: string; app_version: number; base_repo_sha: string | null; detected_at: string | null };
  repo: { name: string; description: string; tags: string[]; queries: PackQuery[] };
  app: { version: number; status: PackStatus; queries: PackQuery[] };
  diff: { added: string[]; removed: string[]; changed: { query_name: string; fields: string[] }[] };
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
  suite?: DqSuiteState | null;  // V040
};
/** A table's suite state on the catalog page (V040). */
export type DqSuiteState = {
  table_name: string; domain_id: string | null; approved_version: number | null; open_version: number | null;
  open_status: "DRAFT" | "IN_REVIEW" | null; open_authors: string[]; submitted_by: string | null; pr_state: string | null;
  model_version: number | null;
};
export type DqSuiteCheck = {
  check_id: string; target_column: string | null; check_type: string; severity: "WARN" | "FAIL"; requirement: string | null;
  definition: Record<string, unknown>; soda_dsl: string; headline: string; status: "ACTIVE" | "PAUSED";
  origin: "PROFILE" | "RUN" | "USER"; source_run_id: string | null;
};
export type DqSuiteChange = {
  check_id: string; kind: "ADDED" | "REMOVED" | "CHANGED"; class: "TIGHTENED" | "LOOSENED" | "CHANGED" | "DOCUMENTATION";
  column: string | null; headline: string | null; from_headline?: string | null; fields?: string[]; message: string;
};
export type DqSuiteDiff = { changes: DqSuiteChange[]; counts: Record<DqSuiteChange["class"], number>; loosened: boolean };
export type DqSuiteIssue = { check_id: string | null; severity: "ERROR" | "WARN"; message: string };
export type DqSuiteVersion = {
  version: number; status: "DRAFT" | "IN_REVIEW" | "APPROVED" | "PUBLISHED" | "SUPERSEDED" | "REJECTED"; base_version: number | null;
  etag: string | null; authors: string[]; origin: string | null; change_note: string | null; model_id: string | null;
  model_version: number | null; submitted_by: string | null; submitted_at: string | null; approved_by: string | null;
  approved_at: string | null; rejected_by: string | null; rejected_at: string | null; reject_reason: string | null;
  review_summary: Record<string, unknown> | null; catalog_synced_at: string | null; repo_id: string | null; branch: string | null;
  pr_url: string | null; pr_number: number | null; pr_state: string | null; merged_sha: string | null; pr_synced_at: string | null;
  created_by: string | null; created_at: string | null;
};
export type DqSuiteModel = {
  model_id: string; model_version: number; entity: string | null; entity_status?: string; columns: Record<string, string> | null; note?: string;
};
export type DqSuiteDetail = {
  suite: { suite_id: string; table_name: string; domain_id: string | null; domain_name: string | null; model_id: string | null;
           approved_version: number | null; open_version: number | null } | null;
  table?: string; versions: DqSuiteVersion[]; open: DqSuiteVersion | null;
  current: (DqSuiteVersion & { checks: DqSuiteCheck[]; issues: DqSuiteIssue[]; model: DqSuiteModel | null; changes: DqSuiteDiff }) | null;
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
  sources: { total: number; shares: number; databases: number; other: number; external: number; external_active: number } | null;
  profiling: {
    tables: number; columns: number; pii: number; catalog_tables: number; high_cardinality: number; drift: number;
    drift_tables: number; health: number | null;
  } | null;
  /** domain models (Model Workbench), table models (Table Model Store) and their pull requests */
  models: { domain_models: number; approved: number; published: number; drafts: number; in_review: number; table_models: number;
            tables_published: number; prs_open: number; failed: number; run_designs: number; legacy_designs: number } | null;
  /** approved follows the run STTM a catalog version came from; pending is REVIEW only */
  sttm: { mappings: number; approved: number; pending: number; drafts: number } | null;
  /** saved tests, tests executed in the last 7 days, and passed over executed (PASS, FAIL, ERROR) of each test's latest result */
  qa: (PackSummary & { saved_tests: number; tests_7d: number; executed_tests: number; failed_tests: number; test_pass_rate: number | null }) | null;
  /** coverage: registered target tables with at least one check */
  dq: (DqSummary & { run_checks: number; store_checks: number; targets: number; covered_targets: number }) | null;
  /** exact counts over the run list's population (not TEST, archived or deleted) */
  runs: { in_flight: number; drafts: number; needs_review: number; failed_7d: number | null } | null;
  /** runs over the last 7 days, by each run's latest outcome; daily has all 7 days */
  delivery: { completed: number; failed: number; started: number; active: number; success_rate: number | null; lead_minutes: number | null;
              daily: { day: string; completed: number; failed: number }[] } | null;
  /** waiting on a person */
  waiting: { runs: number; requests: number; versions: number; total: number; oldest_minutes: number | null } | null;
  /** landed tables */
  freshness: { tables: number; fresh: number; aging: number; stale: number; failed_loads: number; idle_pipelines: number; oldest_hours: number | null } | null;
  /** checks failing now */
  failing: { dq_checks: number; qa_tests: number; total: number; tables: number } | null;
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
