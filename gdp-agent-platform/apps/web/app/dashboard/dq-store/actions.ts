"use server";

import { revalidatePath } from "next/cache";
import { api } from "@/lib/api";
import { storeAction } from "@/lib/store-action";
import type { DqHistory, DqScan, DqSuiteDetail, DqSuiteDiff } from "@/lib/store-types";

const PATH = "/os/design/dq-store";
const post = (body: unknown) => ({ method: "POST", body: JSON.stringify(body) });

export type CheckPayload = {
  check_id?: string | null; table: string; column?: string | null; severity: "WARN" | "FAIL"; requirement?: string | null;
  definition?: Record<string, unknown> | null; dsl?: string | null; domain_id?: string | null;
};
export type Parsed = { ok: true; check: { target_column: string | null; definition: Record<string, unknown>; soda_dsl: string }; headline: string }
  | { ok: false; problems: string[] };

export async function previewCheck(body: CheckPayload) {
  return storeAction(() => api<Parsed>("/api/dq/parse", post(body)));
}

/** What a check edit returns: it lands in the table's suite draft (opened when there was none). */
/** UNCHANGED (staged false): the edit changed nothing, so no draft was opened; `version` is then the approved one. */
export type Staged = {
  table: string; version: number; status: "DRAFT" | "UNCHANGED"; draft_opened: boolean; staged: boolean; check_id?: string; headline?: string;
  action?: "added" | "changed" | "removed" | "paused" | "resumed"; counts: Record<string, number>; loosened: boolean; changed: number;
  added?: number; skipped?: number; problems?: string[];
};

export async function saveCheck(body: CheckPayload) {
  const r = await storeAction(() => api<Staged>("/api/dq/catalog", post(body)));
  revalidatePath(PATH, "layout");
  return r;
}

export async function setCheckStatus(checkId: string, status: "ACTIVE" | "PAUSED" | "RETIRED", table?: string) {
  const r = await storeAction(() => api<Staged>(`/api/dq/catalog/${encodeURIComponent(checkId)}/status`, post({ status, table: table ?? null })));
  revalidatePath(PATH, "layout");
  return r;
}

/** Checks a client requirement asks for (pasted or uploaded text, CSV or JSON rows), staged into the table's draft. */
export type Generated = Staged & { source: "rows" | "brief"; proposed: number; left_out: string[]; model: string | null };

export async function generateChecks(body: { table: string; text: string; filename?: string }) {
  // no revalidation here: the dialog shows the outcome and refreshes the page when it closes
  return storeAction(() => api<Generated>("/api/dq/catalog/generate", post(body)));
}

/** A portable suite file (.dq.json, logical table names) imported into this environment: its checks land in the
 *  table's draft. `staged` is false when the table already had every check. */
export type ImportedSuite = Partial<Omit<Staged, "staged">> & {
  table: string; environment: string | null; checks: number; added: number; skipped: number; staged: boolean;
};

export async function importSuite(body: { text: string; filename?: string }) {
  const r = await storeAction(() => api<ImportedSuite>("/api/dq/suites/import", post(body)));
  revalidatePath(PATH, "layout");
  return r;
}

export async function seedChecks(body: { table?: string; run_id?: string }) {
  const r = await storeAction(() => api<Staged>("/api/dq/catalog/seed", post(body)));
  revalidatePath(PATH, "layout");
  return r;
}

// ---------------------------------------------------------------- suites: versions, review, Git (V040)

const SUITE = "/api/dq/suites";

export async function loadSuite(table: string, version?: number) {
  const qs = new URLSearchParams({ table, ...(version ? { version: String(version) } : {}) });
  return storeAction(() => api<DqSuiteDetail>(`${SUITE}/one?${qs}`));
}

async function suiteWrite<T>(path: string, body: unknown, method = "POST") {
  const r = await storeAction(() => api<T>(`${SUITE}/${path}`, { method, body: JSON.stringify(body) }));
  revalidatePath(PATH, "layout");
  return r;
}

export const openSuiteDraft = async (table: string, fromVersion?: number, note?: string) =>
  suiteWrite<{ version: number; etag: string }>("draft", { table, from_version: fromVersion ?? null, note: note ?? null });
export const submitSuite = async (table: string, note?: string) =>
  suiteWrite<{ version: number; counts: Record<string, number>; loosened: boolean }>("submit", { table, note: note ?? null });
export const withdrawSuite = async (table: string) => suiteWrite<{ version: number }>("withdraw", { table });
/** `etag` is the version as the reviewer read it: a version changed since is refused (409) rather than approved. */
export const approveSuite = async (table: string, version: number, acknowledgeLoosened: boolean, etag?: string | null) =>
  suiteWrite<{ version: number; model_version: number | null; catalog: { applied: boolean; error?: string; checks?: number } }>(
    "approve", { table, version, acknowledge_loosened: acknowledgeLoosened, etag: etag ?? null });
export const applySuite = async (table: string, version: number) =>
  suiteWrite<{ applied: boolean; error?: string; checks?: number }>("apply", { table, version });
export const rejectSuite = async (table: string, version: number, reason: string, etag?: string | null) =>
  suiteWrite<{ version: number }>("reject", { table, version, reason, etag: etag ?? null });

export async function diffSuite(table: string, from: number | null, to: number) {
  const qs = new URLSearchParams({ table, to: String(to), ...(from ? { from: String(from) } : {}) });
  return storeAction(() => api<DqSuiteDiff & { from: number | null; to: number }>(`${SUITE}/diff?${qs}`));
}

export type SuitePublishing = { repos: { repo_id: string; name: string; branch: string }[]; publisher_ready: boolean };
export async function loadSuitePublishing() {
  return storeAction(() => api<SuitePublishing>(`${SUITE}/publishing`));
}
export const publishSuite = async (table: string, version: number, repoId: string) =>
  suiteWrite<{ status: string; detail?: string; head_branch?: string }>("publish", { table, version, repo_id: repoId });
export const syncSuitePrs = async (table: string, force = false) =>
  suiteWrite<{ checked: number; merged: unknown[]; closed: unknown[]; detail?: string }>("pr-sync", { table, force });

export async function runTable(table: string, checkIds?: string[]) {
  const r = await storeAction(() => api<DqScan>("/api/dq/run", post({ table, check_ids: checkIds ?? null })));
  revalidatePath(PATH);
  return r;
}

export async function loadHistory(table: string) {
  return storeAction(() => api<DqHistory>(`/api/dq/history?table=${encodeURIComponent(table)}&limit=20`));
}

export async function setSchedule(table: string, cron: string | null) {
  const r = cron
    ? await storeAction(() => api<{ cron: string; task: string }>("/api/dq/schedules", { method: "PUT", body: JSON.stringify({ table, cron }) }))
    : await storeAction(() => api<{ schedule: null }>(`/api/dq/schedules?table=${encodeURIComponent(table)}`, { method: "DELETE" }));
  revalidatePath(PATH);
  return r;
}

// ---------------------------------------------------------------- run checks (store decisions queue for DQ_APPROVER)

export type RunCheckDecision = { expectation_id: string; decision: "APPROVED" | "REJECTED" | "MODIFIED"; justification?: string;
  requirement?: string; severity?: "FAIL" | "WARN"; definition?: Record<string, unknown> };

export async function decideRunChecks(runId: string, decisions: RunCheckDecision[]) {
  const r = await storeAction(() => api<{ decided: number; skipped: { expectation_id: string; reason: string }[] }>(
    "/api/dq/run-checks/decisions", post({ run_id: runId, decisions })));
  revalidatePath(PATH);
  return r;
}

export async function runCoverage(runId: string) {
  return storeAction(() => api<import("@/lib/store-types").RunCheckCoverage>(`/api/dq/run-checks/coverage?run=${encodeURIComponent(runId)}`));
}

export async function openCase(ref: { qa_result_id?: string; check_result_id?: string }) {
  return storeAction(() => api<{ case: { case_id: string; number: number } }>("/api/cases/from-result", post(ref)));
}

/** The workflow and scan script that publish merged suites to Soda Cloud from CI (services/soda/cloud_ci). */
export type SodaCloudCi = { files: Record<string, string>; secrets: string[] };
export async function loadSodaCloudCi() {
  return storeAction(() => api<SodaCloudCi>("/api/dq/soda-cloud/ci"));
}
