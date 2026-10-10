"use server";

import { revalidatePath } from "next/cache";
import { api, ApiError } from "@/lib/api";

// ---------------------------------------------------------------- types (the /api/cases contract)

export type CaseKind = "DATA_BUG" | "CODE_BUG" | "DATA_QUALITY" | "PIPELINE" | "QUESTION";
export type CaseSource = "JIRA" | "INCIDENT" | "QA_FAILURE" | "DQ_FAILURE" | "APP_REPORT";
export type CaseStatus = "NEW" | "TRIAGED" | "IN_PROGRESS" | "FIX_PROPOSED" | "FIX_APPLIED" | "VERIFIED" | "RESOLVED" | "CLOSED" | "DUPLICATE";
export type CaseSeverity = "P1" | "P2" | "P3" | "P4";

export type CaseRow = {
  case_id: string; number: string; domain_id: string | null; domain_name: string | null; title: string; kind: string; source: string;
  status: string; severity: string; assignee: string | null; team_id: string | null; sla_due_at: string | null; sla_breached: boolean;
  target_table_id: string | null; target_fqn: string | null; run_id: string | null; ai_summary: string | null; opened_by: string | null;
  opened_at: string | null; updated_at: string | null; links_count: number;
};
// ---------------------------------------------------------------- AI triage (Q2). Every field is optional: older cases hold
// a summary only, and the model may leave parts out.

export type CaseCitation = { kind: string; ref: string; path?: string | null; line?: number | null; repo_id?: string | null; url?: string | null };
export type CaseEvidence = { kind?: string | null; ref?: string | null; text?: string | null };
export type CaseHypothesis = { cause: string; confidence?: number | null; evidence?: CaseEvidence[] | null };
export type CaseTargetCandidate = { target_table_id: string; fqn: string; score?: number | null; reasons?: string[] | string | null };
export type CaseSimilar = { kind: "case" | "incident"; id: string; title?: string | null; resolution?: string | null; score?: number | null };
export type CaseDuplicate = { case_id: string; number: string; title?: string | null; score?: number | null };
export type CaseAi = {
  classification?: string | null; confidence?: number | null; summary?: string | null; hypotheses?: CaseHypothesis[] | null;
  blast_radius?: { models?: string[] | null; tables?: string[] | null; domains?: string[] | null } | null;
  questions?: string[] | null; reproducible?: "yes" | "no" | "unknown" | string | null;
  target?: { target_table_fqn?: string | null; models?: string[] | null } | null;
  target_candidates?: CaseTargetCandidate[] | null; citations?: CaseCitation[] | null; similar?: CaseSimilar[] | null;
  duplicate_candidates?: CaseDuplicate[] | null; model?: string | null; generated_at?: string | null; context_parts?: string[] | null;
};
export type CaseFull = CaseRow & {
  description: string | null; models: string[] | null; repo_id: string | null; fingerprint: string | null; duplicate_of: string | null;
  resolution: string | null; ai: CaseAi | null;
};
export type CaseEvent = { event_id: string; kind: string; actor: string | null; detail: unknown; created_at: string | null };
export type CaseLink = { link_id: string; kind: string; ref: string; label: string | null; url: string | null; state: string | null };
export type CaseArtifact = {
  artifact_id: string; type: string; title: string | null; content: string | null; diff: string | null; status: string;
  decided_by: string | null; decided_at: string | null; created_at: string | null;
  /** Type specific fields (SQL, expected, target column, path...); `valid` and `problems` come from the guard. */
  payload?: Record<string, unknown> | null; valid?: boolean | null; problems?: string[] | null; note?: string | null;
};
export type TriageResult = { ai: CaseAi; artifacts: CaseArtifact[]; cached: boolean };
export type CaseAnswer = { answer: string; citations: { kind: string; ref: string; url?: string | null }[] };
/** One reproduction test run. Rows in `sample` are lists (in `columns` order) or objects keyed by column. */
export type TestRun = {
  outcome: string; rows_returned: number | null; measured: unknown; detail: string | null; columns: string[] | null;
  sample: unknown[] | null; masked: string[] | boolean | null;
};
export type PublishPreview = { branch: string; base_branch: string; repo: string; path: string; diff: string; preview_token: string };
export type PublishDone = { pr_url: string; branch: string; status: string };
export type VerifyResult = { results: { artifact_id: string; title: string | null; outcome: string }[]; verified: boolean; case: CaseRow };
export type JiraPosted = { key: string; posted: boolean };
export type CaseSettings = { auto_triage: boolean; auto_triage_severities: string[]; sla_hours: Record<"P1" | "P2" | "P3" | "P4", number> };
export type CaseDetail = { case: CaseFull; events: CaseEvent[]; links: CaseLink[]; artifacts: CaseArtifact[] };
export type CaseSummary = { open: number; mine: number; p1_open: number; sla_breached: number; by_status: Record<string, number> };
export type CaseOpened = { case: CaseRow; created: boolean; duplicate_of?: string | null };
export type MyDomains = { domains: { domain_id: string; name: string; role: string | null }[]; all: boolean };

export type CaseFilters = {
  status?: string[]; severity?: string; kind?: string; domain_id?: string; mine?: boolean; team_id?: string; q?: string;
  sla?: "breached" | ""; limit?: number; offset?: number;
};
export type PageContext = { path: string; run_id?: string; table?: string; check_id?: string; test_id?: string; model?: string };
export type NewCase = {
  title: string; description: string; kind?: CaseKind; severity?: CaseSeverity; domain_id?: string; target_table_id?: string;
  run_id?: string; source: CaseSource; source_ref?: string; page_context?: PageContext;
};

/** A failed call keeps its status: 404 hides a case outside the caller's domains, 428 asks to connect Jira. */
export type CaseFailed = { ok: false; error: string; status: number; retryAfter?: number | null };
export type CaseResult<T> = { ok: true; data: T } | CaseFailed;

async function call<T>(fn: () => Promise<T>): Promise<CaseResult<T>> {
  try {
    return { ok: true, data: await fn() };
  } catch (e) {
    if (e instanceof ApiError) return { ok: false, error: e.message, status: e.status, retryAfter: e.retryAfter };
    throw e;
  }
}

const json = (body: unknown) => JSON.stringify(body);
const enc = encodeURIComponent;

function changed(caseId?: string) {
  revalidatePath("/qa");
  if (caseId) revalidatePath(`/qa/cases/${caseId}`);
}

// ---------------------------------------------------------------- reads

export async function listCases(f: CaseFilters) {
  const p = new URLSearchParams();
  // no list means the API default (open cases); an empty list means every status
  if (f.status) p.set("status", f.status.length ? f.status.join(",") : "any");
  if (f.severity) p.set("severity", f.severity);
  if (f.kind) p.set("kind", f.kind);
  if (f.domain_id) p.set("domain_id", f.domain_id);
  if (f.mine) p.set("mine", "true");
  if (f.team_id) p.set("team_id", f.team_id);
  if (f.q?.trim()) p.set("q", f.q.trim());
  if (f.sla === "breached") p.set("sla", "breached");
  p.set("limit", String(f.limit ?? 50));
  if (f.offset) p.set("offset", String(f.offset));
  return call(() => api<{ cases: CaseRow[]; total: number }>(`/api/cases?${p}`));
}

export async function caseSummary() {
  return call(() => api<CaseSummary>("/api/cases/summary"));
}

export async function caseDetail(caseId: string) {
  return call(() => api<CaseDetail>(`/api/cases/${enc(caseId)}`));
}

export async function myDomains() {
  return call(() => api<MyDomains>("/api/governance/my-domains"));
}

// ---------------------------------------------------------------- intake

export async function openCase(body: NewCase) {
  const r = await call(() => api<CaseOpened>("/api/cases", { method: "POST", body: json(body) }));
  if (r.ok) changed();
  return r;
}

export async function caseFromJira(key: string) {
  const r = await call(() => api<CaseOpened>("/api/cases/from-jira", { method: "POST", body: json({ key }) }));
  if (r.ok) changed();
  return r;
}

export async function caseFromIncident(incidentId: string) {
  const r = await call(() => api<CaseOpened>("/api/cases/from-incident", { method: "POST", body: json({ incident_id: incidentId }) }));
  if (r.ok) changed();
  return r;
}

export async function caseFromResult(ref: { qa_result_id?: string; check_result_id?: string }) {
  const r = await call(() => api<CaseOpened>("/api/cases/from-result", { method: "POST", body: json(ref) }));
  if (r.ok) changed();
  return r;
}

// ---------------------------------------------------------------- one case (each returns nothing useful; reload the detail after)

export async function updateCase(caseId: string, patch: { title?: string; description?: string; severity?: string; kind?: string; target_table_id?: string }) {
  const r = await call(() => api<unknown>(`/api/cases/${enc(caseId)}`, { method: "PUT", body: json(patch) }));
  changed(caseId);
  return r;
}

export async function assignCase(caseId: string, assignee: string) {
  const r = await call(() => api<unknown>(`/api/cases/${enc(caseId)}/assign`, { method: "POST", body: json({ assignee }) }));
  changed(caseId);
  return r;
}

export async function setCaseStatus(caseId: string, body: { status: string; note?: string; override_reason?: string }) {
  const r = await call(() => api<unknown>(`/api/cases/${enc(caseId)}/status`, { method: "POST", body: json(body) }));
  changed(caseId);
  return r;
}

export async function commentCase(caseId: string, text: string) {
  const r = await call(() => api<unknown>(`/api/cases/${enc(caseId)}/comment`, { method: "POST", body: json({ text }) }));
  changed(caseId);
  return r;
}

export async function linkCase(caseId: string, kind: string, ref: string) {
  const r = await call(() => api<unknown>(`/api/cases/${enc(caseId)}/links`, { method: "POST", body: json({ kind, ref }) }));
  changed(caseId);
  return r;
}

export async function unlinkCase(caseId: string, linkId: string) {
  const r = await call(() => api<unknown>(`/api/cases/${enc(caseId)}/links/${enc(linkId)}`, { method: "DELETE" }));
  changed(caseId);
  return r;
}

export async function mergeCase(caseId: string, intoCaseId: string) {
  const r = await call(() => api<unknown>(`/api/cases/${enc(caseId)}/merge`, { method: "POST", body: json({ into_case_id: intoCaseId }) }));
  changed(caseId);
  return r;
}

// ---------------------------------------------------------------- triage and fixes (Q2)

const base = (caseId: string) => `/api/cases/${enc(caseId)}`;
const art = (caseId: string, aid: string) => `${base(caseId)}/artifacts/${enc(aid)}`;

export async function triageCase(caseId: string, body: { force?: boolean; note?: string }) {
  const r = await call(() => api<TriageResult>(`${base(caseId)}/triage`, { method: "POST", body: json(body) }));
  changed(caseId);
  return r;
}

export async function askCase(caseId: string, question: string) {
  return call(() => api<CaseAnswer>(`${base(caseId)}/ask`, { method: "POST", body: json({ question }) }));
}

export async function decideArtifact(caseId: string, aid: string, decision: "accept" | "reject", note?: string) {
  const r = await call(() => api<{ artifact: CaseArtifact }>(`${art(caseId, aid)}/decide`, { method: "POST", body: json({ decision, ...(note ? { note } : {}) }) }));
  changed(caseId);
  return r;
}

export async function runArtifact(caseId: string, aid: string) {
  return call(() => api<TestRun>(`${art(caseId, aid)}/run`, { method: "POST" }));
}

export async function markApplied(caseId: string, aid: string, note?: string) {
  const r = await call(() => api<{ artifact: CaseArtifact; case: CaseRow }>(`${art(caseId, aid)}/applied`, { method: "POST", body: json(note ? { note } : {}) }));
  changed(caseId);
  return r;
}

export async function previewPublish(caseId: string, aid: string) {
  return call(() => api<PublishPreview>(`${art(caseId, aid)}/publish`, { method: "POST", body: json({ dry_run: true }) }));
}

export async function publishArtifact(caseId: string, aid: string, previewToken: string) {
  const r = await call(() => api<PublishDone>(`${art(caseId, aid)}/publish`, { method: "POST", body: json({ dry_run: false, preview_token: previewToken }) }));
  changed(caseId);
  return r;
}

export async function verifyCase(caseId: string) {
  const r = await call(() => api<VerifyResult>(`${base(caseId)}/verify`, { method: "POST" }));
  changed(caseId);
  return r;
}

/** Posts to every Jira issue linked to the case; one link answers with an object, several with a list. */
export async function jiraComment(caseId: string, text?: string) {
  const r = await call(() => api<JiraPosted | JiraPosted[]>(`${base(caseId)}/jira-comment`, { method: "POST", body: json(text ? { text } : {}) }));
  if (r.ok) changed(caseId);
  return r.ok ? { ok: true as const, data: Array.isArray(r.data) ? r.data : [r.data] } : r;
}

export async function getCaseSettings() {
  return call(() => api<CaseSettings>("/api/cases/settings"));
}

export async function saveCaseSettings(body: CaseSettings) {
  const r = await call(() => api<CaseSettings>("/api/cases/settings", { method: "PUT", body: json(body) }));
  if (r.ok) revalidatePath("/admin");
  return r;
}
