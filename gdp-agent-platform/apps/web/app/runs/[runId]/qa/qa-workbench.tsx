"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Bug, Check, Copy, Download, FlaskConical, History, KeyRound, ListChecks, Loader2, Play, Search, Sparkles, Table2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  askQa, deleteQaTest, planQa, runQa, saveQaTest, updateQaTest,
  type QaOutcome, type QaResult, type QaResults, type QaSuite, type QaTest,
} from "./qa-actions";
import { useAccess } from "@/components/access";
import { BAR, CATEGORY_META, RunHistoryTable, useCopy } from "@/components/qa/qa-ui";
import { QaAssistant } from "@/components/qa/qa-assistant";
import { TestCard, type CardOutcome } from "@/components/qa/test-card";
import { JiraPanel } from "./jira-panel";
import { caseFromResult } from "../../../qa/cases/actions";
import { OpenCaseButton } from "../../../qa/cases/case-ui";

/** The run lane's test card: the shared card wired to this run's QA endpoints. */
function RunTestCard({ runId, test, canEdit = true, canCase = false, ...rest }: {
  runId: string; test: QaTest; result?: QaResult; history: { outcome: QaOutcome; at: string }[]; open: boolean;
  onToggle: () => void; selected: boolean; onSelect: () => void; canRun: boolean; canEdit?: boolean; /** CASE.WORK */ canCase?: boolean;
}) {
  const router = useRouter();
  const done = (r: { ok: true } | { ok: false; error: string }): CardOutcome => {
    if (r.ok) router.refresh();
    return r.ok ? { ok: true } : { ok: false, error: r.error };
  };
  const resultId = rest.result?.result_id;
  const failing = rest.result?.outcome === "FAIL" || rest.result?.outcome === "ERROR";
  return (
    <TestCard {...rest} test={test}
              extra={canCase && resultId && failing
                ? <div className="flex justify-end"><OpenCaseButton label="Open case" open={() => caseFromResult({ qa_result_id: resultId })} /></div> : undefined}
              editable={test.origin !== "GENERATED" && test.scope !== "TABLE" && canEdit}
              onRun={async () => done(await runQa(runId, [test.test_id]))}
              onUpdate={async (e) => done(await updateQaTest(runId, test.test_id, { title: e.title, sql: e.sql, expected: e.expected, objective: e.objective, severity: e.severity }))}
              onRemove={async () => done(await deleteQaTest(runId, test.test_id))} />
  );
}

export function QaWorkbench({ runId, suite, results, canRun, canAI = true, canEdit = true }: {
  runId: string; suite: QaSuite; results: QaResults; canRun: boolean; canAI?: boolean; canEdit?: boolean;
}) {
  const router = useRouter();
  const { copied, copy } = useCopy();
  const params = useSearchParams();
  const { can, canAct } = useAccess();
  const [tab, setTab] = useState<"tests" | "ai" | "history" | "jira">(params.get("tab") === "jira" ? "jira" : "tests");
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
    { const done = url; setTimeout(() => URL.revokeObjectURL(done), 1000); }
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
        {([["tests", "Tests", ListChecks], ["ai", "AI assistant", Sparkles], ["history", "Run history", History], ["jira", "Jira", Bug]] as const).map(([k, label, Icon]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                  className={cn("-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium", tab === k ? "border-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>
            <Icon className="h-4 w-4" />{label}
            {k === "history" && results.runs.length > 0 && <span className="rounded-full bg-muted px-1.5 text-[11px]">{results.runs.length}</span>}
          </button>
        ))}
      </nav>

      {tab === "ai" && <QaAssistant canAI={canAI && canEdit} ask={(q) => askQa(runId, q)} plan={(f) => planQa(runId, f)} save={(t) => saveQaTest(runId, t)}
                                    onSaved={() => { setTab("tests"); setCategory("ALL"); router.refresh(); }} />}
      {tab === "history" && <RunHistoryTable runs={results.runs} ready={results.ready} />}
      {tab === "jira" && <JiraPanel runId={runId} savedTests={suite.tests.filter((t) => t.origin !== "GENERATED" && t.scope !== "TABLE").map((t) => ({ test_id: t.test_id, title: t.title }))}
                                    canWrite={canAct("JIRA.WRITE") && canEdit} canAI={canAI && canAct("AI.USE")} />}

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
              <RunTestCard key={t.test_id} runId={runId} test={t} result={byTest.get(t.test_id)} history={results.history[t.test_id] ?? []}
                        open={open === t.test_id} onToggle={() => setOpen(open === t.test_id ? "" : t.test_id)} canRun={canRun} canEdit={canEdit} canCase={can("CASE.WORK")}
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
