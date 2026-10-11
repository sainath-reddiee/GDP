/** URL slug for each workflow stage and the build phase that delivers its workspace.
 *  Access validation and landing run inside the Source stage ("Validate & land"). */
export const STAGES: { slug: string; stage: string; label: string; phase: number }[] = [
  { slug: "source", stage: "SOURCE", label: "Source", phase: 3 },
  { slug: "profile", stage: "PROFILING", label: "Profiling", phase: 4 },
  { slug: "mapping", stage: "MAPPING", label: "Mapping", phase: 6 },
  { slug: "sttm", stage: "STTM", label: "STTM", phase: 8 },
  { slug: "soda", stage: "SODA", label: "Data Quality", phase: 9 },
  { slug: "dbt", stage: "DBT", label: "dbt", phase: 10 },
  { slug: "validation", stage: "VALIDATION", label: "Validation", phase: 11 },
  { slug: "review", stage: "REVIEW", label: "Code review", phase: 12 },
];

export const bySlug = (slug: string) => STAGES.find((s) => s.slug === slug);
export const byStage = (stage: string) => STAGES.find((s) => s.stage === stage);

/** The six lifecycle phases the Command Center shows for a run, and the workflow stages each one covers. */
export const PHASES = [
  { label: "Source", stages: ["SOURCE"] },
  { label: "Profile", stages: ["PROFILING", "DOMAIN"] },
  { label: "Model", stages: ["MAPPING"] },
  { label: "STTM", stages: ["STTM"] },
  { label: "QA", stages: ["SODA"] },
  { label: "Deploy", stages: ["DBT", "VALIDATION", "REVIEW"] },
] as const;

/** Index into PHASES for a run's current stage; -1 before the source is chosen. */
export function phaseIndex(stage: string | null | undefined): number {
  return stage ? PHASES.findIndex((p) => (p.stages as readonly string[]).includes(stage)) : -1;
}
