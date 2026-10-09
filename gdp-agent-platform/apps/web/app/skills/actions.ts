"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";
import type { RunSkills, SkillBinding, SkillCategory, SkillDiff } from "./types";

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
