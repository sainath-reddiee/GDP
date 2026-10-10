"use client";

import { useRef, useState, useTransition, type ReactNode } from "react";
import { Check, CheckCircle2, Copy, ListChecks, Loader2, Send, Sparkles, Wand2, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { CodeCitations, type CodeCitation } from "@/components/code-citations";
import type { QaAnswer, QaProposal } from "@/app/runs/[runId]/qa/qa-actions";
import { CATEGORY_META, SEVERITY_TONE, useCopy } from "./qa-ui";

type Res<T> = { ok: true; data: T } | { ok: false; error: string };
export type QaPlan = { tests: QaProposal[]; code_citations?: CodeCitation[];
                       grounding: { rules: number; profile_columns: number; checks: number; code?: number } };
export type NewTest = {
  title: string; sql: string; objective?: string; expected?: string; category?: string; prompt?: string;
  severity?: string; target_column?: string;
};

const EXAMPLES = [
  "Rows loaded in the target that do not exist in the source",
  "Orders whose total does not equal the sum of their lines",
  "Compare the number of rows per country between source and target",
  "Target rows where the email is not a valid email address",
];

/** AI test plan and "ask for one test", with the endpoints supplied by the caller (a run or a target table). */
export function QaAssistant({ ask: askFn, plan: planFn, save: saveFn, onSaved, canAI = true, saveTarget }: {
  ask: (question: string) => Promise<Res<QaAnswer>>;
  plan: (focus: string) => Promise<Res<QaPlan>>;
  save: (test: NewTest) => Promise<Res<unknown>>;
  onSaved: () => void;
  canAI?: boolean;
  /** Shown next to the save buttons, e.g. a suite picker. */
  saveTarget?: ReactNode;
}) {
  const { copied, copy } = useCopy();
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<QaAnswer | null>(null);
  const [draft, setDraft] = useState("");
  const [focus, setFocus] = useState("");
  const [plan, setPlan] = useState<QaPlan | null>(null);
  const [keep, setKeep] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [asking, startAsk] = useTransition();
  const [planning, startPlan] = useTransition();
  const [saving, startSave] = useTransition();
  const askSeq = useRef(0);
  const planSeq = useRef(0);

  const ask = (text: string) => startAsk(async () => {
    const seq = ++askSeq.current;
    setError(""); setNotice(""); setAnswer(null);
    const r = await askFn(text);
    if (seq !== askSeq.current) return;
    if (!r.ok) { setError(r.error); return; }
    setAnswer(r.data);
    setDraft(r.data.sql);
  });
  const makePlan = () => startPlan(async () => {
    const seq = ++planSeq.current;
    setError(""); setNotice(""); setPlan(null);
    const r = await planFn(focus);
    if (seq !== planSeq.current) return;
    if (!r.ok) { setError(r.error); return; }
    setPlan(r.data);
    setKeep(new Set(r.data.tests.map((t, i) => (t.valid ? i : -1)).filter((i) => i >= 0)));
  });
  const saveOne = () => startSave(async () => {
    if (!answer) return;
    const r = await saveFn({ title: answer.title, sql: draft, objective: answer.objective, expected: answer.expected,
                             category: answer.category, prompt: question });
    if (!r.ok) { setError(r.error); return; }
    setNotice(`Saved "${answer.title}".`);
    setAnswer(null); setQuestion("");
    onSaved();
  });
  const savePlan = () => startSave(async () => {
    if (!plan) return;
    const saved = new Set<number>();
    let failed = false;
    for (const i of Array.from(keep)) {
      const t = plan.tests[i];
      const r = await saveFn({ title: t.title, sql: t.sql, objective: `${t.objective} (${t.why})`, expected: t.expected,
                               category: t.category, severity: t.severity, target_column: t.target_column,
                               prompt: focus || "AI test plan" });
      if (!r.ok) { setError(`${t.title}: ${r.error}`); failed = true; break; }
      saved.add(i);
    }
    const n = saved.size;
    if (!n) return;
    setNotice(`Saved ${n} test${n === 1 ? "" : "s"} from the AI plan.`);
    if (failed) {
      // Drop only what was saved; the unsaved tests (and their selection) stay for another try.
      const remap = new Map<number, number>();
      plan.tests.forEach((_, i) => { if (!saved.has(i)) remap.set(i, remap.size); });
      setPlan({ ...plan, tests: plan.tests.filter((_, i) => !saved.has(i)) });
      setKeep(new Set(Array.from(keep).filter((i) => remap.has(i)).map((i) => remap.get(i)!)));
    } else {
      setPlan(null);
    }
    onSaved();
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
            <div className="flex flex-wrap items-center gap-2">
              <Button disabled={saving || keep.size === 0} onClick={savePlan}>
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}Keep {keep.size} test{keep.size === 1 ? "" : "s"}
              </Button>
              {saveTarget}
            </div>
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
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" disabled={saving || !draft.trim()} onClick={saveOne}>{saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Save to suite</Button>
              <Button size="sm" variant="outline" onClick={() => copy("draft", draft)}>{copied === "draft" ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}Copy</Button>
              <Button size="sm" variant="ghost" disabled={asking} onClick={() => ask(question)}>Regenerate</Button>
              {saveTarget}
            </div>
          </div>
        )}
      </section>
      {error && <p role="alert" className="text-sm text-destructive xl:col-span-2">{error}</p>}
      {notice && <p role="status" className="text-sm text-success xl:col-span-2">{notice}</p>}
    </div>
  );
}
