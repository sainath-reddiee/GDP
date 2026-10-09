export type SkillCategory = {
  category_id: string; name: string; description: string | null; icon: string | null; color: string | null;
  position: number; is_system: boolean;
};

export type VersionBrief = {
  skill_id: string; version: string; revision: number | null; status: string; origin: string | null;
  created_by: string; created_at: string; moved_by?: string | null; moved_at?: string | null;
};

export type SkillCard = {
  skill_name: string; description: string | null; skill_type: string; category_id: string; parent_skill: string | null;
  origin: string; production: VersionBrief | null; candidate: VersionBrief | null; versions: number; drafts?: number;
  stages: { stage: string; enabled: boolean }[]; loads_30d: number; runs_30d: number; last_used: string | null;
  daily: number[]; status?: string;
};

export type SkillsResponse = {
  categories: SkillCategory[]; skills: SkillCard[]; stages?: string[]; upgraded?: boolean;
  stats: { skills?: number; in_production?: number; candidates?: number; loads_30d?: number; unused_30d?: number };
};

export type SkillVersion = VersionBrief & {
  skill_name: string; skill_type: string; category_id: string | null; parent_skill: string | null;
  description: string | null; change_note: string | null; checksum: string; parent_skill_id: string | null;
  loads_30d: number; labels: string[];
};

export type SkillLabel = { skill_name: string; label: string; skill_id: string; moved_by: string; moved_at: string; note: string | null };

export type SkillBinding = { stage: string; skill_name: string; enabled: boolean; position: number; standard: "ANY" | "GDP" };

export type SkillDetail = {
  skill_name: string; versions: SkillVersion[]; labels: Record<string, SkillLabel>;
  label_history: { label: string; from_skill_id: string | null; to_skill_id: string | null; moved_by: string; moved_at: string; note: string | null }[];
  selected: { skill_id: string; files: { path: string; content: string }[]; config: unknown };
  bindings: (SkillBinding & { updated_by: string; updated_at: string })[]; stages: string[];
  runs: { run_id: string; run_name: string | null; current_state: string | null; skill_id: string | null; version: string | null;
          picked_by: string | null; loaded_at: string; loads: number }[];
  overrides: { run_id: string; run_name: string | null; skill_id: string; set_by: string; set_at: string }[];
  children: string[]; category_id: string | null;
};

export type DiffOp = [string, number | null, number | null, string | number];
export type SkillDiff = {
  base: { skill_id: string; version: string; revision: number }; head: { skill_id: string; version: string; revision: number };
  files: { path: string; status: "added" | "removed" | "changed" | "same"; added: number; removed: number; ops: DiffOp[] }[];
  added: number; removed: number;
};

export type RunSkills = {
  loaded: { skill_name: string; version: string; skill_id: string | null; picked_by: string | null; loads: number; loaded_at: string }[];
  overrides: { skill_name: string; skill_id: string; version: string; revision: number; set_by: string; set_at: string }[];
  candidates: { skill_name: string; skill_id: string; version: string; revision: number }[];
};

export const STAGE_LABELS: Record<string, string> = {
  LANDING: "Landing", PROFILING: "Profiling", DOMAIN: "Domain", MODELING: "Modeling", MAPPING: "Mapping",
  STTM: "STTM", SODA: "Data quality", DBT: "dbt", VALIDATION: "Validation",
};

export const pretty = (name: string) => name.replace(/[_-]+/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())
  .replace(/\bAi\b/g, "AI").replace(/\bGdp\b/g, "GDP").replace(/\bDbt\b/g, "dbt").replace(/\bSttm\b/g, "STTM")
  .replace(/\bDdl\b/g, "DDL").replace(/\bQa\b/g, "QA").replace(/\bNl\b/g, "NL").replace(/\bJson\b/g, "JSON");

export const versionLabel = (v: { version: string; revision?: number | null } | null | undefined) =>
  v ? `v${v.version.split("+")[0]}${v.revision ? ` · r${v.revision}` : ""}` : "";

export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  // Snowflake VARCHAR timestamps look like "2026-10-09 10:20:30.123 -0700"
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?/);
  const tz = m?.[4] ? (m[4] === "Z" ? "Z" : `${m[4].slice(0, 3)}:${m[4].slice(-2)}`) : "";
  const s = (Date.now() - new Date(m ? `${m[1]}T${m[2]}${m[3] ?? ""}${tz}` : iso).getTime()) / 1000;
  if (!Number.isFinite(s)) return iso.slice(0, 10);
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.round(s / 86400)} d ago`;
  return iso.slice(0, 10);
}
