import { api, getRun } from "@/lib/api";
import { StageGate } from "@/components/stage-gate";
import { StageAction } from "@/components/stage-action";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { generateDbt } from "../pipeline-actions";

export default async function DbtPage({ params }: { params: { runId: string } }) {
  const [state, { generation, artifacts }] = await Promise.all([
    getRun(params.runId),
    api<{ generation: { generation_id: string; generation_version: number; generation_status: string;
                        files_generated: number; stage_path: string; model_version: string } | null;
          artifacts: { artifact_id: string; artifact_type: string; file_path: string; content: string }[];
        }>(`/api/runs/${params.runId}/dbt`),
  ]);
  const canGenerate = ["SODA_APPROVED", "DBT_PENDING"].includes(state.current_state);
  return (
    <StageGate state={state} stage="DBT">
      <Card>
        <CardHeader>
          <CardTitle>dbt generation</CardTitle>
          <CardDescription>
            A compile-only project is written from the approved STTM and staged. Phase 1 does not run models against
            production and does not open a pull request.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {canGenerate && (
            <StageAction
              label="Generate dbt project"
              pendingLabel="Writing dbt files…"
              action={generateDbt.bind(null, params.runId)}
            />
          )}
          {generation && (
            <p className="mt-3 text-sm">
              Version {generation.generation_version}{" "}
              <Badge variant="outline">{generation.generation_status}</Badge>
              {" · "}{generation.files_generated} files · {generation.stage_path}
            </p>
          )}
        </CardContent>
      </Card>
      {artifacts.map((a) => (
        <Card key={a.artifact_id}>
          <CardHeader>
            <CardTitle className="font-mono text-sm">{a.file_path}</CardTitle>
            <CardDescription>{a.artifact_type}</CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="max-h-96 overflow-auto rounded-md bg-muted p-3 text-xs">{a.content}</pre>
          </CardContent>
        </Card>
      ))}
    </StageGate>
  );
}
