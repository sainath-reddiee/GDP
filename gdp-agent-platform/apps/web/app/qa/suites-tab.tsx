"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import {
  Archive, Check, Database, FlaskConical, History, KeyRound, ListChecks, Loader2, Pencil, Play, Plus, Search, ShieldCheck,
  Sparkles, Table2, Trash2, X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { QaAssistant } from "@/components/qa/qa-assistant";
import { RunHistoryTable } from "@/components/qa/qa-ui";
import { TestCard, type CardOutcome } from "@/components/qa/test-card";
import {
  askTable, createSuite, deleteSuite, deleteTableTest, listLinks, planTable, renameSuite, runSuite, runTable, saveTableTest, tableContext,
  tableHistory, tableResults, tableSuite, updateTableTest,
  type QaLink, type QaTable, type TableContext, type TableResult, type TableRun, type TableSuite, type TableTest,
} from "./actions";
import { Alert, Empty, Notice, Pending, SuiteSelect, plural, useLive, useSeq, useTables, type Access, type Go, type Nav } from "./qa-shared";

const PII_BASIS: Record<string, string> = {
  profile: "Sample rows hide the columns the data profile flagged as personal data, mapped to this table through the STTM.",
  heuristic: "There is no data profile, so sample rows hide the columns the domain marks as personal data, plus columns whose names look personal.",
  conservative: "There is no data profile and nothing is marked as personal data (or a PII tag is set), so sample rows hide every column except the business keys.",
};

function summary(r: TableRun) {
  return `${r.passed} pass · ${r.failed} fail · ${r.review} to review · ${r.not_run} not run${r.errors ? ` · ${r.errors} errors` : ""}`;
}

export function SuitesTab({ nav, go, access, domains }: { nav: Nav; go: Go; access: Access; domains: { domain_id: string; name: string }[] }) {
  const [domainId, setDomainId] = useState("");
  const [q, setQ] = useState("");
  const { tables, error, refresh } = useTables(domainId);
  const shown = (tables ?? []).filter((t) => t.fqn.toLowerCase().includes(q.trim().toLowerCase()));
  return (
    <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
      <aside className="surface flex max-h-[80vh] flex-col self-start overflow-hidden">
        <div className="space-y-2 border-b p-3">
          <Select value={domainId} onChange={(e) => setDomainId(e.target.value)} aria-label="Domain" className="text-xs">
            <option value="">All domains</option>
            {domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.name}</option>)}
          </Select>
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter tables" aria-label="Filter tables" className="h-8 pl-8 text-xs" />
          </div>
        </div>
        {error && <Alert className="m-2" onDismiss={refresh}>{error}</Alert>}
        <ul className="min-h-0 flex-1 divide-y overflow-y-auto overscroll-contain">
          {!tables && <li className="flex items-center gap-2 px-3 py-6 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading tables…</li>}
          {shown.map((t) => <TableRow key={t.target_table_id} t={t} active={nav.table === t.target_table_id} onPick={() => go({ table: t.target_table_id, suite: "" })} />)}
          {tables && !shown.length && <li className="px-3 py-6 text-center text-xs text-muted-foreground">No target tables{domainId ? " in this domain" : ""}.</li>}
        </ul>
      </aside>
      {nav.table ? <TableView key={nav.table} tableId={nav.table} suiteId={nav.suite} go={go} access={access} onChanged={refresh} />
        : <Empty icon={<Table2 className="h-6 w-6" />} title="Pick a target table" text="Its context, suites, tests and run history open here." />}
    </div>
  );
}

function TableRow({ t, active, onPick }: { t: QaTable; active: boolean; onPick: () => void }) {
  const o = t.last_outcome;
  const total = o?.tests || 1;
  return (
    <li>
      <button type="button" onClick={onPick} className={cn("w-full px-3 py-2.5 text-left hover:bg-muted/40", active && "bg-primary/5")}>
        <p className="truncate font-mono text-xs font-medium" title={t.fqn}>{t.fqn}</p>
        <p className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
          {t.has_sttm ? <span className="rounded bg-emerald-50 px-1.5 text-emerald-700">STTM</span> : <span className="rounded bg-muted px-1.5">no STTM</span>}
          <span>{plural(t.tests, "test")}</span>
          {!t.active && <span className="rounded bg-amber-50 px-1.5 text-amber-800">retired</span>}
          {o ? <span className="ml-auto">{o.failed + o.errors ? <span className="text-destructive">{o.failed + o.errors} failing</span> : <span className={o.passed === o.tests ? "text-success" : undefined}>{o.passed}/{o.tests} pass</span>}</span>
            : <span className="ml-auto">not run</span>}
        </p>
        {o && (
          <span className="mt-1.5 flex h-1 overflow-hidden rounded-full bg-muted">
            <span className="bg-success" style={{ width: `${(o.passed / total) * 100}%` }} />
            <span className="bg-destructive" style={{ width: `${((o.failed + o.errors) / total) * 100}%` }} />
            <span className="bg-warning" style={{ width: `${(o.review / total) * 100}%` }} />
          </span>
        )}
      </button>
    </li>
  );
}

type View = "tests" | "ai" | "history";

function TableView({ tableId, suiteId, go, access, onChanged }: { tableId: string; suiteId: string; go: Go; access: Access; onChanged: () => void }) {
  const [ctx, setCtx] = useState<TableContext | null>(null);
  const [data, setData] = useState<TableSuite | null>(null);
  const [results, setResults] = useState<TableResult[]>([]);
  const [runs, setRuns] = useState<TableRun[] | null>(null);
  const [links, setLinks] = useState<QaLink[]>([]);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [view, setView] = useState<View>("tests");
  const [open, setOpen] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saveSuite, setSaveSuite] = useState(suiteId);
  const [running, startRun] = useTransition();
  const seq = useSeq();
  const live = useLive();

  const reload = async () => {
    const n = seq.next();
    const [s, r, h, l] = await Promise.all([tableSuite(tableId), tableResults(tableId), tableHistory(tableId), listLinks({ targetTableId: tableId })]);
    if (!seq.current(n) || !live.current) return;
    if (s.ok) { setData(s.data); setLoadError(""); } else setLoadError(s.error);
    if (r.ok) setResults(r.data.results); else setError(r.error);
    if (h.ok) setRuns(h.data.runs); else { setRuns([]); setError(h.error); }
    if (l.ok) setLinks(l.data.links); else setError(`Jira links: ${l.error}`);
  };
  useEffect(() => {
    tableContext(tableId).then((r) => { if (!live.current) return; if (r.ok) setCtx(r.data); else setLoadError(r.error); },
      (e: unknown) => { if (live.current) setLoadError(e instanceof Error ? e.message : "Could not load the table."); });
    reload().catch((e: unknown) => { if (live.current) setLoadError(e instanceof Error ? e.message : "Could not load the tests."); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tableId]);
  const refresh = () => { reload().catch((e: unknown) => { if (live.current) setError(e instanceof Error ? e.message : "Could not reload."); }); onChanged(); };

  const byTest = useMemo(() => new Map(results.map((r) => [r.test_id, r])), [results]);
  const keysByTest = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const l of links) if (l.qa_test_id) m.set(l.qa_test_id, [...(m.get(l.qa_test_id) ?? []), l.issue_key]);
    return m;
  }, [links]);
  const retired = !!(ctx?.retired ?? data?.retired);
  const canWrite = access.canEdit && !retired;
  const suites = data?.suites ?? [];
  const activeSuite = suites.find((s) => s.suite_id === suiteId) ?? null;

  const run = (how: { suite?: string; ids?: string[] }) => startRun(async () => {
    setMessage(null);
    const r = how.suite ? await runSuite(how.suite, how.ids) : await runTable(tableId, { test_ids: how.ids });
    if (!live.current) return;
    if (!r.ok) { setMessage({ ok: false, text: r.error }); return; }
    setMessage({ ok: true, text: summary(r.data) });
    setSelected(new Set());
    refresh();
  });
  const done = (r: { ok: true } | { ok: false; error: string }): CardOutcome => {
    if (r.ok) refresh();
    return r.ok ? { ok: true } : { ok: false, error: r.error };
  };

  if (loadError && !data) return <Empty title="This table could not be opened" text={<span role="alert">{loadError}</span>} />;
  if (!data) return <Pending text="Loading the table…" />;

  const tests = data.tests.filter((t) => !suiteId || t.suite_id === suiteId);
  const groups: { id: string; name: string; tests: TableTest[] }[] = [];
  const generated = tests.filter((t) => t.origin === "GENERATED" || !t.suite_id);
  if (generated.length) groups.push({ id: "generated", name: "Generated from the STTM", tests: generated });
  for (const s of suites) {
    const inSuite = tests.filter((t) => t.origin !== "GENERATED" && t.suite_id === s.suite_id);
    if (inSuite.length || s.suite_id === suiteId) groups.push({ id: s.suite_id, name: s.name, tests: inSuite });
  }

  return (
    <div className="min-w-0 space-y-4">
      <ContextCard ctx={ctx} data={data} />
      {retired && (
        <p role="status" className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <Archive className="h-4 w-4 shrink-0" />This table is retired. Its suites are kept, but tests cannot be added or run until the table is reactivated in its domain.</p>
      )}
      <SuiteBar tableId={tableId} suites={suites} active={suiteId} canWrite={canWrite} onPick={(id) => { go({ suite: id }); setSaveSuite(id); setSelected(new Set()); }}
                onChanged={(goTo) => { if (goTo !== undefined) go({ suite: goTo }); refresh(); }} />

      <div className="flex flex-wrap items-center gap-2">
        <nav role="tablist" className="flex gap-1">
          {([["tests", "Tests", ListChecks], ["ai", "Ask AI", Sparkles], ["history", "History", History]] as const).map(([k, l, Icon]) => (
            <button key={k} type="button" role="tab" aria-selected={view === k} onClick={() => setView(k)}
                    className={cn("inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm", view === k ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted")}>
              <Icon className="h-4 w-4" />{l}{k === "history" && runs?.length ? <span className="rounded-full bg-muted px-1.5 text-[11px] text-muted-foreground">{runs.length}</span> : null}</button>
          ))}
        </nav>
        {canWrite && (
          <span className="ml-auto flex flex-wrap gap-2">
            {selected.size > 0 && <Button size="sm" variant="outline" disabled={running} onClick={() => run({ ids: Array.from(selected) })}><Play className="h-3.5 w-3.5" />Run {selected.size} selected</Button>}
            {activeSuite
              ? <Button size="sm" disabled={running || !activeSuite.tests} onClick={() => run({ suite: activeSuite.suite_id })}>{running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}Run suite</Button>
              : <Button size="sm" disabled={running || !data.tests.length} onClick={() => run({})}>{running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}{running ? "Running in Snowflake…" : `Run all ${data.tests.length}`}</Button>}
          </span>
        )}
      </div>
      {message && (message.ok ? <Notice onDismiss={() => setMessage(null)}>{message.text}</Notice> : <Alert onDismiss={() => setMessage(null)}>{message.text}</Alert>)}
      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}

      {view === "tests" && (
        <div className="space-y-5">
          {groups.map((g) => (
            <section key={g.id} className="space-y-2">
              <h4 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{g.id === "generated" ? <FlaskConical className="h-3.5 w-3.5" /> : <ListChecks className="h-3.5 w-3.5" />}{g.name}
                <span className="font-normal normal-case">{plural(g.tests.length, "test")}</span></h4>
              {g.tests.map((t) => (
                <TestCard key={t.test_id} test={t} result={byTest.get(t.test_id)} history={[]} open={open === t.test_id}
                          onToggle={() => setOpen(open === t.test_id ? "" : t.test_id)} selected={selected.has(t.test_id)}
                          onSelect={() => setSelected((s) => { const n = new Set(s); if (n.has(t.test_id)) n.delete(t.test_id); else n.add(t.test_id); return n; })}
                          canRun={canWrite} editable={canWrite && t.origin !== "GENERATED"} scopeBadge={false}
                          suites={suites.map((s) => ({ suite_id: s.suite_id, name: s.name }))}
                          onRun={async () => done(await runTable(tableId, { test_ids: [t.test_id] }))}
                          onUpdate={async (e) => done(await updateTableTest(t.test_id, e))}
                          onRemove={async () => done(await deleteTableTest(t.test_id))}
                          extra={keysByTest.get(t.test_id)?.length ? (
                            <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">Jira:
                              {keysByTest.get(t.test_id)!.map((k) => <button key={k} type="button" onClick={() => go({ tab: "triage", key: k })} className="font-mono text-primary hover:underline">{k}</button>)}</p>
                          ) : undefined} />
              ))}
              {!g.tests.length && <p className="rounded-xl border border-dashed p-4 text-center text-xs text-muted-foreground">No tests in this suite yet. Ask AI to add some.</p>}
            </section>
          ))}
          {!groups.length && <div className="rounded-2xl border border-dashed p-10 text-center text-sm text-muted-foreground">No tests yet. {data.sttm_id ? "" : "This table has no STTM, so nothing is generated. "}Ask AI to propose some.</div>}
        </div>
      )}
      {view === "ai" && (
        <QaAssistant canAI={access.canAI && canWrite} ask={(question) => askTable(tableId, question)} plan={(focus) => planTable(tableId, focus)}
                     save={(t) => saveTableTest(tableId, { ...t, ...(saveSuite ? { suite_id: saveSuite } : {}) })}
                     onSaved={() => { setView("tests"); refresh(); }}
                     saveTarget={<span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">into<SuiteSelect suites={suites} value={saveSuite} onChange={setSaveSuite} /></span>} />
      )}
      {view === "history" && (runs ? <RunHistoryTable runs={runs} showScope empty="No QA runs on this table yet." /> : <Pending text="Loading history…" />)}
    </div>
  );
}

function ContextCard({ ctx, data }: { ctx: TableContext | null; data: TableSuite }) {
  const basis = ctx?.pii_basis ?? (data.pii_basis as TableContext["pii_basis"]);
  const allowed = ctx?.allowed ?? data.allowed ?? [];
  const keys = ctx?.business_keys ?? data.business_keys;
  return (
    <section className="space-y-3 rounded-2xl border bg-gradient-to-br from-violet-500/10 via-card to-card p-5 shadow-sm">
      <div className="flex flex-wrap items-start gap-3">
        <span className="grid h-10 w-10 place-items-center rounded-xl bg-violet-50 text-violet-600 ring-1 ring-inset ring-violet-100"><Table2 className="h-5 w-5" /></span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate font-mono text-base font-semibold" title={data.target.fqn}>{data.target.fqn}</h3>
          <div className="mt-1.5 flex flex-wrap gap-2 text-xs">
            {data.sttm_id ? <Badge variant="success">has an STTM{ctx?.lines ? `, ${plural(ctx.lines, "line")}` : ""}</Badge> : <Badge variant="outline">no STTM: only saved tests</Badge>}
            <span className="inline-flex items-center gap-1 rounded-full border bg-card px-2.5 py-0.5"><KeyRound className="h-3.5 w-3.5 text-muted-foreground" />{keys.length ? keys.join(", ") : "no business key"}</span>
            <span className="inline-flex items-center gap-1 rounded-full border bg-card px-2.5 py-0.5"><ListChecks className="h-3.5 w-3.5 text-muted-foreground" />{plural(data.tests.length, "test")}</span>
          </div>
        </div>
      </div>
      <div className="grid gap-3 text-xs md:grid-cols-2">
        <div>
          <p className="mb-1 flex items-center gap-1.5 font-medium"><Database className="h-3.5 w-3.5 text-muted-foreground" />Tests may read</p>
          <ul className="space-y-0.5 font-mono text-[11px] text-muted-foreground">{allowed.map((a) => <li key={a} className="truncate" title={a}>{a}</li>)}</ul>
          {!allowed.length && <p className="text-muted-foreground">Only the target table.</p>}
        </div>
        <div>
          <p className="mb-1 flex items-center gap-1.5 font-medium"><ShieldCheck className="h-3.5 w-3.5 text-muted-foreground" />Personal data in samples</p>
          <p className="text-muted-foreground">{basis ? PII_BASIS[basis] : "Masking is decided when the tests run."}</p>
          {ctx && ctx.pii_columns.length > 0 && basis !== "conservative" && (
            <p className="mt-1 font-mono text-[11px] text-muted-foreground">Masked: {ctx.pii_columns.join(", ")}</p>
          )}
        </div>
      </div>
    </section>
  );
}

function SuiteBar({ tableId, suites, active, canWrite, onPick, onChanged }: {
  tableId: string; suites: TableSuite["suites"]; active: string; canWrite: boolean; onPick: (id: string) => void; onChanged: (goTo?: string) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [renaming, setRenaming] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState("");
  const [busy, start] = useTransition();
  const live = useLive();
  const current = suites.find((s) => s.suite_id === active) ?? null;

  const create = () => start(async () => {
    setError("");
    const r = await createSuite(tableId, name.trim());
    if (!live.current) return;
    if (!r.ok) { setError(r.error); return; }
    setAdding(false); setName("");
    onChanged(r.data.suite_id);
  });
  const rename = () => start(async () => {
    if (!current) return;
    setError("");
    const r = await renameSuite(current.suite_id, renaming.trim());
    if (!live.current) return;
    if (!r.ok) { setError(r.error); return; }
    setRenaming("");
    onChanged();
  });
  const remove = () => start(async () => {
    if (!current) return;
    setError("");
    const r = await deleteSuite(current.suite_id);
    if (!live.current) return;
    setConfirmDelete(false);
    if (!r.ok) { setError(r.error); return; }
    onChanged("");
  });

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <button type="button" onClick={() => onPick("")} aria-pressed={!active}
                className={cn("rounded-full border px-3 py-1 text-xs", !active ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:border-primary/40")}>All tests</button>
        {suites.map((s) => (
          <button key={s.suite_id} type="button" onClick={() => onPick(s.suite_id)} aria-pressed={active === s.suite_id} title={s.description ?? undefined}
                  className={cn("rounded-full border px-3 py-1 text-xs", active === s.suite_id ? "border-primary bg-primary text-primary-foreground" : "bg-card hover:border-primary/40")}>
            {s.name}<span className="ml-1.5 opacity-70">{s.tests}</span></button>
        ))}
        {canWrite && !adding && <Button size="sm" variant="ghost" onClick={() => setAdding(true)}><Plus className="h-3.5 w-3.5" />New suite</Button>}
        {adding && (
          <form className="inline-flex items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create(); }}>
            <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Suite name" aria-label="Suite name" className="h-8 w-48 text-xs" />
            <Button size="sm" type="submit" disabled={!name.trim() || busy}>{busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Create</Button>
            <button type="button" aria-label="Cancel" onClick={() => { setAdding(false); setName(""); }}><X className="h-3.5 w-3.5" /></button>
          </form>
        )}
        {canWrite && current && !current.is_default && !renaming && !confirmDelete && (
          <span className="ml-auto inline-flex gap-1">
            <Button size="sm" variant="ghost" onClick={() => setRenaming(current.name)}><Pencil className="h-3.5 w-3.5" />Rename</Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(true)}><Trash2 className="h-3.5 w-3.5" />Delete</Button>
          </span>
        )}
      </div>
      {renaming && current && (
        <form className="flex items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); if (renaming.trim()) rename(); }}>
          <Input autoFocus value={renaming} onChange={(e) => setRenaming(e.target.value)} aria-label="New suite name" className="h-8 w-64 text-xs" />
          <Button size="sm" type="submit" disabled={!renaming.trim() || busy}>{busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Save</Button>
          <Button size="sm" variant="ghost" type="button" onClick={() => setRenaming("")}>Cancel</Button>
        </form>
      )}
      {confirmDelete && current && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs">
          Delete the suite &quot;{current.name}&quot;? Its {plural(current.tests, "test")} move to the default suite.
          <Button size="sm" variant="destructive" className="ml-auto" disabled={busy} onClick={remove}>{busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Delete</Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>Cancel</Button>
        </div>
      )}
      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
      {!suites.length && <p className="text-xs text-muted-foreground">The default suite is created when the first test is saved.</p>}
    </div>
  );
}
