import Link from "next/link";
import { Suspense, type ReactNode } from "react";
import { getRun } from "@/lib/api";
import { StageRail } from "@/components/stage-rail";
import { RunMenu } from "@/components/run-menu";
import { TagEditor } from "@/components/tag-editor";
import { RunSkills } from "@/components/run-skills";
import { Autopilot } from "@/components/autopilot";
import { Badge } from "@/components/ui/badge";
import { displayDomain, isHiddenTarget } from "@/lib/catalog-display";
import { PIPELINES, pipelineOf } from "@/lib/stages";
import { awaitsApproval, stateLabel, statusLabel } from "@/lib/run-labels";
import { cn } from "@/lib/utils";
import RunLoading from "./loading";

function runTargetLabel(model: string | null | undefined) {
  if (!model) return null;
  const parts = model.split(".");
  const row = {
    fqn: model,
    target_database: parts[0],
    target_schema: parts[1],
    target_table: parts[2] || parts[parts.length - 1],
  };
  if (isHiddenTarget(row)) return null;
  return parts.length >= 3 ? parts[2] : model;
}

async function RunShell({ runId, children }: { runId: string; children: ReactNode }) {
  const state = await getRun(runId);
  const tone = state.status === "FAILED" ? "destructive" : state.status === "COMPLETED" ? "success" : "secondary";
  const targetLabel = runTargetLabel(state.run.target_model);
  const packLabel = displayDomain(state.run.domain_name);
  const pipeline = pipelineOf(state.run.pipeline);
  return (
    <div>
      <p className="eyebrow mb-1">
        <Link href={PIPELINES[pipeline].href} className="hover:underline">{PIPELINES[pipeline].name}</Link>
        <span className="mx-1.5 text-muted-foreground">/</span>
        <span className="font-mono normal-case tracking-normal text-muted-foreground">{runId.slice(0, 8)}</span>
      </p>
      <div className="mb-5 flex flex-wrap items-center gap-2.5">
        <h1>{state.run.run_name}</h1>
        <span className={cn("rounded-full border px-2 py-0.5 text-xs font-semibold", PIPELINES[pipeline].accent.border,
          PIPELINES[pipeline].accent.bg, PIPELINES[pipeline].accent.text)} title={PIPELINES[pipeline].tagline}>{PIPELINES[pipeline].run}</span>
        <Badge variant={tone} title={state.status}>{statusLabel(state.status)}</Badge>
        {awaitsApproval(state.current_state) ? (
          <span title={state.current_state} className="inline-flex items-center gap-1.5 rounded-full bg-human/10 px-2.5 py-0.5 text-xs font-semibold text-human ring-1 ring-inset ring-human/30">
            <span className="h-1.5 w-1.5 animate-glow rounded-full bg-human" />{stateLabel(state.current_state)}
          </span>
        ) : <Badge variant="outline" title={state.current_state}>{stateLabel(state.current_state)}</Badge>}
        <span className="text-sm text-muted-foreground">
          {targetLabel ? `${targetLabel} · ` : ""}{state.run.environment}
        </span>
        <Link href={`/runs/${runId}/domain`} className="text-sm font-medium text-primary hover:underline">
          Knowledge pack{packLabel ? `: ${packLabel}` : ""}
        </Link>
        <TagEditor entityType="RUN" entityKey={runId} canEdit={!state.is_archived} />
        <RunSkills runId={runId} disabled={Boolean(state.is_archived)} />
        {/* stage pages put their "Ask AI to review" button here (components/suggestions-panel.tsx) */}
        <span id="run-ai-slot" className="ml-auto flex items-center" />
        <Autopilot runId={runId} disabled={Boolean(state.is_archived)} />
        <RunMenu runId={runId} archived={Boolean(state.is_archived)}
                 canCancel={!state.is_archived && state.allowed_transitions.some((t) => t.to_state === "CANCELLED")} />
      </div>
      {state.is_archived && (
        <p role="status" className="mb-4 rounded-lg border border-dashed px-4 py-2 text-sm text-muted-foreground">
          This run is archived{state.run.archived_by ? ` by ${state.run.archived_by}` : ""}. Its stages are frozen
          until it is restored.
        </p>
      )}
      <div className="space-y-5">
        <StageRail runId={runId} stages={state.stages} pipeline={pipeline} />
        <section className="min-w-0 space-y-5">{children}</section>
      </div>
    </div>
  );
}

export default function RunLayout({ params, children }: { params: { runId: string }; children: ReactNode }) {
  return (
    <Suspense fallback={<RunLoading />}>
      <RunShell runId={params.runId}>{children}</RunShell>
    </Suspense>
  );
}
