import Link from "next/link";
import { cn } from "@/lib/utils";

function tone(kind: "done" | "active" | "ready" | "idle") {
  if (kind === "done") return "border-success/40 bg-success/10";
  if (kind === "active") return "border-primary/50 bg-primary/10 ring-2 ring-primary/20";
  if (kind === "ready") return "border-dashed border-primary/40 bg-card";
  return "border-border bg-card";
}

function kind(unlocked: boolean, active: boolean, done: boolean): "done" | "active" | "ready" | "idle" {
  if (done) return "done";
  if (active) return "active";
  if (unlocked) return "ready";
  return "idle";
}

export function SttmFork({
  runId, currentState,
}: {
  runId: string;
  currentState: string;
}) {
  const state = currentState.toUpperCase();
  const unlocked = [
    "STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED",
    "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_RUNNING",
    "VALIDATION_PASSED", "VALIDATION_FAILED", "DBT_REVIEW", "DBT_APPROVED", "COMPLETED",
  ].includes(state);
  const dqActive = state === "SODA_PENDING" || state === "SODA_REVIEW";
  const dqDone = ["SODA_APPROVED", "VALIDATION_PENDING", "VALIDATION_RUNNING",
    "VALIDATION_PASSED", "VALIDATION_FAILED", "DBT_REVIEW", "DBT_APPROVED", "COMPLETED"].includes(state);
  const dbtActive = state.startsWith("DBT") || state.startsWith("VALIDATION");
  const dbtDone = ["DBT_REVIEW", "DBT_APPROVED", "COMPLETED"].includes(state);

  return (
    <figure className="rounded-xl border bg-card p-5 shadow-sm">
      <figcaption className="mb-4 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Lineage from the STTM — two parallel flows
      </figcaption>
      <div className="flex flex-col items-center">
        <Link
          href={`/runs/${runId}/sttm`}
          className={cn("w-full max-w-xs rounded-lg border px-4 py-3 text-center", tone(unlocked ? "done" : "active"))}
        >
          <p className="text-sm font-semibold">STTM</p>
          <p className="text-xs text-muted-foreground">Approved source-to-target contract</p>
        </Link>

        <svg className="h-10 w-full max-w-xl text-border" viewBox="0 0 400 40" aria-hidden>
          <path d="M200 0 V12" fill="none" stroke="currentColor" strokeWidth="2" />
          <path d="M100 40 C100 12, 200 12, 200 12 C200 12, 300 12, 300 40" fill="none" stroke="currentColor" strokeWidth="2" />
        </svg>

        <div className="grid w-full max-w-2xl grid-cols-2 gap-4">
          <Link
            href={`/runs/${runId}/soda`}
            className={cn("rounded-lg border px-3 py-3 text-center", tone(kind(unlocked, dqActive, dqDone)))}
          >
            <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Flow A</p>
            <p className="text-sm font-semibold">Data Quality</p>
            <p className="mt-1 text-xs text-muted-foreground">STTM columns → SodaCL checks → pack review</p>
          </Link>
          <Link
            href={`/runs/${runId}/dbt`}
            className={cn("rounded-lg border px-3 py-3 text-center", tone(kind(unlocked, dbtActive, dbtDone)))}
          >
            <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Flow B</p>
            <p className="text-sm font-semibold">dbt</p>
            <p className="mt-1 text-xs text-muted-foreground">STTM + domain skill → models → branch / project</p>
          </Link>
        </div>
        <p className="mt-3 text-center text-xs text-muted-foreground">
          Independent tracks. Neither waits on the other.
        </p>
      </div>
    </figure>
  );
}
