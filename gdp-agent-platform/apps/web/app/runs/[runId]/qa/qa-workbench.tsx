"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle, Check, CheckCircle2, ChevronDown, CircleDashed, Copy, Download, Eye, FlaskConical, History, KeyRound,
  ListChecks, Loader2, Pencil, Play, Search, Send, Sparkles, Table2, Trash2, Wand2, XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  askQa, deleteQaTest, planQa, runQa, saveQaTest, updateQaTest,
  type QaAnswer, type QaOutcome, type QaProposal, type QaResult, type QaResults, type QaSuite, type QaTest,
} from "./qa-actions";
import { CodeCitations, type CodeCitation } from "@/components/code-citations";

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
const SEVERITY_TONE: Record<string, string> = {
  CRITICAL: "bg-destructive/10 text-destructive", HIGH: "bg-warning/10 text-warning",
  MEDIUM: "bg-primary/10 text-primary", LOW: "bg-muted text-muted-foreground",
};
const OUTCOME: Record<QaOutcome, { label: string; tone: string; icon: typeof Check }> = {
  PASS: { label: "pass", tone: "bg-success/10 text-success border-success/20", icon: CheckCircle2 },
  FAIL: { label: "fail", tone: "bg-destructive/10 text-destructive border-destructive/20", icon: XCircle },
  ERROR: { label: "error", tone: "bg-destructive/10 text-destructive border-destructive/20", icon: AlertTriangle },
  REVIEW: { label: "review", tone: "bg-warning/10 text-warning border-warning/20", icon: Eye },
  NOT_RUN: { label: "not run", tone: "bg-muted text-muted-foreground", icon: CircleDashed },
};
const BAR: Record<QaOutcome, string> = {
  PASS: "bg-success", FAIL: "bg-destructive", ERROR: "bg-destructive", REVIEW: "bg-warning", NOT_RUN: "bg-muted-foreground/40",
};
const EXAMPLES = [
  "Rows loaded in the target that do not exist in the source",
  "Orders whose total does not equal the sum of their lines",
  "Compare the number of rows per country between source and target",
  "Target rows where the email is not a valid email address",
];

function useCopy() {
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

function Dots({ points }: { points: { outcome: QaOutcome; at: string }[] }) {
  if (points.length < 2) return null;
  return (
    <span className="flex items-end gap-0.5" aria-label={`Last ${points.length} runs`}>
      {points.slice(-10).map((p, i) => <span key={i} title={`${p.at.slice(0, 16)} · ${OUTCOME[p.outcome].label}`}
                                              className={cn("w-1 rounded-sm", BAR[p.outcome], p.outcome === "PASS" ? "h-2" : "h-3")} />)}
    </span>
  );
}

function SqlBlock({ sql, copied, onCopy }: { sql: string; copied: boolean; onCopy: () => void }) {
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

function ResultPanel({ result }: { result: QaResult }) {
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

function TestCard({ runId, test, result, history, open, onToggle, selected, onSelect, canRun, canEdit = true }: {
  runId: string; test: QaTest; result?: QaResult; history: { outcome: QaOutcome; at: string }[]; open: boolean;
  onToggle: () => void; selected: boolean; onSelect: () => void; canRun: boolean; canEdit?: boolean;
}) {
  const router = useRouter();
  const { copied, copy } = useCopy();
  const [editing, setEditing] = useState(false);
  const [sql, setSql] = useState(test.sql);
  const [expected, setExpected] = useState(test.expected ?? "");
  const [severity, setSeverity] = useState(test.severity);
  const [error, setError] = useState("");
  const [busy, start] = useTransition();
  const saved = test.origin !== "GENERATED" && canEdit;
  const failing = result && (result.outcome === "FAIL" || result.outcome === "ERROR");

  const run = () => start(async () => {
    setError("");
    const r = await runQa(runId, [test.test_id]);
    if (!r.ok) setError(r.error); else router.refresh();
  });
  const saveEdit = () => start(async () => {
    setError("");
    const r = await updateQaTest(runId, test.test_id, { title: test.title, sql, expected, objective: test.objective, severity });
    if (!r.ok) { setError(r.error); return; }
    setEditing(false);
    router.refresh();
  });
  const remove = () => start(async () => {
    const r = await deleteQaTest(runId, test.test_id);
    if (!r.ok) setError(r.error); else router.refresh();
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
            <span><span className="text-muted-foreground">Expected: </span><span className="font-medium">{test.expected || "—"}</span></span>
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
              </div>
            </div>
          ) : (
            <SqlBlock sql={test.sql} copied={copied === "sql"} onCopy={() => copy("sql", test.sql)} />
          )}
          {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
          <div className="flex flex-wrap justify-end gap-2">
            {saved && !editing && <Button size="sm" variant="ghost" disabled={busy} onClick={remove}><Trash2 className="h-3.5 w-3.5" />Remove</Button>}
            {saved && !editing && <Button size="sm" variant="outline" onClick={() => setEditing(true)}><Pencil className="h-3.5 w-3.5" />Edit</Button>}
            {editing && <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setSql(test.sql); }}>Cancel</Button>}
            {editing && <Button size="sm" disabled={busy} onClick={saveEdit}>{busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Save</Button>}
            {!editing && canRun && <Button size="sm" disabled={busy} onClick={run}>{busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}Run this test</Button>}
          </div>
        </div>
      )}
    </div>
  );
}

function Assistant({ runId, onSaved, canAI = true }: { runId: string; onSaved: () => void; canAI?: boolean }) {
  const { copied, copy } = useCopy();
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<QaAnswer | null>(null);
  const [draft, setDraft] = useState("");
  const [focus, setFocus] = useState("");
  const [plan, setPlan] = useState<{ tests: QaProposal[]; code_citations?: CodeCitation[];
                                     grounding: { rules: number; profile_columns: number; checks: number; code?: number } } | null>(null);
  const [keep, setKeep] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [asking, startAsk] = useTransition();
  const [planning, startPlan] = useTransition();
  const [saving, startSave] = useTransition();

  const ask = (text: string) => startAsk(async () => {
    setError(""); setNotice(""); setAnswer(null);
    const r = await askQa(runId, text);
    if (!r.ok) { setError(r.error); return; }
    setAnswer(r.data);
    setDraft(r.data.sql);
  });
  const makePlan = () => startPlan(async () => {
    setError(""); setNotice(""); setPlan(null);
    const r = await planQa(runId, focus);
    if (!r.ok) { setError(r.error); return; }
    setPlan(r.data);
    setKeep(new Set(r.data.tests.map((t, i) => (t.valid ? i : -1)).filter((i) => i >= 0)));
  });
  const saveOne = () => startSave(async () => {
    if (!answer) return;
    const r = await saveQaTest(runId, { title: answer.title, sql: draft, objective: answer.objective, expected: answer.expected,
                                        category: answer.category, prompt: question });
    if (!r.ok) { setError(r.error); return; }
    setNotice(`Saved "${answer.title}".`);
    setAnswer(null); setQuestion("");
    onSaved();
  });
  const savePlan = () => startSave(async () => {
    if (!plan) return;
    let n = 0;
    for (const i of Array.from(keep)) {
      const t = plan.tests[i];
      const r = await saveQaTest(runId, { title: t.title, sql: t.sql, objective: `${t.objective} (${t.why})`, expected: t.expected,
                                          category: t.category, severity: t.severity, target_column: t.target_column,
                                          prompt: focus || "AI test plan" });
      if (!r.ok) { setError(`${t.title}: ${r.error}`); break; }
      n += 1;
    }
    if (n) { setNotice(`Saved ${n} test${n === 1 ? "" : "s"} from the AI plan.`); setPlan(null); onSaved(); }
  });

  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <section className="space-y-3 rounded-2xl border bg-card p-5 shadow-sm">
        <div className="flex items-center gap-2">
          <ListChecks className="h-4 w-4 text-violet-600" />
          <h4 className="text-sm font-semibold">AI test plan</h4>
        </div>
        <p className="text-xs text-muted-foreground">
          Proposes 4 to 8 tests the generated suite misses, from your domain&apos;s business rules, the data profile and the
          approved data quality checks. Every query is checked read-only and compiled before you keep it.
        </p>
        <div className="flex gap-2">
          <Input value={focus} onChange={(e) => setFocus(e.target.value)} placeholder="Optional focus, e.g. money columns, late-arriving data"
                 aria-label="Plan focus" className="flex-1" />
          <Button disabled={planning || !canAI} onClick={makePlan} className="bg-gradient-to-r from-violet-600 to-primary text-white">
            {planning ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}{planning ? "Planning…" : "Plan tests"}
          </Button>
        </div>
        {plan && (
          <div className="space-y-2">
            <p className="text-[11px] text-muted-foreground">
              Grounded in {plan.grounding.rules} knowledge items, {plan.grounding.profile_columns} profiled columns and {plan.grounding.checks} approved checks{plan.grounding.code ? `, plus ${plan.grounding.code} snippets of client code` : ""}.
            </p>
            <CodeCitations items={plan.code_citations} />
            {plan.tests.map((t, i) => (
              <label key={i} className={cn("flex cursor-pointer gap-3 rounded-xl border p-3", keep.has(i) ? "border-primary/40 bg-primary/5" : "bg-card", !t.valid && "opacity-70")}>
                <input type="checkbox" className="mt-1" disabled={!t.valid} checked={keep.has(i)}
                       onChange={() => setKeep((k) => { const n = new Set(k); if (n.has(i)) n.delete(i); else n.add(i); return n; })} />
                <span className="min-w-0 flex-1 space-y-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{t.title}</span>
                    <Badge variant="outline" className="text-[10px]">{CATEGORY_META[t.category]?.label ?? t.category}</Badge>
                    <span className={cn("rounded-full px-1.5 text-[10px] font-semibold", SEVERITY_TONE[t.severity])}>{t.severity}</span>
                    {t.valid ? <span className="text-[11px] text-success">compiles</span> : <span className="text-[11px] text-destructive">needs a fix</span>}
                  </span>
                  <span className="block text-xs text-muted-foreground">{t.objective}</span>
                  <span className="block text-[11px] text-violet-700 dark:text-violet-300">Why: {t.why}</span>
                  {!t.valid && <span className="block text-[11px] text-destructive">{[...t.problems, t.compile_error].filter(Boolean).join(" · ")}</span>}
                  <details className="text-xs"><summary className="cursor-pointer text-muted-foreground">SQL · expected {t.expected}</summary>
                    <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-950 p-2 font-mono text-[11px] text-slate-100">{t.sql}</pre>
                  </details>
                </span>
              </label>
            ))}
            <Button disabled={saving || keep.size === 0} onClick={savePlan}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}Keep {keep.size} test{keep.size === 1 ? "" : "s"}
            </Button>
          </div>
        )}
      </section>

      <section className="space-y-3 rounded-2xl border bg-card p-5 shadow-sm">
        <div className="flex items-center gap-2">
          <Wand2 className="h-4 w-4 text-violet-600" />
          <h4 className="text-sm font-semibold">Ask for one test</h4>
        </div>
        <div className="flex gap-2">
          <Textarea rows={2} value={question} onChange={(e) => setQuestion(e.target.value)} className="flex-1 rounded-xl"
                    onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && question.trim()) ask(question); }}
                    placeholder="Describe what to check in plain English" aria-label="Describe a test" />
          <Button className="self-stretch" disabled={asking || !canAI || question.trim().length < 3} onClick={() => ask(question)}>
            {asking ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}{asking ? "Writing…" : "Generate"}
          </Button>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {EXAMPLES.map((ex) => (
            <button key={ex} type="button" onClick={() => setQuestion(ex)}
                    className="rounded-full border px-2.5 py-1 text-[11px] text-muted-foreground hover:border-violet-400 hover:text-foreground">{ex}</button>
          ))}
        </div>
        {answer && (
          <div className="space-y-3 rounded-xl border bg-muted/20 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{answer.title}</span>
              <Badge variant="outline">{CATEGORY_META[answer.category]?.label ?? answer.category}</Badge>
              {answer.valid
                ? <span className="inline-flex items-center gap-1 rounded-full bg-success/10 px-2 py-0.5 text-xs font-medium text-success"><CheckCircle2 className="h-3.5 w-3.5" />Read-only and compiles</span>
                : <span className="inline-flex items-center gap-1 rounded-full bg-destructive/10 px-2 py-0.5 text-xs font-medium text-destructive"><XCircle className="h-3.5 w-3.5" />Needs a fix</span>}
            </div>
            <p className="text-sm text-muted-foreground">{answer.objective}</p>
            <Textarea rows={Math.min(16, Math.max(6, draft.split("\n").length + 1))} value={draft} onChange={(e) => setDraft(e.target.value)}
                      spellCheck={false} className="rounded-xl bg-slate-950 font-mono text-[12px] text-slate-100" aria-label="Generated SQL" />
            <p className="text-xs"><span className="text-muted-foreground">Expected: </span>{answer.expected}</p>
            {answer.note && <p className="text-xs text-muted-foreground">{answer.note}</p>}
            {(answer.problems.length > 0 || answer.compile_error) && (
              <p className="rounded-lg border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">{[...answer.problems, answer.compile_error].filter(Boolean).join(" · ")}</p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={saving || !draft.trim()} onClick={saveOne}>{saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Save to suite</Button>
              <Button size="sm" variant="outline" onClick={() => copy("draft", draft)}>{copied === "draft" ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}Copy</Button>
              <Button size="sm" variant="ghost" disabled={asking} onClick={() => ask(question)}>Regenerate</Button>
            </div>
          </div>
        )}
      </section>
      {error && <p role="alert" className="text-sm text-destructive xl:col-span-2">{error}</p>}
      {notice && <p role="status" className="text-sm text-success xl:col-span-2">{notice}</p>}
    </div>
  );
}

function RunHistory({ data }: { data: QaResults }) {
  if (!data.ready) return <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">Test results are not switched on here yet; they arrive with the next deploy.</p>;
  if (!data.runs.length) return <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">No test runs yet. Run the suite to start the history.</p>;
  return (
    <div className="overflow-x-auto rounded-xl border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
          <tr><th className="px-3 py-2">When</th><th className="px-3 py-2">Result</th><th className="px-3 py-2 text-right">Pass</th><th className="px-3 py-2 text-right">Fail</th>
            <th className="px-3 py-2 text-right">Review</th><th className="px-3 py-2 text-right">Not run</th><th className="px-3 py-2 text-right">Errors</th><th className="px-3 py-2">By</th></tr>
        </thead>
        <tbody>
          {data.runs.map((r) => {
            const total = r.tests || 1;
            return (
              <tr key={r.qa_run_id} className="border-t">
                <td className="whitespace-nowrap px-3 py-2 text-xs">{r.started_at.slice(0, 16)}</td>
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

export function QaWorkbench({ runId, suite, results, canRun, canAI = true, canEdit = true }: {
  runId: string; suite: QaSuite; results: QaResults; canRun: boolean; canAI?: boolean; canEdit?: boolean;
}) {
  const router = useRouter();
  const { copied, copy } = useCopy();
  const [tab, setTab] = useState<"tests" | "ai" | "history">("tests");
  const [category, setCategory] = useState("ALL");
  const [outcome, setOutcome] = useState<"ALL" | "ISSUES" | QaOutcome>("ALL");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [running, startRun] = useTransition();
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const byTest = useMemo(() => new Map(results.results.map((r) => [r.test_id, r])), [results.results]);
  const categories = Object.keys(CATEGORY_META).filter((c) => (suite.counts[c] ?? 0) > 0);
  const visible = suite.tests.filter((t) => {
    const r = byTest.get(t.test_id);
    if (category !== "ALL" && t.category !== category) return false;
    if (outcome === "ISSUES" && !(r && ["FAIL", "ERROR", "REVIEW"].includes(r.outcome))) return false;
    if (outcome !== "ALL" && outcome !== "ISSUES" && r?.outcome !== outcome) return false;
    return `${t.title} ${t.objective} ${t.target_column ?? ""} ${t.sql}`.toLowerCase().includes(query.toLowerCase());
  });
  const count = (o: QaOutcome) => results.results.filter((r) => r.outcome === o).length;
  const blocking = results.results.filter((r) => ["FAIL", "ERROR"].includes(r.outcome) && ["CRITICAL", "HIGH"].includes(String(r.severity))).length;

  const run = (ids?: string[]) => startRun(async () => {
    setMessage(null);
    const r = await runQa(runId, ids);
    if (!r.ok) { setMessage({ ok: false, text: r.error }); return; }
    setMessage({ ok: true, text: `${r.data.passed} pass · ${r.data.failed} fail · ${r.data.review} to review · ${r.data.not_run} not run${r.data.errors ? ` · ${r.data.errors} errors` : ""}` });
    setSelected(new Set());
    router.refresh();
  });

  const download = () => {
    const url = URL.createObjectURL(new Blob([suite.script], { type: "text/sql" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${suite.target.name.toLowerCase()}_qa_tests.sql`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-5">
      <div className="space-y-4 rounded-2xl border bg-gradient-to-br from-violet-500/10 via-card to-card p-5 shadow-sm">
        <div className="flex flex-wrap items-start gap-4">
          <span className="grid h-11 w-11 place-items-center rounded-xl bg-violet-50 text-violet-600 ring-1 ring-inset ring-violet-100"><FlaskConical className="h-5 w-5" /></span>
          <div className="min-w-0 flex-1">
            <h3 className="text-lg font-semibold tracking-tight">Functional QA tests</h3>
            <p className="text-sm text-muted-foreground">Read-only SQL tests from the STTM, your domain rules and AI. Run them in Snowflake and sign off on the results.</p>
            <div className="mt-2 flex flex-wrap gap-2 text-xs">
              <span className="inline-flex items-center gap-1 rounded-full border bg-card px-2.5 py-1"><Table2 className="h-3.5 w-3.5 text-muted-foreground" /><span className="font-mono">{suite.target.fqn}</span></span>
              <span className="inline-flex items-center gap-1 rounded-full border bg-card px-2.5 py-1"><KeyRound className="h-3.5 w-3.5 text-muted-foreground" />{suite.business_keys.length ? suite.business_keys.join(", ") : "no business key"}</span>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {canRun && (
              <Button disabled={running} onClick={() => run()}>
                {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}{running ? "Running in Snowflake…" : `Run all ${suite.tests.length}`}
              </Button>
            )}
            <Button variant="outline" onClick={download}><Download className="h-4 w-4" />.sql</Button>
            <Button variant="ghost" onClick={() => copy("script", suite.script)} aria-label="Copy all SQL">{copied === "script" ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}</Button>
          </div>
        </div>
        {message && <p role={message.ok ? "status" : "alert"} className={cn("text-sm", message.ok ? "text-muted-foreground" : "text-destructive")}>{message.text}</p>}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          {([
            ["Tests", suite.tests.length, ""],
            ["Passed", count("PASS"), "text-success"],
            ["Failed", count("FAIL") + count("ERROR"), count("FAIL") + count("ERROR") ? "text-destructive" : ""],
            ["Blocking sign-off", blocking, blocking ? "text-destructive" : "text-success"],
            ["To review", count("REVIEW"), count("REVIEW") ? "text-warning" : ""],
            ["Not run", results.run ? count("NOT_RUN") : suite.tests.length, "text-muted-foreground"],
          ] as const).map(([label, value, tone]) => (
            <div key={label} className="rounded-xl border bg-card px-3 py-2">
              <p className={cn("text-xl font-semibold tabular-nums", tone)}>{value}</p>
              <p className="text-[11px] text-muted-foreground">{label}</p>
            </div>
          ))}
        </div>
        {results.run && (
          <p className="text-xs text-muted-foreground">
            Last run {results.run.started_at.slice(0, 16)} by {results.run.created_by}
            {results.run.target_built === false ? " · the model is not built yet, so tests on the target did not run" : ""}
          </p>
        )}
      </div>

      <nav role="tablist" className="flex gap-1 border-b">
        {([["tests", "Tests", ListChecks], ["ai", "AI assistant", Sparkles], ["history", "Run history", History]] as const).map(([k, label, Icon]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                  className={cn("-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium", tab === k ? "border-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>
            <Icon className="h-4 w-4" />{label}
            {k === "history" && results.runs.length > 0 && <span className="rounded-full bg-muted px-1.5 text-[11px]">{results.runs.length}</span>}
          </button>
        ))}
      </nav>

      {tab === "ai" && <Assistant runId={runId} canAI={canAI && canEdit} onSaved={() => { setTab("tests"); setCategory("ALL"); router.refresh(); }} />}
      {tab === "history" && <RunHistory data={results} />}

      {tab === "tests" && (
        <div className="grid gap-5 lg:grid-cols-[250px_minmax(0,1fr)]">
          <nav aria-label="Test categories" className="space-y-1 lg:sticky lg:top-4 lg:self-start">
            {["ALL", ...categories].map((c) => {
              const tests = c === "ALL" ? suite.tests : suite.tests.filter((t) => t.category === c);
              const rs = tests.map((t) => byTest.get(t.test_id)).filter(Boolean) as QaResult[];
              const active = category === c;
              return (
                <button key={c} type="button" onClick={() => setCategory(c)} aria-pressed={active}
                        className={cn("w-full rounded-xl px-3 py-2 text-left text-sm transition", active ? "bg-foreground text-background" : "hover:bg-muted")}>
                  <span className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate font-medium">{c === "ALL" ? "All tests" : CATEGORY_META[c].label}</span>
                    <span className="text-xs tabular-nums opacity-70">{tests.length}</span>
                  </span>
                  {rs.length > 0 && (
                    <span className="mt-1.5 flex h-1.5 overflow-hidden rounded-full bg-muted/60">
                      {(["PASS", "FAIL", "ERROR", "REVIEW", "NOT_RUN"] as QaOutcome[]).map((o) => {
                        const n = rs.filter((r) => r.outcome === o).length;
                        return n ? <span key={o} className={BAR[o]} style={{ width: `${(n / tests.length) * 100}%` }} /> : null;
                      })}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative min-w-[14rem] flex-1">
                <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search tests, columns or SQL" className="rounded-xl pl-9" />
              </div>
              {(["ALL", "ISSUES", "PASS", "NOT_RUN"] as const).map((o) => (
                <button key={o} type="button" onClick={() => setOutcome(o)} aria-pressed={outcome === o}
                        className={cn("rounded-full border px-2.5 py-1 text-xs", outcome === o ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:border-primary/40")}>
                  {o === "ALL" ? "All" : o === "ISSUES" ? "Failing & review" : o === "PASS" ? "Passed" : "Not run"}
                </button>
              ))}
            </div>
            {selected.size > 0 && canRun && (
              <div className="flex items-center gap-2 rounded-xl border border-primary/30 bg-primary/5 px-3 py-2 text-sm">
                {selected.size} selected
                <Button size="sm" className="ml-auto" disabled={running} onClick={() => run(Array.from(selected))}>
                  {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}Run selected
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>Clear</Button>
              </div>
            )}
            {visible.map((t) => (
              <TestCard key={t.test_id} runId={runId} test={t} result={byTest.get(t.test_id)} history={results.history[t.test_id] ?? []}
                        open={open === t.test_id} onToggle={() => setOpen(open === t.test_id ? "" : t.test_id)} canRun={canRun} canEdit={canEdit}
                        selected={selected.has(t.test_id)}
                        onSelect={() => setSelected((s) => { const n = new Set(s); if (n.has(t.test_id)) n.delete(t.test_id); else n.add(t.test_id); return n; })} />
            ))}
            {visible.length === 0 && (
              <div className="rounded-2xl border border-dashed p-10 text-center text-sm text-muted-foreground">No tests match. Try the AI assistant to add some.</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
