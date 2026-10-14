"use server";

import { revalidatePath } from "next/cache";
import { api, attemptValue } from "@/lib/api";
import type { SuggestionResult } from "@/app/runs/[runId]/pipeline-actions";

export type Pack = Record<string, unknown>;

/** AI drafts a pack from a contract document; nothing is saved. */
export async function draftPack(text: string, standard: "GDP" | "GENERIC") {
  return attemptValue(() =>
    api<{ pack: Pack; problems: string[]; model: string | null }>("/api/domains/draft", {
      method: "POST", body: JSON.stringify({ text, standard }),
    }),
  );
}

export async function exportPack(domainId: string) {
  return attemptValue(() => api<{ pack: Pack }>(`/api/domains/${domainId}/export`));
}

export async function importPack(pack: Pack) {
  const result = await attemptValue(() =>
    api<{ domain_id: string; domain_name: string; targets: number; columns: number; knowledge: number;
          inactive_targets: string[] }>("/api/domains/import", { method: "POST", body: JSON.stringify({ pack }) }),
  );
  if (result.ok) {
    revalidatePath("/domains");
    revalidatePath("/dashboard/sources");
  }
  return result;
}


function refreshDomains() {
  revalidatePath("/domains");
  revalidatePath("/dashboard/sources");
}

/** Soft delete of a UI-added domain; `force` also deletes it when runs are still in flight. */
export async function deleteDomain(domainId: string, force: boolean) {
  const result = await attemptValue(() =>
    api<{ deleted: boolean }>(`/api/domains/${domainId}?force=${force}`, { method: "DELETE" }));
  if (result.ok) refreshDomains();
  return result;
}

export async function restoreDomain(domainId: string) {
  const result = await attemptValue(() => api<{ restored: boolean }>(`/api/domains/${domainId}/restore`, { method: "POST" }));
  if (result.ok) refreshDomains();
  return result;
}

export async function loadDomainReview(domainId: string) {
  return attemptValue(() => api<SuggestionResult>(`/api/domains/${domainId}/suggestions`));
}

export async function askDomainReview(domainId: string, refresh: boolean) {
  return attemptValue(() =>
    api<SuggestionResult>(`/api/domains/${domainId}/suggestions?refresh=${refresh}`, { method: "POST" }));
}

export async function decideDomainSuggestion(domainId: string, body: {
  suggestion_id: string | null; scope_key: string; item: Record<string, unknown>; decision: "ACCEPTED" | "REJECTED";
}) {
  const result = await attemptValue(() =>
    api<{ applied?: string | null }>(`/api/domains/${domainId}/suggestions/decision`, {
      method: "POST", body: JSON.stringify({ suggestion_id: body.suggestion_id, item: body.item, decision: body.decision }),
    }));
  if (result.ok) refreshDomains();
  return result;
}

export type DomainAnswer = { answer: string; citations: { kind: string; key: string; title: string }[]; model: string };

export async function askDomain(domainId: string, question: string) {
  return attemptValue(() =>
    api<DomainAnswer>(`/api/domains/${domainId}/ask`, { method: "POST", body: JSON.stringify({ question }) }));
}

// ---------------------------------------------------------------- versions, people, rules

export type DomainVersion = {
  version: number; change_kind: string | null; change_note: string | null; created_by: string; created_at: string;
  summary: string[]; targets: number; columns: number;
};
export type DomainDiff = {
  base: number; head: number; summary: string[]; added: number; removed: number;
  files: { path: string; status: "changed" | "same"; added: number; removed: number; ops: [string, number | null, number | null, string | number][] }[];
};
export type DomainMember = { user_name: string; role: "OWNER" | "STEWARD" | "EXPERT"; added_by: string; added_at: string };
export type DomainRules = { defaults: Record<string, unknown>; platform: Record<string, unknown>; effective: Record<string, unknown>; overrides: Record<string, unknown> };
export type TargetModel = { table: string; description: string | null; active: boolean; type: string | null; grain: string | null;
  columns: { name: string; type: string | null; nullable: boolean; key: boolean; pii: boolean; definition: string | null }[] };

function refreshDomain(domainId: string) {
  refreshDomains();
  revalidatePath(`/domains/${domainId}`);
}

export async function domainDiff(domainId: string, base: number, head: number) {
  return attemptValue(() => api<DomainDiff>(`/api/domains/${domainId}/diff?base=${base}&head=${head}`));
}

export async function rollbackDomain(domainId: string, version: number, note?: string) {
  const r = await attemptValue(() => api<{ version: number | null; changed: boolean }>(`/api/domains/${domainId}/rollback/${version}`, {
    method: "POST", body: JSON.stringify({ note: note || null }),
  }));
  refreshDomain(domainId);
  return r;
}

export async function editDomain(domainId: string, body: { description?: string | null; owner?: string | null; note?: string | null }) {
  const r = await attemptValue(() => api<{ version: number | null }>(`/api/domains/${domainId}`, { method: "PUT", body: JSON.stringify(body) }));
  refreshDomain(domainId);
  return r;
}

export async function saveMembers(domainId: string, members: { user: string; role: string }[]) {
  const r = await attemptValue(() => api<{ members: DomainMember[] }>(`/api/domains/${domainId}/members`, {
    method: "PUT", body: JSON.stringify({ members }),
  }));
  refreshDomain(domainId);
  return r;
}

export async function saveDomainRules(domainId: string, overrides: Record<string, unknown>, note?: string) {
  const r = await attemptValue(() => api(`/api/domains/${domainId}/rules`, { method: "PUT", body: JSON.stringify({ overrides, note: note || null }) }));
  refreshDomain(domainId);
  return r;
}

/** How a domain is built (sources, parts, timeline) and how complete it is (coverage, trust, gaps). */
export type ProvenanceSource = {
  id: string; label: string; meaning: string; count: number; verified: number; used: number; draft: number;
  types: Record<string, number>; people: string[]; files?: string[]; repos?: { name: string; matched: number }[];
  items: { knowledge_id: string; title: string; type: string; ref: string | null; verified: boolean; uses: number; status: string }[];
};
export type DomainManifest = {
  skill: { standard: string; name: string; path: string; contract: string } | null;
  pack: { file: string; generator: string | null; items: number; shipped: boolean } | null;
  standard: string | null;
  stages: { stage: string; skills: { skill: string; position: number | null; loaded: { version: string; loads: number; runs: number; last: string }[] }[] }[];
  repos: { name: string; git_url: string; branch: string; last_commit: string | null; last_indexed_at: string | null;
           files: { path: string; name: string; kind: string | null }[] }[];
  runs: { run_id: string; run_name: string | null; items: number; kinds: string[]; current_state: string | null }[];
  people: string[];
};
export type FlowItem = { label: string; kind: string; href?: string | null; state?: string };
export type FlowStep = { id: string; title: string; role: string; facts: string[]; items: FlowItem[]; state: string };
export type DomainContract = {
  path: string | null; file?: string; format?: string; where?: string; detail?: string; text: string; chars?: number;
  skill: { name: string; version: string; revision: number | null } | null;
  extracted: { database: string | null; schema: string | null; hub: string | null; required: string[];
               targets: { table: string; columns: number; hkey_columns: string[]; reference_ctes: string[] }[] };
};
export type DomainProvenance = {
  manifest: DomainManifest;
  flow: { lanes: (FlowStep & { steps: FlowStep[] })[] };
  proposed: number;
  needs_verification: { knowledge_id: string; title: string; type: string; source: string; source_id: string; ref: string | null; uses: number; created_at: string | null }[];
  provenance: {
    sources: ProvenanceSource[]; upstream: { id: string; label: string; file: string; feeds: string; meaning: string }[];
    parts: { id: string; label: string; count: number }[]; edges: { source: string; part: string; count: number }[];
    totals: { items: number; verified: number; used: number; draft: number };
    timeline: { at: string; kind: string; label: string; detail: string; by: string | null }[];
  };
  coverage: {
    score: number; tables_score: number; trust_score: number;
    knowledge: { items: number; verified: number; used_90d: number; unused_90d: number; awaiting_review: number };
    checks: { id: string; label: string; done: number; of: number }[];
    targets: { table: string; fqn: string; checks: Record<string, boolean>; score: number; keys: string[]; glossary_missing: string[] }[];
    actions: { table: string; check: string; label: string; prompt: string }[];
  };
};

export async function domainProvenance(domainId: string) {
  return attemptValue(() => api<DomainProvenance>(`/api/domains/${encodeURIComponent(domainId)}/provenance`));
}

/** The domain's contract file (Markdown in the dbt skill) and what the platform extracts from it. */
export async function domainContract(domainId: string) {
  return attemptValue(() => api<DomainContract>(`/api/domains/${encodeURIComponent(domainId)}/contract`));
}
