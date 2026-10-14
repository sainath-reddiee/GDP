import Link from "next/link";
import { can } from "@/lib/types";
import { api, getRun, whoami } from "@/lib/api";
import { AiSuggestions } from "@/components/ai-suggestions";
import { StageGate } from "@/components/stage-gate";
import { StageAction } from "@/components/stage-action";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { ProfileCacheTable } from "@/lib/types";
import { runProfiling, runProfilingFresh } from "../pipeline-actions";
import { ProfileWorkspace, type ProfileColumn } from "./profile-workspace";

export default async function ProfilePage({ params }: { params: { runId: string } }) {
  const [state, me, { columns, tables = [], source = "registry" }, graph] = await Promise.all([
    getRun(params.runId),
    whoami(),
    api<{ columns: ProfileColumn[]; tables?: ProfileCacheTable[]; source?: "registry" | "cache" }>(`/api/runs/${params.runId}/profile`),
    api<{ suggestions: { target_table: string }[] }>(`/api/runs/${params.runId}/model-graph`).catch(() => null),
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
      {cachedCount > 0 && (
        <Link href={`/runs/${params.runId}/domain`} className="flex items-center gap-2 rounded-xl border bg-card px-4 py-3 text-sm hover:border-primary/40">
          <span className="font-medium">Next: choose the target model</span>
          <span className="text-muted-foreground">
            {graph?.suggestions.length ? `${graph.suggestions.length} model${graph.suggestions.length === 1 ? "" : "s"} suggested from this profile (${graph.suggestions.slice(0, 2).map((x) => x.target_table).join(", ")})` : "the knowledge pack and the models it maps to"}
          </span>
          <span className="ml-auto text-primary">Open →</span>
        </Link>
      )}
    </StageGate>
  );
}
