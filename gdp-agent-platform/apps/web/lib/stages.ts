/** URL slug for each workflow stage and the build phase that delivers its workspace.
 *  Both pipelines run the same stages on the same engine:
 *  - Silver Pipeline (source to Silver, pre-MDM): Source Ingestion, Profiling, Modeling, Silver Code Generation;
 *  - Gold Pipeline (Silver, read in place, to Gold): Silver Selection, Profiling, Modeling, Gold Code Generation;
 *  each with the quality lane (QA tests, Data Quality, Validation) and Code review. Modeling is one screen for two
 *  workflow stages, MAPPING (field mapping) and STTM (transformations and joins); the backend stage ids are unchanged.
 *  Access validation and landing run inside the first step ("Validate & land", or read in place for Gold). */
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

export type Pipeline = "SILVER" | "GOLD";

/** The two pipelines: names, what they take and produce, the list page and what an item is called. */
export const PIPELINES: Record<Pipeline, { n: number; name: string; from: string; to: string; href: string; run: string; hint: string }> = {
  SILVER: { n: 1, name: "Silver Pipeline", from: "Source", to: "Silver (pre-MDM)", href: "/runs", run: "Silver run",
            hint: "Ingest, profile, model and map, QA, Data Quality, Silver code, validate, review" },
  GOLD: { n: 2, name: "Gold Pipeline", from: "Silver", to: "Gold", href: "/gold", run: "Gold run",
          hint: "Select (read in place), profile, model and map, QA, Data Quality, Gold code, validate, review" },
};

/** Where a Gold run's step is named differently (same stage, same gates). */
const GOLD_LABELS: Record<string, string> = { SOURCE: "Silver Selection", DBT: "Gold Code Generation" };

export const pipelineOf = (value: string | null | undefined): Pipeline => (String(value ?? "").toUpperCase() === "GOLD" ? "GOLD" : "SILVER");

/** A stage's name in a pipeline: "Silver Selection" or "Source Ingestion", "Gold Code Generation" or "Silver Code Generation". */
export const labelFor = (stage: string, pipeline: Pipeline = "SILVER") =>
  (pipeline === "GOLD" && GOLD_LABELS[stage]) || byStage(stage)?.label || stage;

/** The workflow stages Modeling covers, in order. */
export const MODELING_STAGES = ["MAPPING", "STTM"] as const;

export const bySlug = (slug: string) => STAGES.find((s) => s.slug === slug);
export const byStage = (stage: string) => STAGES.find((s) => s.stage === stage);

/** A stage's label with its Modeling step when it has one: "Modeling: Field mapping". */
export const stageLabel = (stage: string, pipeline: Pipeline = "SILVER") => {
  const s = byStage(stage);
  return s ? (s.step ? `${s.label}: ${s.step}` : labelFor(stage, pipeline)) : stage;
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
