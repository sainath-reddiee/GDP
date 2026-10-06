"use client";

import { useCallback, useEffect, useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle, ArrowRight, CheckCircle2, CircleDashed, Database, Eye, Loader2, Plus, RefreshCw, Search,
  Sparkles, XCircle,
} from "lucide-react";
import type {
  InventoryTable, ProfileStatus, SourceInventory, SourceOverviewItem, SourcesOverview,
} from "@/lib/types";
import type { DatabaseRow, SchemaRow } from "@/app/onboarding/catalog-types";
import { loadSchemas } from "@/app/onboarding/catalog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { loadInventory, loadOverview, profileTables, registerConnection, sendToModeling } from "./actions";
import { ProfileDrawer } from "./profile-drawer";

const STATUS_META: Record<ProfileStatus, { label: string; variant: "outline" | "warning" | "success" | "destructive" }> = {
  UNPROFILED: { label: "Unprofiled", variant: "outline" },
  PROFILING: { label: "Profiling…", variant: "warning" },
  STAGED_READY_FOR_MODELING: { label: "Staged & ready", variant: "success" },
  STALE: { label: "Stale: source changed", variant: "warning" },
  FAILED: { label: "Failed", variant: "destructive" },
};
const FILTERS: { value: ProfileStatus | "ALL"; label: string }[] = [
  { value: "ALL", label: "All" },
  { value: "STAGED_READY_FOR_MODELING", label: "Staged" },
  { value: "UNPROFILED", label: "Unprofiled" },
  { value: "STALE", label: "Stale" },
  { value: "PROFILING", label: "Profiling" },
  { value: "FAILED", label: "Failed" },
];

function formatCount(n: number | null | undefined) {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}K`;
  return n.toLocaleString();
}

function Kpi({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-xl border bg-card px-4 py-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function SourceCard({ s, active, onClick }: { s: SourceOverviewItem; active: boolean; onClick: () => void }) {
  const coverage = s.table_count ? Math.round((s.staged_tables / s.table_count) * 100) : 0;
  return (
    <button type="button" onClick={onClick} aria-pressed={active}
            className={cn("flex w-full flex-col gap-2 rounded-xl border bg-card p-4 text-left transition-all",
              active ? "border-primary ring-2 ring-primary/25" : "hover:border-foreground/25")}>
      <div className="flex items-center gap-2">
        <span className={cn("h-2 w-2 rounded-full", s.health === "HEALTHY" ? "bg-success" : "bg-destructive")}
              title={s.health === "HEALTHY" ? "Reachable with your role" : s.health_detail} />
        <span className="truncate text-sm font-semibold">{s.source_system_name}</span>
        <Badge variant="outline" className="ml-auto text-[10px]">{s.source_type === "SNOWFLAKE_SHARE" ? "share" : "database"}</Badge>
      </div>
      <p className="truncate font-mono text-[11px] text-muted-foreground">{s.database_name}.{s.schema_name}</p>
      <div>
        <div className="mb-1 flex justify-between text-[11px] text-muted-foreground">
          <span>{s.staged_tables} of {s.table_count ?? "?"} staged</span>
          <span>{coverage}%</span>
        </div>
        <div className="h-1.5 rounded-full bg-muted">
          <div className="h-1.5 rounded-full bg-success" style={{ width: `${coverage}%` }} />
        </div>
      </div>
      <p className="text-[11px] text-muted-foreground">
        {s.health === "UNREACHABLE" ? "Not reachable with the current role"
          : s.profiling_tables || s.active_jobs ? `Profiling ${s.profiling_tables || ""} now`.replace("  ", " ")
          : s.last_profiled_at ? `Last profiled ${s.last_profiled_at.slice(0, 16)}` : "Not profiled yet"}
      </p>
    </button>
  );
}

function StatusBadge({ t }: { t: InventoryTable }) {
  const meta = STATUS_META[t.status];
  return (
    <span title={t.error_message ?? undefined}>
      <Badge variant={meta.variant}>
        {t.status === "PROFILING" && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
        {meta.label}
      </Badge>
    </span>
  );
}

function AddSource({ databases, onAdded }: { databases: DatabaseRow[]; onAdded: (id: string) => void }) {
  const [database, setDatabase] = useState("");
  const [schemas, setSchemas] = useState<SchemaRow[]>([]);
  const [schema, setSchema] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const type = databases.find((d) => d.database_name === database)?.type === "IMPORTED DATABASE" ? "SNOWFLAKE_SHARE" : "SNOWFLAKE_DATABASE";
  return (
    <div className="flex flex-wrap items-end gap-3 rounded-xl border border-dashed bg-card p-4">
      <div>
        <label htmlFor="add_db" className="mb-1 block text-xs font-medium">Catalog</label>
        <select id="add_db" value={database} className="h-9 w-56 rounded-md border bg-card px-2 text-sm"
                onChange={(e) => {
                  setDatabase(e.target.value); setSchema(""); setSchemas([]);
                  if (e.target.value) start(async () => {
                    try { setSchemas((await loadSchemas(e.target.value)).schemas); }
                    catch (err) { setError(err instanceof Error ? err.message : "Could not list schemas"); }
                  });
                }}>
          <option value="">Choose a database or share</option>
          {databases.map((d) => <option key={d.database_name} value={d.database_name}>{d.database_name}</option>)}
        </select>
      </div>
      <div>
        <label htmlFor="add_schema" className="mb-1 block text-xs font-medium">Schema</label>
        <select id="add_schema" value={schema} disabled={!schemas.length} onChange={(e) => setSchema(e.target.value)}
                className="h-9 w-48 rounded-md border bg-card px-2 text-sm">
          <option value="">{pending ? "Loading…" : "Choose a schema"}</option>
          {schemas.map((s) => <option key={s.schema_name} value={s.schema_name}>{s.schema_name}</option>)}
        </select>
      </div>
      <div>
        <label htmlFor="add_name" className="mb-1 block text-xs font-medium">Name (optional)</label>
        <Input id="add_name" value={name} onChange={(e) => setName(e.target.value)} placeholder={schema || "CRM"} className="w-40" />
      </div>
      <Button disabled={!database || !schema || pending} onClick={() => start(async () => {
        setError("");
        const r = await registerConnection({ database, schema, source_type: type, source_system_name: name || undefined });
        if (!r.ok) setError(r.error);
        else onAdded(r.data.source_system_id);
      })}>
        {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />} Connect source
      </Button>
      {error && <p role="alert" className="w-full text-sm text-destructive">{error}</p>}
    </div>
  );
}

export function SourcesHub({
  initialOverview, databases, initialSelected, initialInventory,
}: {
  initialOverview: SourcesOverview;
  databases: DatabaseRow[];
  initialSelected: string;
  initialInventory: SourceInventory | null;
}) {
  const router = useRouter();
  const [overview, setOverview] = useState(initialOverview);
  const [selectedId, setSelectedId] = useState(initialSelected);
  const [inventory, setInventory] = useState<SourceInventory | null>(initialInventory);
  const [loadingInventory, setLoadingInventory] = useState(false);
  const [checked, setChecked] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ProfileStatus | "ALL">("ALL");
  const [drawer, setDrawer] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [adding, setAdding] = useState(initialOverview.sources.length === 0);
  const [modelingName, setModelingName] = useState("");
  const [confirmModeling, setConfirmModeling] = useState(false);
  const [pending, start] = useTransition();

  const refresh = useCallback(async (id = selectedId) => {
    const [ov, inv] = await Promise.all([loadOverview(), id ? loadInventory(id) : Promise.resolve(null)]);
    if (ov.ok) setOverview(ov.data);
    if (inv && inv.ok) setInventory(inv.data);
    else if (inv && !inv.ok) setNotice({ tone: "error", text: inv.error });
  }, [selectedId]);

  const busyTables = useMemo(() => new Set((inventory?.jobs ?? []).flatMap((j) => j.tables)), [inventory]);
  const tables = useMemo(() => (inventory?.tables ?? []).map((t) =>
    busyTables.has(t.table_name) && t.status !== "PROFILING" ? { ...t, status: "PROFILING" as const } : t,
  ), [inventory, busyTables]);
  const profiling = tables.some((t) => t.status === "PROFILING");

  useEffect(() => {
    if (!profiling) return;
    const timer = setInterval(() => { void refresh(); }, 4000);
    return () => clearInterval(timer);
  }, [profiling, refresh]);

  const selectSource = (id: string) => {
    if (id === selectedId) return;
    setSelectedId(id);
    setChecked([]);
    setFilter("ALL");
    setNotice(null);
    setLoadingInventory(true);
    router.replace(`/sources?source=${id}`, { scroll: false });
    loadInventory(id).then((r) => {
      if (r.ok) setInventory(r.data);
      else { setInventory(null); setNotice({ tone: "error", text: r.error }); }
      setLoadingInventory(false);
    });
  };

  const counts = useMemo(() => {
    const c: Record<string, number> = { ALL: tables.length };
    for (const t of tables) c[t.status] = (c[t.status] ?? 0) + 1;
    return c;
  }, [tables]);
  const visible = tables.filter((t) =>
    (filter === "ALL" || t.status === filter) && t.table_name.toLowerCase().includes(query.toLowerCase()));
  const chosen = tables.filter((t) => checked.includes(t.table_name));
  const allStaged = chosen.length > 0 && chosen.every((t) => t.status === "STAGED_READY_FOR_MODELING");
  const allVisibleChecked = visible.length > 0 && visible.every((t) => checked.includes(t.table_name));

  const toggle = (name: string) =>
    setChecked((prev) => (prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]));

  const runProfile = (names: string[], force: boolean) => start(async () => {
    setNotice(null);
    const r = await profileTables(selectedId, names, force);
    if (!r.ok) { setNotice({ tone: "error", text: r.error }); return; }
    setNotice({ tone: "ok", text: `Profiling ${r.data.tables.length} table(s) in place. Nothing is copied; results go to the profile store.` });
    setChecked([]);
    await refresh();
  });

  const toModeling = () => start(async () => {
    setNotice(null);
    const r = await sendToModeling(selectedId, checked, modelingName);
    if (!r.ok) { setNotice({ tone: "error", text: r.error }); return; }
    if (r.data.error) {
      setNotice({ tone: "error", text: `Run created but stopped at ${r.data.stage}: ${r.data.error}` });
      router.push(`/runs/${r.data.run_id}/source`);
      return;
    }
    router.push(`/runs/${r.data.run_id}/mapping`);
  });

  const source = overview.sources.find((s) => s.source_system_id === selectedId);
  const closeDrawer = useCallback(() => setDrawer(null), []);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h2>Sources</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Profile tables where they live, stage the profiles once, and start modeling from any combination of them.
            Profiling never copies data.
          </p>
        </div>
        <Button variant="outline" className="ml-auto" onClick={() => setAdding((v) => !v)}>
          <Plus className="h-4 w-4" /> Connect source
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi label="Connected sources" value={overview.totals.sources} />
        <Kpi label="Tables available" value={formatCount(overview.totals.tables)} />
        <Kpi label="Staged & ready" value={overview.totals.staged}
             hint={overview.totals.tables ? `${Math.round((overview.totals.staged / overview.totals.tables) * 100)}% coverage` : undefined} />
        <Kpi label="Profiling now" value={overview.totals.profiling} />
      </div>

      {adding && (
        <AddSource databases={databases} onAdded={(id) => {
          setAdding(false);
          start(async () => {
            const ov = await loadOverview();
            if (ov.ok) setOverview(ov.data);
            selectSource(id);
          });
        }} />
      )}

      {overview.sources.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {overview.sources.map((s) => (
            <SourceCard key={s.source_system_id} s={s} active={s.source_system_id === selectedId}
                        onClick={() => selectSource(s.source_system_id)} />
          ))}
        </div>
      )}

      {source && (
        <Card>
          <CardHeader className="flex flex-row flex-wrap items-end gap-3 space-y-0">
            <div className="mr-auto">
              <CardTitle className="flex items-center gap-2"><Database className="h-4 w-4 text-primary" /> {source.source_system_name} inventory</CardTitle>
              <CardDescription className="font-mono text-xs">{source.database_name}.{source.schema_name}</CardDescription>
            </div>
            <div className="relative">
              <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search tables" className="w-60 pl-8" />
            </div>
            <Button variant="ghost" size="sm" disabled={pending} onClick={() => start(() => refresh())} aria-label="Refresh inventory">
              <RefreshCw className={cn("h-4 w-4", pending && "animate-spin")} />
            </Button>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center gap-1">
              {FILTERS.map((f) => (
                <button key={f.value} type="button" onClick={() => setFilter(f.value)} aria-pressed={filter === f.value}
                        className={cn("rounded-full border px-3 py-1 text-xs",
                          filter === f.value ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted")}>
                  {f.label} <span className="tabular-nums opacity-70">{counts[f.value] ?? 0}</span>
                </button>
              ))}
            </div>

            <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 rounded-lg border bg-muted/60 px-3 py-2 backdrop-blur">
              <span className="text-sm font-medium" aria-live="polite">{checked.length} selected</span>
              <button type="button" className="text-xs text-primary hover:underline"
                      onClick={() => setChecked(tables.filter((t) => t.status === "UNPROFILED" || t.status === "STALE").map((t) => t.table_name))}>
                Select unprofiled and stale
              </button>
              <button type="button" className="text-xs text-primary hover:underline"
                      onClick={() => setChecked(tables.filter((t) => t.status === "STAGED_READY_FOR_MODELING").map((t) => t.table_name))}>
                Select staged
              </button>
              {checked.length > 0 && (
                <button type="button" className="text-xs text-muted-foreground hover:underline" onClick={() => setChecked([])}>Clear</button>
              )}
              <div className="ml-auto flex flex-wrap gap-2">
                <Button size="sm" disabled={pending || !checked.length} onClick={() => runProfile(checked, false)}>
                  <Sparkles className="h-3.5 w-3.5" /> Profile selected
                </Button>
                <Button size="sm" variant="outline" disabled={pending || !checked.length} onClick={() => runProfile(checked, true)}>
                  <RefreshCw className="h-3.5 w-3.5" /> Re-profile
                </Button>
                <Button size="sm" variant="secondary" disabled={pending || !allStaged}
                        title={allStaged ? undefined : "Every selected table must be staged & ready"}
                        onClick={() => setConfirmModeling(true)}>
                  Send to modeling <ArrowRight className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>

            {confirmModeling && allStaged && (
              <div className="flex flex-wrap items-end gap-3 rounded-lg border border-primary/40 bg-primary/5 p-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">Start a modeling run with {chosen.length} staged table{chosen.length === 1 ? "" : "s"}</p>
                  <p className="text-xs text-muted-foreground">
                    The run reads the tables in place and reuses their staged profiles, then opens at mapping.
                  </p>
                </div>
                <Input value={modelingName} onChange={(e) => setModelingName(e.target.value)}
                       placeholder={`${source.source_system_name} modeling`} className="w-64" aria-label="Run name" />
                <Button disabled={pending} onClick={toModeling}>
                  {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />} Create run
                </Button>
                <Button variant="ghost" onClick={() => setConfirmModeling(false)}>Cancel</Button>
              </div>
            )}

            {notice && (
              <p role={notice.tone === "error" ? "alert" : "status"}
                 className={cn("text-sm", notice.tone === "error" ? "text-destructive" : "text-muted-foreground")}>
                {notice.text}
              </p>
            )}

            <Table>
              <THead>
                <TR>
                  <TH className="w-10">
                    <input type="checkbox" aria-label="Select all visible tables" className="h-4 w-4" checked={allVisibleChecked}
                           onChange={() => setChecked((prev) => allVisibleChecked
                             ? prev.filter((n) => !visible.some((t) => t.table_name === n))
                             : Array.from(new Set([...prev, ...visible.map((t) => t.table_name)])))} />
                  </TH>
                  <TH>Table</TH><TH>Inferred domain</TH><TH className="text-right">Rows</TH><TH className="text-right">Cols</TH>
                  <TH>Profile</TH><TH>Quality</TH><TH>Staged path</TH><TH>Last profiled</TH><TH className="text-right">Actions</TH>
                </TR>
              </THead>
              <TBody>
                {loadingInventory && (
                  <TR><TD colSpan={10} className="py-6 text-center text-muted-foreground">
                    <Loader2 className="mr-2 inline h-4 w-4 animate-spin" /> Reading the source catalog…
                  </TD></TR>
                )}
                {!loadingInventory && visible.map((t) => {
                  const staged = t.status === "STAGED_READY_FOR_MODELING" || t.status === "STALE";
                  return (
                    <TR key={t.table_name} className={cn(checked.includes(t.table_name) && "bg-primary/5")}>
                      <TD>
                        <input type="checkbox" className="h-4 w-4" aria-label={`select ${t.table_name}`}
                               checked={checked.includes(t.table_name)} onChange={() => toggle(t.table_name)} />
                      </TD>
                      <TD>
                        <button type="button" disabled={!staged} onClick={() => setDrawer(t.table_name)}
                                className={cn("text-left font-medium", staged && "text-primary hover:underline")}>
                          {t.table_name}
                        </button>
                        <div className="text-[11px] text-muted-foreground">{t.table_type === "BASE TABLE" ? "table" : t.table_type.toLowerCase()}</div>
                      </TD>
                      <TD className="text-sm">{t.domain_name ?? <span className="text-muted-foreground">—</span>}</TD>
                      <TD className="text-right tabular-nums">{formatCount(t.row_count)}</TD>
                      <TD className="text-right tabular-nums">{t.column_count}</TD>
                      <TD><StatusBadge t={t} /></TD>
                      <TD className="text-xs text-muted-foreground">
                        {staged ? (
                          <span>
                            {t.avg_null_percentage != null ? `${Number(t.avg_null_percentage).toFixed(1)}% null` : "—"}
                            {t.key_candidates ? ` · ${t.key_candidates} key` : ""}
                            {t.pii_columns ? <span className="text-destructive"> · {t.pii_columns} PII</span> : ""}
                          </span>
                        ) : "—"}
                      </TD>
                      <TD className="max-w-[220px] truncate font-mono text-[11px] text-muted-foreground" title={t.stage_path ?? undefined}>
                        {t.stage_path ?? "—"}
                      </TD>
                      <TD className="text-xs text-muted-foreground">{t.profiled_at?.slice(0, 16) ?? "—"}</TD>
                      <TD className="text-right">
                        <div className="inline-flex gap-1">
                          {staged && (
                            <Button size="sm" variant="ghost" onClick={() => setDrawer(t.table_name)} aria-label={`View profile of ${t.table_name}`}>
                              <Eye className="h-3.5 w-3.5" />
                            </Button>
                          )}
                          <Button size="sm" variant="ghost" disabled={pending || t.status === "PROFILING"}
                                  onClick={() => runProfile([t.table_name], t.status !== "UNPROFILED")}
                                  aria-label={`${t.status === "UNPROFILED" ? "Profile" : "Re-profile"} ${t.table_name}`}>
                            {t.status === "UNPROFILED" ? <Sparkles className="h-3.5 w-3.5" /> : <RefreshCw className="h-3.5 w-3.5" />}
                          </Button>
                        </div>
                      </TD>
                    </TR>
                  );
                })}
                {!loadingInventory && visible.length === 0 && (
                  <TR><TD colSpan={10} className="py-6 text-center text-muted-foreground">
                    {tables.length ? "No tables match this filter." : "No tables visible in this schema with your role."}
                  </TD></TR>
                )}
              </TBody>
            </Table>
            <div className="flex flex-wrap gap-4 pt-1 text-[11px] text-muted-foreground">
              <span className="flex items-center gap-1"><CircleDashed className="h-3 w-3" /> Unprofiled: no stored profile</span>
              <span className="flex items-center gap-1"><CheckCircle2 className="h-3 w-3 text-success" /> Staged: profile on @METADATA.PROFILES_STAGE matches the source</span>
              <span className="flex items-center gap-1"><AlertTriangle className="h-3 w-3 text-warning" /> Stale: columns, row count or last change differ</span>
              <span className="flex items-center gap-1"><XCircle className="h-3 w-3 text-destructive" /> Failed: hover for the reason</span>
            </div>
          </CardContent>
        </Card>
      )}

      {!source && !adding && (
        <Card><CardContent className="py-10 text-center text-sm text-muted-foreground">
          No sources connected yet. Connect a database or share to start profiling.
        </CardContent></Card>
      )}

      {drawer && <ProfileDrawer sourceId={selectedId} table={drawer} onClose={closeDrawer} />}
    </div>
  );
}
