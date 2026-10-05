import Link from "next/link";
import { Suspense, type ReactNode } from "react";
import { getRun } from "@/lib/api";
import { StageRail } from "@/components/stage-rail";
import { Badge } from "@/components/ui/badge";
import RunLoading from "./loading";

async function RunShell({ runId, children }: { runId: string; children: ReactNode }) {
  const state = await getRun(runId);
  const tone = state.status === "FAILED" ? "destructive" : state.status === "COMPLETED" ? "success" : "secondary";
  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center gap-2.5">
        <h2>{state.run.run_name}</h2>
        <Badge variant={tone}>{state.status}</Badge>
        <Badge variant="outline">{state.current_state}</Badge>
        <span className="text-sm text-muted-foreground">
          {state.run.target_model ?? "No target"} · {state.run.environment}
        </span>
        <Link href={`/runs/${runId}/domain`} className="text-sm font-medium text-primary hover:underline">
          Knowledge pack{state.run.domain_name ? `: ${state.run.domain_name}` : ""}
        </Link>
        <Link href={`/runs/${runId}/audit`} className="ml-auto text-sm font-medium text-primary hover:underline">
          Audit trail
        </Link>
      </div>
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
