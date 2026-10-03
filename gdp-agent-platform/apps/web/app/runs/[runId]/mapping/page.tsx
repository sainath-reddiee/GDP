import { api, getRun } from "@/lib/api";
import type { MappingOverview } from "@/lib/types";
import { StageGate } from "@/components/stage-gate";
import { StageAction } from "@/components/stage-action";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { generateMapping } from "../pipeline-actions";
import { MappingBoard } from "./mapping-board";

export default async function MappingPage({ params }: { params: { runId: string } }) {
  const [state, data] = await Promise.all([
    getRun(params.runId),
    api<MappingOverview>(`/api/runs/${params.runId}/mapping`),
  ]);
  const canGenerate = ["DOMAIN_IDENTIFIED", "MAPPING_PENDING"].includes(state.current_state);
  return (
    <StageGate state={state} stage="MAPPING">
      <Card>
        <CardHeader>
          <CardTitle>Hybrid mapping</CardTitle>
          <CardDescription>
            Seven scored components plus Cortex Search. The model only explains ambiguous columns; it does not change
            the ranking. Approve, modify, choose another target, or reject. Required targets must be covered before
            the mapping stage can be approved.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {canGenerate && (
            <StageAction
              label={state.current_state === "MAPPING_PENDING" ? "Retry mapping" : "Generate mapping candidates"}
              pendingLabel="Scoring columns… this can take a few minutes"
              action={generateMapping.bind(null, params.runId)}
            />
          )}
        </CardContent>
      </Card>
      {data.candidates.length > 0 && <MappingBoard runId={params.runId} data={data} />}
    </StageGate>
  );
}
