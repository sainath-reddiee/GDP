/** URL slug for each workflow stage and the build phase that delivers its workspace. */
export const STAGES: { slug: string; stage: string; label: string; phase: number }[] = [
  { slug: "source", stage: "SOURCE", label: "Source", phase: 3 },
  { slug: "access", stage: "ACCESS", label: "Access", phase: 3 },
  { slug: "landing", stage: "LANDING", label: "Landing", phase: 3 },
  { slug: "profile", stage: "PROFILING", label: "Profiling", phase: 4 },
  { slug: "domain", stage: "DOMAIN", label: "Domain", phase: 5 },
  { slug: "mapping", stage: "MAPPING", label: "Mapping", phase: 6 },
  { slug: "sttm", stage: "STTM", label: "STTM", phase: 8 },
  { slug: "soda", stage: "SODA", label: "Soda", phase: 9 },
  { slug: "dbt", stage: "DBT", label: "dbt", phase: 10 },
  { slug: "validation", stage: "VALIDATION", label: "Validation", phase: 11 },
  { slug: "review", stage: "REVIEW", label: "Code review", phase: 12 },
];

export const bySlug = (slug: string) => STAGES.find((s) => s.slug === slug);
export const byStage = (stage: string) => STAGES.find((s) => s.stage === stage);
