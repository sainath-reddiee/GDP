"use client";

import { AlertTriangle, CheckCircle2, CircleDashed, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ModelEr } from "@/components/model-er";
import type { ModelGraph } from "../intent-types";
import { cn } from "@/lib/utils";

export type CheckItem = { label: string; ok: boolean; hint?: string };

export function ReviewStep({
  graph, loading, error, onRefresh, checks, warnings, runName, pathLabel,
}: {
  graph: ModelGraph | null;
  loading: boolean;
  error: string;
  onRefresh: () => void;
  checks: CheckItem[];
  warnings: string[];
  runName: string;
  pathLabel: string;
}) {
  const joins = graph?.joins || [];
  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-4">
        {[
          ["Source tables", graph?.sources.length ?? "—"],
          ["Inferred joins", graph ? joins.length : "—"],
          ["Target models", graph?.targets.length ?? "—"],
          ["Path", pathLabel],
        ].map(([label, value]) => (
          <div key={String(label)} className="rounded-xl border bg-card p-3">
            <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
            <p className="mt-0.5 truncate text-lg font-semibold">{value}</p>
          </div>
        ))}
      </div>

      {warnings.length > 0 && (
        <ul className="space-y-1.5">
          {warnings.map((w) => (
            <li key={w} className="flex items-start gap-2 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {w}
            </li>
          ))}
        </ul>
      )}

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <p className="text-sm font-semibold">Entity relationships and mapping</p>
          <Button type="button" variant="ghost" size="sm" className="ml-auto h-7" onClick={onRefresh} disabled={loading}>
            <RefreshCw className={cn("mr-1 h-3.5 w-3.5", loading && "animate-spin")} /> Refresh
          </Button>
        </div>
        {loading && !graph ? (
          <div className="flex h-64 items-center justify-center gap-2 rounded-lg border text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Reading columns and inferring joins…
          </div>
        ) : error ? (
          <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}</p>
        ) : graph ? (
          <ModelEr graph={graph} runName={runName} />
        ) : null}
        {joins.length > 0 && (
          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full text-xs">
              <thead className="bg-muted/40 text-left text-muted-foreground">
                <tr><th className="px-3 py-1.5">Left</th><th className="px-3 py-1.5">Right</th><th className="px-3 py-1.5">Keys</th><th className="px-3 py-1.5">Cardinality</th><th className="px-3 py-1.5">Confidence</th></tr>
              </thead>
              <tbody>
                {joins.map((j) => (
                  <tr key={`${j.left}-${j.right}`} className="border-t">
                    <td className="px-3 py-1.5 font-medium">{j.left}</td>
                    <td className="px-3 py-1.5 font-medium">{j.right}</td>
                    <td className="px-3 py-1.5 font-mono">{j.keys.join(", ")}</td>
                    <td className="px-3 py-1.5">{j.cardinality}</td>
                    <td className="px-3 py-1.5">{Math.round(j.confidence * 100)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="border-t px-3 py-1.5 text-[11px] text-muted-foreground">Joins are inferred from column names and types. Profiling confirms them with real key overlap.</p>
          </div>
        )}
      </section>

      <section>
        <p className="mb-2 text-sm font-semibold">Checklist</p>
        <ul className="grid gap-1.5 sm:grid-cols-2">
          {checks.map((c) => (
            <li key={c.label} className="flex items-start gap-2 text-sm">
              {c.ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 text-emerald-600" /> : <CircleDashed className="mt-0.5 h-4 w-4 text-muted-foreground" />}
              <span>{c.label}{!c.ok && c.hint && <span className="block text-xs text-muted-foreground">{c.hint}</span>}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
