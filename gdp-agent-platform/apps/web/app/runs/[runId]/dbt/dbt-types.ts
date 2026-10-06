export type DbtArtifact = {
  artifact_id: string;
  artifact_type: string;
  file_path: string;
  content: string;
};

export type DbtGeneration = {
  generation_id: string;
  generation_version: number;
  generation_status: string;
  files_generated: number;
  stage_path: string;
  model_version?: string;
};

export type GitBranch = {
  name: string;
  commit?: string;
  last_modified?: string;
  author?: string;
  message?: string;
};

export type CortexModel = {
  name: string;
  family?: string;
  source?: string;
  kind?: string;
};

export type DbtIntegration = {
  name: string; type: string; enabled?: boolean; allowed_prefixes?: string[]; comment?: string;
  usable?: boolean; provider?: string; detail?: string; allowed_secrets?: string[];
};

export type DbtRepo = {
  name: string; fqn: string; origin: string; api_integration: string; last_fetched?: string;
  usable?: boolean; grant_sql?: string | null;
};

export type DbtWorkspace = {
  role?: string;
  integrations?: DbtIntegration[];
  git_repositories?: DbtRepo[];
  dbt_projects?: { name: string; fqn: string; comment?: string }[];
  skills?: { skill_name?: string; name?: string; version?: string | null; description?: string; skill_type?: string }[];
  models?: CortexModel[];
  default_model?: string;
  warnings?: string[];
  capabilities?: { git_read?: boolean; git_write?: boolean; github_publish?: boolean; dbt_project?: boolean };
};

export type DbtPublication = {
  status: string; origin?: string | null; base_branch?: string | null; head_branch?: string | null;
  commit_sha?: string | null; files_pushed?: number | null; pr_number?: number | null; pr_url?: string | null;
  dbt_project?: string | null; detail?: string | null; generation_id?: string | null; created_at?: string;
};

export type GithubStatus = { ready: boolean; config: { secret?: string; external_access_integration?: string } | null };

export type ReportColumn = {
  target_column: string; class: string; source?: string | null; source_type?: string | null;
  target_type?: string | null; cast?: string | null; note?: string;
};

export type GenerationReport = {
  engine?: string; skill?: string; convention?: string; domain?: string; target?: string; source_key?: string;
  prefix?: string; source_system?: string; primary_source?: string;
  joins?: { table: string; alias: string; on: string; cardinality: string }[];
  source_unique_id?: { expression: string; reason: string; columns: string[] };
  dedup_order?: string; hub?: string; macros_added?: string[];
  columns?: ReportColumn[]; counts?: Record<string, number>; casts?: number; todos?: number;
  anomalies?: string[]; files?: Record<string, "new" | "patched" | "unchanged">; rules?: string[];
  skeleton_files?: number;
};

export type ReviewFinding = { severity: "error" | "warning" | "info"; rule: string; message: string; line_hint: string };

export type ReviewResult = {
  file_path: string; summary: string; findings: ReviewFinding[]; revised_content: string;
  rejected_revision: string[]; model?: string;
};

export type GithubCheck = {
  status: string; detail?: string; repository?: string; default_branch?: string; push?: boolean | null;
  private?: boolean; html_url?: string;
};
