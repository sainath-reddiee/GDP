"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle, ArrowRight, CheckCircle2, ChevronDown, ChevronRight, CircleDashed, Database, Eye, FolderTree, Globe,
  HardDrive, History, Layers, Loader2, Plus, RefreshCw, Search, Sparkles, Table2, X, XCircle,
} from "lucide-react";
import type {
  CatalogInventory, InventoryTable, ProfileStatus, ProfileStoreRow, SourcesOverview,
} from "@/lib/types";
import type { DatabaseRow, SchemaRow } from "@/app/onboarding/catalog-types";
import { loadSchemas } from "@/app/onboarding/catalog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { displayDomain } from "@/lib/catalog-display";
import type { DomainRow } from "@/app/onboarding/intent-types";
import { loadCatalogInventory, loadOverview, loadProfileStore, profileCatalogTables } from "./actions";
import { ModelPanel } from "./model-panel";
import { ConnectSource, type ManagedSource } from "./connect-source";
import { GradeChip, ProfileDrawer, type DrawerTab } from "./profile-drawer";

const STATUS_META: Record<ProfileStatus, { label: string; dot: string; pill: string; icon: typeof CheckCircle2 }> = {
  UNPROFILED: { label: "Unprofiled", dot: "bg-muted-foreground/40", pill: "bg-muted text-muted-foreground", icon: CircleDashed },
  PROFILING: { label: "Profiling", dot: "bg-primary animate-pulse", pill: "bg-primary/10 text-primary", icon: Loader2 },
  STAGED_READY_FOR_MODELING: { label: "Ready", dot: "bg-success", pill: "bg-success/10 text-success", icon: CheckCircle2 },
  STALE: { label: "Stale", dot: "bg-warning", pill: "bg-warning/10 text-warning", icon: AlertTriangle },
  FAILED: { label: "Failed", dot: "bg-destructive", pill: "bg-destructive/10 text-destructive", icon: XCircle },
};
const FILTERS: { value: ProfileStatus | "ALL"; label: string }[] = [
  { value: "ALL", label: "All tables" },
  { value: "STAGED_READY_FOR_MODELING", label: "Ready" },
  { value: "UNPROFILED", label: "Unprofiled" },
  { value: "STALE", label: "Stale" },
  { value: "PROFILING", label: "Profiling" },
  { value: "FAILED", label: "Failed" },
];
const POLL_MS = 4000;

type Target = { database: string; schema: string };
type Drawer = Target & { table: string; tab?: DrawerTab };

function formatCount(n: number | null | undefined) {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}K`;
  return n.toLocaleString();
}

function ago(value: string | null | undefined) {
  if (!value) return "—";
  // Snowflake renders "2026-10-02 23:51:00.123 -0700"; make it ISO 8601 before parsing.
  const iso = value.trim()
    .replace(/^(\d{4}-\d{2}-\d{2})[ T]/, "$1T")
    .replace(/\s*([+-])(\d{2}):?(\d{2})$/, "$1$2:$3");
  const at = new Date(iso);
  const mins = Math.round((Date.now() - at.getTime()) / 60000);
  if (Number.isNaN(mins)) return value.slice(0, 16);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`;
  if (mins < 60 * 24 * 30) return `${Math.round(mins / 1440)}d ago`;
  return value.slice(0, 10);
}

function Kpi({ icon: Icon, label, value, hint, tone }: {
  icon: typeof Database; label: string; value: React.ReactNode; hint?: string; tone: string;
}) {
  return (
    <div className="group relative overflow-hidden rounded-2xl border bg-card p-4 shadow-sm transition hover:shadow-md">
      <div className={cn("absolute -right-6 -top-6 h-20 w-20 rounded-full opacity-10", tone)} />
      <div className="flex items-center gap-3">
        <span className={cn("grid h-9 w-9 place-items-center rounded-xl text-white shadow-sm", tone)}>
          <Icon className="h-4 w-4" />
        </span>
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
      </div>
      <p className="mt-3 text-3xl font-semibold tracking-tight tabular-nums">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** Searchable single-select used for databases and schemas (dozens to hundreds of entries). */
function Picker({ id, label, value, options, placeholder, disabled, loading, icon: Icon, onChange }: {
  id: string; label: string; value: string; options: { value: string; hint?: string }[]; placeholder: string;
  disabled?: boolean; loading?: boolean; icon: typeof Database; onChange: (v: string) => void;
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
    <div ref={ref} className="relative min-w-[240px] flex-1">
      <button id={id} type="button" disabled={disabled} onClick={() => setOpen((v) => !v)} aria-expanded={open}
              aria-label={label}
              className={cn("flex h-12 w-full items-center gap-3 rounded-xl border bg-background px-3 text-left transition",
                "hover:border-primary/50 disabled:opacity-50", open && "border-primary ring-2 ring-primary/15")}>
        <span className="grid h-7 w-7 place-items-center rounded-lg bg-muted text-muted-foreground">
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Icon className="h-3.5 w-3.5" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[10px] font-medium uppercase tracking-wider text-muted-foreground">{label}</span>
          <span className={cn("block truncate font-mono text-sm", !value && "font-sans text-muted-foreground")}>
            {value || placeholder}
          </span>
        </span>
        <ChevronDown className={cn("h-4 w-4 text-muted-foreground transition", open && "rotate-180")} />
      </button>
      {open && (
        <div className="absolute z-30 mt-2 w-full rounded-xl border bg-card p-2 shadow-2xl">
          <div className="relative mb-2">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} placeholder={`Search ${label.toLowerCase()}`} className="pl-8" />
          </div>
          <ul role="listbox" className="max-h-72 overflow-y-auto">
            {shown.map((o) => (
              <li key={o.value}>
                <button type="button" role="option" aria-selected={o.value === value}
                        onClick={() => { onChange(o.value); setOpen(false); setQuery(""); }}
                        className={cn("flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-muted",
                          o.value === value && "bg-primary/10 text-primary")}>
                  <span className="truncate font-mono">{o.value}</span>
                  {o.hint && <Badge variant="outline" className="ml-auto text-[10px]">{o.hint}</Badge>}
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

function StatusPill({ status, error, stagePath, onOpen }: {
  status: ProfileStatus; error?: string | null; stagePath?: string | null; onOpen?: () => void;
}) {
  const meta = STATUS_META[status];
  const title = status === "FAILED" ? error ?? "Profiling failed"
    : status === "STALE" ? "The source changed since it was profiled. Click to see what changed."
    : stagePath ? `Stored at @METADATA.PROFILES_STAGE/${stagePath}` : undefined;
  const body = (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium", meta.pill)}>
      <span className={cn("h-1.5 w-1.5 rounded-full", meta.dot)} />
      {meta.label}
      {status === "STALE" && onOpen && <ChevronRight className="h-3 w-3" />}
    </span>
  );
  return status === "STALE" && onOpen
    ? <button type="button" onClick={onOpen} title={title} className="hover:opacity-80">{body}</button>
    : <span title={title}>{body}</span>;
}

function QualityCell({ t }: { t: InventoryTable }) {
  const score = t.quality?.overall;
  if (score == null) return <span className="text-xs text-muted-foreground">—</span>;
  const tone = score >= 90 ? "bg-success" : score >= 75 ? "bg-primary" : score >= 60 ? "bg-warning" : "bg-destructive";
  return (
    <div className="flex min-w-[150px] items-center gap-2">
      <GradeChip card={t.quality} />
      <div className="flex-1">
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div className={cn("h-full rounded-full", tone)} style={{ width: `${Math.max(4, Math.min(100, score))}%` }} />
        </div>
        <p className="mt-1 text-[11px] text-muted-foreground">
          {t.avg_null_percentage != null ? `${Number(t.avg_null_percentage).toFixed(1)}% null` : ""}
          {t.key_candidates ? ` · ${t.key_candidates} key` : ""}
          {t.pii_columns ? <span className="text-destructive"> · {t.pii_columns} PII</span> : ""}
        </p>
      </div>
    </div>
  );
}

function DomainChip({ t }: { t: InventoryTable }) {
  const name = displayDomain(t.domain_name);
  if (!name) return <span className="text-xs text-muted-foreground">—</span>;
  if (!t.domain_inferred) {
    return <span className="inline-flex rounded-full bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary">{name}</span>;
  }
  return (
    <span title="Inferred from table and column names against the domain contracts"
          className="inline-flex items-center gap-1 whitespace-nowrap rounded-full border border-dashed border-primary/40 px-2.5 py-0.5 text-xs">
      <Sparkles className="h-3 w-3 text-primary" />
      {name}
      <span className="text-muted-foreground">{Math.round((t.domain_confidence ?? 0) * 100)}%</span>
    </span>
  );
}

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: 5 }).map((_, i) => (
        <tr key={i} className="border-b">
          {Array.from({ length: 8 }).map((__, j) => (
            <td key={j} className="px-4 py-4">
              <div className="h-3 animate-pulse rounded bg-muted" style={{ width: `${40 + ((i * 7 + j * 13) % 50)}%` }} />
            </td>
          ))}
        </tr>
      ))}
    </>
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
  // Separate busy flags: a background refresh must never block selection actions.
  const [submitting, setSubmitting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const inFlight = useRef(false);
  const targetRef = useRef<Target>({ database, schema });
  targetRef.current = { database, schema };

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
      const now = targetRef.current;
      if (now.database !== next.database || now.schema !== next.schema) return;
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

  /** One refresh at a time; overlapping calls are dropped instead of queued behind each other. */
  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setRefreshing(true);
    const { database: db, schema: sc } = targetRef.current;
    try {
      const [inv, st, ov] = await Promise.all([
        db && sc ? loadCatalogInventory(db, sc) : Promise.resolve(null),
        loadProfileStore(),
        loadOverview(),
      ]);
      const now = targetRef.current;
      if (inv?.ok && now.database === db && now.schema === sc) setInventory(inv.data);
      if (st.ok) setStore(st.data.profiles);
      if (ov.ok) setOverview(ov.data);
    } finally {
      inFlight.current = false;
      setRefreshing(false);
    }
  }, []);

  const tables = useMemo(() => inventory?.tables ?? [], [inventory]);
  const profilingNow = tables.some((t) => t.status === "PROFILING") || store.some((r) => r.status === "PROFILING");
  useEffect(() => {
    if (!profilingNow) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      await refresh();
      if (!cancelled) timer = setTimeout(tick, POLL_MS);
    };
    timer = setTimeout(tick, POLL_MS);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [profilingNow, refresh]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { ALL: tables.length };
    for (const t of tables) c[t.status] = (c[t.status] ?? 0) + 1;
    return c;
  }, [tables]);
  const visible = tables.filter((t) =>
    (filter === "ALL" || t.status === filter) && t.table_name.toLowerCase().includes(query.toLowerCase()));
  const chosen = tables.filter((t) => checked.includes(t.table_name));
  const notReady = chosen.filter((t) => t.status !== "STAGED_READY_FOR_MODELING");
  const canModel = chosen.length > 0 && notReady.length === 0;
  const allVisibleChecked = visible.length > 0 && visible.every((t) => checked.includes(t.table_name));
  const toggle = (name: string) =>
    setChecked((prev) => (prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]));

  const runProfile = async (names: string[], force: boolean) => {
    if (!names.length || submitting) return;
    setSubmitting(true);
    setNotice(null);
    try {
      const r = await profileCatalogTables(database, schema, names, force);
      if (!r.ok) { setNotice({ tone: "error", text: r.error }); return; }
      setNotice({ tone: "ok", text: `Profiling ${r.data.tables.length} table${r.data.tables.length === 1 ? "" : "s"} of ${database}.${schema} in place. Nothing is copied.` });
      setChecked([]);
    } finally {
      setSubmitting(false);
    }
    void refresh();
  };

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
    return Array.from(seen.values()).slice(0, 10);
  }, [store, overview]);

  const storeVisible = store.filter((r) =>
    `${r.database_name}.${r.schema_name}.${r.table_name}`.toLowerCase().includes(storeQuery.toLowerCase()));
  const storeSchemas = new Set(store.map((r) => `${r.database_name}.${r.schema_name}`)).size;
  const stagedTotal = store.filter((r) => r.status !== "PROFILING").length;
  const profilingTotal = store.filter((r) => r.status === "PROFILING").length;
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

  const ready = counts.STAGED_READY_FOR_MODELING ?? 0;
  const coverage = tables.length ? Math.round((100 * ready) / tables.length) : 0;
  const graded = tables.filter((t) => t.quality?.overall != null);
  const avgQuality = graded.length
    ? Math.round(graded.reduce((n, t) => n + (t.quality!.overall as number), 0) / graded.length) : null;
  const totalRows = tables.reduce((n, t) => n + (t.row_count ?? 0), 0);
  const schemaDomain = inventory?.domain_candidates?.[0];

  return (
    <div className="space-y-6 pb-24">
      {/* header */}
      <div className="flex flex-wrap items-end gap-4">
        <div className="min-w-0">
          <p className="eyebrow">Work</p>
          <h1 className="mt-1">Sources</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Browse any database or share your role can read, profile tables where they live, and start modeling from any
            combination of staged profiles. Profiling never copies data.
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="outline" size="sm" disabled={refreshing} onClick={() => void refresh()} aria-label="Refresh">
            <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} /> Refresh
          </Button>
          <Button onClick={() => setConnect({ managed: null })}>
            <Plus className="h-4 w-4" /> Connect source
          </Button>
        </div>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Kpi icon={Database} label="Catalogs available" value={databases.length} hint="Databases and shares your role can read" tone="bg-sky-500" />
        <Kpi icon={FolderTree} label="Schemas profiled" value={storeSchemas} hint="With at least one stored profile" tone="bg-violet-500" />
        <Kpi icon={Layers} label="Tables staged" value={stagedTotal} hint="Profiles ready to reuse in any run" tone="bg-emerald-500" />
        <Kpi icon={Sparkles} label="Profiling now" value={profilingTotal}
             hint={profilingTotal ? "Updates every few seconds" : "Nothing running"} tone="bg-amber-500" />
      </div>

      {/* catalog bar */}
      <div className="rounded-2xl border bg-card p-4 shadow-sm">
        <div className="flex flex-wrap items-center gap-3">
          <Picker id="pick_db" label="Database" icon={Database} value={database} options={dbOptions}
                  placeholder="Choose any database or share" onChange={pickDatabase} />
          <ChevronRight className="hidden h-4 w-4 text-muted-foreground md:block" />
          <Picker id="pick_schema" label="Schema" icon={FolderTree} value={schema} options={schemaOptions} loading={loadingSchemas}
                  placeholder={database ? "Choose a schema" : "Pick a database first"} disabled={!database || loadingSchemas}
                  onChange={(s) => openTarget({ database, schema: s })} />
        </div>
        {quickTargets.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3">
            <span className="flex items-center gap-1 text-xs font-medium text-muted-foreground">
              <History className="h-3.5 w-3.5" /> Recent
            </span>
            {quickTargets.map((q) => {
              const active = q.target.database === database && q.target.schema === schema;
              return (
                <button key={q.label} type="button" onClick={() => openTarget(q.target)}
                        className={cn("inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition hover:border-primary/50 hover:bg-muted",
                          active && "border-primary bg-primary/10 text-primary")}>
                  <span className="font-mono">{q.label}</span>
                  {q.staged > 0 && <span className="rounded-full bg-success/15 px-1.5 text-[10px] font-semibold text-success">{q.staged}</span>}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* external sources */}
      {externals.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Connected external sources</p>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {externals.map((x) => {
              const landedCount = x.landed_tables ?? 0;
              const isLanded = x.health === "HEALTHY" && landedCount > 0;
              if (x.connection_type === "oracle" && x.oracle) return <OracleCard key={x.source_system_id} x={x} landedCount={landedCount} isLanded={isLanded}
                                                                              onManage={() => manage(x)} onOpen={() => openTarget({ database: x.database_name, schema: x.schema_name })} />;
              return (
                <div key={x.source_system_id} className="flex items-center gap-3 rounded-2xl border bg-card p-4 shadow-sm">
                  <span className="grid h-10 w-10 place-items-center rounded-xl bg-violet-500/10 text-violet-600">
                    <Globe className="h-5 w-5" />
                  </span>
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 text-sm font-semibold">
                      {x.source_system_name}
                      <Badge variant="outline" className="text-[10px]">{x.connection_type ?? "external"}</Badge>
                    </p>
                    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <span className={cn("h-1.5 w-1.5 rounded-full", isLanded ? "bg-success" : "bg-warning")} />
                      {isLanded ? `${landedCount} landed · ${x.staged_tables} staged` : "Not landed into Snowflake yet"}
                    </p>
                  </div>
                  <div className="ml-auto flex gap-1">
                    <Button size="sm" variant="ghost" onClick={() => manage(x)}>Land files</Button>
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

      {/* workspace */}
      <div className="rounded-2xl border bg-card shadow-sm">
        <div className="flex flex-wrap items-center gap-3 border-b px-5 py-3">
          <div role="tablist" className="inline-flex rounded-xl bg-muted p-1">
            {([
              ["explore", "Explore schema"],
              ["store", `Profile store · ${store.length}`],
            ] as const).map(([value, label]) => (
              <button key={value} type="button" role="tab" aria-selected={tab === value} onClick={() => setTab(value)}
                      className={cn("rounded-lg px-3 py-1.5 text-sm font-medium transition",
                        tab === value ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>
                {label}
              </button>
            ))}
          </div>
          {tab === "explore" && database && schema && (
            <div className="flex min-w-0 items-center gap-2">
              <Table2 className="h-4 w-4 text-muted-foreground" />
              <span className="truncate font-mono text-sm font-medium">{database}.{schema}</span>
              {currentExternal && <Badge variant="outline">external · {currentExternal.source_system_name}</Badge>}
              {schemaDomain && displayDomain(schemaDomain.domain_name) && (
                <span className="inline-flex items-center gap-1 rounded-full border border-dashed border-primary/40 px-2 py-0.5 text-xs"
                      title="Detected from table and column names against the domain contracts">
                  <Sparkles className="h-3 w-3 text-primary" /> {schemaDomain.domain_name} domain
                  <span className="text-muted-foreground">{Math.round(schemaDomain.confidence * 100)}%</span>
                </span>
              )}
            </div>
          )}
        </div>

        {notice && (
          <div role={notice.tone === "error" ? "alert" : "status"}
               className={cn("mx-5 mt-4 flex items-start gap-2 rounded-xl border px-3 py-2 text-sm",
                 notice.tone === "error" ? "border-destructive/40 bg-destructive/5 text-destructive" : "border-success/30 bg-success/5")}>
            {notice.tone === "error" ? <XCircle className="mt-0.5 h-4 w-4" /> : <CheckCircle2 className="mt-0.5 h-4 w-4 text-success" />}
            <span className="flex-1">{notice.text}</span>
            <button type="button" aria-label="Dismiss" onClick={() => setNotice(null)} className="opacity-60 hover:opacity-100">
              <X className="h-4 w-4" />
            </button>
          </div>
        )}

        {tab === "explore" && !(database && schema) && (
          <div className="grid place-items-center px-6 py-16 text-center">
            <span className="grid h-14 w-14 place-items-center rounded-2xl bg-primary/10 text-primary">
              <Database className="h-6 w-6" />
            </span>
            <h3 className="mt-4 text-base font-semibold">Pick a schema to explore</h3>
            <p className="mt-1 max-w-md text-sm text-muted-foreground">
              Choose a database and schema above, or open a recent one. Tables are profiled in place and the profiles are
              reused by every run.
            </p>
            <Button className="mt-4" variant="outline" onClick={() => document.getElementById("pick_db")?.click()}>
              Choose a database
            </Button>
          </div>
        )}

        {tab === "explore" && database && schema && (
          <div className="space-y-4 p-5">
            {/* schema summary */}
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="rounded-xl border p-3">
                <p className="text-xs text-muted-foreground">Tables</p>
                <p className="text-xl font-semibold tabular-nums">{tables.length}</p>
                <p className="text-[11px] text-muted-foreground">{formatCount(totalRows)} rows in total</p>
              </div>
              <div className="rounded-xl border p-3">
                <div className="flex items-baseline justify-between">
                  <p className="text-xs text-muted-foreground">Ready for modeling</p>
                  <p className="text-xs font-medium tabular-nums">{coverage}%</p>
                </div>
                <p className="text-xl font-semibold tabular-nums">{ready}<span className="text-sm text-muted-foreground"> / {tables.length}</span></p>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                  <div className="h-full rounded-full bg-success transition-all" style={{ width: `${coverage}%` }} />
                </div>
              </div>
              <div className="rounded-xl border p-3">
                <p className="text-xs text-muted-foreground">Average quality</p>
                <p className="text-xl font-semibold tabular-nums">{avgQuality ?? "—"}</p>
                <p className="text-[11px] text-muted-foreground">across {graded.length} profiled table{graded.length === 1 ? "" : "s"}</p>
              </div>
              <div className="rounded-xl border p-3">
                <p className="text-xs text-muted-foreground">Needs attention</p>
                <p className="text-xl font-semibold tabular-nums">{(counts.STALE ?? 0) + (counts.FAILED ?? 0)}</p>
                <p className="text-[11px] text-muted-foreground">{counts.STALE ?? 0} stale · {counts.FAILED ?? 0} failed</p>
              </div>
            </div>

            {/* toolbar */}
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex flex-wrap gap-1.5">
                {FILTERS.map((f) => (
                  <button key={f.value} type="button" onClick={() => setFilter(f.value)} aria-pressed={filter === f.value}
                          className={cn("inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition",
                            filter === f.value ? "border-foreground bg-foreground text-background" : "hover:bg-muted")}>
                    {f.value !== "ALL" && <span className={cn("h-1.5 w-1.5 rounded-full", STATUS_META[f.value].dot)} />}
                    {f.label}
                    <span className="tabular-nums opacity-60">{counts[f.value] ?? 0}</span>
                  </button>
                ))}
              </div>
              <div className="ml-auto flex items-center gap-2">
                <button type="button" className="text-xs font-medium text-primary hover:underline"
                        onClick={() => setChecked(tables.filter((t) => t.status === "UNPROFILED" || t.status === "STALE").map((t) => t.table_name))}>
                  Select unprofiled & stale
                </button>
                <button type="button" className="text-xs font-medium text-primary hover:underline"
                        onClick={() => setChecked(tables.filter((t) => t.status === "STAGED_READY_FOR_MODELING").map((t) => t.table_name))}>
                  Select ready
                </button>
                <div className="relative">
                  <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                  <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search tables" className="w-56 rounded-xl pl-8" />
                </div>
              </div>
            </div>

            {/* table */}
            <div className="relative overflow-x-auto rounded-xl border">
              <table className="w-full text-sm">
                <thead className="bg-muted/50 text-left text-xs font-medium text-muted-foreground">
                  <tr>
                    <th className="w-10 px-4 py-3">
                      <input type="checkbox" aria-label="Select all visible tables" className="h-4 w-4 accent-[hsl(var(--primary))]"
                             checked={allVisibleChecked}
                             onChange={() => setChecked((prev) => allVisibleChecked
                               ? prev.filter((n) => !visible.some((t) => t.table_name === n))
                               : Array.from(new Set([...prev, ...visible.map((t) => t.table_name)])))} />
                    </th>
                    <th className="px-4 py-3">Table</th>
                    <th className="px-4 py-3">Domain</th>
                    <th className="px-4 py-3 text-right">Rows</th>
                    <th className="px-4 py-3 text-right">Columns</th>
                    <th className="px-4 py-3">Status</th>
                    <th className="px-4 py-3">Quality</th>
                    <th className="px-4 py-3">Profiled</th>
                    <th className="px-4 py-3 text-right"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {loadingInventory && <SkeletonRows />}
                  {!loadingInventory && visible.map((t: InventoryTable) => {
                    const staged = t.status === "STAGED_READY_FOR_MODELING" || t.status === "STALE";
                    const isChecked = checked.includes(t.table_name);
                    const open = () => setDrawer({ database, schema, table: t.table_name });
                    return (
                      <tr key={t.table_name}
                          className={cn("group border-t transition hover:bg-muted/40", isChecked && "bg-primary/[0.04]")}>
                        <td className="px-4 py-3">
                          <input type="checkbox" className="h-4 w-4 accent-[hsl(var(--primary))]" aria-label={`select ${t.table_name}`}
                                 checked={isChecked} onChange={() => toggle(t.table_name)} />
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2.5">
                            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
                              <Table2 className="h-4 w-4" />
                            </span>
                            <div className="min-w-0">
                              <button type="button" disabled={!staged} onClick={open}
                                      className={cn("block truncate text-left font-medium", staged && "hover:text-primary hover:underline")}>
                                {t.table_name}
                              </button>
                              <span className="text-[11px] text-muted-foreground">
                                {t.table_type === "BASE TABLE" ? "Table" : t.table_type.toLowerCase()}
                                {t.last_altered ? ` · changed ${ago(t.last_altered)}` : ""}
                              </span>
                            </div>
                          </div>
                        </td>
                        <td className="px-4 py-3"><DomainChip t={t} /></td>
                        <td className="px-4 py-3 text-right tabular-nums">{formatCount(t.row_count)}</td>
                        <td className="px-4 py-3 text-right tabular-nums">{t.column_count}</td>
                        <td className="px-4 py-3">
                          <StatusPill status={t.status} error={t.error_message} stagePath={t.stage_path}
                                      onOpen={() => setDrawer({ database, schema, table: t.table_name, tab: "overview" })} />
                        </td>
                        <td className="px-4 py-3">{staged ? <QualityCell t={t} /> : <span className="text-xs text-muted-foreground">—</span>}</td>
                        <td className="whitespace-nowrap px-4 py-3 text-xs text-muted-foreground" title={t.profiled_at ?? undefined}>
                          {ago(t.profiled_at)}
                          {t.profiled_by && <span className="block text-[11px]">by {t.profiled_by}</span>}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <div className="inline-flex gap-1 opacity-60 transition group-hover:opacity-100">
                            {staged && (
                              <Button size="sm" variant="ghost" onClick={open} aria-label={`View profile of ${t.table_name}`}>
                                <Eye className="h-3.5 w-3.5" />
                              </Button>
                            )}
                            <Button size="sm" variant="ghost" disabled={submitting || t.status === "PROFILING"}
                                    onClick={() => void runProfile([t.table_name], t.status !== "UNPROFILED")}
                                    aria-label={`${t.status === "UNPROFILED" ? "Profile" : "Re-profile"} ${t.table_name}`}>
                              {t.status === "UNPROFILED" ? <Sparkles className="h-3.5 w-3.5" /> : <RefreshCw className="h-3.5 w-3.5" />}
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {!loadingInventory && visible.length === 0 && (
                    <tr><td colSpan={9} className="px-4 py-12 text-center text-sm text-muted-foreground">
                      {tables.length ? "No tables match this filter." : "No tables visible in this schema with your role."}
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {tab === "store" && (
          <div className="space-y-3 p-5">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm text-muted-foreground">
                Every profile on <span className="font-mono">@METADATA.PROFILES_STAGE</span>, from any database. Open one
                to inspect it, or jump to its schema to profile more or start modeling.
              </p>
              <div className="relative ml-auto">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input value={storeQuery} onChange={(e) => setStoreQuery(e.target.value)} placeholder="Search database, schema or table"
                       className="w-72 rounded-xl pl-8" />
              </div>
            </div>
            <div className="relative overflow-x-auto rounded-xl border">
              <table className="w-full text-sm">
                <thead className="bg-muted/50 text-left text-xs font-medium text-muted-foreground">
                  <tr>
                    <th className="px-4 py-3">Table</th><th className="px-4 py-3">Database.schema</th>
                    <th className="px-4 py-3 text-right">Rows</th><th className="px-4 py-3 text-right">Columns</th>
                    <th className="px-4 py-3">Quality</th><th className="px-4 py-3">Status</th><th className="px-4 py-3">Profiled</th>
                    <th className="px-4 py-3" />
                  </tr>
                </thead>
                <tbody>
                  {storeVisible.map((r) => (
                    <tr key={`${r.source_name}.${r.database_name}.${r.schema_name}.${r.table_name}`} className="group border-t hover:bg-muted/40">
                      <td className="px-4 py-3 font-medium">{r.table_name}</td>
                      <td className="px-4 py-3">
                        <button type="button" className="font-mono text-xs text-primary hover:underline"
                                onClick={() => openTarget({ database: r.database_name, schema: r.schema_name })}>
                          {r.database_name}.{r.schema_name}
                        </button>
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">{formatCount(r.row_count)}</td>
                      <td className="px-4 py-3 text-right tabular-nums">{r.column_count ?? "—"}</td>
                      <td className="px-4 py-3 text-xs text-muted-foreground">
                        {r.avg_null_percentage != null ? `${Number(r.avg_null_percentage).toFixed(1)}% null` : "—"}
                        {r.key_candidates ? ` · ${r.key_candidates} key` : ""}
                        {r.pii_columns ? <span className="text-destructive"> · {r.pii_columns} PII</span> : ""}
                      </td>
                      <td className="px-4 py-3">
                        <StatusPill status={r.status === "PROFILING" ? "PROFILING" : r.status === "FAILED" ? "FAILED" : "STAGED_READY_FOR_MODELING"}
                                    error={r.error_message} />
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-xs text-muted-foreground" title={r.profiled_at ?? undefined}>{ago(r.profiled_at)}</td>
                      <td className="px-4 py-3 text-right">
                        {r.status !== "PROFILING" && (
                          <Button size="sm" variant="ghost" aria-label={`View profile of ${r.table_name}`}
                                  onClick={() => setDrawer({ database: r.database_name, schema: r.schema_name, table: r.table_name })}>
                            <Eye className="h-3.5 w-3.5" />
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                  {storeVisible.length === 0 && (
                    <tr><td colSpan={8} className="px-4 py-12 text-center text-sm text-muted-foreground">
                      {store.length ? "Nothing matches that search." : "Nothing profiled yet. Explore a schema and profile some tables."}
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* floating bulk action bar */}
      {tab === "explore" && checked.length > 0 && (
        <div className="fixed inset-x-0 bottom-6 z-40 flex justify-center px-4">
          <div role="toolbar" aria-label="Selected tables"
               className="flex max-w-4xl flex-wrap items-center gap-2 rounded-2xl border bg-card/95 px-4 py-3 shadow-2xl backdrop-blur">
            <span className="grid h-7 min-w-7 place-items-center rounded-full bg-primary px-2 text-xs font-semibold text-primary-foreground">
              {checked.length}
            </span>
            <span className="text-sm font-medium" aria-live="polite">selected</span>
            {notReady.length > 0 && (
              <span className="text-xs text-muted-foreground">· {notReady.length} not ready for modeling</span>
            )}
            <span className="mx-1 h-5 w-px bg-border" />
            {/* Profile only touches tables without a current profile (unprofiled, stale, failed); ready ones are reused.
                Re-profile forces a fresh run for every selected table even when the source has not changed. */}
            {notReady.length > 0 && (
              <Button size="sm" variant="outline" disabled={submitting}
                      onClick={() => void runProfile(notReady.map((t) => t.table_name), false)}
                      title="Profile the selected tables that have no current profile; ready tables are reused as they are">
                {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                Profile {notReady.length} not ready
              </Button>
            )}
            <Button size="sm" variant="ghost" disabled={submitting} onClick={() => void runProfile(checked, true)}
                    title="Run profiling again for every selected table, even if the source has not changed">
              <RefreshCw className="h-3.5 w-3.5" /> Force re-profile
            </Button>
            <Button size="sm" disabled={!canModel} onClick={() => setModeling([...checked].sort())}
                    title={canModel ? "Open the modeling panel for these tables" : "Every selected table must be ready (profiled and unchanged)"}>
              Send to modeling <ArrowRight className="h-3.5 w-3.5" />
            </Button>
            <button type="button" aria-label="Clear selection" onClick={() => setChecked([])}
                    className="ml-1 rounded-full p-1 text-muted-foreground hover:bg-muted hover:text-foreground">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}

      {drawer && (
        <ProfileDrawer database={drawer.database} schema={drawer.schema} table={drawer.table} initialTab={drawer.tab}
                       onClose={closeDrawer}
                       onReprofile={drawer.database === database && drawer.schema === schema
                         ? (table) => { setDrawer(null); void runProfile([table], true); } : undefined} />
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


type ExternalRow = SourcesOverview["sources"][number];

/** An Oracle source at a glance: where it is, whether it answers, what landed and what runs next. */
function OracleCard({ x, landedCount, isLanded, onManage, onOpen }: {
  x: ExternalRow; landedCount: number; isLanded: boolean; onManage: () => void; onOpen: () => void;
}) {
  const o = x.oracle!;
  const status = !o.ready ? "setup" : o.health?.status ?? null;
  const dot = status === "ok" ? "bg-success" : status === "warn" ? "bg-warning" : status === "fail" ? "bg-destructive" : "bg-muted-foreground/40";
  const label = status === "setup" ? "Setup needed" : status === "ok" ? "Connected" : status === "warn" ? "Needs attention"
    : status === "fail" ? "Unreachable" : "Not checked";
  const job = o.last_job;
  return (
    <div className="group relative overflow-hidden rounded-2xl border bg-card p-4 shadow-sm transition hover:shadow-md">
      <div className="pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full bg-gradient-to-br from-red-500/15 to-orange-400/10 blur-xl" />
      <div className="flex items-start gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-red-600 to-orange-500 text-white shadow-sm">
          <HardDrive className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">
            {x.source_system_name}
            <Badge variant="outline" className="text-[10px]">Oracle</Badge>
            {o.protocol === "tcps" && <Badge variant="success" className="text-[10px]">TLS</Badge>}
          </p>
          <p className="truncate font-mono text-[11px] text-muted-foreground" title={`${o.host}:${o.port}/${o.service}`}>{o.host}:{o.port}/{o.service}</p>
        </div>
        <span className="flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-medium">
          <span className={cn("h-1.5 w-1.5 rounded-full", dot, status === "ok" && "animate-pulse")} />{label}
        </span>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2 text-center">
        <div className="rounded-lg bg-muted/50 px-2 py-1.5"><p className="text-sm font-semibold tabular-nums">{landedCount}</p><p className="text-[10px] text-muted-foreground">landed</p></div>
        <div className="rounded-lg bg-muted/50 px-2 py-1.5"><p className="text-sm font-semibold tabular-nums">{x.staged_tables}</p><p className="text-[10px] text-muted-foreground">ready to model</p></div>
        <div className="rounded-lg bg-muted/50 px-2 py-1.5"><p className="text-sm font-semibold">{o.schedule ? "On" : "Off"}</p><p className="text-[10px] text-muted-foreground">schedule</p></div>
      </div>
      <p className="mt-2 flex items-center gap-1.5 truncate text-[11px] text-muted-foreground">
        {o.running ? <><Loader2 className="h-3 w-3 animate-spin text-primary" /> {o.running.kind === "ingest" ? "Loading" : "Profiling"} {o.running.done}/{o.running.tables} tables</>
          : job ? <>{job.status === "DONE" ? <CheckCircle2 className="h-3 w-3 text-success" /> : <AlertTriangle className="h-3 w-3 text-warning" />}
              Last {job.kind === "ingest" ? "load" : "profile"} {job.status.toLowerCase()} · {job.at.slice(0, 16)} UTC{job.kind === "ingest" ? ` · ${job.rows.toLocaleString()} rows` : ""}</>
          : o.health?.version ? <>Oracle {o.health.version} · schema {o.schema_owner}</> : <>Schema {o.schema_owner} · runs {o.runtime === "snowflake" ? "in Snowflake" : "on the API server"}</>}
      </p>
      <div className="mt-3 flex gap-2">
        <Button size="sm" className="flex-1" variant={o.ready ? "outline" : "default"} onClick={onManage}>{o.ready ? "Tables & loads" : "Finish setup"}</Button>
        {isLanded && <Button size="sm" variant="ghost" onClick={onOpen}>Open landed</Button>}
      </div>
    </div>
  );
}
