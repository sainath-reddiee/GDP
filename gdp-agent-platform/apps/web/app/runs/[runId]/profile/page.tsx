import { api, getRun } from "@/lib/api";
import { AiSuggestions } from "@/components/ai-suggestions";
import { StageGate } from "@/components/stage-gate";
import { StageAction } from "@/components/stage-action";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { ModelEr } from "@/components/model-er";
import type { ModelGraph } from "@/app/onboarding/intent-types";
import type { ProfileCacheTable } from "@/lib/types";
import { runProfiling, runProfilingFresh } from "../pipeline-actions";
import { ProfileWorkspace, type ProfileColumn } from "./profile-workspace";

export default async function ProfilePage({ params }: { params: { runId: string } }) {
  const [state, { columns, tables = [], source = "registry" }, graph] = await Promise.all([
    getRun(params.runId),
    api<{ columns: ProfileColumn[]; tables?: ProfileCacheTable[]; source?: "registry" | "cache" }>(`/api/runs/${params.runId}/profile`),
    api<ModelGraph>(`/api/runs/${params.runId}/model-graph`).catch(() => null),
  ]);
  const canRun = !state.is_archived && ["LANDING_COMPLETE", "PROFILING_PENDING"].includes(state.current_state);
  const canRefresh = !state.is_archived && !["PROFILING_RUNNING", "LANDING_RUNNING"].includes(state.current_state);
  const cachedCount = tables.filter((t) => t.status === "CACHED").length;
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
          {canRun && tables.length > 0 && (
            <p className="mb-3 text-sm text-muted-foreground">
              {cachedCount} of {tables.length} landed table{tables.length === 1 ? "" : "s"} already have a stored
              profile and will be reused without scanning the data again.
            </p>
          )}
          {canRun && (
            <div className="flex flex-wrap items-start gap-3">
              <StageAction
                label={state.current_state === "PROFILING_PENDING" ? "Retry profiling" : "Start profiling"}
                pendingLabel="Profiling tables…"
                action={runProfiling.bind(null, params.runId)}
              />
              {cachedCount > 0 && (
                <StageAction
                  variant="outline"
                  label="Profile all from scratch"
                  pendingLabel="Profiling every table…"
                  action={runProfilingFresh.bind(null, params.runId)}
                />
              )}
            </div>
          )}
        </CardContent>
      </Card>
      {(tables.length > 0 || columns.length > 0) && (
        <Card>
          <CardHeader>
            <CardTitle>Profiles</CardTitle>
            <CardDescription>
              Stored per source table and shared by every run.{source === "cache" ? " This run has no profile rows of its own, so the stored profiles are shown." : ""}
              {" "}Pick a table for its quality grade, column statistics and suggested checks; re-profile when its data changed in a way the row count does not show.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ProfileWorkspace runId={params.runId} tables={tables} columns={columns} canRefresh={canRefresh} />
          </CardContent>
        </Card>
      )}
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
      <AiSuggestions runId={params.runId} stage="PROFILING" canAct={!state.is_archived} />
    </StageGate>
  );
}
