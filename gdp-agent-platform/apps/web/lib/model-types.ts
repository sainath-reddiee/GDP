// Model Workbench (services/modeling/spec_v2.py, domain_model.py): one canonical, versioned model per domain.

export type ModelStatus = "DRAFT" | "IN_REVIEW" | "APPROVED" | "PUBLISHED" | "SUPERSEDED" | "REJECTED";
export type ChangeClass = "ADDITIVE" | "NON_FUNCTIONAL" | "BREAKING";

export type DmAttribute = {
  id: string; name: string; datatype: string; nullable: boolean; is_pk: boolean; description: string; business_definition: string;
  semantic_type: string | null; pii: { flag: boolean; classification: string | null }; accepted_values: string[];
  default: string | null; is_unique: boolean; check: string | null; status: "ACTIVE" | "RETIRED"; tags: string[];
  business_key: boolean; source_columns: string[]; derived: boolean; transformation_hint: string; rationale: string;
};
export type DmEntity = {
  id: string; entity_name: string; table_type: string; kind: string; scd_type: string; grain: string; business_keys: string[];
  description: string; purpose: string; subject_area: string | null; status: "ACTIVE" | "RETIRED";
  database: string | null; schema: string | null; model_spec: Record<string, unknown> | null;
  unique_constraints: string[][]; checks: { name: string; expression: string }[];
  physical: { cluster_by: string[]; transient: boolean; retention_days: number | null };
  attributes: DmAttribute[];
};
export type DmRelationship = {
  id: string; name: string; from_entity: string; from_column: string; to_entity: string; to_column: string;
  cardinality: "N:1" | "1:1" | "1:N"; from_optional: boolean; to_optional: boolean; enforce: "RELY" | "NONE";
};
export type DmSpec = {
  spec_version: 2; domain: string; mode: string; parent_domain: string | null; description: string;
  subject_areas: { name: string; description: string; color: string | null }[];
  naming_standard: Record<string, unknown>;
  physical_defaults: { platform: "SNOWFLAKE" | "ICEBERG"; database: string; schema: string;
    iceberg: { external_volume: string | null; catalog: string; base_location: string | null } };
  target_platform: "SNOWFLAKE" | "ICEBERG"; target_database: string; target_schema: string; external_volume: string | null;
  source_tables: string[]; design: { entities: DmEntity[]; primary_entity: string; decision: string }; relationships: DmRelationship[];
};
export type DmIssue = { path: string; severity: "ERROR" | "WARN"; message: string };
export type DmVersion = {
  version: number; status: ModelStatus; origin: string | null; base_version: number | null; etag: string | null; authors: string[];
  change_note: string | null; issues: DmIssue[]; review_summary: { counts: Record<ChangeClass, number>; breaking: boolean; base_version: number | null } | null;
  submitted_by: string | null; submitted_at: string | null; approved_by: string | null; approved_at: string | null;
  rejected_by: string | null; rejected_at: string | null; reject_reason: string | null; branch: string | null; pr_url: string | null;
  pr_number: number | null; pr_state: string | null; registry_synced_at: string | null; created_by: string | null; created_at: string | null;
  updated_at: string | null;
};
export type DmHeader = {
  model_id: string; domain_id: string; domain_name: string; approved_version: number | null; open_version: number | null;
  naming_standard: Record<string, unknown>; diagram_layout: Record<string, { x: number; y: number }>;
  forked_from_model_id: string | null; forked_from_version: number | null;
};
export type DmDetail = {
  model: DmHeader; versions: DmVersion[]; open: DmVersion | null;
  current: (DmVersion & { spec: DmSpec; issues: DmIssue[]; summary: { entities: number; columns: number; relationships: number; retired: number } }) | null;
};
export type DmListRow = {
  domain_id: string; domain_name: string; description: string | null; active_flag: boolean; model_id: string | null;
  approved_version: number | null; open_version: number | null; open_status: ModelStatus | null; submitted_by: string | null;
  open_authors: string[]; pr_state: string | null; pr_url: string | null; approved_at: string | null; approved_by: string | null;
  updated_at: string | null; counts: { entities: number; columns: number; relationships: number; retired: number } | null;
};
export type DmChange = {
  path: string; kind: string; class: ChangeClass; message: string; before: unknown; after: unknown; entity: string; column: string;
};
export type DmDiff = { from: number | null; to: number; changes: DmChange[]; counts: Record<ChangeClass, number>; breaking: boolean; entities_touched: string[] };
export type DmComment = {
  comment_id: string; path: string; parent_id: string | null; kind: "COMMENT" | "SUGGESTION" | "BLOCKER"; body: string; resolved: boolean;
  resolved_by: string | null; author: string; created_at: string | null;
};
export type DmImpact = {
  from: number | null; to: number; breaking: boolean; counts: Record<ChangeClass, number>;
  items: { entity: string; column: string | null; kind: string; refs: Record<string, Record<string, unknown>[]>; total: number }[];
  totals: Record<string, number>; unavailable: string[]; labels: Record<string, string>;
};

// Table Model Store (services/modeling/table_model.py, V044): one versioned single-table model per source system and table.

export type TmLayer = "LANDING" | "BRONZE" | "SILVER" | "GOLD";
export type TmTableType = "ICEBERG" | "STANDARD" | "DYNAMIC_TABLE";
export const TM_CLASSIFICATIONS = ["BUSINESS_KEY", "SURROGATE_KEY", "ATTRIBUTE", "AUDIT_TIMESTAMP", "MEASURE", "RECORD_SOURCE"] as const;
export type TmHeader = {
  table_model_id: string; table_name: string; source_id: string | null; source_name: string; source_tables: string[];
  domain_id: string | null; domain_name: string | null; origin: "RUN" | "ADHOC" | "IMPORT"; origin_run_id: string | null;
  origin_run_name: string | null; last_run_id: string | null; model_layer: TmLayer; table_type: TmTableType;
  current_version: number | null; etag: string | null; diagram_layout: Record<string, { x: number; y: number }>;
  linked_model_id: string | null; linked_entity: string | null; last_change_id: string | null;
  created_by: string | null; created_at: string | null; updated_by: string | null; updated_at: string | null;
};
export type TmListRow = Omit<TmHeader, "diagram_layout" | "etag"> & {
  status: "DRAFT" | "PUBLISHED" | null; branch: string | null; pr_url: string | null; pr_state: string | null;
  version_at: string | null; columns: number; primary_keys: string[]; errors: number;
};
export type TmVersion = {
  version: number; status: "DRAFT" | "PUBLISHED"; checksum: string | null; issues: DmIssue[]; change_note: string | null;
  change_summary: { counts: Record<ChangeClass, number>; breaking: boolean } | null; origin: string | null;
  origin_run_id: string | null; run_design_version: number | null; repo_id: string | null; branch: string | null;
  pr_url: string | null; pr_number: number | null; pr_state: string | null; created_by: string | null; created_at: string | null;
};
export type TmRendered = { ddl: string; dbt: Record<string, string> };
export type TmDetail = {
  model: TmHeader; versions: TmVersion[];
  current: (TmVersion & TmRendered & { spec: DmSpec; issues: DmIssue[]; summary: { entities: number; columns: number; relationships: number; retired: number } }) | null;
};
export type TmRelated = { model_id: string | null; version?: number; entities: DmEntity[]; relationships: DmRelationship[]; in_domain_model?: boolean };
export type TmSourceTable = {
  landing_id: string; run_id: string; source_system_id: string; source_name: string; domain_id: string | null; source_database: string;
  source_schema: string; source_table: string; row_count: number | null; ingested_at: string | null; table_model_id: string; exists: boolean;
};
export type TmSuggestion = { column: string; classification: string | null; description: string | null; reason: string; item_key: string };
