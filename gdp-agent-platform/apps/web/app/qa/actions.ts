"use server";

import { revalidatePath } from "next/cache";
import { api, ApiError, attemptValue } from "@/lib/api";
import type { JiraIssue, JiraIssueDetail, Triage } from "../jira/actions";
import type { QaAnswer, QaProposal, QaResult, QaRunRow, QaSuite, QaTest } from "../runs/[runId]/qa/qa-actions";
import type { CodeCitation } from "@/components/code-citations";

/** A failed call keeps its HTTP status, so the workspace can tell "connect Jira" (428) and rate limits (429) apart. */
export type Failed = { ok: false; error: string; status: number; retryAfter: number | null };
export type Result<T> = { ok: true; data: T } | Failed;

async function call<T>(fn: () => Promise<T>): Promise<Result<T>> {
  try {
    return { ok: true, data: await fn() };
  } catch (e) {
    if (e instanceof ApiError) return { ok: false, error: e.message, status: e.status, retryAfter: e.retryAfter };
    throw e;
  }
}

function changed() {
  revalidatePath("/qa");
}

const json = (body: unknown) => JSON.stringify(body);
const enc = encodeURIComponent;

// ---------------------------------------------------------------- types

export type IssueSummary = JiraIssue & { linked: number };
export type IssuePage = { issues: IssueSummary[]; next: string | null; jql?: string };
export type SavedFilter = { filter_id: string; name: string; jql: string; shared: boolean; owner: string; mine: boolean };
export type Board = { id: number | string; name: string; type: string; project_key: string | null };
export type Sprint = { id: number | string; name: string; state: string; start: string | null; end: string | null };
export type IssueType = { id: string; name: string; subtask: boolean };
export type BugCreated = { key: string; url: string | null; created: boolean; existing: boolean };
export type BulkAction = "link" | "comment" | "transition";
export type BulkResult = { key: string; ok: boolean; error?: string | null; skipped?: boolean | string | null };
export type Candidate = { target_table_id: string; fqn: string; domain_id: string | null; /** 0 to 100 */ score: number; reasons: string[];
                          has_sttm: boolean; active: boolean };
export type Resolved = { key: string; summary: string; model?: string | null; candidates: Candidate[]; runs: { run_id: string; name: string | null; state: string | null }[] };
export type QaLink = {
  link_id: string; issue_key: string; summary: string | null; status: string | null; status_category?: string | null;
  target_table_id: string | null; target_table?: string | null; suite_id: string | null; qa_test_id: string | null; test_title?: string | null;
  run_id: string | null; origin?: string | null; issue_state?: string | null; url?: string | null;
  linked_by?: string | null; linked_at?: string | null; created_by?: string | null; created_at?: string | null;
};

export type QaTable = {
  target_table_id: string; domain_id: string | null; fqn: string; active: boolean; has_sttm: boolean; tests: number;
  last_run_at: string | null;
  last_outcome: { tests: number; passed: number; failed: number; review: number; not_run: number; errors: number } | null;
};
export type TableContext = {
  target_table_id: string; domain_id: string | null; sttm_id: string | null; target: { fqn: string; name: string };
  sources: Record<string, string>; business_keys: string[]; allowed: string[];
  columns: { name: string; data_type: string | null; is_key: boolean; is_pii: boolean; semantic_type: string | null }[];
  pii_columns: string[]; pii_basis: "profile" | "heuristic" | "conservative" | null; run_id: string | null;
  retired: boolean; lines: number;
};
export type Suite = {
  suite_id: string; domain_id: string | null; target_table_id: string; name: string; description: string | null;
  is_default: boolean; tests: number; created_by: string | null; created_at: string | null; updated_at: string | null;
};
export type TableTest = QaTest & { target_table_id?: string | null };
export type TableSuite = Omit<QaSuite, "tests"> & {
  tests: TableTest[]; suites: Suite[]; target_table_id: string; domain_id: string | null; sttm_id: string | null;
  pii_basis: string | null; retired: boolean; allowed: string[];
};
export type TableRun = QaRunRow & {
  run_id?: string | null; scope?: string | null; suite_id?: string | null; target_table_id?: string | null;
  triggered_by?: string | null; blocking?: number; pii_basis?: string | null;
};
export type TableResult = QaResult & { suite_id?: string | null; qa_run_id?: string | null; created_at?: string | null };
export type TableResults = { run: TableRun | null; results: TableResult[] };
export type Plan = { tests: QaProposal[]; model: string; code_citations?: CodeCitation[];
                     grounding: { rules: number; profile_columns: number; checks: number; code?: number } };
export type NewTableTest = {
  title: string; sql: string; objective?: string; expected?: string; category?: string; prompt?: string;
  severity?: string; target_column?: string; suite_id?: string;
};

// ---------------------------------------------------------------- Jira: inbox

export async function searchIssues(opts: { jql?: string; filterId?: string; next?: string | null; max?: number }) {
  const p = new URLSearchParams({ max: String(opts.max ?? 50) });
  if (opts.jql) p.set("jql", opts.jql);
  if (opts.filterId) p.set("filter_id", opts.filterId);
  if (opts.next) p.set("next", opts.next);
  return call(() => api<IssuePage>(`/api/jira/search?${p}`));
}

export async function validateJql(jql: string) {
  return call(() => api<{ ok: boolean; errors: string[] }>("/api/jira/jql/validate", { method: "POST", body: json({ jql }) }));
}

export async function listFilters() {
  return call(() => api<{ filters: SavedFilter[] }>("/api/jira/filters"));
}

export async function saveFilter(body: { name: string; jql: string; shared: boolean }) {
  return call(() => api<SavedFilter>("/api/jira/filters", { method: "POST", body: json(body) }));
}

export async function deleteFilter(filterId: string) {
  return call(() => api(`/api/jira/filters/${enc(filterId)}`, { method: "DELETE" }));
}

export async function listBoards(q: string) {
  const p = new URLSearchParams();
  if (q.trim()) p.set("q", q.trim());
  return call(() => api<{ boards: Board[] }>(`/api/jira/boards?${p}`));
}

export async function boardSprints(boardId: string) {
  return call(() => api<{ sprints: Sprint[] }>(`/api/jira/boards/${enc(boardId)}/sprints?state=active,future`));
}

export async function sprintIssues(sprintId: string, next?: string | null) {
  const p = new URLSearchParams();
  if (next) p.set("next", next);
  return call(() => api<IssuePage>(`/api/jira/sprints/${enc(sprintId)}/issues?${p}`));
}

export async function bulkJira(body: { action: BulkAction; keys: string[]; comment?: string; transition_name?: string;
                                       link?: { target_table_id: string; suite_id?: string; test_id?: string } }) {
  const r = await call(() => api<{ results: BulkResult[] }>("/api/jira/bulk", { method: "POST", body: json(body) }));
  if (body.action === "link") changed();
  return r;
}

export async function issueDetail(key: string) {
  return call(() => api<JiraIssueDetail>(`/api/jira/issues/${enc(key)}`));
}

// ---------------------------------------------------------------- Jira: bugs

export async function issueTypes(projectKey: string) {
  return call(() => api<{ types: IssueType[] }>(`/api/jira/projects/${enc(projectKey)}/issue-types`));
}

export async function createBug(body: {
  test_id: string; target_table_id: string; qa_run_id?: string | null; project_key?: string; issue_type_id?: string;
  summary?: string; idempotency_key: string;
}) {
  const r = await call(() => api<BugCreated>("/api/jira/bugs", { method: "POST", body: json(body) }));
  changed();
  return r;
}

// ---------------------------------------------------------------- triage and links

export async function resolveTriage(key: string) {
  return call(() => api<Resolved>("/api/qa/triage/resolve", { method: "POST", body: json({ key }) }));
}

export async function triageTable(targetTableId: string, key: string) {
  return call(() => api<Triage>(`/api/qa/tables/${enc(targetTableId)}/triage/${enc(key)}`, { method: "POST" }));
}

export async function createLink(body: { key: string; target_table_id?: string; suite_id?: string; test_id?: string; run_id?: string; remote_link: boolean }) {
  const r = await call(() => api<QaLink & { remote_link?: string | null; already?: boolean }>("/api/qa/links", { method: "POST", body: json(body) }));
  changed();
  return r;
}

export async function listLinks(opts: { key?: string; targetTableId?: string }) {
  const p = new URLSearchParams();
  if (opts.key) p.set("key", opts.key);
  if (opts.targetTableId) p.set("target_table_id", opts.targetTableId);
  return call(() => api<{ links: QaLink[] }>(`/api/qa/links?${p}`));
}

export async function deleteLink(linkId: string) {
  const r = await call(() => api(`/api/qa/links/${enc(linkId)}`, { method: "DELETE" }));
  changed();
  return r;
}

// ---------------------------------------------------------------- tables, suites, tests

export async function listTables(domainId?: string) {
  const p = new URLSearchParams();
  if (domainId) p.set("domain_id", domainId);
  return attemptValue(() => api<{ tables: QaTable[] }>(`/api/qa/tables?${p}`));
}

export async function tableContext(targetTableId: string) {
  return attemptValue(() => api<TableContext>(`/api/qa/tables/${enc(targetTableId)}`));
}

export async function tableSuite(targetTableId: string) {
  return attemptValue(() => api<TableSuite>(`/api/qa/tables/${enc(targetTableId)}/suite`));
}

export async function listSuites(targetTableId: string) {
  return attemptValue(() => api<{ suites: Suite[] }>(`/api/qa/suites?target_table_id=${enc(targetTableId)}`));
}

export async function createSuite(targetTableId: string, name: string, description?: string) {
  const r = await attemptValue(() => api<{ suite_id: string }>("/api/qa/suites",
    { method: "POST", body: json({ target_table_id: targetTableId, name, description: description || null }) }));
  changed();
  return r;
}

export async function renameSuite(suiteId: string, name: string) {
  const r = await attemptValue(() => api(`/api/qa/suites/${enc(suiteId)}`, { method: "PUT", body: json({ name }) }));
  changed();
  return r;
}

export async function deleteSuite(suiteId: string) {
  const r = await attemptValue(() => api<{ moved_to: string }>(`/api/qa/suites/${enc(suiteId)}`, { method: "DELETE" }));
  changed();
  return r;
}

export async function askTable(targetTableId: string, question: string) {
  return attemptValue(() => api<QaAnswer>(`/api/qa/tables/${enc(targetTableId)}/ask`, { method: "POST", body: json({ question }) }));
}

export async function planTable(targetTableId: string, focus: string) {
  return attemptValue(() => api<Plan>(`/api/qa/tables/${enc(targetTableId)}/plan`, { method: "POST", body: json({ focus }) }));
}

export async function saveTableTest(targetTableId: string, test: NewTableTest) {
  const r = await attemptValue(() => api<{ test_id: string; suite_id: string }>(`/api/qa/tables/${enc(targetTableId)}/tests`,
    { method: "POST", body: json(test) }));
  changed();
  return r;
}

export async function updateTableTest(testId: string, test: {
  title: string; sql: string; objective?: string | null; expected?: string | null; severity?: string; suite_id?: string;
}) {
  const r = await attemptValue(() => api<{ test_id: string; note?: string | null }>(`/api/qa/tests/${enc(testId)}`,
    { method: "PUT", body: json(test) }));
  changed();
  return r;
}

export async function deleteTableTest(testId: string) {
  const r = await attemptValue(() => api(`/api/qa/tests/${enc(testId)}`, { method: "DELETE" }));
  changed();
  return r;
}

export async function runTable(targetTableId: string, opts: { test_ids?: string[]; suite_id?: string } = {}) {
  const r = await attemptValue(() => api<TableRun>(`/api/qa/tables/${enc(targetTableId)}/run`,
    { method: "POST", body: json({ test_ids: opts.test_ids?.length ? opts.test_ids : null, suite_id: opts.suite_id || null }) }));
  changed();
  return r;
}

export async function runSuite(suiteId: string, testIds?: string[]) {
  const r = await attemptValue(() => api<TableRun>(`/api/qa/suites/${enc(suiteId)}/run`,
    { method: "POST", body: json({ test_ids: testIds?.length ? testIds : null }) }));
  changed();
  return r;
}

export async function tableHistory(targetTableId: string) {
  return attemptValue(() => api<{ runs: TableRun[] }>(`/api/qa/tables/${enc(targetTableId)}/history`));
}

export async function tableResults(targetTableId: string, suiteId?: string) {
  const p = new URLSearchParams({ target_table_id: targetTableId });
  if (suiteId) p.set("suite_id", suiteId);
  return attemptValue(() => api<TableResults>(`/api/qa/results?${p}`));
}
