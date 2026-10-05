import { api, getRun } from "@/lib/api";
import { StageGate } from "@/components/stage-gate";
import { StageAction } from "@/components/stage-action";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { ModelEr } from "@/components/model-er";
import type { ModelGraph } from "@/app/onboarding/intent-types";
import { runProfiling } from "../pipeline-actions";

export default async function ProfilePage({ params }: { params: { runId: string } }) {
  const [state, { columns }, graph] = await Promise.all([
    getRun(params.runId),
    api<{ columns: {
      profile_id: string; table_name: string; column_name: string; data_type: string; semantic_type: string;
      pii_classification: string; row_count: number; null_percentage: number; distinct_percentage: number;
      cardinality: string | null; potential_key_flag: boolean; generated_description: string | null;
    }[] }>(`/api/runs/${params.runId}/profile`),
    api<ModelGraph>(`/api/runs/${params.runId}/model-graph`).catch(() => null),
  ]);
  const canRun = ["LANDING_COMPLETE", "PROFILING_PENDING"].includes(state.current_state);
  return (
    <StageGate state={state} stage="PROFILING">
      <Card>
        <CardHeader>
          <CardTitle>Profiling</CardTitle>
          <CardDescription>
            Statistics are computed in SQL. The model writes a description per table and only names a semantic type
            when the deterministic rules cannot. PII samples are masked.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {graph?.intent?.path === "profile_suggest" && (
            <p className="mb-3 text-sm text-muted-foreground">
              This source was marked new. After profiling, suggested existing models and a proposed
              new table name appear below. Download the graph to keep the recommendation.
            </p>
          )}
          {canRun && (
            <StageAction
              label={state.current_state === "PROFILING_PENDING" ? "Retry profiling" : "Start profiling"}
              pendingLabel="Profiling tables…"
              action={runProfiling.bind(null, params.runId)}
            />
          )}
        </CardContent>
      </Card>
      {graph && (graph.suggestions.length > 0 || graph.targets.length > 0) && (
        <Card>
          <CardHeader>
            <CardTitle>Suggested models</CardTitle>
            <CardDescription>
              Existing targets scored by column-name overlap. A proposed name is included when the
              source looks net-new. Nothing is created until you register or map it.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {graph.suggestions.length > 0 && (
              <Table>
                <THead><TR><TH>Model</TH><TH>Kind</TH><TH>Score</TH><TH>Why</TH></TR></THead>
                <TBody>
                  {graph.suggestions.map((s) => (
                    <TR key={`${s.kind}-${s.fqn}`}>
                      <TD className="font-medium">{s.target_table}</TD>
                      <TD><Badge variant={s.kind === "existing" ? "outline" : "secondary"}>{s.kind}</Badge></TD>
                      <TD>{s.score.toFixed(2)}</TD>
                      <TD className="text-sm text-muted-foreground">{s.reason}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
            <ModelEr graph={graph} runName={state.run.run_name} />
          </CardContent>
        </Card>
      )}
      {columns.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Current profile</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <THead>
                <TR><TH>Table</TH><TH>Column</TH><TH>Type</TH><TH>Semantic</TH><TH>PII</TH><TH>Null %</TH><TH>Distinct %</TH><TH>Key</TH><TH>Description</TH></TR>
              </THead>
              <TBody>
                {columns.map((c) => (
                  <TR key={c.profile_id}>
                    <TD>{c.table_name}</TD>
                    <TD className="font-medium">{c.column_name}</TD>
                    <TD className="font-mono text-xs">{c.data_type}</TD>
                    <TD><Badge variant="outline">{c.semantic_type}</Badge></TD>
                    <TD>{c.pii_classification !== "NONE" ? <Badge variant="destructive">{c.pii_classification}</Badge> : "—"}</TD>
                    <TD>{c.null_percentage?.toFixed?.(1) ?? c.null_percentage}</TD>
                    <TD>{c.distinct_percentage?.toFixed?.(1) ?? c.distinct_percentage}</TD>
                    <TD>{c.potential_key_flag ? "yes" : ""}</TD>
                    <TD className="text-sm text-muted-foreground">{c.generated_description}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </StageGate>
  );
}
