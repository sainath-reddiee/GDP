import Link from "next/link";
import { api } from "@/lib/api";
import type { RunSummary } from "@/lib/types";
import { RunTable } from "@/components/run-table";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";

export default async function Dashboard() {
  const { runs } = await api<{ runs: RunSummary[] }>("/api/runs");
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
        <Link href="/onboarding" className={buttonVariants({ className: "ml-auto" })}>Start a source onboarding</Link>
      </div>
      <div className="grid grid-cols-4 gap-4">
        {stats.map(([label, value]) => (
          <Card key={label}>
            <CardHeader className="pb-1"><CardDescription>{label}</CardDescription></CardHeader>
            <CardContent className="text-2xl font-semibold">{value}</CardContent>
          </Card>
        ))}
      </div>
      <RunTable runs={runs.slice(0, 10)} />
    </div>
  );
}
