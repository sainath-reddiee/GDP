export type OnboardingPath = "map_existing" | "profile_suggest";
export type SourceOrigin = "snowflake" | "external";
export type DomainRelation = "existing_domain" | "new_domain";

export type SourceDetails = {
  external_connector?: string;
};

export type IntentTarget = {
  fqn: string;
  target_table: string;
  domain_name: string;
  target_table_id?: string;
};

export type OnboardingIntent = {
  path: OnboardingPath;
  run_name: string;
  source: {
    origin?: SourceOrigin;
    database: string;
    schema: string;
    source_system_name: string;
    source_type: string;
    tables: string[];
    details?: SourceDetails;
  };
  relation?: DomainRelation;
  domain_id?: string | null;
  domain_name?: string | null;
  new_domain?: { domain_name: string; description?: string } | null;
  targets: IntentTarget[];
  model_existing: boolean;
  created_at: string;
};

export type GraphColumn = { name: string; type?: string; nullable?: boolean; pk?: boolean };

export type GraphJoin = { left: string; right: string; keys: string[]; cardinality: string; confidence: number };

export type ModelGraph = {
  intent: OnboardingIntent | null;
  source: { database?: string | null; schema?: string | null };
  sources: {
    object_name: string; object_type?: string; row_count_estimate?: number | null; selected_flag?: boolean;
    columns?: GraphColumn[]; missing?: boolean;
  }[];
  targets: {
    target_table: string; fqn: string; domain_name?: string; table_type?: string | null;
    grain?: string | null; columns?: string[]; selected?: boolean;
  }[];
  edges: { from: string; to: string; kind: string; weight?: number; columns?: string[] }[];
  joins?: GraphJoin[];
  isolated?: string[];
  suggestions: {
    kind: "existing" | "proposed"; target_table: string; fqn: string;
    domain_name?: string | null; score: number; overlap_columns: string[]; reason: string;
  }[];
  profiled: boolean;
};

export type DomainRow = {
  domain_id: string; domain_name: string; description?: string | null;
  target_tables?: number;
};