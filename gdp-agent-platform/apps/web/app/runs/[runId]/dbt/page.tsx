import { api, getRun } from "@/lib/api";
import { AiSuggestions } from "@/components/ai-suggestions";
import { StageGate } from "@/components/stage-gate";
import type { CodeRepo } from "../../../code/actions";
import { DbtStudio } from "./dbt-studio";
import type { DbtArtifact, DbtGeneration, DbtPublication, DbtWorkspace, GenerationReport, GithubStatus } from "./dbt-types";

const CAN_GENERATE = new Set([
  "STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED",
  "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_FAILED",
]);

export default async function DbtPage({ params }: { params: { runId: string } }) {
  const [state, data, workspace, github, code] = await Promise.all([
    getRun(params.runId),
    api<{ generation: DbtGeneration | null; artifacts: DbtArtifact[];
          branch: Record<string, unknown> | null;
          skills: { applied?: { name: string; version?: string; description?: string }[] } | null;
          workspace: Record<string, unknown> | null;
          publication: DbtPublication | null;
          report: GenerationReport | null;
          skeleton_base: Record<string, string> | null;
        }>(`/api/runs/${params.runId}/dbt`),
    api<DbtWorkspace>(`/api/runs/${params.runId}/dbt/workspace`).catch(() => ({
      integrations: [], git_repositories: [], dbt_projects: [], skills: [], models: [], warnings: ["Could not list Snowflake git objects"],
    })),
    api<GithubStatus>("/api/dbt/github").catch(() => ({ ready: false, config: null })),
    api<{ repos: CodeRepo[] }>("/api/code/repos").catch(() => ({ repos: [] as CodeRepo[] })),
  ]);
  // repositories configured once in Admin, Integrations: enabled ones that serve this run's domain (none listed means all)
  const domainId = state.run.domain_id ?? null;
  const configured = code.repos.filter((r) => r.enabled && (!r.domain_ids.length || (domainId && r.domain_ids.includes(domainId))));
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
        appliedSkills={data.skills?.applied ?? []}
        lastWorkspace={data.workspace}
        workspace={workspace}
        publication={data.publication ?? null}
        github={github}
        report={data.report ?? null}
        skeletonBase={data.skeleton_base ?? null}
        configuredRepos={configured}
      />
    </StageGate>
  );
}
