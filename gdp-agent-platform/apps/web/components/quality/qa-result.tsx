import { AlertTriangle, CheckCircle2, CircleSlash, Eye, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { QaOutcome, QaResult } from "@/lib/store-types";

const OUTCOME: Record<QaOutcome, { label: string; cls: string; icon: typeof CheckCircle2 }> = {
  PASS: { label: "pass", cls: "bg-emerald-50 text-emerald-700 ring-emerald-200", icon: CheckCircle2 },
  FAIL: { label: "fail", cls: "bg-rose-50 text-rose-700 ring-rose-200", icon: XCircle },
  REVIEW: { label: "review", cls: "bg-amber-50 text-amber-800 ring-amber-200", icon: Eye },
  NOT_RUN: { label: "not run", cls: "bg-slate-100 text-slate-600 ring-slate-200", icon: CircleSlash },
  ERROR: { label: "error", cls: "bg-rose-50 text-rose-700 ring-rose-200", icon: AlertTriangle },
};

export function QaOutcomePill({ outcome }: { outcome: QaOutcome }) {
  const o = OUTCOME[outcome] ?? OUTCOME.NOT_RUN;
  const Icon = o.icon;
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset", o.cls)}>
      <Icon className="h-3 w-3" /> {o.label}
    </span>
  );
}

/** One executed query: outcome, why, and up to the first rows it returned (masked columns stay masked). */
export function QaResultCard({ result }: { result: QaResult }) {
  const cols = result.columns ?? [];
  const sample = result.sample ?? [];
  return (
    <div className="rounded-xl border border-border/80 bg-card p-3">
      <div className="flex flex-wrap items-center gap-2">
        <QaOutcomePill outcome={result.outcome} />
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{result.title}</span>
        <span className="text-xs tabular-nums text-muted-foreground">
          {result.rows_returned != null && `${result.rows_returned} row${result.rows_returned === 1 ? "" : "s"} · `}
          {result.duration_ms != null && `${(result.duration_ms / 1000).toFixed(2)}s`}
        </span>
      </div>
      {result.detail && <p className="mt-1.5 text-xs text-muted-foreground">{result.detail}</p>}
      {result.expected && <p className="mt-1 text-xs text-muted-foreground">Passes when: {result.expected}</p>}
      {cols.length > 0 && sample.length > 0 && (
        <div className="mt-2 max-h-56 overflow-auto rounded-lg border border-border/70">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-muted">
              <tr>{cols.map((c) => <th key={c} className="px-2 py-1.5 text-left font-semibold">{c}</th>)}</tr>
            </thead>
            <tbody>
              {sample.map((row, i) => (
                <tr key={i} className="border-t border-border/60">
                  {cols.map((c) => <td key={c} className="whitespace-nowrap px-2 py-1 font-mono">{row[c] == null ? <span className="text-muted-foreground">null</span> : String(row[c])}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!!result.masked?.length && <p className="mt-1 text-[11px] text-muted-foreground">Masked: {result.masked.join(", ")}</p>}
    </div>
  );
}
