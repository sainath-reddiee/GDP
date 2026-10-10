"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import {
  ArrowRightLeft, Check, CheckCircle2, Filter, KanbanSquare, Link2, ListFilter, Loader2, MessageSquarePlus, Play, RefreshCw,
  Save, Search, Trash2, User, X, XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { StatusPill } from "@/components/jira/jira-controls";
import type { JiraStatus } from "../jira/actions";
import {
  boardSprints, bulkJira, deleteFilter, listBoards, listFilters, saveFilter, searchIssues, sprintIssues, validateJql,
  type Board, type BulkAction, type BulkResult, type IssuePage, type IssueSummary, type Result, type SavedFilter, type Sprint,
} from "./actions";
import {
  Alert, ConnectJira, JiraGate, Notice, SuiteSelect, TableSelect, jiraProblem, plural, useLive, useSeq, useSuites, useTables,
  type Access, type Go, type JiraProblem,
} from "./qa-shared";

type Mode = "mine" | "jql" | "filters" | "boards";
type Source = { kind: "jql"; jql: string; label: string } | { kind: "filter"; filterId: string; label: string }
  | { kind: "sprint"; sprintId: string; label: string };

// empty: the API's own "my open issues" query
const MINE_JQL = "";
const MAX_BULK = 50;
const COMMON_TRANSITIONS = ["To Do", "In Progress", "In Review", "Ready for QA", "In QA", "QA Passed", "QA Failed", "Reopened", "Done"];
const RETURN_TO = "/qa?tab=inbox";

export function InboxTab({ jira, access, go }: { jira: JiraStatus | null; access: Access; go: Go }) {
  return (
    <JiraGate jira={jira} returnTo={RETURN_TO}>
      <Inbox access={access} go={go} who={jira?.connected ? `${jira.connected.display_name ?? "you"} on ${(jira.connected.site_url ?? "").replace(/^https:\/\//, "")}` : ""} />
    </JiraGate>
  );
}

function Inbox({ access, go, who }: { access: Access; go: Go; who: string }) {
  const [mode, setMode] = useState<Mode>("mine");
  const [source, setSource] = useState<Source>({ kind: "jql", jql: MINE_JQL, label: "Assigned to me" });
  const [issues, setIssues] = useState<IssueSummary[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [problem, setProblem] = useState<JiraProblem | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, startLoad] = useTransition();
  const [paging, startPage] = useTransition();
  const seq = useSeq();

  const fetchPage = (s: Source, cursor: string | null): Promise<Result<IssuePage>> =>
    s.kind === "sprint" ? sprintIssues(s.sprintId, cursor)
      : s.kind === "filter" ? searchIssues({ filterId: s.filterId, next: cursor }) : searchIssues({ jql: s.jql, next: cursor });

  const load = (s: Source) => {
    const n = seq.next();
    setSource(s); setProblem(null); setSelected(new Set()); setIssues(null); setNext(null);
    startLoad(async () => {
      const r = await fetchPage(s, null);
      if (!seq.current(n)) return;
      if (!r.ok) { setProblem(jiraProblem(r)); setIssues([]); return; }
      setIssues(r.data.issues); setNext(r.data.next);
    });
  };
  const more = () => {
    if (!next) return;
    const n = seq.next();
    const cursor = next;
    startPage(async () => {
      const r = await fetchPage(source, cursor);
      if (!seq.current(n)) return;
      if (!r.ok) { setProblem(jiraProblem(r)); return; }
      setIssues((list) => {
        const have = new Set((list ?? []).map((i) => i.key));
        return [...(list ?? []), ...r.data.issues.filter((i) => !have.has(i.key))];
      });
      setNext(r.data.next);
    });
  };
  useEffect(() => { load({ kind: "jql", jql: MINE_JQL, label: "Assigned to me" }); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const toggle = (key: string) => setSelected((s) => { const n = new Set(s); if (n.has(key)) n.delete(key); else n.add(key); return n; });
  const all = issues ?? [];
  const allSelected = all.length > 0 && all.every((i) => selected.has(i.key));

  if (problem?.connect) return <ConnectJira returnTo={RETURN_TO} text={problem.text} title={/reconnect/i.test(problem.text) ? "Reconnect Jira" : undefined} />;

  return (
    <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
      <aside className="surface space-y-3 self-start p-3">
        <div className="grid grid-cols-2 gap-1 rounded-lg border p-0.5 text-xs" role="tablist" aria-label="Inbox source">
          {([["mine", "Assigned to me", User], ["jql", "JQL", Filter], ["filters", "Saved filters", ListFilter], ["boards", "Boards", KanbanSquare]] as const).map(([k, l, Icon]) => (
            <button key={k} type="button" role="tab" aria-selected={mode === k}
                    onClick={() => { setMode(k); if (k === "mine") load({ kind: "jql", jql: MINE_JQL, label: "Assigned to me" }); }}
                    className={cn("inline-flex items-center justify-center gap-1.5 rounded-md px-2 py-1.5", mode === k ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted")}>
              <Icon className="h-3.5 w-3.5" />{l}</button>
          ))}
        </div>
        {mode === "mine" && <p className="text-xs text-muted-foreground">Open issues assigned to you, most recently updated first.</p>}
        {mode === "jql" && <JqlPane onRun={(jql) => load({ kind: "jql", jql, label: "JQL" })} initial={source.kind === "jql" ? source.jql : ""} />}
        {mode === "filters" && <FiltersPane onRun={(f) => load({ kind: "filter", filterId: f.filter_id, label: f.name })} activeId={source.kind === "filter" ? source.filterId : ""} />}
        {mode === "boards" && <BoardsPane onSprint={(s, b) => load({ kind: "sprint", sprintId: String(s.id), label: `${b.name}, ${s.name}` })}
                                          activeId={source.kind === "sprint" ? source.sprintId : ""} />}
        {who && <p className="border-t pt-2 text-[11px] text-muted-foreground">As {who}</p>}
      </aside>

      <section className="min-w-0 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold">{source.label}</h3>
          {issues && <span className="text-xs text-muted-foreground">{plural(issues.length, "issue")}{next ? " so far" : ""}</span>}
          <Button size="sm" variant="ghost" className="ml-auto" disabled={loading} onClick={() => load(source)} aria-label="Reload">
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}Reload</Button>
        </div>
        {problem && <Alert onDismiss={() => setProblem(null)}>{problem.text}</Alert>}
        {selected.size > 0 && (
          <BulkBar keys={Array.from(selected)} access={access} onClear={() => setSelected(new Set())}
                   onApplied={(action) => { if (action !== "comment") load(source); }} />
        )}
        <div className="overflow-x-auto rounded-xl border bg-card">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="w-8 px-3 py-2"><input type="checkbox" aria-label="Select all" checked={allSelected} disabled={!all.length}
                                                    onChange={() => setSelected(allSelected ? new Set() : new Set(all.map((i) => i.key)))} /></th>
                <th className="px-2 py-2">Key</th><th className="px-2 py-2">Summary</th><th className="px-2 py-2">Type</th><th className="px-2 py-2">Status</th>
                <th className="px-2 py-2">Priority</th><th className="px-2 py-2">Assignee</th><th className="px-2 py-2 text-right">Linked</th>
              </tr>
            </thead>
            <tbody>
              {all.map((i) => (
                <tr key={i.key} className={cn("border-t hover:bg-muted/30", selected.has(i.key) && "bg-primary/5")}>
                  <td className="px-3 py-2"><input type="checkbox" aria-label={`Select ${i.key}`} checked={selected.has(i.key)} onChange={() => toggle(i.key)} /></td>
                  <td className="whitespace-nowrap px-2 py-2">
                    <button type="button" onClick={() => go({ tab: "triage", key: i.key })} className="font-mono text-xs font-medium text-primary hover:underline" title="Open in Triage">{i.key}</button>
                  </td>
                  <td className="max-w-[28rem] px-2 py-2"><button type="button" onClick={() => go({ tab: "triage", key: i.key })} className="line-clamp-2 text-left hover:text-primary">{i.summary}</button></td>
                  <td className="whitespace-nowrap px-2 py-2 text-xs text-muted-foreground">{i.type}</td>
                  <td className="px-2 py-2"><StatusPill name={i.status} category={i.status_category} /></td>
                  <td className="whitespace-nowrap px-2 py-2 text-xs text-muted-foreground">{i.priority}</td>
                  <td className="whitespace-nowrap px-2 py-2 text-xs text-muted-foreground">{i.assignee ?? "unassigned"}</td>
                  <td className="px-2 py-2 text-right text-xs tabular-nums">{i.linked ? <span className="inline-flex items-center gap-1 text-primary"><Link2 className="h-3 w-3" />{i.linked}</span> : <span className="text-muted-foreground">0</span>}</td>
                </tr>
              ))}
              {loading && !issues && <tr><td colSpan={8} className="px-3 py-8 text-center text-xs text-muted-foreground"><Loader2 className="mr-2 inline h-3.5 w-3.5 animate-spin" />Loading issues…</td></tr>}
              {issues && !issues.length && !problem && <tr><td colSpan={8} className="px-3 py-8 text-center text-xs text-muted-foreground">No issues here.</td></tr>}
            </tbody>
          </table>
        </div>
        {next && (
          <div className="flex justify-center">
            <Button size="sm" variant="outline" disabled={paging || loading} onClick={more}>{paging && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Load more</Button>
          </div>
        )}
      </section>
    </div>
  );
}

function JqlPane({ onRun, initial }: { onRun: (jql: string) => void; initial: string }) {
  const [jql, setJql] = useState(initial);
  const [check, setCheck] = useState<{ ok: boolean; errors: string[] } | null>(null);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [shared, setShared] = useState(false);
  const [saved, setSaved] = useState("");
  const [validating, startValidate] = useTransition();
  const [saving, startSave] = useTransition();
  const seq = useSeq();
  const live = useLive();
  const validate = () => {
    const n = seq.next();
    startValidate(async () => {
      setError(""); setCheck(null);
      const r = await validateJql(jql);
      if (!seq.current(n)) return;
      if (r.ok) setCheck(r.data); else setError(jiraProblem(r).text);
    });
  };
  const save = () => startSave(async () => {
    setError(""); setSaved("");
    const r = await saveFilter({ name: name.trim(), jql, shared });
    if (!live.current) return;
    if (!r.ok) { setError(jiraProblem(r).text); return; }
    setSaved(`Saved "${name.trim()}". Find it under Saved filters.`); setName("");
  });
  return (
    <div className="space-y-2">
      <Textarea rows={4} value={jql} onChange={(e) => { setJql(e.target.value); setCheck(null); }} spellCheck={false}
                placeholder='project = QA AND status = "Ready for QA" ORDER BY priority DESC' aria-label="JQL" className="font-mono text-xs" />
      <div className="flex gap-2">
        <Button size="sm" variant="outline" disabled={!jql.trim() || validating} onClick={validate}>{validating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Validate</Button>
        <Button size="sm" className="ml-auto" disabled={!jql.trim()} onClick={() => onRun(jql.trim())}><Play className="h-3.5 w-3.5" />Run</Button>
      </div>
      {check && (check.ok
        ? <p role="status" className="flex items-center gap-1.5 text-xs text-success"><CheckCircle2 className="h-3.5 w-3.5" />Valid JQL.</p>
        : <div role="alert" className="space-y-0.5 rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">{check.errors.map((e) => <p key={e}>{e}</p>)}</div>)}
      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
      <div className="space-y-1.5 border-t pt-2">
        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Save as a filter</p>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Filter name" aria-label="Filter name" className="h-8 text-xs" />
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground"><input type="checkbox" checked={shared} onChange={() => setShared(!shared)} />Shared with everyone</label>
          <Button size="sm" variant="outline" className="ml-auto" disabled={!name.trim() || !jql.trim() || saving} onClick={save}>
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}Save</Button>
        </div>
        {saved && <Notice onDismiss={() => setSaved("")}>{saved}</Notice>}
      </div>
    </div>
  );
}

function FiltersPane({ onRun, activeId }: { onRun: (f: SavedFilter) => void; activeId: string }) {
  const [filters, setFilters] = useState<SavedFilter[] | null>(null);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, start] = useTransition();
  const live = useLive();
  const refresh = () => start(async () => {
    const r = await listFilters();
    if (!live.current) return;
    if (r.ok) { setFilters(r.data.filters); setError(""); } else { setError(jiraProblem(r).text); setFilters([]); }
  });
  useEffect(() => { refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);
  const remove = (id: string) => start(async () => {
    const r = await deleteFilter(id);
    if (!live.current) return;
    setConfirm("");
    if (!r.ok) { setError(jiraProblem(r).text); return; }
    setFilters((f) => (f ?? []).filter((x) => x.filter_id !== id));
  });
  return (
    <div className="space-y-2">
      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
      {!filters && <p className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading filters…</p>}
      {filters && !filters.length && <p className="text-xs text-muted-foreground">No saved filters yet. Write a JQL query and save it.</p>}
      <ul className="space-y-1">
        {(filters ?? []).map((f) => (
          <li key={f.filter_id} className={cn("rounded-lg border px-2.5 py-2", activeId === f.filter_id && "border-primary/40 bg-primary/5")}>
            <div className="flex items-center gap-2">
              <button type="button" onClick={() => onRun(f)} className="min-w-0 flex-1 truncate text-left text-sm font-medium hover:text-primary" title={f.jql}>{f.name}</button>
              {f.shared && <span className="rounded bg-muted px-1.5 text-[10px] text-muted-foreground">shared</span>}
              {f.mine && (confirm === f.filter_id
                ? <span className="inline-flex items-center gap-1">
                    <Button size="sm" variant="destructive" className="h-6 px-2 text-[11px]" disabled={busy} onClick={() => remove(f.filter_id)}>Delete</Button>
                    <button type="button" aria-label="Cancel" onClick={() => setConfirm("")}><X className="h-3.5 w-3.5" /></button>
                  </span>
                : <button type="button" aria-label={`Delete ${f.name}`} onClick={() => setConfirm(f.filter_id)} className="text-muted-foreground hover:text-destructive"><Trash2 className="h-3.5 w-3.5" /></button>)}
            </div>
            <p className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground">{f.jql}</p>
            {!f.mine && <p className="text-[10px] text-muted-foreground">by {f.owner}</p>}
          </li>
        ))}
      </ul>
      <Button size="sm" variant="ghost" disabled={busy} onClick={refresh}>{busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}Refresh</Button>
    </div>
  );
}

function BoardsPane({ onSprint, activeId }: { onSprint: (s: Sprint, b: Board) => void; activeId: string }) {
  const [q, setQ] = useState("");
  const [boards, setBoards] = useState<Board[] | null>(null);
  const [board, setBoard] = useState<Board | null>(null);
  const [sprints, setSprints] = useState<Sprint[] | null>(null);
  const [problem, setProblem] = useState<JiraProblem | null>(null);
  const [searching, startSearch] = useTransition();
  const [opening, startOpen] = useTransition();
  const boardSeq = useSeq();
  const sprintSeq = useSeq();
  const search = (text = q) => {
    const n = boardSeq.next();
    startSearch(async () => {
      setProblem(null);
      const r = await listBoards(text);
      if (!boardSeq.current(n)) return;
      if (r.ok) setBoards(r.data.boards); else { setProblem(jiraProblem(r)); setBoards([]); }
    });
  };
  useEffect(() => { search(""); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);
  const open = (b: Board) => {
    const n = sprintSeq.next();
    setBoard(b); setSprints(null);
    startOpen(async () => {
      setProblem(null);
      const r = await boardSprints(String(b.id));
      if (!sprintSeq.current(n)) return;
      if (r.ok) setSprints(r.data.sprints); else { setProblem(jiraProblem(r)); setSprints([]); }
    });
  };
  if (problem?.connect) return <ConnectJira returnTo={RETURN_TO} text={problem.text} title="Reconnect Jira" />;
  return (
    <div className="space-y-2">
      {problem && <Alert onDismiss={() => setProblem(null)}>{problem.text}</Alert>}
      {!board ? (
        <>
          <form className="relative" onSubmit={(e) => { e.preventDefault(); search(); }}>
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search boards" aria-label="Search boards" className="h-8 pl-8 text-xs" />
          </form>
          {searching && !boards && <p className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading boards…</p>}
          <ul className="max-h-80 space-y-0.5 overflow-y-auto">
            {(boards ?? []).map((b) => (
              <li key={String(b.id)}>
                <button type="button" onClick={() => open(b)} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-muted">
                  <KanbanSquare className="h-3.5 w-3.5 shrink-0 text-muted-foreground" /><span className="min-w-0 flex-1 truncate">{b.name}</span>
                  <span className="text-[10px] text-muted-foreground">{b.project_key ?? ""} {b.type}</span>
                </button>
              </li>
            ))}
            {boards && !boards.length && !problem && <li className="px-2 py-3 text-xs text-muted-foreground">No boards match.</li>}
          </ul>
        </>
      ) : (
        <>
          <p className="flex items-center gap-2 text-sm font-medium"><KanbanSquare className="h-4 w-4 text-muted-foreground" /><span className="min-w-0 flex-1 truncate">{board.name}</span>
            <button type="button" className="text-xs text-primary hover:underline" onClick={() => { setBoard(null); setSprints(null); }}>All boards</button></p>
          {opening && !sprints && <p className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading sprints…</p>}
          <ul className="space-y-0.5">
            {(sprints ?? []).map((s) => (
              <li key={String(s.id)}>
                <button type="button" onClick={() => onSprint(s, board)}
                        className={cn("flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-muted", activeId === String(s.id) && "bg-primary/10 text-primary")}>
                  <span className="min-w-0 flex-1 truncate">{s.name}</span>
                  <span className={cn("rounded px-1.5 text-[10px]", s.state === "active" ? "bg-emerald-50 text-emerald-700" : "bg-muted text-muted-foreground")}>{s.state}</span>
                </button>
              </li>
            ))}
            {sprints && !sprints.length && !problem && <li className="px-2 py-3 text-xs text-muted-foreground">No active or future sprints on this board.</li>}
          </ul>
        </>
      )}
    </div>
  );
}

function newKey() {
  // randomUUID needs https or localhost
  return typeof crypto.randomUUID === "function" ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

function BulkBar({ keys, access, onClear, onApplied }: { keys: string[]; access: Access; onClear: () => void; onApplied: (action: BulkAction) => void }) {
  const [action, setAction] = useState<BulkAction | "">("");
  const [comment, setComment] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [transition, setTransition] = useState("");
  const [tableId, setTableId] = useState("");
  const [suiteId, setSuiteId] = useState("");
  const [results, setResults] = useState<BulkResult[] | null>(null);
  const [error, setError] = useState("");
  const [busy, start] = useTransition();
  const live = useLive();
  const { tables, error: tablesError } = useTables("", action === "link");
  const { suites } = useSuites(action === "link" ? tableId : "");
  const tooMany = keys.length > MAX_BULK;
  // one idempotency key per bulk submit: retrying the same comment on the same issues (after a timeout or a partial
  // failure) skips the issues it was already posted to; a new comment or selection is a new submit
  const [submits, setSubmits] = useState(0);
  const keysId = keys.join(",");
  const bulkKey = useMemo(() => newKey(), [action, comment, keysId, submits]); // eslint-disable-line react-hooks/exhaustive-deps

  const apply = () => start(async () => {
    if (!action) return;
    setError(""); setResults(null);
    const r = await bulkJira({
      action, keys,
      ...(action === "comment" ? { comment, idempotency_key: bulkKey } : {}),
      ...(action === "transition" ? { transition_name: transition.trim() } : {}),
      ...(action === "link" ? { link: { target_table_id: tableId, ...(suiteId ? { suite_id: suiteId } : {}) } } : {}),
    });
    if (!live.current) return;
    setConfirming(false);
    if (!r.ok) { setError(jiraProblem(r).text); return; }
    setResults(r.data.results);
    if (r.data.results.every((x) => x.ok)) setSubmits((n) => n + 1);   // done: the next submit is a new one
    if (r.data.results.some((x) => x.ok)) onApplied(action);
  });
  const ready = !tooMany && (action === "comment" ? !!comment.trim() : action === "transition" ? !!transition.trim() : action === "link" ? !!tableId : false);
  if (!access.canJiraWrite) {
    return <p className="rounded-xl border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">{plural(keys.length, "issue")} selected. Your role cannot change Jira issues (JIRA.WRITE).</p>;
  }
  return (
    <div className="space-y-2 rounded-xl border border-primary/30 bg-primary/5 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{plural(keys.length, "issue")} selected</span>
        {([["link", "Link to table", Link2], ["comment", "Comment", MessageSquarePlus], ["transition", "Transition", ArrowRightLeft]] as const)
          .filter(([k]) => k !== "link" || access.canEdit)
          .map(([k, l, Icon]) => (
            <Button key={k} size="sm" variant={action === k ? "default" : "outline"} onClick={() => { setAction(action === k ? "" : k); setResults(null); setConfirming(false); setError(""); }}>
              <Icon className="h-3.5 w-3.5" />{l}</Button>
          ))}
        <Button size="sm" variant="ghost" className="ml-auto" onClick={onClear}>Clear</Button>
      </div>
      {tooMany && <Alert>Select at most {MAX_BULK} issues per action.</Alert>}
      {action === "link" && (
        <div className="flex flex-wrap items-center gap-2">
          <TableSelect tables={tables} value={tableId} onChange={(id) => { setTableId(id); setSuiteId(""); }} className="max-w-md" />
          {tableId && <SuiteSelect suites={suites} value={suiteId} onChange={setSuiteId} allLabel="No suite" />}
          {tablesError && <Alert>{tablesError}</Alert>}
        </div>
      )}
      {action === "comment" && (
        <Textarea rows={4} value={comment} onChange={(e) => { setComment(e.target.value); setConfirming(false); }} placeholder="Comment for every selected issue (Markdown)"
                  aria-label="Bulk comment" className="text-xs" />
      )}
      {action === "transition" && (
        <div className="flex flex-wrap items-center gap-2">
          <Input list="qa-transitions" value={transition} onChange={(e) => setTransition(e.target.value)} placeholder="Status or transition name, e.g. In QA"
                 aria-label="Transition name" className="h-8 max-w-xs text-xs" />
          <datalist id="qa-transitions">{COMMON_TRANSITIONS.map((t) => <option key={t} value={t} />)}</datalist>
          <span className="text-[11px] text-muted-foreground">Issues without that transition are reported and left as they are.</span>
        </div>
      )}
      {action && !confirming && (
        <div className="flex justify-end">
          <Button size="sm" disabled={!ready || busy} onClick={() => setConfirming(true)}>Apply to {plural(keys.length, "issue")}</Button>
        </div>
      )}
      {action && confirming && (
        <div className="space-y-2 rounded-lg border bg-card p-3">
          <p className="text-xs font-medium">
            {action === "comment" ? `Post this comment on ${plural(keys.length, "issue")} as you? It is visible to everyone who can see them.`
              : action === "transition" ? `Move ${plural(keys.length, "issue")} with "${transition.trim()}"?`
                : `Link ${plural(keys.length, "issue")} to ${tables?.find((t) => t.target_table_id === tableId)?.fqn ?? "the table"}?`}
          </p>
          <p className="font-mono text-[11px] text-muted-foreground">{keys.join(", ")}</p>
          <div className="flex gap-2">
            <Button size="sm" disabled={busy} onClick={apply}>{busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Yes, apply</Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirming(false)}>Cancel</Button>
          </div>
        </div>
      )}
      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
      {results && (
        <ul className="max-h-48 space-y-0.5 overflow-y-auto rounded-lg border bg-card p-2 text-xs" aria-label="Results">
          <li className="pb-1 text-muted-foreground">{results.filter((x) => x.ok).length} of {results.length} done</li>
          {results.map((x) => (
            <li key={x.key} className={cn("flex items-start gap-1.5", x.ok ? "text-success" : x.skipped ? "text-muted-foreground" : "text-destructive")}>
              {x.ok ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" /> : <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
              <span className="font-mono">{x.key}</span>
              <span>{x.ok ? (x.already ? "already posted" : "done") : x.skipped ? (typeof x.skipped === "string" ? `skipped: ${x.skipped}` : `skipped${x.error ? `: ${x.error}` : ""}`) : x.error ?? "failed"}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
