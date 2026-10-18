"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, useTransition } from "react";
import {
  Archive, BrainCircuit, Check, Download, FlaskConical, GitCompare, GitPullRequest, History, Loader2, Play, Plus, Save, Send, Trash2, Undo2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { PackConflict, PackDetail, PackQuery, PackRun, PackSeverity, QaExecution, QaResult } from "@/lib/store-types";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { Drawer } from "@/components/hub/overlay";
import { StatusPill, explain } from "@/components/hub/states";
import { QaOutcomePill, QaResultCard } from "@/components/quality/qa-result";
import { SqlEditor, TagChip } from "@/components/quality/sql-editor";
import { useToast } from "@/components/ui/toast";
import { executeQa, loadPack, loadPackRuns, loadPackSync, savePack, transitionPack, type PackPayload } from "./actions";
import { ConflictBanner, GitTab } from "./pack-git";
import { splitTags } from "./qa-store-view";

type Working = { name: string; description: string; tags: string; queries: PackQuery[] };
type Tab = "queries" | "versions" | "runs" | "git";

const SEVERITY_DOT: Record<PackSeverity, string> = { ERROR: "bg-rose-500", WARN: "bg-amber-500", INFO: "bg-sky-500" };
const blankQuery = (n: number): PackQuery => ({
  query_name: `new_query_${n}`, description: null, sql_expression: "", severity: "ERROR", expectation: "0 rows", tags: [],
});

function toWorking(d: PackDetail): Working {
  return { name: d.pack.name, description: d.pack.description ?? "", tags: d.pack.tags.join(", "), queries: d.queries.map((q) => ({ ...q })) };
}

function payload(w: Working, base: number, note: string): PackPayload {
  return {
    name: w.name.trim(), description: w.description.trim() || null, tags: splitTags(w.tags), base_version: base, change_note: note || null,
    queries: w.queries.map((q) => ({ query_name: q.query_name.trim(), description: q.description || null, sql_expression: q.sql_expression,
                                     severity: q.severity, expectation: q.expectation || "0 rows", tags: q.tags })),
  };
}

/** Field-level differences between two versions' queries, by query name. */
function diffQueries(a: PackQuery[], b: PackQuery[]) {
  const key = (q: PackQuery) => q.query_name.toLowerCase();
  const am = new Map(a.map((q) => [key(q), q]));
  const bm = new Map(b.map((q) => [key(q), q]));
  const fields = ["sql_expression", "severity", "expectation", "description"] as const;
  return {
    added: b.filter((q) => !am.has(key(q))),
    removed: a.filter((q) => !bm.has(key(q))),
    changed: b.filter((q) => am.has(key(q))).map((q) => {
      const old = am.get(key(q))!;
      return { name: q.query_name, old, now: q, fields: fields.filter((f) => String(old[f] ?? "") !== String(q[f] ?? "")) };
    }).filter((c) => c.fields.length),
  };
}

function VersionsTab({ detail, packId, onView }: { detail: PackDetail; packId: string; onView: (v: number) => void }) {
  const versions = detail.versions;
  const [from, setFrom] = useState(versions[1]?.version ?? versions[0]?.version);
  const [to, setTo] = useState(versions[0]?.version);
  const [diff, setDiff] = useState<ReturnType<typeof diffQueries> | null>(null);
  const [pending, start] = useTransition();
  const compare = () => start(async () => {
    const [a, b] = await Promise.all([loadPack(packId, from), loadPack(packId, to)]);
    if (a.ok && b.ok) setDiff(diffQueries(a.data.queries, b.data.queries));
  });
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
      <ol className="relative space-y-3 border-l border-border pl-5">
        {versions.map((v) => (
          <li key={v.version} className="relative">
            <span className={cn("absolute -left-[27px] top-1.5 h-3 w-3 rounded-full border-2 border-card",
              v.status === "APPROVED" ? "bg-emerald-500" : v.status === "PENDING_APPROVAL" ? "bg-amber-500" : v.status === "DRAFT" ? "bg-blue-400" : "bg-slate-300")} />
            <div className="rounded-xl border border-border/80 bg-card p-3">
              <div className="flex items-center gap-2">
                <span className="font-semibold">v{v.version}</span>
                <StatusPill status={v.status} />
                <button type="button" onClick={() => onView(v.version)} className="ml-auto text-xs font-medium text-primary hover:underline">View</button>
              </div>
              {v.change_note && <p className="mt-1 text-sm">{v.change_note}</p>}
              <p className="mt-1 text-xs text-muted-foreground">
                {v.created_by} · {String(v.created_at).slice(0, 16)}
                {v.approved_by && ` · approved by ${v.approved_by} ${String(v.approved_at ?? "").slice(0, 10)}`}
              </p>
            </div>
          </li>
        ))}
      </ol>
      <div className="surface p-4">
        <div className="flex flex-wrap items-end gap-2">
          <div><Label htmlFor="d-from">From</Label>
            <Select id="d-from" value={from} onChange={(e) => setFrom(Number(e.target.value))}>{versions.map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}</Select></div>
          <div><Label htmlFor="d-to">To</Label>
            <Select id="d-to" value={to} onChange={(e) => setTo(Number(e.target.value))}>{versions.map((v) => <option key={v.version} value={v.version}>v{v.version}</option>)}</Select></div>
          <Button variant="outline" onClick={compare} disabled={pending || from === to}>
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitCompare className="h-4 w-4" />} Compare
          </Button>
        </div>
        {!diff ? <p className="mt-4 text-sm text-muted-foreground">Pick two versions to see which queries were added, removed or changed.</p> : (
          <div className="mt-4 space-y-3 text-sm">
            {!diff.added.length && !diff.removed.length && !diff.changed.length && <p className="text-muted-foreground">The queries are identical.</p>}
            {diff.added.map((q) => <p key={`a${q.query_name}`} className="rounded-lg bg-emerald-50 px-3 py-2 text-emerald-800"><b>+ {q.query_name}</b> added</p>)}
            {diff.removed.map((q) => <p key={`r${q.query_name}`} className="rounded-lg bg-rose-50 px-3 py-2 text-rose-800"><b>- {q.query_name}</b> removed</p>)}
            {diff.changed.map((c) => (
              <div key={c.name} className="rounded-lg border border-border/80 p-3">
                <p className="font-medium">{c.name} <span className="text-xs font-normal text-muted-foreground">changed {c.fields.join(", ").replace(/_/g, " ")}</span></p>
                {c.fields.includes("sql_expression") ? (
                  <div className="mt-2 grid gap-2 md:grid-cols-2">
                    <pre className="max-h-48 overflow-auto rounded-lg bg-rose-50 p-2 font-mono text-[11px] text-rose-900">{c.old.sql_expression}</pre>
                    <pre className="max-h-48 overflow-auto rounded-lg bg-emerald-50 p-2 font-mono text-[11px] text-emerald-900">{c.now.sql_expression}</pre>
                  </div>
                ) : (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {c.fields.map((f) => `${f}: ${String(c.old[f] ?? "-")} to ${String(c.now[f] ?? "-")}`).join(" · ")}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function RunsTab({ runs }: { runs: PackRun[] | null }) {
  if (!runs) return <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-4 w-4 animate-spin" /> Loading runs</p>;
  if (!runs.length) return <p className="text-sm text-muted-foreground">This pack has not been run yet.</p>;
  return (
    <div className="overflow-hidden rounded-xl border border-border/80">
      <table className="w-full text-sm">
        <thead className="bg-muted/60 text-left text-[11px] uppercase tracking-[0.06em] text-muted-foreground">
          <tr><th className="px-3 py-2">When</th><th className="px-3 py-2">Version</th><th className="px-3 py-2">Result</th><th className="px-3 py-2">Duration</th><th className="px-3 py-2">By</th></tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.qa_run_id} className="border-t border-border/60">
              <td className="px-3 py-2 tabular-nums">{r.started_at.slice(0, 16)}</td>
              <td className="px-3 py-2">v{r.version ?? "?"}</td>
              <td className="px-3 py-2">
                <span className="text-emerald-700">{r.passed} passed</span>
                {r.failed > 0 && <span className="text-rose-700"> · {r.failed} failed</span>}
                {r.errors > 0 && <span className="text-rose-700"> · {r.errors} errors</span>}
                {r.review > 0 && <span className="text-amber-700"> · {r.review} review</span>}
              </td>
              <td className="px-3 py-2 tabular-nums">{r.duration_ms != null ? `${(r.duration_ms / 1000).toFixed(1)}s` : "-"}</td>
              <td className="px-3 py-2 text-muted-foreground">{r.created_by ?? r.triggered_by ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One pack: edit its queries (always into a draft), test them, compare versions, submit, approve, run, export. */
export function PackDrawer({ packId, onClose, canEdit, canApprove, canRun }: {
  packId: string; onClose: () => void; canEdit: boolean; canApprove: boolean; canRun: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const [detail, setDetail] = useState<PackDetail | null>(null);
  const [error, setError] = useState("");
  const [working, setWorking] = useState<Working | null>(null);
  const [dirty, setDirty] = useState(false);
  const [selected, setSelected] = useState(0);
  const [tab, setTab] = useState<Tab>("queries");
  const [results, setResults] = useState<Record<string, QaResult>>({});
  const [suite, setSuite] = useState<QaExecution | null>(null);
  const [runs, setRuns] = useState<PackRun[] | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");
  const [conflict, setConflict] = useState<PackConflict | null>(null);
  const [, start] = useTransition();

  const load = useCallback(async (version?: number | null) => {
    const r = await loadPack(packId, version);
    if (!r.ok) { setError(explain(r.error).message); return; }
    setDetail(r.data); setWorking(toWorking(r.data)); setDirty(false); setSelected(0); setError("");
  }, [packId]);
  useEffect(() => { void load(); }, [load]);
  const loadConflict = useCallback(() => { void loadPackSync(packId).then((r) => setConflict(r.ok ? r.data.conflict : null)); }, [packId]);
  useEffect(() => { loadConflict(); }, [loadConflict]);
  useEffect(() => {
    if (tab === "runs" && runs == null) void loadPackRuns(packId).then((r) => setRuns(r.ok ? r.data : []));
  }, [tab, runs, packId]);

  const newest = detail?.versions[0];
  const viewingNewest = !!detail && !!newest && detail.pack.version === newest.version;
  const editable = canEdit && viewingNewest && newest?.status !== "RETIRED";
  const query = working?.queries[selected];
  const names = useMemo(() => new Set(working?.queries.map((q) => q.query_name.toLowerCase())), [working]);

  const edit = (fn: (w: Working) => Working) => { setWorking((w) => (w ? fn(w) : w)); setDirty(true); };
  const editQuery = (patch: Partial<PackQuery>) => edit((w) => ({ ...w, queries: w.queries.map((q, i) => (i === selected ? { ...q, ...patch } : q)) }));

  const act = (label: string, fn: () => Promise<void>) => start(async () => { setBusy(label); try { await fn(); } finally { setBusy(""); } });

  const save = () => act("save", async () => {
    if (!working || !newest) return;
    const r = await savePack(packId, payload(working, newest.version, note));
    if (r.ok) { toast("ok", `Saved as draft v${r.data.version}`); setNote(""); await load(); router.refresh(); }
    else if (r.pending) toast("info", r.error);
    else toast("error", explain(r.error).message);
  });
  const step = (s: "submit" | "reject" | "approve" | "retire", done: string) => act(s, async () => {
    const r = await transitionPack(packId, s, note);
    if (r.ok) { toast("ok", done); setNote(""); await load(); router.refresh(); }
    else if (r.pending) { toast("info", r.error); }
    else toast("error", explain(r.error).message);
  });
  const testQuery = () => act("test", async () => {
    if (!detail || !query) return;
    const r = await executeQa({ domain_id: detail.pack.domain_id, sql: query.sql_expression });
    if (r.ok && r.data.results[0]) setResults((m) => ({ ...m, [query.query_name]: { ...r.data.results[0], title: query.query_name, expected: query.expectation } }));
    else if (!r.ok) toast("error", explain(r.error).message);
  });
  const runSuite = (version?: number) => act("suite", async () => {
    if (!detail) return;
    const r = await executeQa({ domain_id: detail.pack.domain_id, pack_id: packId, version: version ?? null });
    if (r.ok) {
      setSuite(r.data); setRuns(null);
      const failed = (r.data.failed ?? 0) + (r.data.errors ?? 0);
      toast(failed ? "error" : "ok", `${r.data.passed ?? 0} of ${r.data.tests ?? 0} passed${failed ? `, ${failed} failed or errored` : ""}`);
      router.refresh();
    } else toast("error", explain(r.error).message);
  });

  const header = detail && (
    <div className="flex items-center gap-2">
      <StatusPill status={detail.pack.status} />
      <Select aria-label="Version" className="h-8 w-auto text-xs" value={detail.pack.version} onChange={(e) => void load(Number(e.target.value))}>
        {detail.versions.map((v) => <option key={v.version} value={v.version}>v{v.version} · {v.status.toLowerCase().replace(/_/g, " ")}</option>)}
      </Select>
      <a href={`/bff/qa-pack?pack=${packId}&version=${detail.pack.version}`} title="Export this version as domain/<name>/qa/<pack>.sql"
         className="grid h-8 w-8 place-items-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground"><Download className="h-4 w-4" /></a>
    </div>
  );

  const footer = detail && newest && (canEdit || canApprove || canRun) && (
    <>
      {(editable || (newest.status === "PENDING_APPROVAL" && canApprove)) && (
        <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder={newest.status === "PENDING_APPROVAL" ? "Note for the author (optional)" : "Change note (optional)"}
               className="mr-auto h-8 max-w-sm text-xs" aria-label="Change note" />
      )}
      {canApprove && newest.status !== "RETIRED" && (
        <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => { if (confirm("Retire this pack? It stops running and leaves the AI context.")) step("retire", "Pack retired"); }}>
          <Archive className="h-3.5 w-3.5" /> Retire
        </Button>
      )}
      {canRun && detail.approved_version && (
        <Button size="sm" variant="outline" disabled={!!busy} onClick={() => runSuite()}>
          {busy === "suite" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />} Run approved v{detail.approved_version}
        </Button>
      )}
      {canRun && detail.pack.version !== detail.approved_version && detail.pack.status !== "RETIRED" && (
        <Button size="sm" variant="outline" disabled={!!busy || dirty} onClick={() => runSuite(detail.pack.version)} title={dirty ? "Save first" : "Runs this version and stores the result"}>
          <FlaskConical className="h-3.5 w-3.5" /> Test v{detail.pack.version}
        </Button>
      )}
      {editable && dirty && (
        <Button size="sm" disabled={!!busy} onClick={save} data-testid="save-pack">
          {busy === "save" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
          {newest.status === "DRAFT" ? `Save draft v${newest.version}` : `Save as draft v${newest.version + 1}`}
        </Button>
      )}
      {canEdit && !dirty && newest.status === "DRAFT" && viewingNewest && (
        <Button size="sm" disabled={!!busy} onClick={() => step("submit", `v${newest.version} submitted for approval`)}>
          <Send className="h-3.5 w-3.5" /> Submit for approval
        </Button>
      )}
      {newest.status === "PENDING_APPROVAL" && (canApprove || canEdit) && (
        <Button size="sm" variant="outline" disabled={!!busy} onClick={() => step("reject", "Sent back to draft")}><Undo2 className="h-3.5 w-3.5" /> Send back</Button>
      )}
      {newest.status === "PENDING_APPROVAL" && canApprove && (
        <Button size="sm" disabled={!!busy} onClick={() => step("approve", `v${newest.version} approved and published to the AI context`)} data-testid="approve-pack">
          {busy === "approve" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />} Approve v{newest.version}
        </Button>
      )}
    </>
  );

  return (
    <Drawer open onClose={onClose} wide title={working?.name || detail?.pack.name || "QA pack"} actions={header} footer={footer}
            subtitle={detail && (
              <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span>{detail.pack.domain_name ?? "No domain"}</span>
                <span>·</span>
                <span>{detail.approved_version ? `approved v${detail.approved_version}` : "not approved yet"}</span>
                {detail.approved_version && detail.pack.status !== "RETIRED" && (
                  <span className="inline-flex items-center gap-1 text-blue-600"><BrainCircuit className="h-3.5 w-3.5" /> feeds AI context</span>
                )}
              </span>
            )}>
      {error && <p className="mb-4 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-sm text-destructive">{error}</p>}
      {!detail || !working ? (!error && <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-4 w-4 animate-spin" /> Loading pack</p>) : (
        <div className="space-y-5">
          {!viewingNewest && (
            <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              Viewing v{detail.pack.version} ({detail.pack.status.toLowerCase()}). Edits start from the newest version.{" "}
              <button type="button" className="font-medium underline" onClick={() => void load()}>Open v{newest?.version}</button>
            </p>
          )}
          <nav className="flex gap-1 border-b border-border/70" aria-label="Pack sections">
            {([["queries", `Queries (${working.queries.length})`, FlaskConical], ["versions", `Versions (${detail.versions.length})`, GitCompare], ["runs", "Runs", History], ["git", "Git", GitPullRequest]] as const).map(([id, label, Icon]) => (
              <button key={id} type="button" onClick={() => setTab(id)} aria-current={tab === id ? "page" : undefined}
                      className={cn("relative flex items-center gap-1.5 px-3 pb-2 pt-1 text-sm transition", tab === id ? "font-medium text-foreground" : "text-muted-foreground hover:text-foreground")}>
                <Icon className="h-4 w-4" /> {label}
                {tab === id && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-primary" />}
              </button>
            ))}
          </nav>

          {conflict && <ConflictBanner packId={packId} conflict={conflict} canEdit={canEdit}
                                       onResolved={(msg) => { toast("ok", msg); setConflict(null); void load(); router.refresh(); }} />}
          {tab === "git" && <GitTab packId={packId} detail={detail} canEdit={canEdit} />}
          {tab === "versions" && <VersionsTab detail={detail} packId={packId} onView={(v) => { setTab("queries"); void load(v); }} />}
          {tab === "runs" && <RunsTab runs={runs} />}
          {tab === "queries" && (
            <>
              {suite && (
                <div className="space-y-2 rounded-xl border border-border/80 bg-muted/30 p-3">
                  <div className="flex items-center gap-2 text-sm">
                    <b>Suite run</b>
                    <span className="text-muted-foreground">{suite.passed ?? 0} of {suite.tests ?? 0} passed · {((suite.duration_ms ?? 0) / 1000).toFixed(1)}s</span>
                    <button type="button" onClick={() => setSuite(null)} className="ml-auto text-xs text-muted-foreground hover:text-foreground">Dismiss</button>
                  </div>
                  <div className="grid gap-2 md:grid-cols-2">{suite.results.map((r, i) => <QaResultCard key={i} result={r} />)}</div>
                </div>
              )}
              <div className="grid gap-x-4 sm:grid-cols-[1fr_1fr]">
                <div><Label htmlFor="pk-name">Pack name</Label>
                  <Input id="pk-name" value={working.name} disabled={!editable} onChange={(e) => edit((w) => ({ ...w, name: e.target.value }))} /></div>
                <div><Label htmlFor="pk-tags">Tags</Label>
                  <Input id="pk-tags" value={working.tags} disabled={!editable} onChange={(e) => edit((w) => ({ ...w, tags: e.target.value }))} placeholder="reconciliation, sla" /></div>
                <div className="sm:col-span-2"><Label htmlFor="pk-desc">Description</Label>
                  <Textarea id="pk-desc" value={working.description} disabled={!editable} className="min-h-14" onChange={(e) => edit((w) => ({ ...w, description: e.target.value }))} /></div>
              </div>
              <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
                <div className="space-y-1">
                  {working.queries.map((q, i) => {
                    const res = results[q.query_name] ?? suite?.results.find((r) => r.title.endsWith(`: ${q.query_name}`));
                    return (
                      <button key={i} type="button" onClick={() => setSelected(i)}
                              className={cn("flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition",
                                i === selected ? "bg-primary/10 text-foreground ring-1 ring-inset ring-primary/20" : "hover:bg-muted")}>
                        <span className={cn("h-2 w-2 shrink-0 rounded-full", SEVERITY_DOT[q.severity] ?? "bg-slate-400")} title={q.severity} />
                        <span className="min-w-0 flex-1 truncate font-mono text-xs">{q.query_name}</span>
                        {res && <QaOutcomePill outcome={res.outcome} />}
                      </button>
                    );
                  })}
                  {editable && (
                    <button type="button" onClick={() => { edit((w) => ({ ...w, queries: [...w.queries, blankQuery(w.queries.length + 1)] })); setSelected(working.queries.length); }}
                            className="flex w-full items-center gap-2 rounded-lg border border-dashed border-border px-2.5 py-2 text-sm text-muted-foreground transition hover:border-primary/40 hover:text-foreground">
                      <Plus className="h-4 w-4" /> Add query
                    </button>
                  )}
                </div>
                {query && (
                  <div className="min-w-0 space-y-3">
                    <div className="grid gap-x-3 sm:grid-cols-[1fr_130px_1fr]">
                      <div><Label htmlFor="q-name">Query name</Label>
                        <Input id="q-name" className="font-mono" value={query.query_name} disabled={!editable}
                               onChange={(e) => editQuery({ query_name: e.target.value })} aria-invalid={[...names].filter((n) => n === query.query_name.toLowerCase()).length > 1} /></div>
                      <div><Label htmlFor="q-sev">Severity</Label>
                        <Select id="q-sev" value={query.severity} disabled={!editable} onChange={(e) => editQuery({ severity: e.target.value as PackSeverity })}>
                          <option value="ERROR">Error</option><option value="WARN">Warn</option><option value="INFO">Info</option>
                        </Select></div>
                      <div><Label htmlFor="q-exp">Passes when</Label>
                        <Input id="q-exp" value={query.expectation ?? ""} disabled={!editable} placeholder="0 rows" onChange={(e) => editQuery({ expectation: e.target.value })} /></div>
                    </div>
                    <div><Label htmlFor="q-desc">What it checks</Label>
                      <Input id="q-desc" value={query.description ?? ""} disabled={!editable} onChange={(e) => editQuery({ description: e.target.value })} /></div>
                    <SqlEditor value={query.sql_expression} readOnly={!editable} onChange={(v) => editQuery({ sql_expression: v })} rows={12} />
                    {query.tags.length > 0 && <div className="flex flex-wrap gap-1">{query.tags.map((t) => <TagChip key={t} tag={t} />)}</div>}
                    <div className="flex flex-wrap items-center gap-2">
                      {canRun && (
                        <Button size="sm" variant="outline" disabled={!!busy || query.sql_expression.trim().length < 6} onClick={testQuery} data-testid="test-query">
                          {busy === "test" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />} Test query
                        </Button>
                      )}
                      <span className="text-xs text-muted-foreground">Read-only, against the domain&apos;s tables only. A test run is not stored.</span>
                      {editable && working.queries.length > 1 && (
                        <Button size="sm" variant="ghost" className="ml-auto text-destructive hover:text-destructive"
                                onClick={() => { edit((w) => ({ ...w, queries: w.queries.filter((_, i) => i !== selected) })); setSelected(Math.max(0, selected - 1)); }}>
                          <Trash2 className="h-3.5 w-3.5" /> Remove
                        </Button>
                      )}
                    </div>
                    {results[query.query_name] && <QaResultCard result={results[query.query_name]} />}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </Drawer>
  );
}
