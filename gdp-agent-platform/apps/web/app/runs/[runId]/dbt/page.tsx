import { api, getRun } from "@/lib/api";
import { AiSuggestions } from "@/components/ai-suggestions";
import { StageGate } from "@/components/stage-gate";
import { DbtStudio } from "./dbt-studio";
import type { DbtArtifact, DbtGeneration, DbtPublication, DbtSetup, DbtWorkspace, GenerationReport } from "./dbt-types";

const CAN_GENERATE = new Set([
  "STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED",
  "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_FAILED",
]);

const NO_SETUP: DbtSetup = { repository: null, candidates: [], legacy: false, legacy_setup: null, default_cut_branch: "", publishing: { ready: false } };

export default async function DbtPage({ params }: { params: { runId: string } }) {
  const [state, data, workspace] = await Promise.all([
    getRun(params.runId),
    api<{ generation: DbtGeneration | null; artifacts: DbtArtifact[];
          branch: Record<string, unknown> | null;
          workspace: Record<string, unknown> | null;
          publication: DbtPublication | null;
          report: GenerationReport | null;
          skeleton_base: Record<string, string> | null;
        } & Partial<DbtSetup>>(`/api/runs/${params.runId}/dbt`),
    // models for the Cortex review only; the repository and publishing come from Admin's configuration above
    api<DbtWorkspace>(`/api/runs/${params.runId}/dbt/workspace?git=false`).catch(() => ({ models: [], default_model: "" } as DbtWorkspace)),
  ]);
  const slug = state.run.run_name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "run";
  const setup: DbtSetup = {
    repository: data.repository ?? null, candidates: data.candidates ?? [], legacy: Boolean(data.legacy),
    legacy_setup: data.legacy_setup ?? null, publishing: data.publishing ?? NO_SETUP.publishing,
    default_cut_branch: data.default_cut_branch || `feat/onboard-${slug}`,
  };
  const models = workspace.models ?? [];
  return (
    <StageGate state={state} stage="DBT">
      <AiSuggestions runId={params.runId} stage="DBT" canAct={!state.is_archived} />
      <DbtStudio
        runId={params.runId}
        runName={state.run.run_name}
        domainName={state.run.domain_name}
        canGenerate={CAN_GENERATE.has(state.current_state)}
        generation={data.generation}
        artifacts={data.artifacts}
        branch={data.branch}
        lastWorkspace={data.workspace}
        publication={data.publication ?? null}
        report={data.report ?? null}
        skeletonBase={data.skeleton_base ?? null}
        models={models}
        defaultModel={workspace.default_model || models[0]?.name || "claude-sonnet-4-5"}
        setup={setup}
      />
    </StageGate>
  );
}
