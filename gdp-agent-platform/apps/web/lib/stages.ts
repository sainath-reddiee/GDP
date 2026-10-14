/** URL slug for each workflow stage and the build phase that delivers its workspace.
 *  Pipeline 1, Source to Silver (pre-MDM): Source Ingestion, Profiling, Modeling, Silver Code Generation, with the
 *  quality lane (QA tests, Data Quality, Validation) and Code review. Modeling is one screen for two workflow stages,
 *  MAPPING (field mapping) and STTM (transformations and joins); the backend stage ids are unchanged.
 *  Access validation and landing run inside Source Ingestion ("Validate & land"). */
export const STAGES: { slug: string; stage: string; label: string; step?: string; phase: number }[] = [
  { slug: "source", stage: "SOURCE", label: "Source Ingestion", phase: 3 },
  { slug: "profile", stage: "PROFILING", label: "Profiling", phase: 4 },
  { slug: "modeling?tab=mapping", stage: "MAPPING", label: "Modeling", step: "Field mapping", phase: 6 },
  { slug: "modeling?tab=transformations", stage: "STTM", label: "Modeling", step: "Transformations and joins", phase: 8 },
  { slug: "soda", stage: "SODA", label: "Data Quality", phase: 9 },
  { slug: "dbt", stage: "DBT", label: "Silver Code Generation", phase: 10 },
  { slug: "validation", stage: "VALIDATION", label: "Validation", phase: 11 },
  { slug: "review", stage: "REVIEW", label: "Code review", phase: 12 },
];

/** The workflow stages Modeling covers, in order. */
export const MODELING_STAGES = ["MAPPING", "STTM"] as const;

export const bySlug = (slug: string) => STAGES.find((s) => s.slug === slug);
export const byStage = (stage: string) => STAGES.find((s) => s.stage === stage);

/** A stage's label with its Modeling step when it has one: "Modeling: Field mapping". */
export const stageLabel = (stage: string) => {
  const s = byStage(stage);
  return s ? (s.step ? `${s.label}: ${s.step}` : s.label) : stage;
};

/** The five lifecycle phases the Command Center shows for a run, and the workflow stages each one covers. */
export const PHASES = [
  { label: "Ingestion", stages: ["SOURCE"] },
  { label: "Profiling", stages: ["PROFILING", "DOMAIN"] },
  { label: "Modeling", stages: ["MAPPING", "STTM"] },
  { label: "Quality", stages: ["SODA"] },
  { label: "Code", stages: ["DBT", "VALIDATION", "REVIEW"] },
] as const;

/** Index into PHASES for a run's current stage; -1 before the source is chosen. */
export function phaseIndex(stage: string | null | undefined): number {
  return stage ? PHASES.findIndex((p) => (p.stages as readonly string[]).includes(stage)) : -1;
}
