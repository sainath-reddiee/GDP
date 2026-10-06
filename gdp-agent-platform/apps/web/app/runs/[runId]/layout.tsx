import Link from "next/link";
import { Suspense, type ReactNode } from "react";
import { getRun } from "@/lib/api";
import { StageRail } from "@/components/stage-rail";
import { ArchiveToggle } from "@/components/archive-toggle";
import { Badge } from "@/components/ui/badge";
import { displayDomain, isHiddenTarget } from "@/lib/catalog-display";
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
  return (
    <div>
      <p className="eyebrow mb-1">
        <Link href="/runs" className="hover:underline">Runs</Link>
        <span className="mx-1.5 text-muted-foreground">/</span>
        <span className="font-mono normal-case tracking-normal text-muted-foreground">{runId.slice(0, 8)}</span>
      </p>
      <div className="mb-5 flex flex-wrap items-center gap-2.5">
        <h1>{state.run.run_name}</h1>
        <Badge variant={tone}>{state.status}</Badge>
        <Badge variant="outline">{state.current_state}</Badge>
        <span className="text-sm text-muted-foreground">
          {targetLabel ? `${targetLabel} · ` : ""}{state.run.environment}
        </span>
        <Link href={`/runs/${runId}/domain`} className="text-sm font-medium text-primary hover:underline">
          Knowledge pack{packLabel ? `: ${packLabel}` : ""}
        </Link>
        <Link href={`/runs/${runId}/audit`} className="ml-auto text-sm font-medium text-primary hover:underline">
          Audit trail
        </Link>
        <ArchiveToggle runId={runId} archived={Boolean(state.is_archived)} />
      </div>
      {state.is_archived && (
        <p role="status" className="mb-4 rounded-lg border border-dashed px-4 py-2 text-sm text-muted-foreground">
          This run is archived{state.run.archived_by ? ` by ${state.run.archived_by}` : ""}. Its stages are frozen
          until it is restored.
        </p>
      )}
      <div className="space-y-5">
        <StageRail runId={runId} stages={state.stages} />
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
