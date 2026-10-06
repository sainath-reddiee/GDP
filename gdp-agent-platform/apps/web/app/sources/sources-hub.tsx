"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle, ArrowRight, CheckCircle2, ChevronDown, CircleDashed, Database, Eye, FolderTree, Globe, History, Layers,
  Loader2, Plus, RefreshCw, Search, Sparkles, XCircle,
} from "lucide-react";
import type {
  CatalogInventory, InventoryTable, ProfileStatus, ProfileStoreRow, SourcesOverview,
} from "@/lib/types";
import type { DatabaseRow, SchemaRow } from "@/app/onboarding/catalog-types";
import { loadSchemas } from "@/app/onboarding/catalog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import type { DomainRow } from "@/app/onboarding/intent-types";
import { loadCatalogInventory, loadOverview, loadProfileStore, profileCatalogTables } from "./actions";
import { ModelPanel } from "./model-panel";
import { ConnectSource, type ManagedSource } from "./connect-source";
import { GradeChip, ProfileDrawer, type DrawerTab } from "./profile-drawer";

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

type Target = { database: string; schema: string };
type Drawer = Target & { table: string; tab?: DrawerTab };

function formatCount(n: number | null | undefined) {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}K`;
  return n.toLocaleString();
}

function Kpi({ icon: Icon, label, value, hint }: { icon: typeof Database; label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border bg-card px-4 py-3">
      <span className="rounded-lg bg-primary/10 p-2 text-primary"><Icon className="h-4 w-4" /></span>
      <div>
        <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
        <p className="text-2xl font-semibold tabular-nums">{value}</p>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
    </div>
  );
}

/** Searchable single-select used for databases and schemas (dozens to hundreds of entries). */
function Picker({ id, label, value, options, placeholder, disabled, loading, onChange }: {
  id: string; label: string; value: string; options: { value: string; hint?: string }[]; placeholder: string;
  disabled?: boolean; loading?: boolean; onChange: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const shown = options.filter((o) => o.value.toLowerCase().includes(query.toLowerCase())).slice(0, 200);
  return (
    <div ref={ref} className="relative min-w-[220px] flex-1">
      <label htmlFor={id} className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</label>
      <button id={id} type="button" disabled={disabled} onClick={() => setOpen((v) => !v)} aria-expanded={open}
              className="flex h-10 w-full items-center gap-2 rounded-lg border bg-card px-3 text-left text-sm disabled:opacity-50">
        {loading ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
        <span className={cn("truncate font-mono", !value && "font-sans text-muted-foreground")}>{value || placeholder}</span>
        <ChevronDown className="ml-auto h-4 w-4 text-muted-foreground" />
      </button>
      {open && (
        <div className="absolute z-30 mt-1 w-full rounded-lg border bg-card p-2 shadow-xl">
          <div className="relative mb-2">
            <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} placeholder={`Search ${label.toLowerCase()}`} className="pl-8" />
          </div>
          <ul role="listbox" className="max-h-72 overflow-y-auto">
            {shown.map((o) => (
              <li key={o.value}>
                <button type="button" role="option" aria-selected={o.value === value}
                        onClick={() => { onChange(o.value); setOpen(false); setQuery(""); }}
                        className={cn("flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted",
                          o.value === value && "bg-primary/10 text-primary")}>
                  <span className="truncate font-mono">{o.value}</span>
                  {o.hint && <span className="ml-auto text-[11px] text-muted-foreground">{o.hint}</span>}
                </button>
              </li>
            ))}
            {shown.length === 0 && <li className="px-2 py-1.5 text-sm text-muted-foreground">No match</li>}
          </ul>
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status, error, onOpen }: { status: ProfileStatus; error?: string | null; onOpen?: () => void }) {
  const meta = STATUS_META[status];
  if (status === "STALE" && onOpen) {
    return (
      <button type="button" onClick={onOpen} title="See what changed since profiling" className="hover:opacity-80">
        <Badge variant={meta.variant}>{meta.label} · see changes</Badge>
      </button>
    );
  }
  return (
    <span title={error ?? undefined}>
      <Badge variant={meta.variant}>
        {status === "PROFILING" && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
        {meta.label}
      </Badge>
    </span>
  );
}

export function SourcesHub({
  initialOverview, initialStore, databases, domains = [], initialTarget, initialInventory,
}: {
  initialOverview: SourcesOverview | null;
  domains?: DomainRow[];
  initialStore: ProfileStoreRow[];
  databases: DatabaseRow[];
  initialTarget: Target | null;
  initialInventory: CatalogInventory | null;
}) {
  const router = useRouter();
  const [overview, setOverview] = useState(initialOverview);
  const [store, setStore] = useState(initialStore);
  const [tab, setTab] = useState<"explore" | "store">("explore");
  const [database, setDatabase] = useState(initialTarget?.database ?? "");
  const [schema, setSchema] = useState(initialTarget?.schema ?? "");
  const [schemas, setSchemas] = useState<SchemaRow[]>([]);
  const [loadingSchemas, setLoadingSchemas] = useState(false);
  const [inventory, setInventory] = useState<CatalogInventory | null>(initialInventory);
  const [loadingInventory, setLoadingInventory] = useState(false);
  const [checked, setChecked] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ProfileStatus | "ALL">("ALL");
  const [storeQuery, setStoreQuery] = useState("");
  const [drawer, setDrawer] = useState<Drawer | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [modeling, setModeling] = useState<string[] | null>(null);
  const [connect, setConnect] = useState<{ managed: ManagedSource | null } | null>(null);
  const [pending, start] = useTransition();

  useEffect(() => {
    if (!initialTarget?.database) return;
    loadSchemas(initialTarget.database).then((r) => setSchemas(r.schemas)).catch(() => undefined);
  }, [initialTarget?.database]);

  const openTarget = useCallback((next: Target) => {
    setDatabase(next.database);
    setSchema(next.schema);
    setChecked([]);
    setFilter("ALL");
    setNotice(null);
    setTab("explore");
    setLoadingInventory(true);
    router.replace(`/sources?db=${encodeURIComponent(next.database)}&schema=${encodeURIComponent(next.schema)}`, { scroll: false });
    loadCatalogInventory(next.database, next.schema).then((r) => {
      if (r.ok) setInventory(r.data);
      else { setInventory(null); setNotice({ tone: "error", text: r.error }); }
      setLoadingInventory(false);
    });
  }, [router]);

  const pickDatabase = (name: string) => {
    setDatabase(name);
    setSchema("");
    setSchemas([]);
    setInventory(null);
    setLoadingSchemas(true);
    loadSchemas(name)
      .then((r) => setSchemas(r.schemas))
      .catch((e) => setNotice({ tone: "error", text: e instanceof Error ? e.message : "Could not list schemas" }))
      .finally(() => setLoadingSchemas(false));
  };

  const refresh = useCallback(async () => {
    const [inv, st, ov] = await Promise.all([
      database && schema ? loadCatalogInventory(database, schema) : Promise.resolve(null),
      loadProfileStore(),
      loadOverview(),
    ]);
    if (inv?.ok) setInventory(inv.data);
    if (st.ok) setStore(st.data.profiles);
    if (ov.ok) setOverview(ov.data);
  }, [database, schema]);

  const tables = inventory?.tables ?? [];
  const profilingNow = tables.some((t) => t.status === "PROFILING") || store.some((r) => r.status === "PROFILING");
  useEffect(() => {
    if (!profilingNow) return;
    const timer = setInterval(() => { void refresh(); }, 4000);
    return () => clearInterval(timer);
  }, [profilingNow, refresh]);

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
    const r = await profileCatalogTables(database, schema, names, force);
    if (!r.ok) { setNotice({ tone: "error", text: r.error }); return; }
    setNotice({ tone: "ok", text: `Profiling ${r.data.tables.length} table(s) of ${database}.${schema} in place. Nothing is copied.` });
    setChecked([]);
    await refresh();
  });

  const quickTargets = useMemo(() => {
    const seen = new Map<string, { target: Target; label: string; staged: number }>();
    for (const r of store) {
      const key = `${r.database_name}.${r.schema_name}`;
      const item = seen.get(key) ?? { target: { database: r.database_name, schema: r.schema_name }, label: key, staged: 0 };
      item.staged += r.status === "PROFILING" ? 0 : 1;
      seen.set(key, item);
    }
    for (const s of overview?.sources ?? []) {
      const key = `${s.database_name}.${s.schema_name}`;
      if (!seen.has(key)) seen.set(key, { target: { database: s.database_name, schema: s.schema_name }, label: key, staged: 0 });
    }
    return Array.from(seen.values()).slice(0, 12);
  }, [store, overview]);

  const storeVisible = store.filter((r) =>
    `${r.database_name}.${r.schema_name}.${r.table_name}`.toLowerCase().includes(storeQuery.toLowerCase()));
  const storeSchemas = new Set(store.map((r) => `${r.database_name}.${r.schema_name}`)).size;
  const closeDrawer = useCallback(() => setDrawer(null), []);
  const closeModeling = useCallback(() => setModeling(null), []);
  const closeConnect = useCallback(() => {
    setConnect(null);
    loadOverview().then((r) => r.ok && setOverview(r.data));
  }, []);
  const externals = (overview?.sources ?? []).filter((x) => x.source_type.startsWith("EXTERNAL_"));
  const currentExternal = externals.find((x) => x.database_name === database && x.schema_name === schema);
  const manage = (x: (typeof externals)[number]) => setConnect({ managed: {
    id: x.source_system_id, name: x.source_system_name, connector: x.connection_type ?? "upload",
    database: x.database_name, schema: x.schema_name,
  } });
  const dbOptions = databases.map((d) => ({ value: d.database_name, hint: d.type === "IMPORTED DATABASE" ? "share" : undefined }));
  const schemaOptions = schemas.map((s) => ({ value: s.schema_name }));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-3">
       <div>
        <h2>Sources</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Browse any database or share your role can read, profile the tables you choose where they live, and start
          modeling from any combination of staged profiles. Profiling never copies data.
        </p>
       </div>
        <Button className="ml-auto" onClick={() => setConnect({ managed: null })}>
          <Plus className="h-4 w-4" /> Connect source
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi icon={Database} label="Catalogs available" value={databases.length} />
        <Kpi icon={FolderTree} label="Schemas profiled" value={storeSchemas} />
        <Kpi icon={Layers} label="Tables staged" value={store.filter((r) => r.status !== "PROFILING").length} />
        <Kpi icon={Sparkles} label="Profiling now" value={store.filter((r) => r.status === "PROFILING").length} />
      </div>

      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="flex flex-wrap items-end gap-3">
            <Picker id="pick_db" label="Database" value={database} options={dbOptions} placeholder="Choose any database or share"
                    onChange={pickDatabase} />
            <Picker id="pick_schema" label="Schema" value={schema} options={schemaOptions} loading={loadingSchemas}
                    placeholder={database ? "Choose a schema" : "Pick a database first"} disabled={!database || loadingSchemas}
                    onChange={(s) => openTarget({ database, schema: s })} />
          </div>
          {quickTargets.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="flex items-center gap-1 text-[11px] uppercase tracking-wide text-muted-foreground">
                <History className="h-3 w-3" /> Jump to
              </span>
              {quickTargets.map((q) => (
                <button key={q.label} type="button" onClick={() => openTarget(q.target)}
                        className={cn("rounded-full border px-3 py-1 font-mono text-[11px] hover:bg-muted",
                          q.target.database === database && q.target.schema === schema && "border-primary bg-primary/10 text-primary")}>
                  {q.label}{q.staged ? <span className="ml-1 text-success">· {q.staged} staged</span> : null}
                </button>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {externals.length > 0 && (
        <div className="space-y-2">
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">External sources</p>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {externals.map((x) => {
              const landedCount = x.landed_tables ?? 0;
              const isLanded = x.health === "HEALTHY" && landedCount > 0;
              return (
                <div key={x.source_system_id} className="flex items-center gap-3 rounded-xl border bg-card p-3">
                  <Globe className="h-4 w-4 text-muted-foreground" />
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 text-sm font-semibold">
                      {x.source_system_name}
                      <Badge variant="outline" className="text-[10px]">{x.connection_type ?? "external"}</Badge>
                    </p>
                    <p className="text-[11px] text-muted-foreground">
                      {isLanded ? `${landedCount} table${landedCount === 1 ? "" : "s"} landed · ${x.staged_tables} staged` : "Not landed into Snowflake yet"}
                    </p>
                  </div>
                  <div className="ml-auto flex gap-1">
                    <Button size="sm" variant="ghost" onClick={() => manage(x)}>Land</Button>
                    {isLanded && (
                      <Button size="sm" variant="outline" onClick={() => openTarget({ database: x.database_name, schema: x.schema_name })}>
                        Open
                      </Button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="flex gap-1 border-b" role="tablist">
        {([
          ["explore", database && schema ? `Explore ${database}.${schema}${currentExternal ? ` (external: ${currentExternal.source_system_name})` : ""}` : "Explore"],
          ["store", `Profile store (${store.length})`],
        ] as const).map(([value, label]) => (
          <button key={value} type="button" role="tab" aria-selected={tab === value} onClick={() => setTab(value)}
                  className={cn("-mb-px border-b-2 px-4 py-2 text-sm font-medium",
                    tab === value ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>
            {label}
          </button>
        ))}
        <Button variant="ghost" size="sm" className="ml-auto" disabled={pending} onClick={() => start(() => refresh())} aria-label="Refresh">
          <RefreshCw className={cn("h-4 w-4", pending && "animate-spin")} />
        </Button>
      </div>

      {notice && (
        <p role={notice.tone === "error" ? "alert" : "status"}
           className={cn("rounded-lg border px-3 py-2 text-sm", notice.tone === "error" ? "border-destructive/40 text-destructive" : "text-muted-foreground")}>
          {notice.text}
        </p>
      )}

      {tab === "explore" && !(database && schema) && (
        <Card><CardContent className="py-12 text-center text-sm text-muted-foreground">
          Pick a database and schema above, or jump to one you profiled before.
        </CardContent></Card>
      )}

      {tab === "explore" && database && schema && (
        <Card>
          <CardContent className="space-y-3 pt-6">
            <div className="flex flex-wrap items-center gap-2">
              {FILTERS.map((f) => (
                <button key={f.value} type="button" onClick={() => setFilter(f.value)} aria-pressed={filter === f.value}
                        className={cn("rounded-full border px-3 py-1 text-xs",
                          filter === f.value ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted")}>
                  {f.label} <span className="tabular-nums opacity-70">{counts[f.value] ?? 0}</span>
                </button>
              ))}
              <div className="relative ml-auto">
                <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search tables" className="w-60 pl-8" />
              </div>
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
                        onClick={() => setModeling([...checked].sort())}>
                  Send to modeling <ArrowRight className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>

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
                    <Loader2 className="mr-2 inline h-4 w-4 animate-spin" /> Reading {database}.{schema}…
                  </TD></TR>
                )}
                {!loadingInventory && visible.map((t: InventoryTable) => {
                  const staged = t.status === "STAGED_READY_FOR_MODELING" || t.status === "STALE";
                  const open = () => setDrawer({ database, schema, table: t.table_name });
                  return (
                    <TR key={t.table_name} className={cn(checked.includes(t.table_name) && "bg-primary/5")}>
                      <TD>
                        <input type="checkbox" className="h-4 w-4" aria-label={`select ${t.table_name}`}
                               checked={checked.includes(t.table_name)} onChange={() => toggle(t.table_name)} />
                      </TD>
                      <TD>
                        <button type="button" disabled={!staged} onClick={open}
                                className={cn("text-left font-medium", staged && "text-primary hover:underline")}>
                          {t.table_name}
                        </button>
                        <div className="text-[11px] text-muted-foreground">{t.table_type === "BASE TABLE" ? "table" : t.table_type.toLowerCase()}</div>
                      </TD>
                      <TD className="text-sm">{t.domain_name ?? <span className="text-muted-foreground">—</span>}</TD>
                      <TD className="text-right tabular-nums">{formatCount(t.row_count)}</TD>
                      <TD className="text-right tabular-nums">{t.column_count}</TD>
                      <TD>
                        <StatusBadge status={t.status} error={t.error_message}
                                     onOpen={() => setDrawer({ database, schema, table: t.table_name, tab: "overview" })} />
                      </TD>
                      <TD className="text-xs text-muted-foreground">
                        {staged ? (
                          <div className="flex items-center gap-2">
                            <GradeChip card={t.quality} />
                            <span>
                              {t.avg_null_percentage != null ? `${Number(t.avg_null_percentage).toFixed(1)}% null` : ""}
                              {t.key_candidates ? ` · ${t.key_candidates} key` : ""}
                              {t.pii_columns ? <span className="text-destructive"> · {t.pii_columns} PII</span> : ""}
                            </span>
                          </div>
                        ) : "—"}
                      </TD>
                      <TD className="max-w-[220px] truncate font-mono text-[11px] text-muted-foreground" title={t.stage_path ?? undefined}>
                        {t.stage_path ?? "—"}
                      </TD>
                      <TD className="text-xs text-muted-foreground">{t.profiled_at?.slice(0, 16) ?? "—"}</TD>
                      <TD className="text-right">
                        <div className="inline-flex gap-1">
                          {staged && (
                            <Button size="sm" variant="ghost" onClick={open} aria-label={`View profile of ${t.table_name}`}>
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
              <span className="flex items-center gap-1"><CheckCircle2 className="h-3 w-3 text-success" /> Staged: stored profile matches the source</span>
              <span className="flex items-center gap-1"><AlertTriangle className="h-3 w-3 text-warning" /> Stale: columns, row count or last change differ</span>
              <span className="flex items-center gap-1"><XCircle className="h-3 w-3 text-destructive" /> Failed: hover for the reason</span>
            </div>
          </CardContent>
        </Card>
      )}

      {tab === "store" && (
        <Card>
          <CardContent className="space-y-3 pt-6">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm text-muted-foreground">
                Every profile on <span className="font-mono">@METADATA.PROFILES_STAGE</span>, from any database. Open one to
                inspect it, or jump to its schema to profile more or start modeling.
              </p>
              <div className="relative ml-auto">
                <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input value={storeQuery} onChange={(e) => setStoreQuery(e.target.value)} placeholder="Search database, schema or table" className="w-72 pl-8" />
              </div>
            </div>
            <Table>
              <THead>
                <TR>
                  <TH>Table</TH><TH>Database.schema</TH><TH className="text-right">Rows</TH><TH className="text-right">Cols</TH>
                  <TH>Quality</TH><TH>Status</TH><TH>Profiled</TH><TH className="text-right">Actions</TH>
                </TR>
              </THead>
              <TBody>
                {storeVisible.map((r) => (
                  <TR key={`${r.source_name}.${r.database_name}.${r.schema_name}.${r.table_name}`}>
                    <TD className="font-medium">{r.table_name}</TD>
                    <TD>
                      <button type="button" className="font-mono text-xs text-primary hover:underline"
                              onClick={() => openTarget({ database: r.database_name, schema: r.schema_name })}>
                        {r.database_name}.{r.schema_name}
                      </button>
                    </TD>
                    <TD className="text-right tabular-nums">{formatCount(r.row_count)}</TD>
                    <TD className="text-right tabular-nums">{r.column_count ?? "—"}</TD>
                    <TD className="text-xs text-muted-foreground">
                      {r.avg_null_percentage != null ? `${Number(r.avg_null_percentage).toFixed(1)}% null` : "—"}
                      {r.key_candidates ? ` · ${r.key_candidates} key` : ""}
                      {r.pii_columns ? <span className="text-destructive"> · {r.pii_columns} PII</span> : ""}
                    </TD>
                    <TD>
                      <StatusBadge status={r.status === "PROFILING" ? "PROFILING" : r.status === "FAILED" ? "FAILED" : "STAGED_READY_FOR_MODELING"}
                                   error={r.error_message} />
                    </TD>
                    <TD className="text-xs text-muted-foreground">{r.profiled_at?.slice(0, 16)}</TD>
                    <TD className="text-right">
                      {r.status !== "PROFILING" && (
                        <Button size="sm" variant="ghost" aria-label={`View profile of ${r.table_name}`}
                                onClick={() => setDrawer({ database: r.database_name, schema: r.schema_name, table: r.table_name })}>
                          <Eye className="h-3.5 w-3.5" />
                        </Button>
                      )}
                    </TD>
                  </TR>
                ))}
                {storeVisible.length === 0 && (
                  <TR><TD colSpan={8} className="py-6 text-center text-muted-foreground">
                    {store.length ? "Nothing matches that search." : "Nothing profiled yet. Explore a schema and profile some tables."}
                  </TD></TR>
                )}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {drawer && (
        <ProfileDrawer database={drawer.database} schema={drawer.schema} table={drawer.table} initialTab={drawer.tab}
                       onClose={closeDrawer}
                       onReprofile={drawer.database === database && drawer.schema === schema
                         ? (table) => { setDrawer(null); runProfile([table], true); } : undefined} />
      )}
      {connect && (
        <ConnectSource initial={connect.managed} onClose={closeConnect}
                       onSnowflake={() => document.getElementById("pick_db")?.click()}
                       onOpenSchema={(t) => openTarget(t)} />
      )}
      {modeling && (
        <ModelPanel database={database} schema={schema} tables={modeling} domains={domains} onClose={closeModeling} />
      )}
    </div>
  );
}
