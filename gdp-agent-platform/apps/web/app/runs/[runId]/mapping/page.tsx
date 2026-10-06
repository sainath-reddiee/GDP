import { api, getRun } from "@/lib/api";
import type { MappingOverview } from "@/lib/types";
import { StageGate } from "@/components/stage-gate";
import { StageAction } from "@/components/stage-action";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { generateMapping } from "../pipeline-actions";
import { MappingBoard } from "./mapping-board";
import { MappingGate } from "./mapping-gate";

export default async function MappingPage({ params }: { params: { runId: string } }) {
  const [state, data] = await Promise.all([
    getRun(params.runId),
    api<MappingOverview>(`/api/runs/${params.runId}/mapping`),
  ]);
  const canGenerate = ["DOMAIN_IDENTIFIED", "MAPPING_PENDING"].includes(state.current_state);
  return (
    <StageGate state={state} stage="MAPPING">
      {canGenerate && (
        <Card>
          <CardHeader>
            <CardTitle>Hybrid mapping</CardTitle>
            <CardDescription>
              Seven scored signals (semantic, name, type, statistics, domain, context, history) plus Cortex Search and an
              AI adjudicator rank target candidates for every source column.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <StageAction
              label={state.current_state === "MAPPING_PENDING" ? "Retry mapping" : "Generate mapping candidates"}
              pendingLabel="Scoring columns… this can take a few minutes"
              action={generateMapping.bind(null, params.runId)}
            />
          </CardContent>
        </Card>
      )}
      {state.current_state === "MAPPING_REVIEW" && data.candidates.length > 0 && (
        <MappingGate
          runId={params.runId}
          complete={data.status.complete}
          decided={data.status.decided}
          total={data.status.source_columns}
          missing={data.status.missing_required_targets}
          undecided={data.status.undecided}
        />
      )}
      {data.candidates.length > 0 && <MappingBoard runId={params.runId} data={data} />}
    </StageGate>
  );
}
