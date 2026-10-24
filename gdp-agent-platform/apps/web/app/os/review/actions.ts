"use server";

import { api, attemptValue } from "@/lib/api";

export type StandardsFinding = { severity: string; message: string; rule?: string; file?: string; line_hint?: string };
export type StandardsCheck = { validation_type: string; status: string; error_count: number; warning_count: number; findings: StandardsFinding[] };
export type StandardsReview = {
  run_id: string; generation_id: string; generation_version: number | null; summary: string; score: number | null;
  checks: StandardsCheck[]; reviewed: string[]; left_out: string[]; skills: string[]; model: string | null; block: boolean;
};

/** The code standards agent alone on the run's current generation (CORE.REVIEW_STANDARDS). It reports; it records no
 *  validation and does not move the run, so nothing is revalidated. */
export async function reviewStandards(runId: string) {
  return attemptValue(() => api<StandardsReview>(`/api/runs/${encodeURIComponent(runId)}/standards`, { method: "POST" }));
}
