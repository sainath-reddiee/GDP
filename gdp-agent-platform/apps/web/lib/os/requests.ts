// Onboarding requests (apps/api/app/requests_api.py, V075): the shapes the /os screens read, and how a stage reads.
// Shared by server pages and client components (no server-only imports).

export type RequestStage = "NEW" | "DOMAIN" | "ENABLE" | "DESIGN" | "BUILD" | "REVIEW" | "DONE" | "CLOSED";
export type RequestStatus = "OPEN" | "DONE" | "CANCELLED" | "REJECTED";
export type RequestType = "SOURCE" | "DOMAIN" | "PRODUCT";
export type Wait = { privilege: string; label: string; item_id?: string | null };
/** What a request (or one of its sources) is doing, in its owner's words (services/onboarding/requests.py PHASES). */
export type RequestPhase = "DOMAIN" | "ENABLE" | "ONBOARDED" | "READY" | "DESIGN" | "STORED" | "BUILD" | "FLAGGED" | "REVIEW" | "DONE" | "CLOSED";

export type RequestItem = {
  item_id: string; source_system_id: string | null; source_name: string; source_note: string | null; run_id: string | null;
  status: string; status_detail: string | null; stage: RequestStage; phase?: RequestPhase; waits: Wait[]; attention: string | null;
  run_name?: string | null; run_state?: string | null; added_by: string; added_at: string; removed_at: string | null;
};
export type RequestEvent = { event_id: string; item_id: string | null; kind: string; from_value: string | null; to_value: string | null;
  note: string | null; actor: string; automatic: boolean; created_at: string };
export type OnboardingRequest = {
  request_id: string; request_type: RequestType; title: string; business_outcome: string | null; domain_id: string | null;
  domain_name: string | null; proposed_domain: string | null; pipeline: string; priority: "LOW" | "MEDIUM" | "HIGH"; needed_by: string | null;
  status: RequestStatus; stage: RequestStage; waiting_on: string | null; waiting_label: string | null; attention: string | null;
  requested_by: string; created_at: string; updated_at: string; stage_changed_at: string | null;
  closed_by: string | null; closed_at: string | null; close_reason: string | null;
  phase?: RequestPhase; waits: Wait[]; items: RequestItem[];
};
export type RequestDetail = OnboardingRequest & {
  events: RequestEvent[];
  may: { add_source: boolean; link_source: boolean; link_run: boolean; link_domain: boolean; remove_source: boolean;
         cancel: boolean; reject: boolean; reopen: boolean; comment: boolean };
  duplicates: { request_id: string; title: string; stage: string; requested_by: string; source_name: string }[];
};
export type Duplicate = RequestDetail["duplicates"][number];

/** The request's path through the platform, in order (DOMAIN only for a request for a new domain). */
export const REQUEST_STEPS: { stage: RequestStage; label: string; owner: string }[] = [
  { stage: "DOMAIN", label: "Domain", owner: "Data steward" },
  { stage: "ENABLE", label: "Enable", owner: "Data OS Administrator" },
  { stage: "DESIGN", label: "Design", owner: "Data Designer" },
  { stage: "BUILD", label: "Build", owner: "Forward Engineering Lead" },
  { stage: "REVIEW", label: "Review", owner: "Data Developer" },
  { stage: "DONE", label: "Done", owner: "" },
];

export const stageOwner = (stage: RequestStage) => REQUEST_STEPS.find((s) => s.stage === stage)?.owner ?? "";

/** Steps with a status for a request: done before its stage, current at it (attention when blocked), pending after. */
export function requestSteps(r: Pick<OnboardingRequest, "stage" | "status" | "attention" | "domain_id" | "request_type">) {
  const steps = REQUEST_STEPS.filter((s) => s.stage !== "DOMAIN" || !r.domain_id || r.request_type === "DOMAIN");
  const at = r.status === "DONE" ? steps.length - 1 : steps.findIndex((s) => s.stage === r.stage);
  return steps.map((s, i) => ({
    ...s,
    state: r.status === "CANCELLED" || r.status === "REJECTED" ? (i < at ? "done" : "closed")
      : r.status === "DONE" || i < at ? "done" : i === at ? (r.attention ? "attention" : "current") : "pending",
  }));
}

export const PHASE: Record<RequestPhase, { label: string; tone: "neutral" | "blue" | "amber" | "green" | "red" | "purple" }> = {
  DOMAIN: { label: "Waiting on the domain", tone: "amber" },
  ENABLE: { label: "Waiting on admin", tone: "amber" },
  ONBOARDED: { label: "Source onboarded", tone: "blue" },
  READY: { label: "Ready to build", tone: "blue" },
  DESIGN: { label: "Designer profiling and modelling", tone: "blue" },
  STORED: { label: "Design stored", tone: "purple" },
  BUILD: { label: "Building", tone: "purple" },
  FLAGGED: { label: "Validation flagged", tone: "red" },
  REVIEW: { label: "In review", tone: "amber" },
  DONE: { label: "Done", tone: "green" },
  CLOSED: { label: "Closed", tone: "neutral" },
};

/** The phase of a request or item in words, falling back to its stage (an API from before phases). */
export function phaseOf(r: { phase?: RequestPhase; stage: RequestStage; status?: string }) {
  if (r.phase && PHASE[r.phase]) return PHASE[r.phase];
  const stage = r.status === "CANCELLED" || r.status === "REJECTED" ? "CLOSED" : r.stage;
  return PHASE[stage as RequestPhase] ?? { label: stage.charAt(0) + stage.slice(1).toLowerCase(), tone: "neutral" as const };
}

export const PRIORITY_TONE = { HIGH: "red", MEDIUM: "amber", LOW: "neutral" } as const;

/** A needed-by date that has passed while the request is open. */
export const overdue = (r: Pick<OnboardingRequest, "needed_by" | "status">) =>
  r.status === "OPEN" && !!r.needed_by && new Date(`${r.needed_by.slice(0, 10)}T23:59:59`) < new Date();
