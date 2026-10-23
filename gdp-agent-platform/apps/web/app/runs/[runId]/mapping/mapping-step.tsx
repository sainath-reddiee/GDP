import { can, canAct } from "@/lib/types";
import { api, getRun, whoami } from "@/lib/api";
import type { MappingOverview } from "@/lib/types";
import { StageGate } from "@/components/stage-gate";
import { StageAction } from "@/components/stage-action";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { generateMapping } from "../pipeline-actions";
import { MappingBoard } from "./mapping-board";
import { MappingGate } from "./mapping-gate";
import { TableRunsPanel } from "./table-runs";
import { GoldModelPanel } from "./gold-model";
import { QuestionsPanel } from "@/components/learning/questions-panel";
import { ModelDesign } from "./model-design";
import type { ModelPayload } from "./model-actions";

/** Mapping, step 1: the field mapping (target model design, scored candidates, the mapping gate). A Gold run designs
 *  the domain's gold model and picks the gold table it builds instead of a silver target design. */
export async function MappingStep({ runId }: { runId: string }) {
  const params = { runId };
  const [state, me, data, model] = await Promise.all([
    getRun(params.runId),
    whoami(),
    api<MappingOverview>(`/api/runs/${params.runId}/mapping`),
    api<ModelPayload>(`/api/runs/${params.runId}/model`).catch(() => null),
  ]);
  const gold = state.run.pipeline === "GOLD";
  const canApplyModel = canAct(me, "MODEL.APPROVE") && !state.is_archived
    && ["PROFILING_COMPLETE", "DOMAIN_IDENTIFIED", "MAPPING_PENDING", "MAPPING_REVIEW"].includes(state.current_state);
  const canGenerate = can(me, "RUN.OPERATE") && ["DOMAIN_IDENTIFIED", "MAPPING_PENDING"].includes(state.current_state)
    && (!gold || Boolean(state.run.target_model));
  return (
    <StageGate state={state} stage="MAPPING">
      {gold ? <GoldModelPanel runId={params.runId} />
        : model && <ModelDesign runId={params.runId} data={model} canApply={canApplyModel} state={state.current_state}
                                canEdit={canAct(me, "MODEL.EDIT")} canAI={can(me, "AI.USE") && can(me, "MODEL.EDIT")} />}
      {canGenerate && (
        <Card>
          <CardHeader>
            <CardTitle>Hybrid mapping</CardTitle>
            <CardDescription>
              Seven scored signals (semantic, name, type, statistics, domain, context, history) plus Cortex Search and an
              AI adjudicator rank target candidates for every source column.{gold && " For gold, the mastered keys, the data source and dimension keys come from the gold model's rules (resolved through the cross-reference)."}
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
      <QuestionsPanel runId={params.runId} title="The engine asks" />
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
      {(data.table_runs?.length ?? 0) > 0 && (
        <TableRunsPanel runId={params.runId} runs={data.table_runs!} canPublish={canAct(me, "DBT.EDIT")} />
      )}
      {data.candidates.length > 0 && <MappingBoard runId={params.runId} data={data} />}
    </StageGate>
  );
}
