"use client";

import { useState } from "react";
import { AlertTriangle, Check, CheckCircle2, CircleDashed, Copy, Eye, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { QaOutcome, QaResult, QaRunRow } from "@/app/runs/[runId]/qa/qa-actions";

/** Shared QA presentation: the run QA workbench and the QA workspace render tests and results the same way. */
export const CATEGORY_META: Record<string, { label: string; hint: string }> = {
  RECONCILIATION: { label: "Reconciliation", hint: "Row and key counts, source vs target" },
  GRAIN: { label: "Grain & keys", hint: "Duplicate and missing business keys" },
  COMPLETENESS: { label: "Completeness", hint: "Required columns populated" },
  SOURCE_TO_TARGET: { label: "Source to target", hint: "Direct columns moved unchanged" },
  TRANSFORMATION: { label: "Transformations", hint: "STTM rules applied correctly" },
  LOOKUP: { label: "Lookups & references", hint: "Resolved keys and hub orphans" },
  VALUES: { label: "Values", hint: "Accepted values and constants" },
  JOINS: { label: "Joins", hint: "No unexpected fan-out" },
  BUSINESS_RULE: { label: "Business rules", hint: "Rules from domain knowledge" },
  EDGE_CASE: { label: "Edge cases", hint: "Late, duplicate and boundary data" },
  CUSTOM: { label: "Custom", hint: "Saved tester and AI queries" },
};
export const SEVERITY_TONE: Record<string, string> = {
  CRITICAL: "bg-destructive/10 text-destructive", HIGH: "bg-warning/10 text-warning",
  MEDIUM: "bg-primary/10 text-primary", LOW: "bg-muted text-muted-foreground",
};
export const OUTCOME: Record<QaOutcome, { label: string; tone: string; icon: typeof Check }> = {
  PASS: { label: "pass", tone: "bg-success/10 text-success border-success/20", icon: CheckCircle2 },
  FAIL: { label: "fail", tone: "bg-destructive/10 text-destructive border-destructive/20", icon: XCircle },
  ERROR: { label: "error", tone: "bg-destructive/10 text-destructive border-destructive/20", icon: AlertTriangle },
  REVIEW: { label: "review", tone: "bg-warning/10 text-warning border-warning/20", icon: Eye },
  NOT_RUN: { label: "not run", tone: "bg-muted text-muted-foreground", icon: CircleDashed },
};
export const BAR: Record<QaOutcome, string> = {
  PASS: "bg-success", FAIL: "bg-destructive", ERROR: "bg-destructive", REVIEW: "bg-warning", NOT_RUN: "bg-muted-foreground/40",
};

export function useCopy() {
  const [copied, setCopied] = useState("");
  const copy = (key: string, text: string) => {
    void navigator.clipboard?.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(""), 1500);
  };
  return { copied, copy };
}

export function OutcomePill({ outcome }: { outcome?: QaOutcome }) {
  if (!outcome) return <span className="text-[11px] text-muted-foreground/70">not run yet</span>;
  const o = OUTCOME[outcome];
  const Icon = o.icon;
  return <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium", o.tone)}><Icon className="h-3 w-3" />{o.label}</span>;
}

export function Dots({ points }: { points: { outcome: QaOutcome; at: string }[] }) {
  if (points.length < 2) return null;
  return (
    <span className="flex items-end gap-0.5" aria-label={`Last ${points.length} runs`}>
      {points.slice(-10).map((p, i) => <span key={i} title={`${p.at.slice(0, 16)} · ${OUTCOME[p.outcome].label}`}
                                              className={cn("w-1 rounded-sm", BAR[p.outcome], p.outcome === "PASS" ? "h-2" : "h-3")} />)}
    </span>
  );
}

export function SqlBlock({ sql, copied, onCopy }: { sql: string; copied: boolean; onCopy: () => void }) {
  return (
    <div className="relative">
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-xl border bg-slate-950 p-3 pr-12 font-mono text-[12px] leading-relaxed text-slate-100">{sql}</pre>
      <button type="button" onClick={onCopy} aria-label="Copy SQL"
              className="absolute right-2 top-2 rounded-lg bg-white/10 p-1.5 text-slate-200 hover:bg-white/20">
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
    </div>
  );
}

export function ResultPanel({ result }: { result: QaResult }) {
  const sample = result.sample ?? [];
  const cols = result.columns?.length ? result.columns : sample.length ? Object.keys(sample[0]) : [];
  return (
    <div className={cn("space-y-2 rounded-xl border p-3 text-xs",
                       result.outcome === "FAIL" || result.outcome === "ERROR" ? "border-destructive/30 bg-destructive/5"
                         : result.outcome === "PASS" ? "border-success/30 bg-success/5" : "bg-muted/30")}>
      <div className="flex flex-wrap items-center gap-2">
        <OutcomePill outcome={result.outcome} />
        <span className="font-medium">{result.detail}</span>
        {result.duration_ms != null && <span className="ml-auto text-muted-foreground">{result.duration_ms} ms</span>}
      </div>
      {sample.length > 0 && (
        <div className="max-h-56 overflow-auto rounded-lg border bg-card">
          <table className="w-full">
            <thead className="sticky top-0 bg-muted"><tr>{cols.map((c) => <th key={c} className="px-2 py-1 text-left font-mono font-medium">{c}</th>)}</tr></thead>
            <tbody>
              {sample.map((row, i) => (
                <tr key={i} className="border-t">{cols.map((c) => <td key={c} className="whitespace-nowrap px-2 py-1 font-mono">{row[c] == null ? <span className="text-muted-foreground">NULL</span> : String(row[c])}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {sample.length > 0 && <p className="text-muted-foreground">First {sample.length} result rows; PII columns are masked.</p>}
    </div>
  );
}

/** Past QA runs as a table of outcome counts. */
export function RunHistoryTable({ runs, ready = true, empty = "No test runs yet. Run the suite to start the history.", showScope = false }: {
  runs: (QaRunRow & { scope?: string | null; triggered_by?: string | null })[]; ready?: boolean; empty?: string; showScope?: boolean;
}) {
  if (!ready) return <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">Test results are not switched on here yet; they arrive with the next deploy.</p>;
  if (!runs.length) return <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">{empty}</p>;
  return (
    <div className="overflow-x-auto rounded-xl border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
          <tr><th className="px-3 py-2">When</th>{showScope && <th className="px-3 py-2">From</th>}<th className="px-3 py-2">Result</th><th className="px-3 py-2 text-right">Pass</th><th className="px-3 py-2 text-right">Fail</th>
            <th className="px-3 py-2 text-right">Review</th><th className="px-3 py-2 text-right">Not run</th><th className="px-3 py-2 text-right">Errors</th><th className="px-3 py-2">By</th></tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const total = r.tests || 1;
            return (
              <tr key={r.qa_run_id} className="border-t">
                <td className="whitespace-nowrap px-3 py-2 text-xs">{r.started_at.slice(0, 16)}</td>
                {showScope && <td className="px-3 py-2 text-xs text-muted-foreground">{r.scope === "RUN" ? "a run" : "this table"}{r.triggered_by && r.triggered_by !== "UI" ? `, ${r.triggered_by.toLowerCase().replace(/_/g, " ")}` : ""}</td>}
                <td className="w-48 px-3 py-2">
                  <span className="flex h-2 overflow-hidden rounded-full bg-muted">
                    <span className="bg-success" style={{ width: `${(r.passed / total) * 100}%` }} />
                    <span className="bg-destructive" style={{ width: `${((r.failed + r.errors) / total) * 100}%` }} />
                    <span className="bg-warning" style={{ width: `${(r.review / total) * 100}%` }} />
                  </span>
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-success">{r.passed}</td>
                <td className="px-3 py-2 text-right tabular-nums text-destructive">{r.failed}</td>
                <td className="px-3 py-2 text-right tabular-nums text-warning">{r.review}</td>
                <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">{r.not_run}</td>
                <td className="px-3 py-2 text-right tabular-nums">{r.errors}</td>
                <td className="px-3 py-2 text-xs text-muted-foreground">{r.created_by}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
