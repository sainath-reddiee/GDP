import { api, getRun } from "@/lib/api";
import type { AuditEvent, RunState, RunSummary, SourcesOverview } from "@/lib/types";
import type { ModelEntity, ModelRelationship, QualitySummary } from "@/lib/hub-types";
import type { KnowledgeItem } from "@/app/knowledge/actions";
import type { SkillVersion, SkillsResponse } from "@/app/skills/types";
import type { DagRow, OpsSummary } from "@/app/ops/actions";
import type { OnboardingRequest, RequestDetail } from "./requests";

// Every /os screen reads the platform through these adapters, over the endpoints the classic pages already use (no new
// backend). There is no sample data: a source that fails or is not deployed comes back empty with its error, and the
// screen says so.

export type Loaded<T> = { data: T; error: string | null };

async function load<T>(p: Promise<T>, empty: T): Promise<Loaded<T>> {
  try { return { data: await p, error: null }; }
  catch (e) { return { data: empty, error: e instanceof Error ? e.message : String(e) }; }
}

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

export type SkillDetail = { skill_name: string; versions: SkillVersion[];
  labels: Record<string, { skill_id: string; moved_by: string; moved_at: string; note: string | null }>;
  selected: { skill_id: string; files: { path: string; content: string }[] } | null };
export const loadSkill = (name: string) => load(api<SkillDetail>(`/api/skills/${encodeURIComponent(name)}`), null as SkillDetail | null);

export type SkillDiff = { base: { version: string; revision: number }; head: { version: string; revision: number }; added: number; removed: number;
  files: { path: string; status: string; added: number; removed: number; ops: [string, number | null, number | null, string | number][] }[] };
export const loadSkillDiff = (name: string, base: string, head: string) =>
  load(api<SkillDiff>(`/api/skills/${encodeURIComponent(name)}/diff?base=${base}&head=${head}`), null as SkillDiff | null);

/** The scout's open improvement proposal for one skill, if any. */
export const loadSkillProposalFor = (name: string) =>
  load(api<{ proposal: SkillProposal | null }>(`/api/skill-proposals/for-skill/${encodeURIComponent(name)}`).then((r) => r.proposal), null as SkillProposal | null);

export type SkillProposal = { proposal_id?: string; skill_name?: string; title?: string; status?: string; rationale?: string | null;
  created_at?: string; [k: string]: unknown };
export const loadSkillProposals = () =>
  load(api<{ items: SkillProposal[] }>("/api/skill-proposals?limit=20").then((r) => r.items ?? []), [] as SkillProposal[]);

export const loadSources = () =>
  load(api<SourcesOverview>("/api/sources/overview"), { sources: [], totals: { sources: 0, tables: 0, staged: 0, profiling: 0 } } as SourcesOverview);

export const loadQuality = () => load(api<QualitySummary>("/api/hub/quality/summary?days=30"), null as QualitySummary | null);
export const loadOps = () => load(api<OpsSummary>("/api/ops/summary"), null as OpsSummary | null);

export const loadDags = () => load(api<{ dags: DagRow[] }>("/api/ops/dags?limit=50").then((r) => r.dags ?? []), [] as DagRow[]);

export type Execution = { id: string; kind: "SODA" | "QA"; run_id: string | null; run_name: string | null; target: string | null;
  total: number | null; passed: number | null; warned: number | null; failed: number | null; errors: number | null; health: number | null;
  started_at: string; duration_ms: number | null };
export const loadExecutions = () =>
  load(api<{ executions: Execution[] }>("/api/hub/quality/executions?limit=12").then((r) => r.executions ?? []), [] as Execution[]);

export type Incident = { incident_id: string; title?: string | null; kind: string; severity: string; status: string; dag_id: string; task_id?: string | null;
  first_seen: string; last_seen?: string | null; assignee?: string | null };
export const loadIncidents = () =>
  load(api<{ incidents: Incident[] }>("/api/ops/incidents?limit=12").then((r) => r.incidents ?? []), [] as Incident[]);

export type PullRequest = { title: string | null; asset_name: string | null; label: string | null; state: string; pr_url: string | null;
  href: string | null; opened_by: string | null; opened_at: string | null; kind: string; pr_number?: number | null; repo?: string | null };
export type PrListing = { prs: PullRequest[]; counts: { total: number; open: number; merged: number; attention: number };
  repos: { repo_id: string; name: string; git_url: string; branch: string }[]; publisher_ready?: boolean };
export const loadPrs = () =>
  load(api<PrListing>("/api/source-control/prs"), { prs: [], counts: { total: 0, open: 0, merged: 0, attention: 0 }, repos: [] } as PrListing);

/* onboarding requests (V075) */

export type RequestList = { requests: OnboardingRequest[]; counts: Record<string, number>; user: string };
export const loadRequests = (scope: "all" | "mine" | "waiting" = "all", status: "OPEN" | "CLOSED" | "ALL" = "OPEN", domainId?: string) =>
  load(api<RequestList>(`/api/onboarding-requests?${new URLSearchParams({ scope, status, ...(domainId ? { domain_id: domainId } : {}) })}`),
       { requests: [], counts: {}, user: "" } as RequestList);
export const loadRequest = (id: string) => load(api<RequestDetail>(`/api/onboarding-requests/${encodeURIComponent(id)}`), null as RequestDetail | null);

/* design: a domain's data model, its open questions and a run's mapping proposals */

export type DomainDiagram = {
  model_id: string; version: number; status: string; approved_by: string | null; approved_at: string | null; updated_at: string | null;
  entities: ModelEntity[]; relationships: (ModelRelationship & { from_optional?: boolean })[];
};
export const loadDomainModel = (domainId: string) =>
  load(api<{ model: { model_id: string; approved_version: number | null; open_version: number | null } | null; diagram: DomainDiagram | null }>(
    `/api/domains/${domainId}/data-model`), { model: null, diagram: null });

export type DomainQuestion = { question_id: string; run_id: string | null; stage: string | null; kind: string; question: string; created_at: string };
export const loadDomainQuestions = (domainId: string) =>
  load(api<{ questions: DomainQuestion[] }>(`/api/domains/${domainId}/questions?status=OPEN`).then((r) => r.questions ?? []), [] as DomainQuestion[]);

export const loadDomainContract = (domainId: string) =>
  load(api<{ file: string; format: string; skill?: { name: string; version: string } | null; text: string }>(`/api/domains/${domainId}/contract`), null);

export type MappingCandidate = { candidate_id: string; source_column: string; source_table: string; source_datatype: string; target_column: string;
  target_datatype: string; confidence: number; final_score: number; rank: number; recommendation: string; generated_reason: string | null;
  target_table_id?: string | null };
export type MappingDecision = { decision_id: string; candidate_id: string | null; source_column_id: string; decision: string; reviewer: string; reviewed_at: string };
export type MappingView = { candidates: MappingCandidate[]; decisions: MappingDecision[];
  targets: { target_table_id: string; target_table: string; role: string }[];
  status: { source_columns: number; decided: number; complete: boolean } | null; bands: { confident: number; weak: number } | null };
export const loadMapping = (runId: string) =>
  load(api<MappingView>(`/api/runs/${runId}/mapping`), { candidates: [], decisions: [], targets: [], status: null, bands: null } as MappingView);

export type IngestionControl = Record<string, string | number | boolean | null> & {
  child_id: number; source_name: string | null; source_type: string | null; domain_name: string | null; source_account: string | null;
  source_table_sql: string | null; parent_pipeline_name: string | null; child_pipeline_name: string | null; is_active: boolean;
  load_type: string | null; load_frequency: string | null; landing_table: string | null; bronze_table: string | null;
};
export type IngestionPipeline = { source_id: string; source_name: string; source_type: string; status: string;
  schedule: { cron?: string; frequency?: string } | null; target_landing_table: string | null; snaplogic_pipeline_name: string | null;
  created_by: string; created_at: string; has_secret: boolean };
export const loadIngestion = async () => {
  const [controls, pipelines] = await Promise.all([
    load(api<{ controls: IngestionControl[]; deployed: boolean }>("/api/hub/ingestion-control"), { controls: [], deployed: false }),
    load(api<{ pipelines: IngestionPipeline[] }>("/api/hub/pipelines").then((r) => r.pipelines ?? []), [] as IngestionPipeline[]),
  ]);
  return { controls: controls.data.controls ?? [], pipelines: pipelines.data, error: controls.error ?? pipelines.error };
};

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
  /** every validation of the run, newest first: its score against the gate of the time */
  attempts: ValidationAttempt[];
  /** the gate VALIDATE_DBT enforces for this run (rules validation.*, by its domain and pipeline) */
  gate: ValidationGate;
  generation: DbtGeneration | null;
  artifacts: DbtArtifact[];
  skills: RunSkill[];
  /** where the generated code goes (the plan stored at generation) and what was published, if anything */
  branch: { base_branch?: string; cut_branch?: string; repo?: string; origin?: string } | null;
  publication: { status: string; pr_number?: number | null; pr_url?: string | null; head_branch?: string | null; detail?: string | null; created_at?: string } | null;
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
    load(api<{ runs: ValidationCheck[]; gate?: ValidationGate }>(`/api/runs/${runId}/validation`), { runs: [] as ValidationCheck[], gate: undefined }),
    load(api<{ generation: DbtGeneration | null; artifacts: DbtArtifact[]; branch?: RunBundle["branch"]; publication?: RunBundle["publication"] }>(
      `/api/runs/${runId}/dbt`), { generation: null, artifacts: [] }),
    load(api<{ loaded: RunSkill[] }>(`/api/runs/${runId}/skills`).then((r) => r.loaded ?? []), [] as RunSkill[]),
  ]);
  return {
    data: {
      run,
      events: audit.data.events ?? [],
      reviews: audit.data.reviews ?? [],
      checks: latestChecks(validation.data.runs ?? []),
      attempts: attemptsOf(validation.data.runs ?? []),
      gate: validation.data.gate ?? DEFAULT_GATE,
      generation: dbt.data.generation ?? null,
      // the file bodies are not needed on the overview screens
      artifacts: (dbt.data.artifacts ?? []).map(({ content: _content, ...a }) => a),
      skills: skills.data,
      branch: dbt.data.branch ?? null,
      publication: dbt.data.publication ?? null,
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
  const exact = total ? (passed / total) * 100 : null;
  return { score: exact === null ? null : Math.round(exact), exact, passed, total, skipped, critical, warnings };
}

export type ValidationGate = { min_score: number; block_on_errors: boolean; standards_review?: boolean; standards_block?: boolean };
/** Only when the API does not say (services.common.rules DEFAULTS): the gate itself is a platform rule. */
export const DEFAULT_GATE: ValidationGate = { min_score: 80, block_on_errors: true };

/** Whether a score clears the gate: at least the minimum and, while errors block, no error. */
export function gatePassed(score: ReturnType<typeof scoreOf>, gate: ValidationGate) {
  return score.exact !== null && score.exact >= gate.min_score && (!gate.block_on_errors || score.critical === 0);
}

/** The gate in words: "80% with no errors" or, when errors do not block, "80%". */
export const gateText = (gate: ValidationGate) => `${gate.min_score}%${gate.block_on_errors ? " with no errors" : ""}`;

export type ValidationAttempt = { validation_id: string; at: string; status: string; score: number | null; errors: number;
  warnings: number; min_score: number | null; reasons: string[] };

/** One entry per validation the run had (its SUMMARY row), newest first, scored from the checks it recorded; the gate
 *  of the time when the attempt recorded it (validations before the gate rule carry none). */
export function attemptsOf(runs: ValidationCheck[]): ValidationAttempt[] {
  return runs.filter((r) => r.validation_type === "SUMMARY")
    .sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? ""))
    .map((r) => {
      let body: { results?: { validation_type: string; status: string; error_count: number; warning_count: number }[];
        gate?: { score?: number | null; min_score?: number; reasons?: string[] } } = {};
      try { body = r.result_json ? JSON.parse(r.result_json) : {}; } catch { body = {}; }
      const checks = (body.results ?? []).map((c) => ({ ...c, validation_id: "", result_json: null, started_at: "", completed_at: null }));
      const score = body.gate?.score ?? scoreOf(checks).score;
      return { validation_id: r.validation_id, at: r.completed_at ?? r.started_at, status: r.status, score: score === null || score === undefined ? null : Math.round(score),
        errors: r.error_count ?? 0, warnings: r.warning_count ?? 0, min_score: body.gate?.min_score ?? null, reasons: body.gate?.reasons ?? [] };
    });
}

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
