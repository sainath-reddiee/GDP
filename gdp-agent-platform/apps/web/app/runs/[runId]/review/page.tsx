import { api, getRun } from "@/lib/api";
import { StageGate } from "@/components/stage-gate";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { RunConsole } from "../console";

export default async function ReviewPage({ params }: { params: { runId: string } }) {
  const [state, dbt, validation] = await Promise.all([
    getRun(params.runId),
    api<{ generation: { generation_status: string; files_generated: number } | null;
          artifacts: { artifact_id: string; file_path: string; content: string; artifact_type: string }[];
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
            Approve the generated project to complete Phase 1. Approval records the reviewer and does not deploy,
            merge a PR, or enable a pipeline.
          </CardDescription>
        </CardHeader>
        <CardContent className="text-sm">
          {dbt.generation
            ? `${dbt.generation.files_generated} files · ${dbt.generation.generation_status}`
            : "No generation on this run yet."}
          <ul className="mt-2 list-disc pl-5 text-muted-foreground">
            {validation.runs.filter((r) => r.validation_type !== "SUMMARY").map((r) => (
              <li key={r.validation_id}>{r.validation_type}: {r.status} ({r.error_count} errors)</li>
            ))}
          </ul>
        </CardContent>
      </Card>
      <RunConsole state={state} />
      {dbt.artifacts.map((a) => (
        <Card key={a.artifact_id}>
          <CardHeader><CardTitle className="font-mono text-sm">{a.file_path}</CardTitle></CardHeader>
          <CardContent><pre className="max-h-80 overflow-auto rounded-md bg-muted p-3 text-xs">{a.content}</pre></CardContent>
        </Card>
      ))}
    </StageGate>
  );
}
