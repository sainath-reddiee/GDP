"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import { Bug, ChevronDown, ExternalLink, Loader2, MessageSquarePlus, RefreshCw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useScrollLock } from "@/components/use-scroll-lock";
import { OutcomePill, ResultPanel, SEVERITY_TONE } from "@/components/qa/qa-ui";
import { CommentComposer } from "@/components/jira/jira-controls";
import {
  createBug, issueTypes, listLinks, tableResults,
  type BugCreated, type IssueType, type QaLink, type TableResult, type TableResults,
} from "./actions";
import {
  Alert, Empty, Notice, Pending, SuiteSelect, TableSelect, jiraProblem, useLive, useSeq, useSuites, useTables,
  type Access, type Go, type Nav,
} from "./qa-shared";
import { caseFromResult } from "./cases/actions";
import { OpenCaseButton } from "./cases/case-ui";

const failing = (r: TableResult) => r.outcome === "FAIL" || r.outcome === "ERROR";

export function ResultsTab({ nav, go, access, me }: { nav: Nav; go: Go; access: Access; me: string }) {
  const { tables, error: tablesError } = useTables("");
  const { suites } = useSuites(nav.table);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <TableSelect tables={tables} value={nav.table} onChange={(id) => go({ table: id, suite: "" })} className="max-w-lg" />
        {nav.table && <SuiteSelect suites={suites} value={nav.suite} onChange={(id) => go({ suite: id })} allLabel="All suites" />}
      </div>
      {tablesError && <Alert>{tablesError}</Alert>}
      {nav.table ? <Results key={`${nav.table}:${nav.suite}`} tableId={nav.table} suiteId={nav.suite} access={access} me={me} go={go}
                            fqn={tables?.find((t) => t.target_table_id === nav.table)?.fqn ?? ""} />
        : <Empty title="Pick a target table" text="The latest result of every test on it shows here. A failing test can be opened as a case or filed as a Jira bug." />}
    </div>
  );
}

function Results({ tableId, suiteId, fqn, access, me, go }: { tableId: string; suiteId: string; fqn: string; access: Access; me: string; go: Go }) {
  const [data, setData] = useState<TableResults | null>(null);
  const [links, setLinks] = useState<QaLink[]>([]);
  const [error, setError] = useState("");
  const [open, setOpen] = useState("");
  const [bugFor, setBugFor] = useState<TableResult | null>(null);
  const [commentFor, setCommentFor] = useState("");
  const [note, setNote] = useState("");
  const [loading, startLoad] = useTransition();
  const seq = useSeq();
  const live = useLive();
  const load = () => {
    const n = seq.next();
    startLoad(async () => {
      const [r, l] = await Promise.all([tableResults(tableId, suiteId || undefined), listLinks({ targetTableId: tableId })]);
      if (!seq.current(n) || !live.current) return;
      if (r.ok) { setData(r.data); setError(""); } else { setError(r.error); setData((d) => d ?? { run: null, results: [] }); }
      if (l.ok) setLinks(l.data.links); else setError((e) => (e ? `${e}\n` : "") + `Jira links: ${l.error}`);
    });
  };
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);
  const keysByTest = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const l of links) if (l.qa_test_id && !(m.get(l.qa_test_id) ?? []).includes(l.issue_key)) m.set(l.qa_test_id, [...(m.get(l.qa_test_id) ?? []), l.issue_key]);
    return m;
  }, [links]);

  if (!data) return error ? <Alert>{error}</Alert> : <Pending text="Loading results…" />;
  const results = [...data.results].sort((a, b) => Number(failing(b)) - Number(failing(a)));
  const run = data.run;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {run ? <span>Last run {run.started_at.slice(0, 16)} by {run.created_by}: {run.passed} pass · {run.failed} fail · {run.review} to review · {run.not_run} not run{run.errors ? ` · ${run.errors} errors` : ""}</span>
          : <span>No QA run on this table{suiteId ? " and suite" : ""} yet. Run it from Suites.</span>}
        <Button size="sm" variant="ghost" className="ml-auto" disabled={loading} onClick={load}>{loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}Reload</Button>
        <Button size="sm" variant="ghost" onClick={() => go({ tab: "suites" })}>Open in Suites</Button>
      </div>
      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
      {note && <Notice onDismiss={() => setNote("")}>{note}</Notice>}
      <ul className="space-y-2">
        {results.map((r) => {
          const keys = keysByTest.get(r.test_id) ?? [];
          const isOpen = open === r.test_id;
          return (
            <li key={r.test_id} className={cn("overflow-hidden rounded-xl border border-l-4 bg-card shadow-sm",
                                             failing(r) ? "border-l-destructive" : r.outcome === "PASS" ? "border-l-success" : r.outcome === "REVIEW" ? "border-l-warning" : "border-l-primary/30")}>
              <div className="flex flex-wrap items-start gap-3 px-4 py-3">
                <button type="button" onClick={() => setOpen(isOpen ? "" : r.test_id)} aria-expanded={isOpen} className="min-w-0 flex-1 text-left">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{r.title ?? r.test_id}</span>
                    {r.severity && <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-semibold", SEVERITY_TONE[r.severity] ?? SEVERITY_TONE.MEDIUM)}>{r.severity}</span>}
                    {keys.map((k) => <span key={k} className="rounded bg-primary/10 px-1.5 font-mono text-[10px] text-primary">{k}</span>)}
                  </span>
                  {r.detail && <span className={cn("mt-0.5 block text-xs", failing(r) ? "text-destructive" : "text-muted-foreground")}>{r.detail}</span>}
                  {(r.measured || r.expected) && <span className="mt-0.5 block text-[11px] text-muted-foreground">Measured {r.measured ?? "nothing"} · expected {r.expected ?? "not set"}</span>}
                </button>
                <span className="flex shrink-0 items-center gap-2">
                  {access.canCase && failing(r) && r.result_id && <OpenCaseButton label="Open case" open={() => caseFromResult({ qa_result_id: r.result_id! })} />}
                  {access.canJiraWrite && failing(r) && <Button size="sm" variant="outline" onClick={() => setBugFor(r)}><Bug className="h-3.5 w-3.5" />Create Jira bug</Button>}
                  {access.canJiraWrite && keys.length > 0 && (
                    <Button size="sm" variant="ghost" onClick={() => setCommentFor(commentFor === r.test_id ? "" : r.test_id)}><MessageSquarePlus className="h-3.5 w-3.5" />Comment on linked ticket</Button>
                  )}
                  <OutcomePill outcome={r.outcome} />
                  <ChevronDown onClick={() => setOpen(isOpen ? "" : r.test_id)} className={cn("h-4 w-4 cursor-pointer text-muted-foreground transition", isOpen && "rotate-180")} />
                </span>
              </div>
              {commentFor === r.test_id && <LinkedComment result={r} keys={keys} fqn={fqn} me={me} onPosted={(t) => { setNote(t); setCommentFor(""); }} onCancel={() => setCommentFor("")} />}
              {isOpen && (
                <div className="space-y-2 border-t bg-muted/10 px-4 pb-4 pt-3">
                  <ResultPanel result={r} />
                  {r.sql_text && <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-xl bg-slate-950 p-3 font-mono text-[11px] text-slate-100">{r.sql_text}</pre>}
                  {r.created_at && <p className="text-[11px] text-muted-foreground">Result from {r.created_at.slice(0, 16)}</p>}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {!results.length && <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">No results yet.</p>}
      {bugFor && <BugDialog result={bugFor} tableId={tableId} fqn={fqn} qaRunId={bugFor.qa_run_id ?? run?.qa_run_id ?? null}
                            onClose={() => setBugFor(null)} onFiled={(b) => { setNote(b.existing && !b.created ? `Already filed: ${b.key}` : `Created ${b.key}`); load(); }} />}
    </div>
  );
}

// a count ("3", "12+ rows", "2 values differ", "not 0"); anything else may be a data value and stays out of Jira
const COUNT_TEXT = /^\s*(?:-?\d+\+?(?:\s+(?:rows?|values differ))?|not 0)\s*$/i;

function resultText(r: TableResult, fqn: string) {
  // counts only: the result detail can name compared values, which must not be copied into Jira
  const lines = [`QA result for "${r.title ?? r.test_id}"${fqn ? ` on ${fqn}` : ""}: ${r.outcome}`];
  const measured = r.measured != null && COUNT_TEXT.test(String(r.measured)) ? String(r.measured) : null;
  if (measured || r.expected) lines.push(`Measured: ${measured ?? "see the QA workspace"}. Expected: ${r.expected ?? "not set"}.`);
  if (r.rows_returned != null) lines.push(`Rows returned: ${r.rows_returned}`);
  if (r.created_at) lines.push(`Checked ${r.created_at.slice(0, 16)}`);
  return lines.join("\n");
}

function LinkedComment({ result, keys, fqn, me, onPosted, onCancel }: {
  result: TableResult; keys: string[]; fqn: string; me: string; onPosted: (text: string) => void; onCancel: () => void;
}) {
  const [key, setKey] = useState(keys[0] ?? "");
  return (
    <div className="space-y-2 border-t px-4 py-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium">Comment on</span>
        {keys.length > 1
          ? <Select value={key} onChange={(e) => setKey(e.target.value)} className="h-8 w-auto text-xs" aria-label="Linked ticket">{keys.map((k) => <option key={k}>{k}</option>)}</Select>
          : <span className="font-mono">{key}</span>}
        <span className="text-muted-foreground">Counts and findings only; sample rows are left out on purpose.</span>
      </div>
      <CommentComposer key={key} issueKey={key} initial={resultText(result, fqn)} me={me} rows={6} onPosted={onPosted} onCancel={onCancel} />
    </div>
  );
}

function BugDialog({ result, tableId, fqn, qaRunId, onClose, onFiled }: {
  result: TableResult; tableId: string; fqn: string; qaRunId: string | null; onClose: () => void; onFiled: (b: BugCreated) => void;
}) {
  // one key per dialog: submitting twice (or retrying after a timeout) returns the first issue instead of a duplicate
  const [idempotencyKey] = useState(() => (typeof crypto.randomUUID === "function" ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`)); // randomUUID needs https or localhost
  const [project, setProject] = useState("");
  const [types, setTypes] = useState<IssueType[] | null>(null);
  const [typeId, setTypeId] = useState("");
  const [typesError, setTypesError] = useState("");
  const [summary, setSummary] = useState("");
  const [filed, setFiled] = useState<BugCreated | null>(null);
  const [error, setError] = useState("");
  const [loadingTypes, startTypes] = useTransition();
  const [busy, start] = useTransition();
  const typeSeq = useSeq();
  const live = useLive();
  useScrollLock();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const loadTypes = (p: string) => {
    const n = typeSeq.next();
    setTypes(null); setTypeId(""); setTypesError("");
    if (!p) return;
    startTypes(async () => {
      const r = await issueTypes(p);
      if (!typeSeq.current(n) || !live.current) return;
      if (!r.ok) { setTypesError(jiraProblem(r).text); return; }
      const list = r.data.types.filter((t) => !t.subtask);
      setTypes(list);
      setTypeId(list.find((t) => /bug/i.test(t.name))?.id ?? "");
    });
  };
  const submit = () => start(async () => {
    setError("");
    const r = await createBug({
      test_id: result.test_id, target_table_id: tableId, qa_run_id: qaRunId, idempotency_key: idempotencyKey,
      ...(project ? { project_key: project } : {}), ...(project && typeId ? { issue_type_id: typeId } : {}),
      ...(summary.trim() ? { summary: summary.trim() } : {}),
    });
    if (!live.current) return;
    if (!r.ok) { setError(jiraProblem(r).text); return; }
    setFiled(r.data);
    onFiled(r.data);
  });

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-labelledby="bug-title" className="w-full max-w-lg space-y-3 rounded-2xl border bg-card p-5 shadow-xl">
        <div className="flex items-start gap-2">
          <Bug className="mt-0.5 h-5 w-5 text-destructive" />
          <div className="min-w-0 flex-1">
            <h3 id="bug-title" className="text-base font-semibold">Create a Jira bug</h3>
            <p className="truncate text-xs text-muted-foreground">{result.title ?? result.test_id}{fqn ? ` on ${fqn}` : ""}</p>
          </div>
          <button type="button" aria-label="Close" disabled={busy} onClick={onClose} className="rounded p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
        </div>
        {filed ? (
          <div className="space-y-3">
            <p role="status" className="rounded-lg bg-success/10 px-3 py-2 text-sm text-success">
              {filed.existing && !filed.created ? "Already filed: " : "Created "}
              {filed.url ? <a href={filed.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono font-medium underline">{filed.key}<ExternalLink className="h-3 w-3" /></a>
                : <span className="font-mono font-medium">{filed.key}</span>}
            </p>
            <div className="flex justify-end"><Button onClick={onClose}>Done</Button></div>
          </div>
        ) : (
          <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); submit(); }}>
            <p className="text-xs text-muted-foreground">The bug carries counts, expected and measured values, severity and the table. Sample rows are never included.
              It is filed as you and linked back to the test.</p>
            <label className="block text-xs font-medium">Project key
              <Input value={project} onChange={(e) => { setProject(e.target.value.toUpperCase()); setTypes(null); setTypeId(""); }} onBlur={() => loadTypes(project.trim())}
                     placeholder="Leave blank for the configured default" aria-label="Project key" className="mt-1 font-mono" />
            </label>
            {project.trim() && (
              <label className="block text-xs font-medium">Issue type
                <Select value={typeId} onChange={(e) => setTypeId(e.target.value)} className="mt-1" aria-label="Issue type" disabled={!types}>
                  <option value="">{loadingTypes ? "Loading types…" : types ? "Default type" : "Leave the project field to load its types"}</option>
                  {(types ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </Select>
              </label>
            )}
            {typesError && <Alert>{typesError}</Alert>}
            <label className="block text-xs font-medium">Summary (optional)
              <Input value={summary} onChange={(e) => setSummary(e.target.value)} maxLength={255} placeholder="Written from the test when left blank" className="mt-1" />
            </label>
            {error && <Alert>{error}</Alert>}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button>
              <Button type="submit" disabled={busy || loadingTypes}>{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bug className="h-4 w-4" />}Create bug</Button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
