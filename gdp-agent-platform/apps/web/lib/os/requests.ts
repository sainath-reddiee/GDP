// Onboarding requests (apps/api/app/requests_api.py, V075): the shapes the /os screens read, and how a stage reads.
// Shared by server pages and client components (no server-only imports).

export type RequestStage = "NEW" | "DOMAIN" | "ENABLE" | "DESIGN" | "BUILD" | "REVIEW" | "DONE" | "CLOSED";
export type RequestStatus = "OPEN" | "DONE" | "CANCELLED" | "REJECTED";
export type RequestType = "SOURCE" | "DOMAIN" | "PRODUCT";
/** A new domain, source or product, or more from one already on the platform (V078; NEW before it). */
export type RequestIntent = "NEW" | "ENHANCE";
export type Wait = { privilege: string; label: string; item_id?: string | null };
/** What a request (or one of its sources) is doing, in its owner's words (services/onboarding/requests.py PHASES). */
export type RequestPhase = "DOMAIN" | "ENABLE" | "ONBOARDED" | "READY" | "IMPACT" | "DESIGN" | "STORED" | "BUILD" | "FLAGGED" | "REVIEW" | "DONE" | "CLOSED";

export type RequestItem = {
  item_id: string; source_system_id: string | null; source_name: string; source_note: string | null; run_id: string | null;
  status: string; status_detail: string | null; stage: RequestStage; phase?: RequestPhase; waits: Wait[]; attention: string | null;
  run_name?: string | null; run_state?: string | null; added_by: string; added_at: string; removed_at: string | null;
};
export type RequestEvent = { event_id: string; item_id: string | null; kind: string; from_value: string | null; to_value: string | null;
  note: string | null; actor: string; automatic: boolean; created_at: string };
export type OnboardingRequest = {
  request_id: string; request_type: RequestType; intent?: RequestIntent; title: string; business_outcome: string | null; domain_id: string | null;
  domain_name: string | null; proposed_domain: string | null; pipeline: string; priority: "LOW" | "MEDIUM" | "HIGH"; needed_by: string | null;
  status: RequestStatus; stage: RequestStage; waiting_on: string | null; waiting_label: string | null; attention: string | null;
  requested_by: string; created_at: string; updated_at: string; stage_changed_at: string | null;
  closed_by: string | null; closed_at: string | null; close_reason: string | null;
  phase?: RequestPhase; waits: Wait[]; items: RequestItem[];
};
/** A file attached to a request (V079); domain_source_id once it was handed to the domain's documents. */
export type RequestFile = {
  file_id: string; file_name: string; content_type: string | null; bytes: number | null; added_by: string | null; added_at: string | null;
  domain_source_id: string | null; handed_over_at: string | null;
};

export type RequestDetail = OnboardingRequest & {
  events: RequestEvent[];
  files?: RequestFile[];
  may: { add_source: boolean; link_source: boolean; link_run: boolean; link_domain: boolean; remove_source: boolean;
         cancel: boolean; reject: boolean; reopen: boolean; comment: boolean; add_files?: boolean };
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

/** An Enhance source request's own path, the delta flow: the source is already onboarded, so the administrator only
 *  lands what is added (Land the delta, shown only while it is needed) and the Data Designer maps only the delta. */
export const ENHANCE_SOURCE_STEPS: { stage: RequestStage; label: string; owner: string }[] = [
  { stage: "NEW", label: "Scope", owner: "Requester" },
  { stage: "ENABLE", label: "Land the delta", owner: "Data OS Administrator" },
  { stage: "DESIGN", label: "Impact & delta mapping", owner: "Data Designer" },
  { stage: "BUILD", label: "Regenerate & regression", owner: "Forward Engineering Lead" },
  { stage: "REVIEW", label: "Review", owner: "Data Developer" },
  { stage: "DONE", label: "Done", owner: "" },
];

export const stageOwner = (stage: RequestStage) => REQUEST_STEPS.find((s) => s.stage === stage)?.owner ?? "";

export const TYPE_LABEL: Record<RequestType, string> = { DOMAIN: "Domain", SOURCE: "Source", PRODUCT: "Data product" };
export const intentOf = (r: Pick<OnboardingRequest, "intent">): RequestIntent => (r.intent === "ENHANCE" ? "ENHANCE" : "NEW");
/** "New source", "Enhance domain". */
export const intentLabel = (r: Pick<OnboardingRequest, "intent" | "request_type">) =>
  `${intentOf(r) === "ENHANCE" ? "Enhance" : "New"} ${(TYPE_LABEL[r.request_type] ?? r.request_type).toLowerCase()}`;
/** An Enhance source request: more from sources already onboarded (the delta flow). */
export const enhancesSource = (r: Pick<OnboardingRequest, "intent" | "request_type">) => intentOf(r) === "ENHANCE" && r.request_type === "SOURCE";

/** Steps with a status for a request: done before its stage, current at it (attention when blocked), pending after.
 *  The Domain step shows only while the domain is to be created (a request for a new one, or one without a domain). */
export function requestSteps(r: Pick<OnboardingRequest, "stage" | "status" | "attention" | "domain_id" | "request_type" | "intent">) {
  const newDomain = r.request_type === "DOMAIN" && intentOf(r) === "NEW";
  const steps = enhancesSource(r)
    ? ENHANCE_SOURCE_STEPS.filter((s) => s.stage !== "ENABLE" || (r.status === "OPEN" && r.stage === "ENABLE"))
    : REQUEST_STEPS.filter((s) => s.stage !== "DOMAIN" || !r.domain_id || newDomain);
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
  IMPACT: { label: "Impact and delta mapping", tone: "blue" },
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

/** Where the person a wait names does the work. */
export function actHref(w: Wait, item: RequestItem | undefined, domainId: string | null, product: boolean, requestId: string,
                        enhance = false): string | null {
  // an Enhance source request's enhancement run is started from the request itself (Intake, the request selected)
  if (enhance && w.privilege === "RUN.CREATE" && item && !item.run_id) return `/os/intake?${new URLSearchParams({ view: "selected", request: requestId })}`;
  // a domain to onboard opens the administrator's domain wizard for this request; a retired one is restored in Domains
  if (w.privilege === "DOMAIN.ONBOARD") return `/os/enable/new-domain?${new URLSearchParams({ request: requestId })}`;
  if (w.privilege === "DOMAIN.EDIT") return "/os/knowledge?view=domains";
  if (w.privilege === "MODEL.APPROVE" || w.privilege === "MODEL.EDIT") return domainId ? `/os/knowledge/domains/${domainId}` : "/os/knowledge?view=domains";
  if (item?.run_id) {
    const run = encodeURIComponent(item.run_id);
    if (["MAPPING.APPROVE", "STTM.APPROVE", "SODA.APPROVE", "REVIEW.APPROVE", "QA.SIGNOFF"].includes(w.privilege)) {
      return w.privilege === "QA.SIGNOFF" ? `/os/release?run=${run}` : `/os/review?run=${run}`;
    }
    // the Data Designer's own steps are worked on Design (Build is not theirs: it would refuse them)
    if (["PROFILE.RUN", "MAPPING.DECIDE", "STTM.EDIT", "MODEL.EDIT"].includes(w.privilege)) return `/os/design?view=selected&run=${run}`;
    return `/os/build?view=selected&run=${run}`;
  }
  if (w.privilege === "RUN.CREATE") return product ? "/os/build/gold" : "/os/build";
  if (w.privilege === "SOURCE.CONNECT" || w.privilege === "PROFILE.RUN") {
    // a source to register, or whose run is to start, opens the administrator's wizard for this request (Enable shows
    // the same); one unreachable or not landed goes to the source store
    if (item && (!item.source_system_id || item.status === "NO_RUN" || item.status === "ATTENTION")) {
      return `/os/enable/onboarding?type=source&${new URLSearchParams({ request: requestId, item: item.item_id, name: item.source_name })}`;
    }
    return item?.source_system_id ? "/os/design/sources" : "/os/enable";
  }
  return null;
}

/** The role a wait names ("Model approver: approve the field mapping" is the Model approver), else null. */
export function waitRole(w: Pick<Wait, "label">): string | null {
  const head = w.label.split(":")[0];
  return head !== w.label && head.length <= 40 ? head.trim() : null;
}

/** The button for a wait, in its own words ("Approve the field mapping"), short enough for a button; the whole label
 *  is the button's title. */
export function waitCta(w: Pick<Wait, "label">, max = 34): string {
  const tail = (waitRole(w) ? w.label.slice(w.label.indexOf(":") + 1) : w.label).trim();
  const text = tail.charAt(0).toUpperCase() + tail.slice(1);
  return text.length > max ? `${text.slice(0, max - 1).replace(/\s+\S*$/, "")}…` : text;
}
