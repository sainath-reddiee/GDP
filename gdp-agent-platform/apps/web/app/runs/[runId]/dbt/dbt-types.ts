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

export type DbtWorkspace = {
  integrations?: { name: string; type: string; enabled?: boolean; allowed_prefixes?: string[]; comment?: string }[];
  git_repositories?: { name: string; fqn: string; origin: string; api_integration: string; last_fetched?: string }[];
  dbt_projects?: { name: string; fqn: string; comment?: string }[];
  skills?: { skill_name?: string; name?: string; version?: string | null; description?: string; skill_type?: string }[];
  models?: CortexModel[];
  default_model?: string;
  warnings?: string[];
};
