"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";

export type QaTest = {
  test_id: string; category: string; title: string; objective: string | null; sql: string; expected: string | null;
  severity: string; target_column: string | null; source?: string | null; origin: "GENERATED" | "AI" | "USER";
  prompt?: string | null; created_by?: string | null; created_at?: string | null; warning?: string | null;
};

export type QaSuite = {
  target: { fqn: string; name: string };
  sources: Record<string, string>;
  business_keys: string[];
  driving_table: string | null;
  tests: QaTest[];
  script: string;
  counts: Record<string, number>;
};

export type QaAnswer = {
  title: string; category: string; objective: string; sql: string; expected: string; assumptions: string[];
  valid: boolean; problems: string[]; compile_error: string | null; note?: string | null; model: string;
};

export async function askQa(runId: string, question: string) {
  return attemptValue(() =>
    api<QaAnswer>(`/api/runs/${runId}/qa/ask`, { method: "POST", body: JSON.stringify({ question }) }));
}

export async function saveQaTest(runId: string, test: {
  title: string; sql: string; objective?: string; expected?: string; category?: string; prompt?: string;
  severity?: string; target_column?: string;
}) {
  const result = await attemptValue(() =>
    api<{ test_id: string }>(`/api/runs/${runId}/qa/tests`, { method: "POST", body: JSON.stringify(test) }));
  if (result.ok) revalidatePath(`/runs/${runId}/qa`);
  return result;
}

export async function deleteQaTest(runId: string, testId: string) {
  const result = await attemptValue(() =>
    api<{ deleted: string }>(`/api/runs/${runId}/qa/tests/${testId}`, { method: "DELETE" }));
  if (result.ok) revalidatePath(`/runs/${runId}/qa`);
  return result;
}

export type QaSignoff = { decision: "APPROVED" | "REJECTED"; note: string | null; decided_by: string; decided_at: string };

/** Approve or reject the QA tests of the current STTM version (a rejection needs a reason). */
export async function signOffQa(runId: string, decision: "APPROVED" | "REJECTED", note: string, override = false) {
  const result = await attemptValue(() =>
    api<{ signoff: QaSignoff | null }>(`/api/runs/${runId}/qa/signoff`, {
      method: "POST", body: JSON.stringify({ decision, note, override }),
    }),
  );
  if (result.ok) revalidatePath(`/runs/${runId}`, "layout");
  return result;
}

export type QaOutcome = "PASS" | "FAIL" | "REVIEW" | "NOT_RUN" | "ERROR";

export type QaResult = {
  test_id: string; category: string | null; title: string | null; severity: string | null; origin: string | null;
  outcome: QaOutcome; rows_returned: number | null; measured: string | null; expected: string | null;
  detail: string | null; columns: string[] | null; sample: Record<string, unknown>[] | null; sql_text: string | null;
  duration_ms: number | null;
};

export type QaRunRow = {
  qa_run_id: string; started_at: string; tests: number; passed: number; failed: number; review: number;
  not_run: number; errors: number; created_by: string | null; target_built?: boolean; duration_ms?: number;
};

export type QaResults = {
  run: QaRunRow | null;
  results: QaResult[];
  history: Record<string, { outcome: QaOutcome; at: string }[]>;
  runs: QaRunRow[];
  ready: boolean;
};

export async function runQa(runId: string, testIds?: string[]) {
  const result = await attemptValue(() =>
    api<QaRunRow & { blocking: number }>(`/api/runs/${runId}/qa/run`, {
      method: "POST", body: JSON.stringify({ test_ids: testIds ?? null }),
    }));
  if (result.ok) revalidatePath(`/runs/${runId}`, "layout");
  return result;
}

export type QaProposal = {
  title: string; category: string; severity: string; target_column?: string; objective: string; sql: string;
  expected: string; why: string; valid: boolean; problems: string[]; compile_error: string | null; note?: string | null;
};

export async function planQa(runId: string, focus: string) {
  return attemptValue(() =>
    api<{ tests: QaProposal[]; model: string; code_citations?: import("@/components/code-citations").CodeCitation[];
          grounding: { rules: number; profile_columns: number; checks: number; code?: number } }>(
      `/api/runs/${runId}/qa/plan`, { method: "POST", body: JSON.stringify({ focus }) }));
}

export async function updateQaTest(runId: string, testId: string, test: {
  title: string; sql: string; objective?: string | null; expected?: string | null; severity?: string;
}) {
  const result = await attemptValue(() =>
    api<{ test_id: string; note?: string | null }>(`/api/runs/${runId}/qa/tests/${testId}`, {
      method: "PUT", body: JSON.stringify(test),
    }));
  if (result.ok) revalidatePath(`/runs/${runId}/qa`);
  return result;
}
