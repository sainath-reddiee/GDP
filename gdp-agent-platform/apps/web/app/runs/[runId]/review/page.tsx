import { api, getRun } from "@/lib/api";
import { StageGate } from "@/components/stage-gate";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { RunConsole } from "../console";
import { DbtStudio } from "../dbt/dbt-studio";
import type { DbtArtifact, DbtGeneration } from "../dbt/dbt-types";

export default async function ReviewPage({ params }: { params: { runId: string } }) {
  const [state, dbt, validation] = await Promise.all([
    getRun(params.runId),
    api<{ generation: DbtGeneration | null; artifacts: DbtArtifact[];
          branch: { base_branch?: string; cut_branch?: string; repo?: string; instruction?: string } | null;
        }>(`/api/runs/${params.runId}/dbt`),
    api<{ runs: { validation_id: string; validation_type: string; status: string; error_count: number }[] }>(
      `/api/runs/${params.runId}/validation`,
    ),
  ]);
  return (
    <StageGate state={state} stage="REVIEW">
      <Card>
        <CardHeader>
          <CardTitle>Code review</CardTitle>
          <CardDescription>
            Review the generated dbt project and the branch it should be cut from. Approval records the
            reviewer and does not deploy, merge a PR, or enable a pipeline.
          </CardDescription>
        </CardHeader>
        <CardContent className="text-sm">
          {dbt.branch && (
            <p className="mb-2">
              Cut <span className="font-mono">{dbt.branch.cut_branch}</span> from{" "}
              <span className="font-mono">{dbt.branch.base_branch}</span>
              {dbt.branch.repo ? ` in ${dbt.branch.repo}` : ""}.
            </p>
          )}
          <ul className="list-disc pl-5 text-muted-foreground">
            {validation.runs.filter((r) => r.validation_type !== "SUMMARY").map((r) => (
              <li key={r.validation_id}>{r.validation_type}: {r.status} ({r.error_count} errors)</li>
            ))}
          </ul>
        </CardContent>
      </Card>
      <RunConsole state={state} />
      <DbtStudio
        runId={params.runId}
        runName={state.run.run_name}
        domainName={state.run.domain_name}
        canGenerate={false}
        generation={dbt.generation}
        artifacts={dbt.artifacts}
        branch={dbt.branch}
      />
    </StageGate>
  );
}
