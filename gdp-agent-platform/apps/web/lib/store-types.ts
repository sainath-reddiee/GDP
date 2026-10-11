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
  pass_rate: number | null; deployed?: boolean;
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
  scheduled_tables?: number; pass_rate: number | null; coverage: number | null;
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
  ingested: { bytes: number; gb: number; tables: number; rows: number; last_day: string | null; daily: { day: string; gb: number; tables: number }[] } | null;
  sources: { total: number; shares: number; databases: number; other: number; external: number; external_active: number } | null;
  profiling: { tables: number; columns: number; high_cardinality: number; pii: number; drift: number; drift_tables: number; health: number | null } | null;
  models: { designs: number; published: number; branches: number; in_design: number; failed: number } | null;
  sttm: { mappings: number; verified: number; pending: number } | null;
  qa: PackSummary | null;
  dq: DqSummary | null;
};
