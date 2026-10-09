"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";
import type {
  BuilderIssue, BuilderQuestion, DraftRequest, DraftResponse, RunSkills, SkillBinding, SkillCategory, SkillDiff, SkillDraft,
  SkillTest, TestSummary,
} from "./types";

function done(name?: string) {
  revalidatePath("/skills");
  if (name) revalidatePath(`/skills/${encodeURIComponent(name)}`);
}

export async function moveLabel(name: string, label: "production" | "candidate", skillId: string, note?: string) {
  const r = await attemptValue(() => api(`/api/skills/${encodeURIComponent(name)}/labels/${label}`, {
    method: "POST", body: JSON.stringify({ skill_id: skillId, note: note || null }),
  }));
  done(name);
  return r;
}

export async function clearCandidate(name: string) {
  const r = await attemptValue(() => api(`/api/skills/${encodeURIComponent(name)}/labels/candidate`, { method: "DELETE" }));
  done(name);
  return r;
}

export async function saveVersion(name: string, body: {
  content: string; base_skill_id?: string; description?: string | null; change_note: string;
  status?: "DRAFT" | "ACTIVE"; set_candidate?: boolean;
}) {
  const r = await attemptValue(() => api<{ skill_id: string; version: string }>(
    `/api/skills/${encodeURIComponent(name)}/versions`, { method: "POST", body: JSON.stringify(body) }));
  done(name);
  return r;
}

export async function setVersionStatus(name: string, skillId: string, action: "retire" | "restore") {
  const r = await attemptValue(() => api(`/api/skills/${encodeURIComponent(name)}/versions/${skillId}/${action}`, { method: "POST" }));
  done(name);
  return r;
}

export async function loadDiff(name: string, base: string, head: string) {
  return attemptValue(() => api<SkillDiff>(
    `/api/skills/${encodeURIComponent(name)}/diff?base=${encodeURIComponent(base)}&head=${encodeURIComponent(head)}`));
}

export async function moveCategory(name: string, categoryId: string) {
  const r = await attemptValue(() => api(`/api/skills/${encodeURIComponent(name)}/category`, {
    method: "PUT", body: JSON.stringify({ category_id: categoryId }),
  }));
  done(name);
  return r;
}

export async function saveCategory(body: Omit<SkillCategory, "category_id" | "is_system" | "position"> & { position?: number | null },
                                   categoryId?: string) {
  const r = await attemptValue(() => api<{ categories: SkillCategory[] }>(
    categoryId ? `/api/skills/categories/${categoryId}` : "/api/skills/categories",
    { method: categoryId ? "PUT" : "POST", body: JSON.stringify(body) }));
  done();
  return r;
}

export async function deleteCategory(categoryId: string) {
  const r = await attemptValue(() => api(`/api/skills/categories/${categoryId}`, { method: "DELETE" }));
  done();
  return r;
}

export async function saveBindings(bindings: SkillBinding[], stages: string[]) {
  const r = await attemptValue(() => api("/api/skills/bindings", {
    method: "PUT", body: JSON.stringify({ bindings, stages }),
  }));
  done();
  revalidatePath("/admin");
  return r;
}

export async function runSkills(runId: string) {
  return attemptValue(() => api<RunSkills>(`/api/runs/${runId}/skills`));
}

export async function tryOnRun(runId: string, name: string, skillId: string | null) {
  const path = `/api/runs/${runId}/skills/${encodeURIComponent(name)}`;
  const r = await attemptValue(() => skillId
    ? api<RunSkills>(path, { method: "PUT", body: JSON.stringify({ skill_id: skillId }) })
    : api<RunSkills>(path, { method: "DELETE" }));
  revalidatePath(`/runs/${runId}`, "layout");
  done(name);
  return r;
}

// ---------------------------------------------------------------- AI skill builder

export async function builderModels() {
  return attemptValue(() => api<{ default: string; models: string[] }>("/api/skills/builder/models"));
}

export async function builderQuestions(goal: string, categoryId?: string | null, model?: string | null) {
  return attemptValue(() => api<{ questions: BuilderQuestion[]; model: string }>("/api/skills/builder/questions", {
    method: "POST", body: JSON.stringify({ goal, category_id: categoryId || null, model: model || null }),
  }));
}

export async function builderDraft(body: DraftRequest) {
  return attemptValue(() => api<DraftResponse>("/api/skills/builder/draft", { method: "POST", body: JSON.stringify(body) }));
}

export async function builderCheck(draft: SkillDraft, accepted: boolean[], improving?: string | null) {
  return attemptValue(() => api<{ content: string; issues: BuilderIssue[] }>("/api/skills/builder/check", {
    method: "POST", body: JSON.stringify({ draft, accepted, improving: improving || null }),
  }));
}

export async function builderTest(content: string, tests: SkillTest[], skillName?: string | null, model?: string | null) {
  return attemptValue(() => api<TestSummary>("/api/skills/builder/test", {
    method: "POST", body: JSON.stringify({ content, tests, skill_name: skillName || null, model: model || null }),
  }));
}

export async function createSkill(body: { name: string; description: string; category_id: string; content: string;
  change_note: string; origin?: "AI" | "USER"; eval?: TestSummary | null }) {
  const r = await attemptValue(() => api<{ skill_name: string; skill_id: string; version: string }>("/api/skills", {
    method: "POST", body: JSON.stringify(body),
  }));
  done(body.name);
  return r;
}

export async function saveAiVersion(name: string, body: { content: string; base_skill_id?: string; description?: string;
  change_note: string; eval?: TestSummary | null }) {
  const r = await attemptValue(() => api<{ skill_id: string; version: string }>(`/api/skills/${encodeURIComponent(name)}/versions`, {
    method: "POST", body: JSON.stringify({ ...body, status: "DRAFT", set_candidate: true, origin: "AI" }),
  }));
  done(name);
  return r;
}

export async function listDomains() {
  return attemptValue(() => api<{ domains: { domain_id: string; domain_name: string; active_flag: boolean; knowledge_items: number }[] }>("/api/domains"));
}
