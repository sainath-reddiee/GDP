import { api, getRun } from "@/lib/api";
import { StageGate } from "@/components/stage-gate";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DbtStudio } from "./dbt-studio";
import type { DbtArtifact, DbtGeneration, DbtWorkspace } from "./dbt-types";

const CAN_GENERATE = new Set([
  "STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED",
  "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_FAILED",
]);

export default async function DbtPage({ params }: { params: { runId: string } }) {
  const [state, data, workspace] = await Promise.all([
    getRun(params.runId),
    api<{ generation: DbtGeneration | null; artifacts: DbtArtifact[];
          branch: Record<string, unknown> | null;
          skills: { applied?: { name: string; version?: string; description?: string }[] } | null;
          workspace: Record<string, unknown> | null;
        }>(`/api/runs/${params.runId}/dbt`),
    api<DbtWorkspace>(`/api/runs/${params.runId}/dbt/workspace`).catch(() => ({
      integrations: [], git_repositories: [], dbt_projects: [], skills: [], models: [], warnings: ["Could not list Snowflake git objects"],
    })),
  ]);
  return (
    <StageGate state={state} stage="DBT">
      <Card>
        <CardHeader>
          <CardTitle>dbt workspace</CardTitle>
          <CardDescription>
            Parallel to Data Quality. Models come from the approved STTM. Optional git copy uses
            COPY FILES onto a Snowflake branch path — it does not open a pull request.
            CREATE DBT PROJECT stays WRITEBACK=FALSE. Enhance any file with a Cortex model from this account.
          </CardDescription>
        </CardHeader>
      </Card>
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
      />
    </StageGate>
  );
}
