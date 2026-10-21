import Link from "next/link";
import { Siren } from "lucide-react";
import { ago } from "@/lib/run-insights";
import { cn } from "@/lib/utils";

/** One check failing now, from /api/dq/failing (services/quality/alerts.py raises its table's incident). */
export type FailingCheck = {
  check_id: string; table: string; column: string | null; check_type: string | null; outcome: "FAIL" | "ERROR";
  measured: number | null; threshold: string | null; failed_rows: number | null; at: string | null;
  domain_id: string | null; domain_name: string | null; href: string;
  incident: { incident_id: string; severity: string | null; status: string | null; assignee: string | null } | null;
};
export type FailingChecks = { checks: FailingCheck[]; total: number; tables: number; incidents: number; deployed?: boolean };

/** A domain's daily data quality digest, from /api/dq/digests. */
export type DqDigest = {
  domain_id: string; domain_name?: string; date: string; pass_rate: number | null; pass_rate_week_ago: number | null;
  trend: number | null; failing: number; new_failing: number; still_failing: number; recovered: number;
  open_incidents: number; checks: number; tables: number; stale_tables: string[];
};

const num = (v: number | null) => (v == null ? "n/a" : Number.isInteger(v) ? String(v) : v.toPrecision(4).replace(/\.?0+$/, ""));

/** Failing checks as rows: table and check, measured value against the threshold, and the table's incident. */
export function FailingCheckList({ checks, className }: { checks: FailingCheck[]; className?: string }) {
  return (
    <ul className={cn("divide-y divide-border/60", className)} data-testid="failing-checks">
      {checks.map((c) => (
        <li key={c.check_id} className="flex items-center gap-3 py-2">
          <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-semibold",
            c.outcome === "FAIL" ? "bg-rose-500/10 text-rose-700 dark:text-rose-400" : "bg-amber-500/10 text-amber-800 dark:text-amber-400")}>
            {c.outcome}
          </span>
          <Link href={c.href} className="min-w-0 flex-1">
            <p className="truncate text-sm hover:text-primary" title={c.table}>
              <span className="font-medium">{c.table.split(".").pop()}</span>
              <span className="text-muted-foreground">{c.column ? ` · ${c.column}` : ""} · {c.check_type ?? "check"}</span>
            </p>
            <p className="truncate text-xs text-muted-foreground">
              measured {num(c.measured)} vs {c.threshold ?? "n/a"}{c.failed_rows ? ` · ${c.failed_rows} failing rows` : ""}
              {c.domain_name ? ` · ${c.domain_name}` : ""}{c.at ? ` · ${ago(c.at)}` : ""}
            </p>
          </Link>
          {c.incident && (
            <Link href={`/incidents/${encodeURIComponent(c.incident.incident_id)}`} aria-label="Open the incident"
                  className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-rose-700 hover:bg-rose-500/10 dark:text-rose-400">
              <Siren className="h-3.5 w-3.5" />{c.incident.severity}{c.incident.assignee ? ` · ${c.incident.assignee.split("@")[0]}` : ""}
            </Link>
          )}
        </li>
      ))}
    </ul>
  );
}

/** One line for a digest: pass rate, its trend against last week, and what is newly failing. */
export function digestLine(d: DqDigest) {
  const rate = d.pass_rate == null ? "no results yet" : `${d.pass_rate}% passing`;
  const trend = d.trend == null ? "" : ` (${d.trend >= 0 ? "+" : ""}${d.trend} pts vs last week)`;
  return `${rate}${trend} · ${d.new_failing} newly failing · ${d.still_failing} still failing${d.recovered ? ` · ${d.recovered} recovered` : ""}`;
}
