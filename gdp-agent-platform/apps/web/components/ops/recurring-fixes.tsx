import Link from "next/link";
import { Repeat } from "lucide-react";
import { cn } from "@/lib/utils";
import { PermanentFixStatusPill } from "@/components/ops/status-pill";

/** A known fix that keeps firing (GET /api/ops/resolve/summary `recurring`, /api/ops/reliability `recurring_fixes`). */
export type RecurringFix = {
  issue_id: string; title: string | null; status: string | null; proposed_by_agent: boolean; runs: number; ok: number;
  success_rate: number | null; dags: number; dag_ids: string[]; last_run_at: string | null;
  /** the newest permanent fix proposal (V066); absent on an older API */
  permanent_fix?: { fix_id: string; status: string; pr_url: string | null } | null;
};

const rate = (v: number | null) => (v === null ? "-" : `${Math.round(v * 100)}%`);

/** Known fixes that ran more than once lately. A fix that fires every week points at a cause worth repairing for good. */
export function RecurringFixes({ items, days = 30, className }: { items: RecurringFix[]; days?: number; className?: string }) {
  return (
    <section className={cn("surface overflow-hidden", className)} data-testid="recurring-fixes">
      <header className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
        <Repeat className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-sm font-semibold">Recurring known issues</h2>
        <span className="text-xs text-muted-foreground">known fixes that ran more than once in {days} days: worth a permanent fix</span>
      </header>
      {items.length ? (
        <ul className="divide-y text-xs">
          {items.map((r) => (
            <li key={r.issue_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2">
              <Link href={`/os/run/ops/fixes/${encodeURIComponent(r.issue_id)}`} className="min-w-0 flex-1 truncate font-medium text-primary hover:underline"
                    title={r.title ?? r.issue_id}>{r.title ?? r.issue_id}</Link>
              {r.proposed_by_agent && <span className="rounded-full bg-agent/10 px-2 py-0.5 text-[10px] font-semibold text-agent">agent</span>}
              {r.permanent_fix
                ? <Link href={`/os/run/ops/permanent-fixes/${encodeURIComponent(r.permanent_fix.fix_id)}`} className="shrink-0" title="Permanent fix"><PermanentFixStatusPill status={r.permanent_fix.status} /></Link>
                : r.status === "PUBLISHED" && <Link href={`/os/run/ops/fixes/${encodeURIComponent(r.issue_id)}`} className="shrink-0 text-[11px] font-medium text-primary hover:underline">Draft permanent fix</Link>}
              <span className="shrink-0 tabular-nums text-amber-700">{r.runs} runs</span>
              <span className="shrink-0 text-muted-foreground" title={r.dag_ids.join(", ")}>{r.dags} DAG{r.dags === 1 ? "" : "s"}</span>
              <span className={cn("w-10 shrink-0 text-right tabular-nums", r.success_rate !== null && r.success_rate < 1 ? "text-rose-700" : "text-emerald-700")}
                    title="Runs that repaired the failure">{rate(r.success_rate)}</span>
              <span className="shrink-0 tabular-nums text-muted-foreground">{r.last_run_at ? String(r.last_run_at).slice(0, 10) : "-"}</span>
            </li>
          ))}
        </ul>
      ) : <p className="px-4 py-6 text-center text-sm text-muted-foreground">No known fix ran more than once in this period.</p>}
    </section>
  );
}
