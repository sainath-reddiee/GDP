"use server";

import { revalidatePath } from "next/cache";
import { api, apiForm, attemptValue } from "@/lib/api";
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
export type DomainRules = { defaults: Record<string, unknown>; platform: Record<string, unknown>; effective: Record<string, unknown>; overrides: Record<string, unknown>;
  /** SILVER or GOLD; for Gold, base is the domain's Silver values the Gold overrides sit on */
  pipeline?: "SILVER" | "GOLD"; base?: Record<string, unknown> };
export type TargetModel = { table: string; description: string | null; active: boolean; type: string | null; grain: string | null;
  /** set to GOLD on gold tables (facts, dimensions, bridges); silver tables carry none */
  layer?: "GOLD";
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

export async function saveDomainRules(domainId: string, overrides: Record<string, unknown>, note?: string, pipeline: "SILVER" | "GOLD" = "SILVER") {
  const r = await attemptValue(() => api(`/api/domains/${domainId}/rules?pipeline=${pipeline}`, { method: "PUT", body: JSON.stringify({ overrides, note: note || null }) }));
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
  stages: { stage: string; skills: { skill: string; position: number | null; domain?: boolean; replaces?: string | null; loaded: { version: string; loads: number; runs: number; last: string }[] }[] }[];
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
export type ReadinessItem = { id: string; label: string; met: boolean; detail: string; href: string; action: string };
export type DomainReadiness = {
  level: "none" | "minimum" | "recommended" | "best";
  levels: { id: string; title: string; meaning: string; met: boolean; done: number; items: ReadinessItem[] }[];
  next: ReadinessItem | null;
};
export type DomainProvenance = {
  readiness?: DomainReadiness;
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

/** The domain's own skills per stage (V051) and what its runs load. */
export type DomainSkillRow = { stage: string; skill_name: string; mode: "ADD" | "REPLACE"; replaces: string | null; position: number;
                               enabled: boolean; note: string | null; updated_by?: string; updated_at?: string;
                               /** ANY: both layers; SILVER or GOLD: that layer's runs only */
                               pipeline?: "ANY" | "SILVER" | "GOLD" };
export type DomainSkills = {
  standard: string; deployed: boolean; available: { name: string; description: string; version: string | null }[];
  pipeline?: "SILVER" | "GOLD"; mdm_role?: string | null; other_layer?: number;
  stages: { stage: string; standard_skills: string[]; domain_skills: DomainSkillRow[]; effective: string[];
            mdm_skills?: { skill_name: string; stage: string }[] }[];
};
export async function domainSkills(domainId: string, pipeline: "SILVER" | "GOLD" = "SILVER") {
  return attemptValue(() => api<DomainSkills>(`/api/domains/${encodeURIComponent(domainId)}/skills?pipeline=${pipeline}`));
}
export async function saveDomainSkills(domainId: string, skills: Omit<DomainSkillRow, "updated_by" | "updated_at">[],
                                       pipeline: "SILVER" | "GOLD" = "SILVER") {
  const r = await attemptValue(() => api<DomainSkills>(`/api/domains/${encodeURIComponent(domainId)}/skills?pipeline=${pipeline}`,
    { method: "PUT", body: JSON.stringify({ skills }) }));
  revalidatePath(`/domains/${domainId}`);
  return r;
}

/** A domain's playbook for one layer: how its runs model and code (skills, conventions, rules, knowledge, readiness). */
export type Conventions = { pipeline: "SILVER" | "GOLD"; standard: string; platform: Record<string, unknown>;
  overrides: Record<string, unknown>; effective: Record<string, unknown> };
export type Playbook = {
  domain_id: string; pipeline: "SILVER" | "GOLD"; mdm_role: string | null;
  skills: Pick<DomainSkills, "standard" | "stages" | "available" | "deployed" | "other_layer">;
  conventions: Conventions;
  rules: { changed: { key: string; value: unknown; base: unknown }[] };
  knowledge: { type: string; why: string; stages: string[]; layer_items: number; shared_items: number; gap: boolean }[];
  model: { model_id: string; approved_version: number | null; open_version: number | null } | null;
  readiness: { id: string; label: string; met: boolean; detail: string }[];
};
export async function domainPlaybook(domainId: string, pipeline: "SILVER" | "GOLD") {
  return attemptValue(() => api<Playbook>(`/api/domains/${encodeURIComponent(domainId)}/playbook?pipeline=${pipeline}`));
}
export async function playbookPreview(domainId: string, pipeline: "SILVER" | "GOLD", stage: string, target?: string) {
  const qs = new URLSearchParams({ pipeline, stage, ...(target ? { target } : {}) });
  return attemptValue(() => api<{ skills: string[]; context: string; stage: string }>(`/api/domains/${encodeURIComponent(domainId)}/playbook/preview?${qs}`));
}
export async function saveDomainConventions(domainId: string, pipeline: "SILVER" | "GOLD", overrides: Record<string, unknown>, note?: string) {
  const r = await attemptValue(() => api<Conventions>(`/api/domains/${encodeURIComponent(domainId)}/conventions?pipeline=${pipeline}`,
    { method: "PUT", body: JSON.stringify({ overrides, note: note || null }) }));
  revalidatePath(`/domains/${domainId}`);
  return r;
}
export async function mdmLayer(domainId: string) {
  return attemptValue(() => api<{ readiness: Playbook["readiness"]; rules: { knowledge_id: string; title: string; content: string }[] }>(
    `/api/domains/${encodeURIComponent(domainId)}/mdm-layer`));
}

/** Confluence pages, spaces and uploaded documents linked to the domain (V051). */
export type DomainDocument = {
  source_id: string; kind: "PAGE" | "SPACE" | "UPLOAD"; space_key: string | null; page_id: string | null; title: string | null;
  url: string | null; file_name: string | null; created_by: string; created_at: string; last_refreshed_at: string | null;
  last_result: { read?: number; unchanged?: number; proposed?: number; errors?: { page_id: string; error: string }[] } | null;
  pages: { page_id: string; title: string; version: number | null; url: string | null; proposed: number; chars: number; fetched_at: string }[];
  /** V052: the kept original and how it was read */
  stage_path?: string | null; file_size?: number | null; content_type?: string | null; file_sha256?: string | null;
  file_kind?: "text" | "excel" | "document" | "image" | "binary" | null;
  read_status?: "QUEUED" | "READING" | "READY" | "PARTIAL" | "NOT_READABLE" | "FAILED" | "INTERRUPTED" | null;
  read_method?: string | null; read_note?: string | null; pages_count?: number | null; chars_total?: number | null; chars_read?: number | null;
  read_finished_at?: string | null;
};
export type DomainDocuments = { sources: DomainDocument[]; deployed: boolean; files?: boolean;
  limits?: { max_mb: number; max_files: number }; confluence: { enabled: boolean; granted: boolean; connected: boolean } };
export async function domainDocuments(domainId: string) {
  return attemptValue(() => api<DomainDocuments>(`/api/domains/${encodeURIComponent(domainId)}/documents`));
}
export async function linkConfluence(domainId: string, url: string) {
  const r = await attemptValue(() => api<{ source_id: string; title: string; kind: string; result: { read: number; unchanged: number; proposed: number } }>(
    `/api/domains/${encodeURIComponent(domainId)}/documents/confluence`, { method: "POST", body: JSON.stringify({ url }) }));
  revalidatePath(`/domains/${domainId}`);
  return r;
}
export type UploadedDocument = { file_name: string; source_id?: string; kind?: string; size?: number; read_status?: string;
  duplicate?: boolean; proposed?: number; error?: string; note?: string | null };
export async function uploadDomainDocuments(domainId: string, form: FormData) {
  const r = await attemptValue(() => apiForm<{ documents: UploadedDocument[]; skipped?: number }>(
    `/api/domains/${encodeURIComponent(domainId)}/documents/upload`, form));
  revalidatePath(`/domains/${domainId}`);
  return r;
}
export async function refreshDocument(domainId: string, sourceId: string) {
  return attemptValue(() => api<{ read?: number; unchanged?: number; proposed?: number; errors?: { page_id: string; error: string }[]; read_status?: string }>(
    `/api/domains/${encodeURIComponent(domainId)}/documents/${encodeURIComponent(sourceId)}/refresh`, { method: "POST" }));
}
export async function removeDocument(domainId: string, sourceId: string) {
  return attemptValue(() => api<{ removed: boolean }>(
    `/api/domains/${encodeURIComponent(domainId)}/documents/${encodeURIComponent(sourceId)}/remove`, { method: "POST" }));
}

/** The domain's data model for the Lineage tab: approved, or the open draft with what it changes. */
export type DomainDiagramEntity = {
  entity_name: string; kind: string; purpose: string; grain: string; business_keys: string[]; subject_area: string | null; scd_type: string | null;
  attributes: { name: string; datatype: string; nullable: boolean; is_pk: boolean; source_columns: string[]; derived: boolean; description: string | null; business_key: boolean }[];
};
export type DomainDataModel = {
  model: { model_id: string; approved_version: number | null; open_version: number | null } | null;
  diagram?: {
    model_id: string; version: number; status: string; approved_by: string | null; approved_at: string | null; updated_at: string | null;
    platform: "SNOWFLAKE" | "ICEBERG"; location: { database: string | null; schema: string | null };
    entities: DomainDiagramEntity[];
    relationships: { from_entity: string; from_column: string; to_entity: string; to_column: string; cardinality: string; from_optional?: boolean }[];
    positions: Record<string, { x: number; y: number }>;
    coverage: Record<string, { knowledge: number; sttm: boolean; table_model: boolean }>;
    highlight: { entities: Record<string, "added" | "changed" | "removed">; columns: Record<string, "added" | "changed" | "removed">;
      removed_entities: string[]; changes: { path: string; kind: string; class: string; message: string }[]; counts?: Record<string, number> };
    subject_areas: string[]; summary: { tables: number; columns: number; relationships: number };
  } | null;
};
export async function domainDataModel(domainId: string, view: "approved" | "draft" = "approved") {
  return attemptValue(() => api<DomainDataModel>(`/api/domains/${encodeURIComponent(domainId)}/data-model?view=${view}`));
}

/** Correct-output examples per table: the accepted ones and the ones waiting for review. */
export type TableExamples = { knowledge_id: string; columns: string[]; rows: string[][]; row_count: number; file: string | null;
  warnings: string[]; created_by: string | null; created_at: string | null; verified_by: string | null };
export type DomainExamples = { tables: Record<string, { table: string; active: TableExamples | null; proposed: TableExamples | null }> };
export type ExampleUpload = { table: string; columns: string[]; row_count: number; unknown_columns: string[]; absent_columns: string[];
  missing_keys: string[]; warnings: string[]; knowledge_id: string | null; unchanged: boolean };
export async function domainExamples(domainId: string) {
  return attemptValue(() => api<DomainExamples>(`/api/domains/${encodeURIComponent(domainId)}/examples`));
}
export async function uploadExamples(domainId: string, table: string, form: FormData) {
  return attemptValue(() => apiForm<ExampleUpload>(`/api/domains/${encodeURIComponent(domainId)}/examples/${encodeURIComponent(table)}`, form));
}

/** A document as it was read, and every item proposed from it, for review side by side. */
export type ReviewItem = {
  knowledge_id: string; knowledge_type: string; title: string; content: string; status: "PROPOSED" | "ACTIVE" | "REJECTED";
  confidence: number | null; quote: string; page_id: string; found: boolean; page: number | null; target_table: string | null;
  target_column: string | null; created_by: string | null; created_at: string | null; reviewed_by: string | null; review_note: string | null;
};
export type DocumentReview = {
  source: DomainDocument & { status: string };
  pages: { page_id: string; title: string | null; version: number | null; url: string | null; fetched_at: string | null; text: string; truncated: boolean }[];
  items: ReviewItem[]; counts: { PROPOSED: number; ACTIVE: number; REJECTED: number };
};
export async function documentReview(domainId: string, sourceId: string) {
  return attemptValue(() => api<DocumentReview>(`/api/domains/${encodeURIComponent(domainId)}/documents/${encodeURIComponent(sourceId)}/review`));
}

/** Where the domain sits in the medallion: mastered by Reltio, a consumer of mastered domains, or not linked. */
export type MdmHop = { layer: "SILVER_PRE_MDM" | "MDM" | "SILVER_POST_MDM" | "GOLD"; object: string; key: string[]; kind?: string; cardinality?: string; skey?: string };
export type DomainMdm = {
  domain: { domain_id: string; domain_name: string }; role: "MASTERED" | "CONSUMER" | null; env: string; rules: number;
  mastered_domains: { domain_id: string; domain_name: string; silver_column: string | null; gold_column: string | null }[];
  contract?: Record<string, unknown>; chain?: MdmHop[];
  sql?: { resolution: string; coverage: string };
  consumers?: { domain_id: string; domain_name: string; via: { silver_column: string; gold_column: string }[] }[];
  consumes?: { domain: string; silver_column: string; gold_column: string; domain_id: string | null; known: boolean; chain?: MdmHop[];
               gold?: { schema: string; dimension: string; skey: string; mastered_id: string } }[];
};
export type MdmCoverage = { sources: { source_system: string; records: number; matched: number; matched_pct: number | null }[];
  records: number; matched: number; matched_pct: number | null; sql: string };
export async function domainMdm(domainId: string) {
  return attemptValue(() => api<DomainMdm>(`/api/domains/${encodeURIComponent(domainId)}/mdm`));
}
export async function domainMdmCoverage(domainId: string) {
  return attemptValue(() => api<MdmCoverage>(`/api/domains/${encodeURIComponent(domainId)}/mdm/coverage`));
}
export async function saveDomainMdm(domainId: string, consumes: { domain: string; silver_column: string; gold_column: string }[]) {
  const r = await attemptValue(() => api<DomainMdm>(`/api/domains/${encodeURIComponent(domainId)}/mdm`,
    { method: "PUT", body: JSON.stringify({ consumes }) }));
  revalidatePath(`/domains/${domainId}`);
  return r;
}
