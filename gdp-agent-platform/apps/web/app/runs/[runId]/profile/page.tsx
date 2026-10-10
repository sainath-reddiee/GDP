import { can } from "@/lib/types";
import { api, getRun, whoami } from "@/lib/api";
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
  const [state, me, { columns, tables = [], source = "registry" }, graph] = await Promise.all([
    getRun(params.runId),
    whoami(),
    api<{ columns: ProfileColumn[]; tables?: ProfileCacheTable[]; source?: "registry" | "cache" }>(`/api/runs/${params.runId}/profile`),
    api<ModelGraph>(`/api/runs/${params.runId}/model-graph`).catch(() => null),
  ]);
  const canRun = can(me, "RUN.OPERATE") && !state.is_archived && ["LANDING_COMPLETE", "PROFILING_PENDING"].includes(state.current_state);
  const canRefresh = can(me, "PROFILE.RUN") && !state.is_archived && !["PROFILING_RUNNING", "LANDING_RUNNING"].includes(state.current_state);
  const cachedCount = tables.filter((t) => t.status === "CACHED").length;
  return (
    <StageGate state={state} stage="PROFILING">
      <AiSuggestions runId={params.runId} stage="PROFILING" canAct={!state.is_archived} />
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3 space-y-0">
          <div className="min-w-0 space-y-1">
            <CardTitle>Profiles</CardTitle>
            <CardDescription>
              Computed in SQL, PII masked, stored per table and reused by every run.
              {source === "cache" ? " Showing the stored profiles; this run has none of its own." : ""}
              {canRun && tables.length > 0 ? ` ${cachedCount} of ${tables.length} tables already have a stored profile.` : ""}
            </CardDescription>
          </div>
          {canRun && (
            <div className="flex flex-wrap items-start gap-2">
              <StageAction
                size="sm"
                label={state.current_state === "PROFILING_PENDING" ? "Retry profiling" : "Start profiling"}
                pendingLabel="Profiling tables…"
                action={runProfiling.bind(null, params.runId)}
              />
              {cachedCount > 0 && (
                <StageAction
                  size="sm"
                  variant="outline"
                  label="Profile all from scratch"
                  pendingLabel="Profiling every table…"
                  action={runProfilingFresh.bind(null, params.runId)}
                />
              )}
            </div>
          )}
        </CardHeader>
        {(tables.length > 0 || columns.length > 0) && (
          <CardContent>
            <ProfileWorkspace runId={params.runId} tables={tables} columns={columns} canRefresh={canRefresh} />
          </CardContent>
        )}
      </Card>
      {graph && (graph.suggestions.length > 0 || graph.targets.length > 0) && (
        <Card>
          <CardHeader>
            <CardTitle>Suggested models</CardTitle>
            <CardDescription>
              {graph?.intent?.path === "profile_suggest" ? "This source was marked new. " : ""}Existing targets scored by column-name overlap. A proposed name is included when the
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
    </StageGate>
  );
}
