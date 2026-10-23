import { cookies } from "next/headers";
import { api, getRun } from "@/lib/api";
import type { AuditEvent, RunState, RunSummary, SourcesOverview } from "@/lib/types";
import type { QualitySummary } from "@/lib/hub-types";
import type { KnowledgeItem } from "@/app/knowledge/actions";
import type { SkillsResponse } from "@/app/skills/types";
import type { OpsSummary } from "@/app/ops/actions";
import { DEFAULT_PERSONA, PERSONA_COOKIE, personaById } from "./personas";

// Every /os screen reads the platform through these adapters, over the endpoints the classic pages already use (no new
// backend). There is no sample data: a source that fails or is not deployed comes back empty with its error, and the
// screen says so.

export type Loaded<T> = { data: T; error: string | null };

async function load<T>(p: Promise<T>, empty: T): Promise<Loaded<T>> {
  try { return { data: await p, error: null }; }
  catch (e) { return { data: empty, error: e instanceof Error ? e.message : String(e) }; }
}

export const currentPersona = () => personaById(cookies().get(PERSONA_COOKIE)?.value ?? DEFAULT_PERSONA);

/** Runs, newest activity first. Test runs are left out, as on the classic lists. */
export const loadRuns = (query = "sort=updated&limit=60") =>
  load(api<{ runs: RunSummary[]; total?: number }>(`/api/runs?${query}`).then((r) => ({ runs: r.runs ?? [], total: r.total ?? r.runs?.length ?? 0 })),
       { runs: [] as RunSummary[], total: 0 });

export type Domain = {
  domain_id: string; domain_name: string; description: string | null; owner: string | null; standard: string | null;
  active_flag: boolean; version: number; knowledge_items: number; target_tables: number; gold_tables?: number;
  mdm_role?: string | null; consumes?: string | null;
};
export const loadDomains = () =>
  load(api<{ domains: Domain[] }>("/api/domains").then((r) => (r.domains ?? []).filter((d) => d.active_flag !== false)), [] as Domain[]);

export const loadKnowledge = (params: Record<string, string> = {}) =>
  load(api<{ items: KnowledgeItem[]; total: number }>(`/api/knowledge?${new URLSearchParams({ limit: "200", status: "ACTIVE", ...params })}`)
    .then((r) => ({ items: r.items ?? [], total: r.total ?? 0 })), { items: [] as KnowledgeItem[], total: 0 });

export type KnowledgeOverview = {
  totals: { active: number; inbox: number; learned_7d: number; stale: number; verified: number; used_30d: number; uses_30d: number };
  by: { type: Record<string, number>; origin: Record<string, number>; domain: Record<string, number>; status: Record<string, number> };
  top_used: { knowledge_id: string; title: string; knowledge_type: string; domain_name: string; uses: number; runs: number }[];
};
export const loadKnowledgeOverview = () => load(api<KnowledgeOverview>("/api/knowledge/overview"), null as KnowledgeOverview | null);

export type KnowledgeFeedItem = { knowledge_id: string; title: string; knowledge_type: string; origin: string | null; source_run_id: string | null;
  run_name: string | null; version: number; status: string; domain_name?: string | null; created_at: string; created_by?: string | null };
export const loadKnowledgeFeed = (limit = 12) =>
  load(api<{ items: KnowledgeFeedItem[] }>(`/api/knowledge/feed?limit=${limit}`).then((r) => r.items ?? []), [] as KnowledgeFeedItem[]);

export type KnowledgeUsage = { run_id: string; run_name: string; current_state: string; stage: string; version: number; uses: number; last_used: string };
/** One knowledge record with its version history and the runs that used it. */
export async function loadKnowledgeRecord(id: string) {
  const [versions, usage] = await Promise.all([
    load(api<{ versions: KnowledgeItem[] }>(`/api/knowledge/${encodeURIComponent(id)}/versions`).then((r) => r.versions ?? []), [] as KnowledgeItem[]),
    load(api<{ runs: KnowledgeUsage[] }>(`/api/knowledge/${encodeURIComponent(id)}/usage`).then((r) => r.runs ?? []), [] as KnowledgeUsage[]),
  ]);
  const item = versions.data.find((v) => v.knowledge_id === id) ?? versions.data.find((v) => v.is_current) ?? null;
  return { item, versions: versions.data, usage: usage.data, error: versions.error ?? usage.error };
}

export type KnowledgeInboxItem = { knowledge_id: string; title: string; knowledge_type: string; domain_name: string | null; origin: string | null; created_at: string };
export const loadKnowledgeInbox = () =>
  load(api<{ items: KnowledgeInboxItem[] }>("/api/knowledge/inbox").then((r) => r.items ?? []), [] as KnowledgeInboxItem[]);

export const loadSkills = () =>
  load(api<SkillsResponse>("/api/skills"), { categories: [], skills: [], stats: {} } as SkillsResponse);

export type SkillProposal = { proposal_id?: string; skill_name?: string; title?: string; status?: string; rationale?: string | null;
  created_at?: string; [k: string]: unknown };
export const loadSkillProposals = () =>
  load(api<{ items: SkillProposal[] }>("/api/skill-proposals?limit=20").then((r) => r.items ?? []), [] as SkillProposal[]);

export const loadSources = () =>
  load(api<SourcesOverview>("/api/sources/overview"), { sources: [], totals: { sources: 0, tables: 0, staged: 0, profiling: 0 } } as SourcesOverview);

export const loadQuality = () => load(api<QualitySummary>("/api/hub/quality/summary?days=30"), null as QualitySummary | null);
export const loadOps = () => load(api<OpsSummary>("/api/ops/summary"), null as OpsSummary | null);

export type Execution = { id: string; kind: "SODA" | "QA"; run_id: string | null; run_name: string | null; target: string | null;
  total: number | null; passed: number | null; warned: number | null; failed: number | null; errors: number | null; health: number | null;
  started_at: string; duration_ms: number | null };
export const loadExecutions = () =>
  load(api<{ executions: Execution[] }>("/api/hub/quality/executions?limit=12").then((r) => r.executions ?? []), [] as Execution[]);

export type Incident = { incident_id: string; title: string; severity: string; status: string; dag_id: string; last_seen: string };
export const loadIncidents = () =>
  load(api<{ incidents: Incident[] }>("/api/ops/incidents?limit=12").then((r) => r.incidents ?? []), [] as Incident[]);

export type PullRequest = { title: string | null; asset_name: string | null; label: string | null; state: string; pr_url: string | null;
  href: string | null; opened_by: string | null; opened_at: string | null; kind: string; pr_number?: number | null; repo?: string | null };
export type PrListing = { prs: PullRequest[]; counts: { total: number; open: number; merged: number; attention: number };
  repos: { repo_id: string; name: string; git_url: string; branch: string }[]; publisher_ready?: boolean };
export const loadPrs = () =>
  load(api<PrListing>("/api/source-control/prs"), { prs: [], counts: { total: 0, open: 0, merged: 0, attention: 0 }, repos: [] } as PrListing);

/* QA for one run: the planned test pack, the latest results and the signoff */

export type QaTest = { test_id: string; category: string; title: string; objective?: string; severity?: string };
export type QaResult = { result_id: string; test_id: string; category: string; title: string; severity: string; outcome: string; detail: string | null };
export type QaRun = { qa_run_id: string; started_at: string; tests: number; passed: number; failed: number; review: number; not_run: number; errors: number;
  target_built: boolean; created_by: string };
export type QaSignoff = { decision: string; note: string | null; decided_by: string; decided_at: string };
export async function loadRunQa(runId: string) {
  const [pack, results, signoff] = await Promise.all([
    load(api<{ tests: QaTest[] }>(`/api/runs/${runId}/qa`).then((r) => r.tests ?? []), [] as QaTest[]),
    load(api<{ run: QaRun | null; results: QaResult[] }>(`/api/runs/${runId}/qa/results`), { run: null, results: [] }),
    load(api<{ signoff: QaSignoff | null }>(`/api/runs/${runId}/qa/signoff`).then((r) => r.signoff), null as QaSignoff | null),
  ]);
  return { tests: pack.data, run: results.data.run, results: results.data.results ?? [], signoff: signoff.data,
           error: pack.error ?? results.error ?? signoff.error };
}

/* one run, everything the build workbench and the review gate show */

export type ValidationCheck = { validation_id: string; validation_type: string; status: string; error_count: number; warning_count: number;
  result_json: string | null; started_at: string; completed_at: string | null };
export type Finding = { severity?: string; level?: string; message?: string; detail?: string; rule?: string; file?: string; path?: string;
  column?: string; table?: string; [k: string]: unknown };
export type DbtArtifact = { artifact_id: string; artifact_type: string; file_path: string; content_sha256: string; content?: string };
export type DbtGeneration = { generation_id: string; generation_version: number; generation_status: string; files_generated: number;
  stage_path: string; skill_version: string | null; model_version: string | null; created_at: string };
export type RunSkill = { skill_name: string; version: string; skill_id: string; picked_by: string; loads: number; loaded_at: string };
export type RunBundle = {
  run: RunState;
  events: AuditEvent[];
  reviews: { review_id: string; stage: string; decision: string; business_justification: string | null; reviewer: string; reviewed_at: string }[];
  checks: ValidationCheck[];
  generation: DbtGeneration | null;
  artifacts: DbtArtifact[];
  skills: RunSkill[];
  errors: string[];
};

/** The latest attempt of each validation check, oldest type first (SUMMARY is the roll-up of the others, not a check). */
function latestChecks(runs: ValidationCheck[]): ValidationCheck[] {
  const byType = new Map<string, ValidationCheck>();
  for (const c of runs) {
    if (c.validation_type === "SUMMARY") continue;
    const prev = byType.get(c.validation_type);
    if (!prev || (c.started_at ?? "") > (prev.started_at ?? "")) byType.set(c.validation_type, c);
  }
  return Array.from(byType.values());
}

export function findingsOf(check: ValidationCheck): Finding[] {
  if (!check.result_json) return [];
  try {
    const body = JSON.parse(check.result_json);
    return Array.isArray(body?.findings) ? body.findings : [];
  } catch { return []; }
}

export async function loadRunBundle(runId: string): Promise<Loaded<RunBundle | null>> {
  let run: RunState;
  try { run = await getRun(runId); }
  catch (e) { return { data: null, error: e instanceof Error ? e.message : String(e) }; }
  const [audit, validation, dbt, skills] = await Promise.all([
    load(api<{ events: AuditEvent[]; reviews: RunBundle["reviews"] }>(`/api/runs/${runId}/audit`), { events: [], reviews: [] }),
    load(api<{ runs: ValidationCheck[] }>(`/api/runs/${runId}/validation`).then((r) => r.runs ?? []), [] as ValidationCheck[]),
    load(api<{ generation: DbtGeneration | null; artifacts: DbtArtifact[] }>(`/api/runs/${runId}/dbt`), { generation: null, artifacts: [] }),
    load(api<{ loaded: RunSkill[] }>(`/api/runs/${runId}/skills`).then((r) => r.loaded ?? []), [] as RunSkill[]),
  ]);
  return {
    data: {
      run,
      events: audit.data.events ?? [],
      reviews: audit.data.reviews ?? [],
      checks: latestChecks(validation.data),
      generation: dbt.data.generation ?? null,
      // the file bodies are not needed on the overview screens
      artifacts: (dbt.data.artifacts ?? []).map(({ content: _content, ...a }) => a),
      skills: skills.data,
      errors: [audit.error, validation.error, dbt.error, skills.error].filter((x): x is string => !!x),
    },
    error: null,
  };
}

export const checkPassed = (c: ValidationCheck) => c.status === "PASSED" || c.status === "WARNING" || c.status === "WARN";
export const checkSkipped = (c: ValidationCheck) => c.status === "SKIPPED";

/** Validation score: the share of the evaluated checks (the latest of each; skipped ones are not evaluated) that
 *  passed, warnings counting as passed: 0..100, or null before the first validation. A check that errors is a
 *  critical failure whatever the score. */
export function scoreOf(checks: ValidationCheck[]) {
  const skipped = checks.filter(checkSkipped).length;
  const total = checks.length - skipped;
  const passed = checks.filter(checkPassed).length;
  const critical = checks.reduce((n, c) => n + (c.error_count ?? 0), 0);
  const warnings = checks.reduce((n, c) => n + (c.warning_count ?? 0), 0);
  return { score: total ? Math.round((passed / total) * 100) : null, passed, total, skipped, critical, warnings };
}

/** The validation gate's threshold (guide: 80% AND no critical failure before a PR). */
export const PASS_THRESHOLD = 80;

/** Artifacts grouped by what they are, for the "Generated artifacts" table. */
export function artifactGroups(artifacts: Pick<DbtArtifact, "artifact_type" | "file_path">[]) {
  // by the artifact types the build agent writes (DBT_MODEL, DBT_SOURCES_YML, SODA_CHECKS, STTM_EXPORT, RELEASE_PLAN...)
  const label = (a: Pick<DbtArtifact, "artifact_type" | "file_path">) => {
    const t = a.artifact_type.toUpperCase();
    if (t.includes("TEST") || /(^|\/)tests?\//.test(a.file_path)) return { key: "Quality and unit tests", type: "SQL / YAML" };
    if (t.includes("DAG") || /(^|\/)dags?\//.test(a.file_path)) return { key: "Airflow DAG", type: "Python" };
    if (t === "DBT_MODEL") return { key: "dbt models", type: "SQL / dbt" };
    if (t === "DBT_SOURCES_YML" || t === "DBT_SCHEMA_YML") return { key: "Sources and schema", type: "YAML" };
    if (t === "DBT_MACRO") return { key: "dbt macros", type: "SQL / Jinja" };
    if (t.startsWith("SODA")) return { key: "Data quality checks", type: "Soda YAML" };
    if (t === "STTM_EXPORT") return { key: "Mapping specification", type: "CSV / JSON" };
    if (t === "RELEASE_PLAN") return { key: "Release plan", type: "JSON / SQL" };
    if (t === "DBT_PROJECT") return { key: "dbt project config", type: "YAML" };
    return { key: pretty(t), type: a.file_path.split(".").pop()?.toUpperCase() ?? "File" };
  };
  const groups = new Map<string, { key: string; type: string; files: string[] }>();
  for (const a of artifacts) {
    const g = label(a);
    const cur = groups.get(g.key) ?? { ...g, files: [] };
    cur.files.push(a.file_path);
    groups.set(g.key, cur);
  }
  return Array.from(groups.values()).sort((a, b) => b.files.length - a.files.length);
}

const pretty = (raw: string) => { const s = raw.replace(/_/g, " ").toLowerCase(); return s.charAt(0).toUpperCase() + s.slice(1); };

/** The run a workbench opens when none is named: the newest one in the given states, else the newest that is not a
 *  draft. */
export function defaultRun(runs: RunSummary[], states: string[]): RunSummary | null {
  const live = runs.filter((r) => !r.is_archived);
  return live.find((r) => states.includes(r.current_state)) ?? live.find((r) => r.lifecycle !== "DRAFT") ?? live[0] ?? null;
}

/** Options for the run switcher, grouped by domain. */
export const runOptions = (runs: RunSummary[]) =>
  runs.filter((r) => !r.is_archived && r.lifecycle !== "DRAFT").slice(0, 60)
    .map((r) => ({ id: r.run_id, label: r.run_name, group: r.domain_name ?? "No domain" }));

/** "COMPANY_HIERARCHY" from "DEV_GDP_SILVER_DB.COMPANY.COMPANY_HIERARCHY". */
export const tableOf = (target: string | null | undefined) => target?.split(".").pop() ?? null;

/** Whole runs grouped by domain: what each onboarded domain has in flight and done. */
export function runsByDomain(runs: RunSummary[]) {
  const map = new Map<string, RunSummary[]>();
  for (const r of runs) {
    const k = r.domain_name ?? "Unassigned";
    map.set(k, [...(map.get(k) ?? []), r]);
  }
  return map;
}
