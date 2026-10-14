"use server";

import { api, attemptValue } from "@/lib/api";

/** The learning loop (V058): rules learned from reviewer decisions, replays of a change on past runs, and the questions
 *  the engine asks a domain's owner. */
export type ReplayMeasure = { target_pct: number | null; target_hits: number; columns: number; transform_pct: number | null;
  transform_hits: number; transforms: number };
export type Replay = { verdict: "BETTER" | "SAME" | "WORSE" | "NO_EVIDENCE"; baseline: ReplayMeasure | null; candidate: ReplayMeasure | null;
  delta: { target_pct: number | null; transform_pct: number | null } | null; runs: number;
  flipped?: { run: string; column: string; before: string | null; after: string | null; approved: string; better: boolean }[];
  skipped?: string[] };
export type LearnedView = { key: string; habit: "TRANSFORMATION" | "OVERRIDE" | "RENAME"; title: string; knowledge_id?: string | null;
  evidence: { decisions: number; runs: number; tables: number; share: number; corrected_engine: number; examples: string[] };
  replay: Replay };
export type LearnResult = { layer: string; decisions: number; found: number; already_decided: number; proposed: LearnedView[];
  held_back: LearnedView[]; runs: { run_id: string; run_name: string; target: string; columns: number }[]; skipped: string[] };
export type Learning = { layer: string; deployed: boolean; decisions: number; open_questions: number;
  learned: { knowledge_id: string; knowledge_type: string; title: string; content: string; status: string; created_at: string;
    evidence: LearnedView["evidence"] | null; replay: Replay | null }[];
  evals: { subject_kind: string; subject_id: string; verdict: string; delta: Replay["delta"]; runs: number | null; created_by: string; created_at: string }[] };
export type Question = { question_id: string; domain_id: string; layer: string; run_id: string | null; stage: string;
  subject_key: string; kind: "MAPPING" | "NOTE"; question: string; status: "OPEN" | "ANSWERED" | "DISMISSED";
  context: { source_table?: string; source_column?: string; target_table?: string; data_type?: string; description?: string;
    values?: string[]; candidates?: { target: string; score: number; definition?: string | null }[]; stage?: string } | null;
  options: string[] | null; answer: { choice?: string | null; text?: string | null } | null; answered_by: string | null;
  answered_at: string | null; knowledge_id: string | null; created_at: string };
export type Questions = { questions: Question[]; deployed: boolean };

export async function domainLearning(domainId: string, layer: string) {
  return attemptValue(() => api<Learning>(`/api/domains/${encodeURIComponent(domainId)}/learning?layer=${layer}`));
}
export async function learnFromDecisions(domainId: string, layer: string) {
  // no revalidation: the knowledge inbox reads fresh on every visit, and a refresh here would clear the result shown
  return attemptValue(() => api<LearnResult>(`/api/domains/${encodeURIComponent(domainId)}/learning/learn?layer=${layer}`,
                                             { method: "POST" }));
}
export async function replayKnowledge(knowledgeId: string) {
  return attemptValue(() => api<Replay & { knowledge_id: string; layer: string }>(
    `/api/knowledge/${encodeURIComponent(knowledgeId)}/replay`, { method: "POST" }));
}
export async function domainQuestions(domainId: string, layer: string, status = "OPEN") {
  return attemptValue(() => api<Questions>(`/api/domains/${encodeURIComponent(domainId)}/questions?layer=${layer}&status=${status}`));
}
export async function runQuestions(runId: string) {
  return attemptValue(() => api<Questions>(`/api/runs/${encodeURIComponent(runId)}/questions`));
}
export async function answerQuestion(questionId: string, body: { choice?: string | null; text?: string | null }) {
  return attemptValue(() => api<{ question_id: string; status: string; knowledge_id: string | null; kind: string }>(
    `/api/questions/${encodeURIComponent(questionId)}/answer`, { method: "POST", body: JSON.stringify(body) }));
}
export async function dismissQuestion(questionId: string) {
  return attemptValue(() => api<{ question_id: string; status: string }>(`/api/questions/${encodeURIComponent(questionId)}/dismiss`,
                                                                        { method: "POST" }));
}
