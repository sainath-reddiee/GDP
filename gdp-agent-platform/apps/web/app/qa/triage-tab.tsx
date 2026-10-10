"use client";

import Link from "next/link";
import { useEffect, useState, useTransition } from "react";
import { Check, ExternalLink, Link2, Loader2, MessageSquarePlus, Search, Sparkles, Table2, TriangleAlert, Unlink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { CommentComposer, StatusPill, TransitionControl, when } from "@/components/jira/jira-controls";
import type { JiraIssueDetail, JiraStatus, Triage, TriageTest } from "../jira/actions";
import {
  createLink, deleteLink, issueDetail, listLinks, resolveTriage, saveTableTest, triageTable, type QaLink, type Resolved,
} from "./actions";
import {
  Alert, ConnectJira, Empty, JiraGate, Notice, Pending, SuiteSelect, TableSelect, jiraProblem, useLive, useSeq, useSuites, useTables,
  type Access, type Go, type JiraProblem, type Nav,
} from "./qa-shared";

const KEY = /^[A-Z][A-Z0-9_]+-\d+$/;

export function TriageTab({ nav, go, access, jira, me }: { nav: Nav; go: Go; access: Access; jira: JiraStatus | null; me: string }) {
  const [text, setText] = useState(nav.key);
  const key = text.trim().toUpperCase();
  return (
    <JiraGate jira={jira} returnTo={`/qa?tab=triage${nav.key ? `&key=${encodeURIComponent(nav.key)}` : ""}`}>
      <div className="space-y-4">
        <form className="flex max-w-lg gap-2" onSubmit={(e) => { e.preventDefault(); if (KEY.test(key)) go({ key }); }}>
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="Issue key, e.g. QA-123" aria-label="Issue key" className="pl-8 font-mono" />
          </div>
          <Button type="submit" disabled={!KEY.test(key)}>Open</Button>
        </form>
        {text.trim() && !KEY.test(key) && <p className="text-xs text-muted-foreground">An issue key looks like PROJECT-123.</p>}
        {nav.key ? <IssueTriage key={nav.key} issueKey={nav.key} go={go} access={access} me={me} />
          : <Empty title="Triage a Jira issue" text="Open an issue by key, or pick one in the Inbox. Find the table it is about, let AI propose tests that reproduce it, then save them into a suite and link the ticket." />}
      </div>
    </JiraGate>
  );
}

function IssueTriage({ issueKey, go, access, me }: { issueKey: string; go: Go; access: Access; me: string }) {
  const [issue, setIssue] = useState<JiraIssueDetail | null>(null);
  const [problem, setProblem] = useState<JiraProblem | null>(null);
  const [links, setLinks] = useState<QaLink[] | null>(null);
  const [linkError, setLinkError] = useState("");
  const [note, setNote] = useState("");
  const [resolved, setResolved] = useState<Resolved | null>(null);
  const [resolveError, setResolveError] = useState("");
  const [table, setTable] = useState("");
  const [triage, setTriage] = useState<Triage | null>(null);
  const [triageError, setTriageError] = useState("");
  const [comment, setComment] = useState<{ n: number; text: string } | null>(null);
  const [resolving, startResolve] = useTransition();
  const [triaging, startTriage] = useTransition();
  const [unlinking, startUnlink] = useTransition();
  const live = useLive();
  const triageSeq = useSeq();
  const linkSeq = useSeq();
  const { tables } = useTables("");

  const loadIssue = () => issueDetail(issueKey).then((r) => {
    if (!live.current) return;
    if (r.ok) { setIssue(r.data); setProblem(null); } else setProblem(jiraProblem(r));
  }, (e: unknown) => { if (live.current) setProblem({ connect: false, text: e instanceof Error ? e.message : "Could not open the issue." }); });
  const loadLinks = () => {
    const n = linkSeq.next();
    return listLinks({ key: issueKey }).then((r) => {
      if (!live.current || !linkSeq.current(n)) return;
      if (r.ok) { setLinks(r.data.links); setLinkError(""); } else { setLinks([]); setLinkError(r.error); }
    }, (e: unknown) => { if (live.current) setLinkError(e instanceof Error ? e.message : "Could not load the links."); });
  };
  useEffect(() => { void loadIssue(); void loadLinks(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [issueKey]);

  const resolve = () => startResolve(async () => {
    setResolveError("");
    const r = await resolveTriage(issueKey);
    if (!live.current) return;
    if (!r.ok) { setResolveError(jiraProblem(r).text); return; }
    setResolved(r.data);
    const best = r.data.candidates.find((c) => c.active) ?? r.data.candidates[0];
    if (best && !table) setTable(best.target_table_id);
  });
  const runTriage = () => {
    const n = triageSeq.next();
    const target = table;
    startTriage(async () => {
      setTriageError(""); setTriage(null);
      const r = await triageTable(target, issueKey);
      if (!triageSeq.current(n) || !live.current) return;
      if (r.ok) setTriage(r.data); else setTriageError(jiraProblem(r).text);
    });
  };
  const unlink = (id: string) => startUnlink(async () => {
    const r = await deleteLink(id);
    if (!live.current) return;
    if (!r.ok) { setLinkError(r.error); return; }
    await loadLinks();
  });

  if (problem?.connect) return <ConnectJira returnTo={`/qa?tab=triage&key=${encodeURIComponent(issueKey)}`} text={problem.text} />;
  if (problem && !issue) return <Empty title={`Could not open ${issueKey}`} text={<span role="alert">{problem.text}</span>} />;
  if (!issue) return <Pending text={`Opening ${issueKey}…`} />;

  const draftComment = () => {
    const lines = [`QA triage of ${issueKey}`];
    const fqn = tables?.find((t) => t.target_table_id === table)?.fqn ?? resolved?.candidates.find((c) => c.target_table_id === table)?.fqn;
    if (fqn) lines.push(`Table: ${fqn}`);
    if (triage) {
      lines.push(`What is reported: ${triage.diagnosis}`, `Likely cause: ${triage.likely_cause}`, `Severity: ${triage.severity.toLowerCase()}`);
      if (triage.questions.length) lines.push("", "Questions for the reporter:", ...triage.questions.map((q) => `* ${q}`));
    }
    setComment((c) => ({ n: (c?.n ?? 0) + 1, text: lines.join("\n") }));
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <section className="surface min-w-0 space-y-3 self-start p-5">
        <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span className="font-mono font-semibold text-foreground">{issue.key}</span><StatusPill name={issue.status} category={issue.status_category} />
          <span>{issue.type}</span>{issue.priority && <span>· {issue.priority}</span>}
          {issue.url && <a href={issue.url} target="_blank" rel="noreferrer" className="ml-auto inline-flex items-center gap-1 text-primary hover:underline">Open in Jira<ExternalLink className="h-3 w-3" /></a>}
        </p>
        <h3 className="text-base font-semibold">{issue.summary}</h3>
        <p className="text-xs text-muted-foreground">Reported by {issue.reporter ?? "someone"} · assigned to {issue.assignee ?? "nobody"} · updated {when(issue.updated)}</p>
        {access.canJiraWrite && (
          <div className="flex flex-wrap items-center gap-2">
            <TransitionControl issueKey={issueKey} onDone={(to) => { setNote(`${issueKey} moved to ${to}.`); void loadIssue(); }} />
            {!comment && <Button size="sm" variant="ghost" onClick={() => setComment({ n: 0, text: "" })}><MessageSquarePlus className="h-3.5 w-3.5" />Comment</Button>}
          </div>
        )}
        {note && <Notice onDismiss={() => setNote("")}>{note}</Notice>}
        {comment && access.canJiraWrite && (
          <div className="space-y-1.5 rounded-lg border p-3">
            <p className="flex items-center gap-2 text-xs font-medium">Comment on {issueKey}
              <button type="button" onClick={draftComment} className="ml-auto text-primary hover:underline">Draft from the triage</button></p>
            <CommentComposer key={comment.n} issueKey={issueKey} initial={comment.text} me={me} onCancel={() => setComment(null)}
                             onPosted={(t) => { setNote(t); setComment(null); void loadIssue(); }} />
            <p className="text-[11px] text-muted-foreground">Comments carry counts and findings only; never paste sample rows.</p>
          </div>
        )}
        <details className="text-sm" open={!!issue.description && issue.description.length < 1200}>
          <summary className="cursor-pointer text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Description</summary>
          {issue.description ? <div className="mt-1 max-h-80 overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-muted/30 p-3 text-[13px] leading-relaxed">{issue.description}</div>
            : <p className="mt-1 text-xs text-muted-foreground">No description.</p>}
        </details>
        <div>
          <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Linked in QA</p>
          {linkError && <Alert onDismiss={() => setLinkError("")}>{linkError}</Alert>}
          {!links && !linkError && <p className="text-xs text-muted-foreground">Loading links…</p>}
          <ul className="space-y-1">
            {(links ?? []).map((l) => (
              <li key={l.link_id} className="flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs">
                <span className="min-w-0 flex-1 truncate">
                  {l.target_table_id ? <button type="button" className="hover:text-primary" onClick={() => go({ tab: "suites", table: l.target_table_id!, suite: l.suite_id ?? "" })}>
                    {tables?.find((t) => t.target_table_id === l.target_table_id)?.fqn ?? l.target_table ?? "a table"}</button>
                    : l.run_id ? <Link href={`/runs/${l.run_id}/qa?tab=jira`} className="hover:text-primary">a run</Link> : "QA"}
                  {l.qa_test_id && <span className="text-muted-foreground"> · {l.test_title ?? `test ${l.qa_test_id.slice(0, 8)}`}</span>}
                  {(l.linked_by ?? l.created_by) && <span className="text-muted-foreground"> · {l.linked_by ?? l.created_by}</span>}
                </span>
                {access.canEdit && <button type="button" onClick={() => unlink(l.link_id)} disabled={unlinking} className="text-muted-foreground hover:text-destructive" aria-label="Unlink"><Unlink className="h-3.5 w-3.5" /></button>}
              </li>
            ))}
            {links && !links.length && <li className="text-xs text-muted-foreground">Not linked to any table, suite or test yet.</li>}
          </ul>
        </div>
      </section>

      <section className="min-w-0 space-y-4">
        <div className="surface space-y-3 p-5">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="text-sm font-semibold">Which table is this about?</h4>
            <Button size="sm" variant={resolved ? "ghost" : "default"} className="ml-auto" disabled={resolving} onClick={resolve}>
              {resolving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}{resolved ? "Find again" : "Find tables"}</Button>
          </div>
          {!resolved && !resolving && <p className="text-xs text-muted-foreground">Ranks target tables from existing links, table names in the ticket and an AI read of the report. You pick one.</p>}
          {resolveError && <Alert onDismiss={() => setResolveError("")}>{resolveError}</Alert>}
          {resolved && (
            <ul className="space-y-1.5">
              {resolved.candidates.map((c) => (
                <li key={c.target_table_id}>
                  <label className={cn("flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2", table === c.target_table_id ? "border-primary/40 bg-primary/5" : "hover:bg-muted/30")}>
                    <input type="radio" name="triage-table" className="mt-1" checked={table === c.target_table_id} onChange={() => { setTable(c.target_table_id); setTriage(null); }} />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-2 text-xs">
                        <span className="truncate font-mono font-medium">{c.fqn}</span>
                        {c.has_sttm ? <span className="rounded bg-emerald-50 px-1.5 text-[10px] text-emerald-700">STTM</span> : <span className="rounded bg-muted px-1.5 text-[10px] text-muted-foreground">no STTM</span>}
                        {!c.active && <span className="rounded bg-amber-50 px-1.5 text-[10px] text-amber-800">retired</span>}
                        <span className="ml-auto tabular-nums text-muted-foreground">score {c.score}</span>
                      </span>
                      {c.reasons.length > 0 && <span className="mt-0.5 block text-[11px] text-muted-foreground">{c.reasons.join("; ")}</span>}
                    </span>
                  </label>
                </li>
              ))}
              {!resolved.candidates.length && <li className="text-xs text-muted-foreground">No table matched this ticket. Pick one below.</li>}
            </ul>
          )}
          {(resolved || resolveError) && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground">Or choose any table:</span>
              <TableSelect tables={tables} value={resolved?.candidates.some((c) => c.target_table_id === table) ? "" : table}
                           onChange={(id) => { setTable(id); setTriage(null); }} className="max-w-md" />
            </div>
          )}
          {resolved && resolved.runs.length > 0 && (
            <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">Linked runs:
              {resolved.runs.map((r) => <Link key={r.run_id} href={`/runs/${r.run_id}/qa?tab=jira`} className="text-primary hover:underline">{r.name ?? r.run_id.slice(0, 8)}{r.state ? ` (${r.state.toLowerCase().replace(/_/g, " ")})` : ""}</Link>)}</p>
          )}
          {table && (
            <div className="flex flex-wrap items-center gap-2 border-t pt-3">
              <Button disabled={!access.canAI || triaging} onClick={runTriage}>
                {triaging ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}{triaging ? "Reading the report…" : triage ? "Triage again" : "Triage against this table"}</Button>
              {access.canEdit && <LinkTable issueKey={issueKey} tableId={table} onLinked={(t) => { setNote(t); void loadLinks(); }} />}
              {!access.canAI && <p className="text-xs text-muted-foreground">Your role cannot use AI (AI.USE).</p>}
            </div>
          )}
          {triageError && <Alert onDismiss={() => setTriageError("")}>{triageError}</Alert>}
        </div>
        {triage && <TriageResult issueKey={issueKey} tableId={table} triage={triage} canSave={access.canEdit}
                                 onSaved={(t) => { setNote(t); void loadLinks(); }} />}
      </section>
    </div>
  );
}

function LinkTable({ issueKey, tableId, onLinked }: { issueKey: string; tableId: string; onLinked: (text: string) => void }) {
  const [remote, setRemote] = useState(true);
  const [error, setError] = useState("");
  const [busy, start] = useTransition();
  const live = useLive();
  const link = () => start(async () => {
    setError("");
    const r = await createLink({ key: issueKey, target_table_id: tableId, remote_link: remote });
    if (!live.current) return;
    if (r.ok) onLinked(`${issueKey} linked to the table.`); else setError(jiraProblem(r).text);
  });
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Button variant="outline" disabled={busy} onClick={link}>{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}Link ticket to table</Button>
      <label className="flex items-center gap-1.5 text-xs text-muted-foreground"><input type="checkbox" checked={remote} onChange={() => setRemote(!remote)} />also add a link in Jira</label>
      {error && <span role="alert" className="text-xs text-destructive">{error}</span>}
    </span>
  );
}

function TriageResult({ issueKey, tableId, triage, canSave, onSaved }: {
  issueKey: string; tableId: string; triage: Triage; canSave: boolean; onSaved: (text: string) => void;
}) {
  const { suites } = useSuites(tableId);
  const [suiteId, setSuiteId] = useState("");
  const [remote, setRemote] = useState(true);
  const [saved, setSaved] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");
  const [busy, start] = useTransition();
  const live = useLive();
  const save = (t: TriageTest, i: number) => start(async () => {
    setError("");
    const r = await saveTableTest(tableId, { title: `${issueKey}: ${t.title}`.slice(0, 500), sql: t.sql, objective: t.objective, expected: t.expected,
                                             category: t.category, prompt: `Reproduce Jira ${issueKey}`, ...(suiteId ? { suite_id: suiteId } : {}) });
    if (!live.current) return;
    if (!r.ok) { setError(r.error); return; }
    const l = await createLink({ key: issueKey, target_table_id: tableId, suite_id: r.data.suite_id, test_id: r.data.test_id, remote_link: remote });
    if (!live.current) return;
    if (!l.ok) { setError(`The test was saved, but linking ${issueKey} failed: ${jiraProblem(l).text}`); return; }
    setSaved((s) => new Set(s).add(i));
    onSaved(`Saved "${t.title}" into the suite and linked it to ${issueKey}. Run it from Suites.`);
  });
  return (
    <div className="space-y-3 text-sm">
      <div className="space-y-2 rounded-xl border bg-muted/20 p-4">
        <p className="flex flex-wrap items-center gap-2 text-xs"><span className={cn("rounded-full px-2 py-0.5 font-medium", triage.reproducible ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700")}>
          {triage.reproducible ? "testable" : "needs more detail"}</span><span className="text-muted-foreground">severity {triage.severity.toLowerCase()} · {triage.model}</span></p>
        <p><span className="font-medium">What is reported: </span>{triage.diagnosis}</p>
        <p><span className="font-medium">Likely cause: </span>{triage.likely_cause}</p>
        {triage.affected_columns.length > 0 && <p className="text-xs"><span className="font-medium">Columns: </span><span className="font-mono">{triage.affected_columns.join(", ")}</span></p>}
        {triage.questions.length > 0 && <div className="text-xs"><p className="font-medium">Ask the reporter</p><ul className="list-inside list-disc text-muted-foreground">{triage.questions.map((q) => <li key={q}>{q}</li>)}</ul></div>}
      </div>
      {canSave && triage.tests.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Table2 className="h-3.5 w-3.5" />Save into<SuiteSelect suites={suites} value={suiteId} onChange={setSuiteId} />
          <label className="flex items-center gap-1.5"><input type="checkbox" checked={remote} onChange={() => setRemote(!remote)} />also add a link in Jira</label>
        </div>
      )}
      {triage.tests.map((t, i) => (
        <article key={i} className="rounded-xl border bg-card">
          <header className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
            <p className="text-sm font-medium">{t.title}</p>
            <span className="rounded bg-muted px-1.5 text-[10px]">{t.category.toLowerCase().replace(/_/g, " ")}</span>
            {t.valid ? <span className="rounded bg-emerald-50 px-1.5 text-[10px] text-emerald-700">compiles</span>
              : <span className="inline-flex items-center gap-1 rounded bg-rose-50 px-1.5 text-[10px] text-rose-700"><TriangleAlert className="h-3 w-3" />{t.compile_error ? "does not compile" : "rejected by the guard"}</span>}
            {canSave && (saved.has(i)
              ? <span className="ml-auto inline-flex items-center gap-1 text-xs text-emerald-700"><Check className="h-3.5 w-3.5" />saved and linked</span>
              : <Button size="sm" variant="outline" className="ml-auto" disabled={!t.valid || busy} onClick={() => save(t, i)}>{busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Save and link</Button>)}
          </header>
          <div className="space-y-1.5 px-3 py-2 text-xs">
            <p className="text-muted-foreground">{t.objective}</p>
            <p><span className="font-medium">Passes when: </span>{t.expected}</p>
            <pre className="max-h-56 overflow-auto rounded bg-muted/40 p-2 font-mono text-[11px]">{t.sql}</pre>
            {!t.valid && <p className="text-destructive">{t.compile_error ?? t.problems.join("; ")}</p>}
            {t.note && <p className="text-muted-foreground">{t.note}</p>}
          </div>
        </article>
      ))}
      {!triage.tests.length && <p className="text-xs text-muted-foreground">No test could be proposed from this report. Ask the reporter the questions above.</p>}
      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
    </div>
  );
}
