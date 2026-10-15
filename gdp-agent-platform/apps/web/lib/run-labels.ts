// Plain-language names for a run's workflow states and statuses (snowflake/database/seed/workflow_graph.json), so the
// UI never shows a raw state like DOMAIN_IDENTIFIED. The raw value stays available as a tooltip for engineers.

const STATE_LABEL: Record<string, string> = {
  CREATED: "Created",
  SOURCE_REGISTERED: "Source registered",
  ACCESS_VALIDATION: "Checking source access",
  ACCESS_APPROVED: "Source access confirmed",
  LANDING_PENDING: "Ready to land the tables",
  LANDING_RUNNING: "Landing the tables",
  LANDING_COMPLETE: "Tables landed",
  PROFILING_PENDING: "Ready to profile",
  PROFILING_RUNNING: "Profiling",
  PROFILING_COMPLETE: "Profiled",
  DOMAIN_IDENTIFIED: "Domain matched",
  MAPPING_PENDING: "Ready for field mapping",
  MAPPING_REVIEW: "Field mapping awaits approval",
  MAPPING_APPROVED: "Field mapping approved",
  STTM_PENDING: "Ready for transformations and joins",
  STTM_REVIEW: "STTM awaits approval",
  STTM_APPROVED: "STTM approved",
  SODA_PENDING: "Data quality checks in progress",
  SODA_REVIEW: "Data quality awaits approval",
  SODA_APPROVED: "Data quality approved",
  DBT_PENDING: "Ready to generate code",
  DBT_GENERATING: "Code generated",
  VALIDATION_PENDING: "Ready to validate",
  VALIDATION_RUNNING: "Validating",
  VALIDATION_PASSED: "Validation passed",
  VALIDATION_FAILED: "Validation failed",
  DBT_REVIEW: "Code awaits review",
  DBT_APPROVED: "Code approved",
  COMPLETED: "Completed",
  FAILED: "Failed",
  CANCELLED: "Cancelled",
};

const STATUS_LABEL: Record<string, string> = {
  IN_PROGRESS: "In progress", RUNNING: "In progress", COMPLETED: "Completed", FAILED: "Failed", CANCELLED: "Cancelled",
  DRAFT: "Draft", CREATED: "Created",
};

const sentence = (raw: string) => {
  const s = raw.replace(/_/g, " ").toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
};

/** "Domain matched" for DOMAIN_IDENTIFIED; unknown states read as a sentence. */
export function stateLabel(state: string | null | undefined): string {
  if (!state) return "Not started";
  return STATE_LABEL[state] ?? sentence(state);
}

export function statusLabel(status: string | null | undefined): string {
  if (!status) return "";
  return STATUS_LABEL[status] ?? sentence(status);
}

/** The four human approval gates: what is approved, by which role, where it is reviewed and the state approval enters. */
export type Gate = { name: string; role: string; slug: string; approveTo: string };

export const GATES: Record<string, Gate> = {
  MAPPING_REVIEW: { name: "Field mapping", role: "Model approver", slug: "modeling?tab=mapping", approveTo: "MAPPING_APPROVED" },
  STTM_REVIEW: { name: "Transformations and joins (STTM)", role: "STTM approver", slug: "modeling?tab=transformations", approveTo: "STTM_APPROVED" },
  SODA_REVIEW: { name: "Data quality checks", role: "DQ approver", slug: "soda", approveTo: "SODA_APPROVED" },
  DBT_REVIEW: { name: "Code review", role: "Code reviewer", slug: "review", approveTo: "DBT_APPROVED" },
};

/** The run waits on a person (one of the four gates). */
export const awaitsApproval = (state: string | null | undefined) => !!state && state in GATES;
