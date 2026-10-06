"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle, Check, CheckCircle2, ChevronDown, Copy, Download, FlaskConical, KeyRound, Loader2, Search, Send,
  Sparkles, Table2, Trash2, Wand2, XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { askQa, deleteQaTest, saveQaTest, type QaAnswer, type QaSuite, type QaTest } from "./qa-actions";

const CATEGORY_META: Record<string, { label: string; hint: string }> = {
  RECONCILIATION: { label: "Reconciliation", hint: "Row and key counts, source vs target" },
  GRAIN: { label: "Grain & keys", hint: "Duplicate and missing business keys" },
  COMPLETENESS: { label: "Completeness", hint: "Required columns populated" },
  SOURCE_TO_TARGET: { label: "Source to target", hint: "Direct columns moved unchanged" },
  TRANSFORMATION: { label: "Transformations", hint: "STTM rules applied correctly" },
  LOOKUP: { label: "Lookups & references", hint: "Resolved keys and hub orphans" },
  VALUES: { label: "Values", hint: "Accepted values and constants" },
  JOINS: { label: "Joins", hint: "No unexpected fan-out" },
  CUSTOM: { label: "Custom", hint: "Saved tester and AI queries" },
};
const SEVERITY_TONE: Record<string, string> = {
  CRITICAL: "bg-destructive/10 text-destructive", HIGH: "bg-warning/10 text-warning",
  MEDIUM: "bg-primary/10 text-primary", LOW: "bg-muted text-muted-foreground",
};
const EXAMPLES = [
  "Rows loaded in the target today that do not exist in the source",
  "Customers whose status changed in the source but not in the target",
  "Compare the number of rows per country between source and target",
  "Find target rows where the email is not a valid email address",
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

function SqlBlock({ sql, id, copied, onCopy }: { sql: string; id: string; copied: boolean; onCopy: () => void }) {
  return (
    <div className="relative">
      <pre className="max-h-80 overflow-auto rounded-xl border bg-slate-950 p-3 pr-12 font-mono text-[12px] leading-relaxed text-slate-100">
        {sql}
      </pre>
      <button type="button" onClick={onCopy} aria-label={`Copy SQL of ${id}`}
              className="absolute right-2 top-2 rounded-lg bg-white/10 p-1.5 text-slate-200 hover:bg-white/20">
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
    </div>
  );
}

function TestCard({ test, open, onToggle, copied, onCopy, onDelete, deleting }: {
  test: QaTest; open: boolean; onToggle: () => void; copied: boolean; onCopy: () => void;
  onDelete?: () => void; deleting?: boolean;
}) {
  return (
    <div className={cn("rounded-2xl border bg-card shadow-sm transition", open && "ring-2 ring-primary/15")}>
      <button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full items-start gap-3 p-4 text-left">
        <span className="mt-0.5 rounded-md bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">{test.test_id.slice(0, 10)}</span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{test.title}</span>
            {test.origin !== "GENERATED" && (
              <Badge variant="outline" className="gap-1 text-[10px]">
                {test.origin === "AI" ? <Sparkles className="h-3 w-3" /> : null}{test.origin === "AI" ? "AI" : "Saved"}
              </Badge>
            )}
          </span>
          <span className="mt-0.5 block text-xs text-muted-foreground">{test.objective}</span>
          {test.warning && (
            <span className="mt-1.5 flex items-start gap-1.5 text-xs text-warning">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {test.warning}
            </span>
          )}
        </span>
        <span className={cn("whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-semibold", SEVERITY_TONE[test.severity] ?? SEVERITY_TONE.MEDIUM)}>
          {test.severity}
        </span>
        <ChevronDown className={cn("mt-0.5 h-4 w-4 text-muted-foreground transition", open && "rotate-180")} />
      </button>
      {open && (
        <div className="space-y-3 border-t px-4 pb-4 pt-3">
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs">
            <span><span className="text-muted-foreground">Expected: </span><span className="font-medium">{test.expected || "—"}</span></span>
            {test.target_column && <span><span className="text-muted-foreground">Target column: </span><span className="font-mono">{test.target_column}</span></span>}
            {test.source && <span><span className="text-muted-foreground">Source: </span><span className="font-mono">{test.source}</span></span>}
            {test.prompt && <span className="basis-full"><span className="text-muted-foreground">Asked: </span>{test.prompt}</span>}
          </div>
          <SqlBlock sql={test.sql} id={test.test_id} copied={copied} onCopy={onCopy} />
          {onDelete && (
            <div className="flex justify-end">
              <Button size="sm" variant="ghost" disabled={deleting} onClick={onDelete}>
                {deleting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />} Remove from suite
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function QaWorkbench({ runId, suite }: { runId: string; suite: QaSuite }) {
  const router = useRouter();
  const [category, setCategory] = useState<string>("ALL");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<string>(suite.tests[0]?.test_id ?? "");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<QaAnswer | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [asking, startAsk] = useTransition();
  const [saving, startSave] = useTransition();
  const [deleting, setDeleting] = useState("");
  const { copied, copy } = useCopy();

  const categories = Object.keys(CATEGORY_META).filter((c) => (suite.counts[c] ?? 0) > 0);
  const visible = useMemo(() => suite.tests.filter((t) =>
    (category === "ALL" || t.category === category)
    && `${t.title} ${t.objective} ${t.target_column ?? ""} ${t.sql}`.toLowerCase().includes(query.toLowerCase())),
  [suite.tests, category, query]);
  const critical = suite.tests.filter((t) => t.severity === "CRITICAL").length;
  const saved = suite.tests.filter((t) => t.origin !== "GENERATED").length;

  const download = () => {
    const blob = new Blob([suite.script], { type: "text/sql" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${suite.target.name.toLowerCase()}_qa_tests.sql`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const ask = (text: string) => startAsk(async () => {
    setError("");
    setNotice("");
    setAnswer(null);
    const r = await askQa(runId, text);
    if (!r.ok) { setError(r.error); return; }
    setAnswer(r.data);
    setDraft(r.data.sql);
  });

  const save = () => startSave(async () => {
    if (!answer) return;
    setError("");
    const r = await saveQaTest(runId, {
      title: answer.title, sql: draft, objective: answer.objective, expected: answer.expected,
      category: answer.category, prompt: question,
    });
    if (!r.ok) { setError(r.error); return; }
    setNotice(`Saved "${answer.title}" to the suite.`);
    setAnswer(null);
    setQuestion("");
    setCategory("CUSTOM");
    router.refresh();
  });

  const remove = async (id: string) => {
    setDeleting(id);
    const r = await deleteQaTest(runId, id);
    setDeleting("");
    if (!r.ok) setError(r.error);
    else router.refresh();
  };

  return (
    <div className="space-y-5">
      {/* header */}
      <div className="flex flex-wrap items-start gap-4 rounded-2xl border bg-gradient-to-br from-violet-500/10 via-card to-card p-5 shadow-sm">
        <span className="grid h-11 w-11 place-items-center rounded-xl bg-violet-600 text-white shadow-sm">
          <FlaskConical className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-lg font-semibold tracking-tight">Functional QA tests</h3>
          <p className="text-sm text-muted-foreground">
            SQL test cases derived from the approved STTM. Run them in Snowsight after the model is built; every query is
            read-only and traces to a target column.
          </p>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <span className="inline-flex items-center gap-1 rounded-full border bg-card px-2.5 py-1">
              <Table2 className="h-3.5 w-3.5 text-muted-foreground" /> Target <span className="font-mono">{suite.target.fqn}</span>
            </span>
            {suite.driving_table && (
              <span className="inline-flex items-center gap-1 rounded-full border bg-card px-2.5 py-1">
                Driving source <span className="font-mono">{suite.sources[suite.driving_table] ?? suite.driving_table}</span>
              </span>
            )}
            <span className="inline-flex items-center gap-1 rounded-full border bg-card px-2.5 py-1">
              <KeyRound className="h-3.5 w-3.5 text-muted-foreground" />
              {suite.business_keys.length ? suite.business_keys.join(", ") : "no business key recorded"}
            </span>
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => copy("script", suite.script)}>
            {copied === "script" ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />} Copy all
          </Button>
          <Button onClick={download}><Download className="h-4 w-4" /> Download .sql</Button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {[
          ["Test cases", suite.tests.length, "across every category"],
          ["Critical", critical, "grain and hub integrity"],
          ["Categories", categories.length, "reconciliation to joins"],
          ["Saved queries", saved, "from testers and AI"],
        ].map(([label, value, hint]) => (
          <div key={String(label)} className="rounded-2xl border bg-card p-4 shadow-sm">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
            <p className="text-[11px] text-muted-foreground">{hint}</p>
          </div>
        ))}
      </div>

      {/* ask */}
      <div className="rounded-2xl border bg-card p-5 shadow-sm">
        <div className="flex items-center gap-2">
          <Wand2 className="h-4 w-4 text-violet-600" />
          <h4 className="text-sm font-semibold">Ask for a test</h4>
          <span className="text-xs text-muted-foreground">Describe what to check; Cortex writes one read-only SELECT over this run&apos;s tables.</span>
        </div>
        <div className="mt-3 flex gap-2">
          <Textarea rows={2} value={question} onChange={(e) => setQuestion(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && question.trim()) ask(question); }}
                    placeholder="e.g. Find customers whose name in the target does not match the trimmed, title-cased source name"
                    className="flex-1 rounded-xl" />
          <Button className="self-stretch" disabled={asking || question.trim().length < 3} onClick={() => ask(question)}>
            {asking ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            {asking ? "Writing…" : "Generate"}
          </Button>
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {EXAMPLES.map((ex) => (
            <button key={ex} type="button" onClick={() => setQuestion(ex)}
                    className="rounded-full border px-2.5 py-1 text-[11px] text-muted-foreground hover:border-violet-400 hover:text-foreground">
              {ex}
            </button>
          ))}
        </div>

        {answer && (
          <div className="mt-4 space-y-3 rounded-xl border bg-muted/20 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{answer.title}</span>
              <Badge variant="outline">{CATEGORY_META[answer.category]?.label ?? answer.category}</Badge>
              {answer.valid ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-success/10 px-2 py-0.5 text-xs font-medium text-success">
                  <CheckCircle2 className="h-3.5 w-3.5" /> Read-only and compiles
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 rounded-full bg-destructive/10 px-2 py-0.5 text-xs font-medium text-destructive">
                  <XCircle className="h-3.5 w-3.5" /> Needs a fix
                </span>
              )}
            </div>
            <p className="text-sm text-muted-foreground">{answer.objective}</p>
            <Textarea rows={Math.min(18, Math.max(6, draft.split("\n").length + 1))} value={draft}
                      onChange={(e) => setDraft(e.target.value)} spellCheck={false}
                      className="rounded-xl bg-slate-950 font-mono text-[12px] text-slate-100" aria-label="Generated SQL" />
            <p className="text-xs"><span className="text-muted-foreground">Expected: </span>{answer.expected}</p>
            {answer.note && <p className="text-xs text-muted-foreground">{answer.note}</p>}
            {answer.assumptions.length > 0 && (
              <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
                {answer.assumptions.map((a) => <li key={a}>{a}</li>)}
              </ul>
            )}
            {(answer.problems.length > 0 || answer.compile_error) && (
              <div className="flex gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>{[...answer.problems, answer.compile_error].filter(Boolean).join(" · ")}</span>
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={saving || !draft.trim()} onClick={save}
                      title="Saved queries are checked again by the read-only guard">
                {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />} Save to suite
              </Button>
              <Button size="sm" variant="outline" onClick={() => copy("draft", draft)}>
                {copied === "draft" ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />} Copy
              </Button>
              <Button size="sm" variant="ghost" disabled={asking} onClick={() => ask(question)}>Regenerate</Button>
            </div>
          </div>
        )}
        {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
        {notice && <p role="status" className="mt-3 text-sm text-success">{notice}</p>}
      </div>

      {/* suite */}
      <div className="grid gap-5 lg:grid-cols-[240px_1fr]">
        <nav aria-label="Test categories" className="space-y-1 lg:sticky lg:top-4 lg:self-start">
          {["ALL", ...categories].map((c) => {
            const active = category === c;
            const count = c === "ALL" ? suite.tests.length : suite.counts[c] ?? 0;
            return (
              <button key={c} type="button" onClick={() => setCategory(c)} aria-pressed={active}
                      className={cn("flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm transition",
                        active ? "bg-foreground text-background" : "hover:bg-muted")}>
                <span className="min-w-0 flex-1">
                  <span className="block font-medium">{c === "ALL" ? "All tests" : CATEGORY_META[c].label}</span>
                  {c !== "ALL" && <span className={cn("block truncate text-[11px]", active ? "opacity-70" : "text-muted-foreground")}>{CATEGORY_META[c].hint}</span>}
                </span>
                <span className="tabular-nums text-xs opacity-70">{count}</span>
              </button>
            );
          })}
        </nav>
        <div className="space-y-3">
          <div className="relative">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search tests, columns or SQL"
                   className="rounded-xl pl-9" />
          </div>
          {visible.map((t) => (
            <TestCard key={t.test_id} test={t} open={open === t.test_id}
                      onToggle={() => setOpen(open === t.test_id ? "" : t.test_id)}
                      copied={copied === t.test_id} onCopy={() => copy(t.test_id, t.sql)}
                      onDelete={t.origin !== "GENERATED" ? () => void remove(t.test_id) : undefined}
                      deleting={deleting === t.test_id} />
          ))}
          {visible.length === 0 && (
            <div className="rounded-2xl border border-dashed p-10 text-center text-sm text-muted-foreground">
              No tests match. Ask for one above.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
