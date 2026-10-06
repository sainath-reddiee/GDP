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
