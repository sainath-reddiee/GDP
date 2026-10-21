"use client";

import { CheckCircle2, ChevronRight, XCircle } from "lucide-react";
import type { DryRun } from "@/lib/store-types";
import { cn } from "@/lib/utils";

/** A pack's queries as they would run in one environment: resolved SQL, the read-only guard and EXPLAIN. */
export function DryRunResults({ result, className }: { result: DryRun; className?: string }) {
  const failed = result.queries.filter((q) => !q.ok).length;
  return (
    <div className={cn("space-y-2", className)} data-testid="dry-run">
      <p className="text-sm">
        <b>{result.name} v{result.version}</b>{" "}
        <span className="text-muted-foreground">
          in {result.environment ?? "this environment"}{result.current_environment ? " (this deployment)" : ""}:{" "}
          {failed ? `${failed} of ${result.queries.length} queries would fail` : `all ${result.queries.length} queries compile`}
        </span>
      </p>
      <ul className="divide-y rounded-xl border">
        {result.queries.map((q) => (
          <li key={q.query_name} className="px-3 py-2">
            <details>
              <summary className="flex cursor-pointer list-none items-center gap-2 text-sm">
                <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                {q.ok ? <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" aria-label="ok" />
                      : <XCircle className="h-4 w-4 shrink-0 text-destructive" aria-label="fails" />}
                <span className="font-mono text-xs">{q.query_name}</span>
                {q.rewritten && <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">resolved</span>}
                <span className="ml-auto truncate text-xs text-muted-foreground">
                  {q.problems[0] ?? (q.explain && !q.explain.ok ? q.explain.error : "guard and EXPLAIN passed")}
                </span>
              </summary>
              <div className="mt-2 space-y-2 pl-6">
                {q.problems.length > 1 && <ul className="list-disc pl-4 text-xs text-destructive">{q.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
                {q.explain && !q.explain.ok && <p className="text-xs text-destructive">EXPLAIN: {q.explain.error}</p>}
                <pre className="max-h-56 overflow-auto rounded-lg bg-muted/60 p-2 font-mono text-[11px]">{q.sql}</pre>
                {q.rewritten && (
                  <pre className="max-h-40 overflow-auto rounded-lg border border-dashed p-2 font-mono text-[11px] text-muted-foreground">{q.written}</pre>
                )}
              </div>
            </details>
          </li>
        ))}
      </ul>
    </div>
  );
}
