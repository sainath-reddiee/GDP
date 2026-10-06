import Link from "next/link";
import { api } from "@/lib/api";
import type { RunSummary, SourcesOverview } from "@/lib/types";
import { RunTable } from "@/components/run-table";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";

export default async function Dashboard() {
  const [{ runs }, sources] = await Promise.all([
    api<{ runs: RunSummary[] }>("/api/runs"),
    api<SourcesOverview>("/api/sources/overview").catch(() => null),
  ]);
  const count = (pred: (r: RunSummary) => boolean) => runs.filter(pred).length;
  const stats = [
    ["Open runs", count((r) => !["COMPLETED", "CANCELLED"].includes(r.status))],
    ["Awaiting review", count((r) => r.status === "AWAITING_REVIEW")],
    ["Failed", count((r) => r.status === "FAILED")],
    ["Completed", count((r) => r.status === "COMPLETED")],
  ] as const;
  return (
    <div className="space-y-6">
      <div className="flex items-end">
        <div>
          <h2>Dashboard</h2>
          <p className="mt-1 text-sm text-muted-foreground">Open onboarding runs and the gates waiting on you.</p>
        </div>
        <Link href="/sources" className={buttonVariants({ className: "ml-auto" })}>Profile & model a source</Link>
      </div>
      <div className="grid grid-cols-4 gap-4">
        {stats.map(([label, value]) => (
          <Card key={label}>
            <CardHeader className="pb-1"><CardDescription>{label}</CardDescription></CardHeader>
            <CardContent className="text-2xl font-semibold">{value}</CardContent>
          </Card>
        ))}
      </div>
      {sources && (
        <Link href="/sources" className="flex flex-wrap items-center gap-4 rounded-xl border bg-card px-5 py-4 hover:border-primary/50">
          <div>
            <p className="text-sm font-semibold">Profile store</p>
            <p className="text-xs text-muted-foreground">
              {sources.totals.staged} of {sources.totals.tables} tables across {sources.totals.sources} sources are staged
              and ready for modeling{sources.totals.profiling ? `, ${sources.totals.profiling} profiling now` : ""}.
            </p>
          </div>
          <div className="h-2 min-w-[160px] flex-1 rounded-full bg-muted">
            <div className="h-2 rounded-full bg-success"
                 style={{ width: `${sources.totals.tables ? Math.round((sources.totals.staged / sources.totals.tables) * 100) : 0}%` }} />
          </div>
          <span className="text-sm font-medium text-primary">Open Sources →</span>
        </Link>
      )}
      <RunTable runs={runs.slice(0, 10)} selectable={false} />
    </div>
  );
}
