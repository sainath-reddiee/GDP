"use client";

import { useState, useTransition, type ReactNode } from "react";
import { AlertTriangle, ChevronDown, Loader2, Pencil, Play, Sparkles, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { QaOutcome, QaResult, QaTest } from "@/app/runs/[runId]/qa/qa-actions";
import { Dots, OutcomePill, ResultPanel, SEVERITY_TONE, SqlBlock, useCopy } from "./qa-ui";

export type CardOutcome = { ok: true } | { ok: false; error: string };
export type TestEdit = { title: string; sql: string; expected: string; objective: string | null; severity: string; suite_id?: string };

/** One QA test: header with outcome and severity, expandable SQL, result, edit, remove and run. The caller supplies the
 *  actions (run lane or table workspace) and refreshes its data when one succeeds. */
export function TestCard({ test, result, history, open, onToggle, selected, onSelect, canRun, editable, onRun, onUpdate, onRemove,
                           scopeBadge = true, suites, extra }: {
  test: QaTest; result?: QaResult; history: { outcome: QaOutcome; at: string }[]; open: boolean;
  onToggle: () => void; selected: boolean; onSelect: () => void; canRun: boolean; editable: boolean;
  onRun: () => Promise<CardOutcome>; onUpdate: (edit: TestEdit) => Promise<CardOutcome>; onRemove: () => Promise<CardOutcome>;
  /** Show the "Domain suite" badge on TABLE-scope tests (the run workbench; the QA workspace hides it). */
  scopeBadge?: boolean;
  /** When given, editing can move the test to another suite. */
  suites?: { suite_id: string; name: string }[];
  extra?: ReactNode;
}) {
  const { copied, copy } = useCopy();
  const [editing, setEditing] = useState(false);
  const [sql, setSql] = useState(test.sql);
  const [expected, setExpected] = useState(test.expected ?? "");
  const [severity, setSeverity] = useState(test.severity);
  const [suiteId, setSuiteId] = useState(test.suite_id ?? "");
  const [error, setError] = useState("");
  const [busy, start] = useTransition();
  const failing = result && (result.outcome === "FAIL" || result.outcome === "ERROR");

  const run = () => start(async () => {
    setError("");
    const r = await onRun();
    if (!r.ok) setError(r.error);
  });
  const saveEdit = () => start(async () => {
    setError("");
    const r = await onUpdate({ title: test.title, sql, expected, objective: test.objective, severity, ...(suites && suiteId ? { suite_id: suiteId } : {}) });
    if (!r.ok) { setError(r.error); return; }
    setEditing(false);
  });
  const remove = () => start(async () => {
    const r = await onRemove();
    if (!r.ok) setError(r.error);
  });

  return (
    <div className={cn("overflow-hidden rounded-xl border border-l-4 bg-card shadow-sm transition",
                       failing ? "border-l-destructive" : result?.outcome === "PASS" ? "border-l-success"
                         : result?.outcome === "REVIEW" ? "border-l-warning" : "border-l-primary/30",
                       open && "ring-1 ring-primary/20")}>
      <div className="flex items-start gap-3 px-4 py-3">
        <input type="checkbox" checked={selected} onChange={onSelect} aria-label={`Select ${test.title}`} className="mt-1" />
        <button type="button" onClick={onToggle} aria-expanded={open} className="min-w-0 flex-1 text-left">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{test.title}</span>
            {scopeBadge && test.scope === "TABLE" && <Badge variant="outline" className="text-[10px]" title="A domain suite test on this table: edit it in the QA workspace">Domain suite</Badge>}
            {test.origin !== "GENERATED" && (
              <Badge variant="outline" className="gap-1 text-[10px]">{test.origin === "AI" && <Sparkles className="h-3 w-3" />}{test.origin === "AI" ? "AI" : "Saved"}</Badge>
            )}
          </span>
          <span className="mt-0.5 block text-xs text-muted-foreground">{test.objective}</span>
          {result && result.outcome !== "PASS" && <span className={cn("mt-1 block text-xs", failing ? "text-destructive" : "text-muted-foreground")}>{result.detail}</span>}
          {test.warning && <span className="mt-1 flex items-start gap-1.5 text-xs text-warning"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{test.warning}</span>}
        </button>
        <span className="flex shrink-0 flex-col items-end gap-1.5">
          <span className="flex items-center gap-2">
            <Dots points={history} />
            <OutcomePill outcome={result?.outcome} />
          </span>
          <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-semibold", SEVERITY_TONE[test.severity] ?? SEVERITY_TONE.MEDIUM)}>{test.severity}</span>
        </span>
        <ChevronDown onClick={onToggle} className={cn("mt-1 h-4 w-4 shrink-0 cursor-pointer text-muted-foreground transition", open && "rotate-180")} />
      </div>
      {open && (
        <div className="space-y-3 border-t bg-muted/10 px-4 pb-4 pt-3">
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs">
            <span><span className="text-muted-foreground">Expected: </span><span className="font-medium">{test.expected || "not set"}</span></span>
            <span className="font-mono text-muted-foreground">{test.test_id.slice(0, 12)}</span>
            {test.target_column && <span><span className="text-muted-foreground">Column: </span><span className="font-mono">{test.target_column}</span></span>}
            {test.source && <span><span className="text-muted-foreground">Source: </span><span className="font-mono">{test.source}</span></span>}
            {test.prompt && <span className="basis-full"><span className="text-muted-foreground">Asked: </span>{test.prompt}</span>}
          </div>
          {result && <ResultPanel result={result} />}
          {editing ? (
            <div className="space-y-2">
              <Textarea rows={Math.min(16, Math.max(6, sql.split("\n").length + 1))} value={sql} onChange={(e) => setSql(e.target.value)}
                        spellCheck={false} aria-label="Test SQL" className="rounded-xl bg-slate-950 font-mono text-[12px] text-slate-100" />
              <div className="flex flex-wrap items-center gap-2">
                <Input className="min-w-[16rem] flex-1" value={expected} onChange={(e) => setExpected(e.target.value)}
                       placeholder="Expected, e.g. 0 rows or difference = 0" aria-label="Expected result" />
                <select value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Severity" className="h-9 rounded-lg border bg-card px-2 text-sm">
                  {["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((s) => <option key={s}>{s}</option>)}
                </select>
                {suites && suites.length > 0 && (
                  <select value={suiteId} onChange={(e) => setSuiteId(e.target.value)} aria-label="Suite" className="h-9 rounded-lg border bg-card px-2 text-sm">
                    {suites.map((s) => <option key={s.suite_id} value={s.suite_id}>{s.name}</option>)}
                  </select>
                )}
              </div>
            </div>
          ) : (
            <SqlBlock sql={test.sql} copied={copied === "sql"} onCopy={() => copy("sql", test.sql)} />
          )}
          {extra}
          {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
          <div className="flex flex-wrap justify-end gap-2">
            {editable && !editing && <Button size="sm" variant="ghost" disabled={busy} onClick={remove}><Trash2 className="h-3.5 w-3.5" />Remove</Button>}
            {editable && !editing && <Button size="sm" variant="outline" onClick={() => setEditing(true)}><Pencil className="h-3.5 w-3.5" />Edit</Button>}
            {editing && <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setSql(test.sql); }}>Cancel</Button>}
            {editing && <Button size="sm" disabled={busy} onClick={saveEdit}>{busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Save</Button>}
            {!editing && canRun && <Button size="sm" disabled={busy} onClick={run}>{busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}Run this test</Button>}
          </div>
        </div>
      )}
    </div>
  );
}
