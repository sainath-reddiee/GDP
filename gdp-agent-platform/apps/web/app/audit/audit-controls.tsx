"use client";

import { useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Row = Record<string, unknown>;

function csv(rows: Row[]): string {
  if (!rows.length) return "";
  const cols = Object.keys(rows[0]);
  const cell = (v: unknown) => {
    const t = v == null ? "" : String(v);
    return /[",\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t;
  };
  return [cols.join(","), ...rows.map((r) => cols.map((c) => cell(r[c])).join(","))].join("\n");
}

function useUrl() {
  const router = useRouter();
  const path = usePathname();
  const params = useSearchParams();
  const go = (changes: Record<string, string | null>, keepOffset = false) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(changes)) { if (v) next.set(k, v); else next.delete(k); }
    if (!keepOffset) next.delete("offset");
    router.push(`${path}?${next.toString()}`);
  };
  return { params, go };
}

export function AuditTabs({ tab }: { tab: "events" | "cost" }) {
  const { go } = useUrl();
  return (
    <div className="flex gap-1 rounded-lg border p-1 text-sm">
      {([["events", "Workflow events"], ["cost", "AI cost"]] as const).map(([v, l]) => (
        <button key={v} type="button" aria-pressed={tab === v} onClick={() => go({ tab: v === "events" ? null : v, offset: null })}
                className={cn("rounded-md px-3 py-1.5", tab === v ? "bg-primary text-primary-foreground" : "hover:bg-muted")}>{l}</button>
      ))}
    </div>
  );
}

const field = "h-8 rounded-md border bg-card px-2 text-xs";

/** Filters, paging and CSV export for the workflow event list. */
export function EventFilters({ rows, total, offset, limit }: { rows: Row[]; total: number; offset: number; limit: number }) {
  const { params, go } = useUrl();
  const [q, setQ] = useState(params.get("q") ?? "");
  const download = () => {
    const blob = new Blob([csv(rows)], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "audit-events.csv";
    a.click();
    { const done = a.href; setTimeout(() => URL.revokeObjectURL(done), 1000); }
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      <form onSubmit={(e) => { e.preventDefault(); go({ q: q.trim() || null }); }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search events"
               placeholder="Run, actor or reason" className={cn(field, "w-56")} />
      </form>
      <select aria-label="Actor" className={field} value={params.get("actor_type") ?? ""} onChange={(e) => go({ actor_type: e.target.value || null })}>
        <option value="">Anyone</option>
        <option value="HUMAN">People</option>
        <option value="SYSTEM">System</option>
        <option value="AGENT">Agent</option>
      </select>
      <label className="flex items-center gap-1 text-xs text-muted-foreground">From
        <input type="date" className={field} value={params.get("since") ?? ""} onChange={(e) => go({ since: e.target.value || null })} />
      </label>
      <label className="flex items-center gap-1 text-xs text-muted-foreground">To
        <input type="date" className={field} value={params.get("until") ?? ""} onChange={(e) => go({ until: e.target.value || null })} />
      </label>
      <Button size="sm" variant="outline" disabled={!rows.length} onClick={download}><Download className="h-3.5 w-3.5" /> CSV</Button>
      <div className="ml-auto flex items-center gap-1 text-xs text-muted-foreground">
        <span>{total === 0 ? "0" : `${offset + 1}–${Math.min(offset + limit, total)}`} of {total}</span>
        <Button size="sm" variant="ghost" disabled={offset === 0} onClick={() => go({ offset: String(Math.max(0, offset - limit)) }, true)}>Previous</Button>
        <Button size="sm" variant="ghost" disabled={offset + limit >= total} onClick={() => go({ offset: String(offset + limit) }, true)}>Next</Button>
      </div>
    </div>
  );
}

/** Grouping and date range for the cost view, plus CSV export. */
export function CostFilters({ rows }: { rows: Row[] }) {
  const { params, go } = useUrl();
  const download = () => {
    const blob = new Blob([csv(rows)], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `ai-cost-by-${params.get("group_by") ?? "stage"}.csv`;
    a.click();
    { const done = a.href; setTimeout(() => URL.revokeObjectURL(done), 1000); }
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select aria-label="Group by" className={field} value={params.get("group_by") ?? "stage"} onChange={(e) => go({ group_by: e.target.value === "stage" ? null : e.target.value })}>
        <option value="stage">By stage</option>
        <option value="model">By model</option>
        <option value="run">By run</option>
        <option value="day">By day</option>
      </select>
      <label className="flex items-center gap-1 text-xs text-muted-foreground">From
        <input type="date" className={field} value={params.get("since") ?? ""} onChange={(e) => go({ since: e.target.value || null })} />
      </label>
      <label className="flex items-center gap-1 text-xs text-muted-foreground">To
        <input type="date" className={field} value={params.get("until") ?? ""} onChange={(e) => go({ until: e.target.value || null })} />
      </label>
      <Button size="sm" variant="outline" disabled={!rows.length} onClick={download}><Download className="h-3.5 w-3.5" /> CSV</Button>
    </div>
  );
}
