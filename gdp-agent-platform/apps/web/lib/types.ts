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
};

export type RunState = {
  run_id: string;
  current_state: string;
  current_stage: string | null;
  status: string;
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
  target_model: string | null;
  created_by: string;
  created_at: string;
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
  }[];
  decisions: {
    decision_id: string; source_column_id: string; target_column_id: string | null; candidate_id: string | null;
    decision: string; transformation: string | null; business_justification: string | null; reviewer: string;
    reviewed_at: string;
  }[];
  targets: {
    target_column_id: string; column_name: string; data_type: string; nullable: boolean;
    is_business_key: boolean; semantic_type: string;
  }[];
  status: { source_columns: number; decided: number; undecided: string[]; missing_required_targets: string[]; complete: boolean };
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
