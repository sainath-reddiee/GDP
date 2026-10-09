"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle, ArrowRight, CheckCircle2, ChevronRight, CircleDashed, Database, Eye, FolderTree, Globe,
  Layers, Loader2, Plus, RefreshCw, Search, Snowflake, Sparkles, Table2, X, XCircle,
} from "lucide-react";
import type {
  CatalogInventory, InventoryTable, ProfileStatus, ProfileStoreRow, SourcesOverview,
} from "@/lib/types";
import type { DatabaseRow } from "@/app/onboarding/catalog-types";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { displayDomain } from "@/lib/catalog-display";
import type { DomainRow } from "@/app/onboarding/intent-types";
import { loadCatalogInventory, loadOverview, loadProfileStore, profileCatalogTables } from "./actions";
import { ModelPanel } from "./model-panel";
import { ConnectSource, type ManagedSource } from "./connect-source";
import { ExternalSources } from "./external-sources";
import { GradeChip, ProfileDrawer, type DrawerTab } from "./profile-drawer";
import { CatalogBrowser, type Target } from "./catalog-browser";
import { ProfileStore } from "./profile-store";

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

type Mode = "snowflake" | "external" | "store";

function ModeTab({ active, onClick, icon: Icon, title, body, count, tone }: {
  active: boolean; onClick: () => void; icon: typeof Database; title: string; body: string; count: React.ReactNode; tone: string;
}) {
  return (
    <button type="button" role="tab" aria-selected={active} onClick={onClick}
            className={cn("group relative flex items-start gap-3 overflow-hidden rounded-2xl border p-4 text-left transition",
              active ? "border-primary bg-primary/[0.06] shadow-md ring-2 ring-primary/20" : "bg-card hover:border-primary/40 hover:shadow-sm")}>
      <span className={cn("grid h-10 w-10 shrink-0 place-items-center rounded-xl text-white shadow-sm", tone, !active && "opacity-80")}>
        <Icon className="h-5 w-5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className={cn("text-sm font-semibold", active && "text-primary")}>{title}</span>
          <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-semibold tabular-nums",
            active ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>{count}</span>
        </span>
        <span className="mt-0.5 block text-xs text-muted-foreground">{body}</span>
      </span>
      {active && <span className="absolute inset-x-4 bottom-0 h-0.5 rounded-full bg-primary" />}
    </button>
  );
}

export function SourcesHub({
  initialOverview, initialStore, databases, domains = [], initialTarget, initialInventory, initialMode = "snowflake",
}: {
  initialOverview: SourcesOverview | null;
  domains?: DomainRow[];
  initialStore: ProfileStoreRow[];
  databases: DatabaseRow[];
  initialTarget: Target | null;
  initialInventory: CatalogInventory | null;
  initialMode?: Mode;
}) {
  const router = useRouter();
  const [overview, setOverview] = useState(initialOverview);
  const [store, setStore] = useState(initialStore);
  const [mode, setModeState] = useState<Mode>(initialMode);
  const [browserOpen, setBrowserOpen] = useState(false);
  const [database, setDatabase] = useState(initialTarget?.database ?? "");
  const [schema, setSchema] = useState(initialTarget?.schema ?? "");
  const [inventory, setInventory] = useState<CatalogInventory | null>(initialInventory);
  const [loadingInventory, setLoadingInventory] = useState(false);
  const [checked, setChecked] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ProfileStatus | "ALL">("ALL");
  const [drawer, setDrawer] = useState<Drawer | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [modeling, setModeling] = useState<string[] | null>(null);
  const [connect, setConnect] = useState<{ managed: ManagedSource | null; external?: boolean } | null>(null);
  // Separate busy flags: a background refresh must never block selection actions.
  const [submitting, setSubmitting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const inFlight = useRef(false);
  const targetRef = useRef<Target>({ database, schema });
  targetRef.current = { database, schema };

  const syncUrl = useCallback((next: Mode, t: Target | null) => {
    const params = new URLSearchParams({ mode: next });
    if (t?.database && t.schema) { params.set("db", t.database); params.set("schema", t.schema); }
    router.replace(`/sources?${params.toString()}`, { scroll: false });
  }, [router]);

  const setMode = (next: Mode) => {
    setModeState(next);
    setNotice(null);
    syncUrl(next, database && schema ? { database, schema } : null);
  };

  const openTarget = useCallback((next: Target) => {
    setModeState("snowflake");
    setDatabase(next.database);
    setSchema(next.schema);
    setChecked([]);
    setFilter("ALL");
    setNotice(null);
    setLoadingInventory(true);
    syncUrl("snowflake", next);
    loadCatalogInventory(next.database, next.schema).then((r) => {
      const now = targetRef.current;
      if (now.database !== next.database || now.schema !== next.schema) return;
      if (r.ok) setInventory(r.data);
      else { setInventory(null); setNotice({ tone: "error", text: r.error }); }
      setLoadingInventory(false);
    });
  }, [syncUrl]);

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

  const recents = useMemo(() => {
    const seen = new Map<string, { target: Target; staged: number }>();
    for (const r of store) {
      if (r.source_name.startsWith("ORACLE_")) continue;  // profiled inside Oracle: not a Snowflake schema
      const key = `${r.database_name}.${r.schema_name}`;
      const item = seen.get(key) ?? { target: { database: r.database_name, schema: r.schema_name }, staged: 0 };
      item.staged += r.status === "PROFILING" ? 0 : 1;
      seen.set(key, item);
    }
    for (const s of overview?.sources ?? []) {
      const key = `${s.database_name}.${s.schema_name}`;
      if (!seen.has(key) && s.database_name && s.schema_name) {
        seen.set(key, { target: { database: s.database_name, schema: s.schema_name }, staged: 0 });
      }
    }
    return Array.from(seen.values()).slice(0, 12);
  }, [store, overview]);
  const profiledBySchema = useMemo(() => {
    const out: Record<string, number> = {};
    for (const r of store) if (r.status !== "PROFILING") out[`${r.database_name}.${r.schema_name}`] = (out[`${r.database_name}.${r.schema_name}`] ?? 0) + 1;
    return out;
  }, [store]);

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
  const externalAttention = externals.filter((x) => x.failed_tables > 0 || (x.oracle && (!x.oracle.ready || x.oracle.health?.status === "fail"))).length;
  const currentExternal = externals.find((x) => x.database_name === database && x.schema_name === schema);
  const manage = (x: (typeof externals)[number]) => setConnect({ managed: {
    id: x.source_system_id, name: x.source_system_name, connector: x.connection_type ?? "upload",
    database: x.database_name, schema: x.schema_name,
  } });

  const ready = counts.STAGED_READY_FOR_MODELING ?? 0;
  const coverage = tables.length ? Math.round((100 * ready) / tables.length) : 0;
  const graded = tables.filter((t) => t.quality?.overall != null);
  const avgQuality = graded.length
    ? Math.round(graded.reduce((n, t) => n + (t.quality!.overall as number), 0) / graded.length) : null;
  const totalRows = tables.reduce((n, t) => n + (t.row_count ?? 0), 0);
  const schemaDomain = inventory?.domain_candidates?.[0];
  const hasSchema = !!(database && schema);

  return (
    <div className="space-y-6 pb-24">
      <PageHeader eyebrow="Work" title="Sources"
                  description="Profile Snowflake tables where they live, land external systems into Snowflake, and reuse every profile in any run. Profiling never copies data."
                  actions={<>
                    <Button variant="outline" size="sm" disabled={refreshing} onClick={() => void refresh()} aria-label="Refresh">
                      <RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} /> Refresh
                    </Button>
                    <Button onClick={() => setConnect({ managed: null })}><Plus className="h-4 w-4" /> Connect source</Button>
                  </>} />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Kpi icon={Database} label="Catalogs available" value={databases.length} hint="Databases and shares your role can read" tone="bg-sky-500" />
        <Kpi icon={FolderTree} label="Schemas profiled" value={storeSchemas} hint="With at least one stored profile" tone="bg-violet-500" />
        <Kpi icon={Layers} label="Tables staged" value={stagedTotal} hint="Profiles ready to reuse in any run" tone="bg-emerald-500" />
        <Kpi icon={Sparkles} label="Profiling now" value={profilingTotal}
             hint={profilingTotal ? "Updates every few seconds" : "Nothing running"} tone="bg-amber-500" />
      </div>

      <div role="tablist" aria-label="Where the data is" className="grid gap-3 md:grid-cols-3">
        <ModeTab active={mode === "snowflake"} onClick={() => setMode("snowflake")} icon={Snowflake} tone="bg-sky-500"
                 title="Snowflake catalog" count={databases.length}
                 body="Browse any database or share and profile tables in place." />
        <ModeTab active={mode === "external"} onClick={() => setMode("external")} icon={Globe} tone="bg-violet-500"
                 title="External systems" count={externalAttention ? `${externals.length} · ${externalAttention} attention` : externals.length}
                 body="Oracle, files and cloud storage, landed into Snowflake first." />
        <ModeTab active={mode === "store"} onClick={() => setMode("store")} icon={Layers} tone="bg-emerald-500"
                 title="Profile store" count={store.length}
                 body="Every stored profile, from any source, ready for any run." />
      </div>

      {notice && (
        <div role={notice.tone === "error" ? "alert" : "status"}
             className={cn("flex items-start gap-2 rounded-xl border px-3 py-2 text-sm",
               notice.tone === "error" ? "border-destructive/40 bg-destructive/5 text-destructive" : "border-success/30 bg-success/5")}>
          {notice.tone === "error" ? <XCircle className="mt-0.5 h-4 w-4" /> : <CheckCircle2 className="mt-0.5 h-4 w-4 text-success" />}
          <span className="flex-1">{notice.text}</span>
          <button type="button" aria-label="Dismiss" onClick={() => setNotice(null)} className="opacity-60 hover:opacity-100">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {mode === "snowflake" && (
        <section className="space-y-4">
          <CatalogBrowser databases={databases} value={hasSchema ? { database, schema } : null} recents={recents}
                          profiledBySchema={profiledBySchema} open={browserOpen} onOpenChange={setBrowserOpen} onPick={openTarget} />

          {!hasSchema && (
            <div className="grid place-items-center rounded-2xl border border-dashed bg-card px-6 py-14 text-center">
              <span className="grid h-14 w-14 place-items-center rounded-2xl bg-primary/10 text-primary"><Database className="h-6 w-6" /></span>
              <h3 className="mt-4 text-base font-semibold">Pick a schema to explore</h3>
              <p className="mt-1 max-w-md text-sm text-muted-foreground">
                Choose a database and schema in the catalog above. Tables are profiled in place and the profiles are reused by every run.
              </p>
              <Button className="mt-4" variant="outline" onClick={() => setBrowserOpen(true)}>Open the catalog</Button>
            </div>
          )}

          {hasSchema && (
            <div className="rounded-2xl border bg-card shadow-sm">
              <div className="flex flex-wrap items-center gap-3 border-b px-5 py-3">
                <Table2 className="h-4 w-4 text-muted-foreground" />
                <span className="truncate font-mono text-sm font-semibold">{database}.{schema}</span>
                {currentExternal && <Badge variant="outline">external · {currentExternal.source_system_name}</Badge>}
                {schemaDomain && displayDomain(schemaDomain.domain_name) && (
                  <span className="inline-flex items-center gap-1 rounded-full border border-dashed border-primary/40 px-2 py-0.5 text-xs"
                        title="Detected from table and column names against the domain contracts">
                    <Sparkles className="h-3 w-3 text-primary" /> {schemaDomain.domain_name} domain
                    <span className="text-muted-foreground">{Math.round(schemaDomain.confidence * 100)}%</span>
                  </span>
                )}
                <button type="button" onClick={() => setBrowserOpen(true)} className="ml-auto text-xs font-medium text-primary hover:underline">
                  Change schema
                </button>
              </div>
              <div className="space-y-4 p-5">
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
                  <div className="ml-auto flex items-center gap-3">
                    <button type="button" className="text-xs font-medium text-primary hover:underline"
                            onClick={() => setChecked(tables.filter((t) => t.status === "UNPROFILED" || t.status === "STALE").map((t) => t.table_name))}>
                      Select unprofiled &amp; stale
                    </button>
                    <button type="button" className="text-xs font-medium text-primary hover:underline"
                            onClick={() => setChecked(tables.filter((t) => t.status === "STAGED_READY_FOR_MODELING").map((t) => t.table_name))}>
                      Select ready
                    </button>
                    <div className="relative">
                      <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                      <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search tables" aria-label="Search tables" className="w-56 rounded-xl pl-8" />
                    </div>
                  </div>
                </div>

                <div className="relative overflow-x-auto rounded-xl border">
                  <table className="w-full min-w-[980px] text-sm">
                    <thead className="bg-muted/50 text-left text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                      <tr>
                        <th className="w-10 px-4 py-2.5">
                          <input type="checkbox" aria-label="Select all visible tables" className="h-4 w-4 accent-[hsl(var(--primary))]"
                                 checked={allVisibleChecked}
                                 onChange={() => setChecked((prev) => allVisibleChecked
                                   ? prev.filter((n) => !visible.some((t) => t.table_name === n))
                                   : Array.from(new Set([...prev, ...visible.map((t) => t.table_name)])))} />
                        </th>
                        <th className="px-4 py-2.5">Table</th>
                        <th className="w-40 px-4 py-2.5">Domain</th>
                        <th className="w-24 px-4 py-2.5 text-right">Rows</th>
                        <th className="w-24 px-4 py-2.5 text-right">Columns</th>
                        <th className="w-32 px-4 py-2.5">Status</th>
                        <th className="w-56 px-4 py-2.5">Quality</th>
                        <th className="w-32 px-4 py-2.5">Profiled</th>
                        <th className="w-24 px-4 py-2.5 text-right"><span className="sr-only">Actions</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {loadingInventory && <SkeletonRows />}
                      {!loadingInventory && visible.map((t: InventoryTable) => {
                        const staged = t.status === "STAGED_READY_FOR_MODELING" || t.status === "STALE";
                        const isChecked = checked.includes(t.table_name);
                        const open = () => setDrawer({ database, schema, table: t.table_name });
                        return (
                          <tr key={t.table_name} className={cn("group border-t transition hover:bg-muted/40", isChecked && "bg-primary/[0.04]")}>
                            <td className="px-4 py-2.5">
                              <input type="checkbox" className="h-4 w-4 accent-[hsl(var(--primary))]" aria-label={`select ${t.table_name}`}
                                     checked={isChecked} onChange={() => toggle(t.table_name)} />
                            </td>
                            <td className="px-4 py-2.5">
                              <div className="flex items-center gap-2.5">
                                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground"><Table2 className="h-4 w-4" /></span>
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
                            <td className="px-4 py-2.5"><DomainChip t={t} /></td>
                            <td className="px-4 py-2.5 text-right tabular-nums">{formatCount(t.row_count)}</td>
                            <td className="px-4 py-2.5 text-right tabular-nums">{t.column_count}</td>
                            <td className="px-4 py-2.5">
                              <StatusPill status={t.status} error={t.error_message} stagePath={t.stage_path}
                                          onOpen={() => setDrawer({ database, schema, table: t.table_name, tab: "overview" })} />
                            </td>
                            <td className="px-4 py-2.5">{staged ? <QualityCell t={t} /> : <span className="text-xs text-muted-foreground">—</span>}</td>
                            <td className="whitespace-nowrap px-4 py-2.5 text-xs text-muted-foreground" title={t.profiled_at ?? undefined}>
                              {ago(t.profiled_at)}
                              {t.profiled_by && <span className="block text-[11px]">by {t.profiled_by}</span>}
                            </td>
                            <td className="px-4 py-2.5 text-right">
                              <div className="inline-flex gap-1 opacity-60 transition group-hover:opacity-100">
                                {staged && (
                                  <Button size="sm" variant="ghost" onClick={open} aria-label={`View profile of ${t.table_name}`}><Eye className="h-3.5 w-3.5" /></Button>
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
            </div>
          )}
        </section>
      )}

      {mode === "external" && (
        externals.length > 0 ? (
          <ExternalSources sources={externals} onManage={manage} onAdd={() => setConnect({ managed: null, external: true })}
                           onOpen={(x) => openTarget({ database: x.database_name, schema: x.schema_name })} />
        ) : (
          <div className="grid place-items-center rounded-2xl border border-dashed bg-card px-6 py-14 text-center">
            <span className="grid h-14 w-14 place-items-center rounded-2xl bg-violet-500/10 text-violet-600"><Globe className="h-6 w-6" /></span>
            <h3 className="mt-4 text-base font-semibold">No external systems yet</h3>
            <p className="mt-1 max-w-md text-sm text-muted-foreground">
              Connect Oracle, upload files or point at cloud storage. Data lands in Snowflake first, then it is profiled and modeled like any table.
            </p>
            <Button className="mt-4" onClick={() => setConnect({ managed: null, external: true })}><Plus className="h-4 w-4" /> Connect an external system</Button>
          </div>
        )
      )}

      {mode === "store" && (
        <ProfileStore store={store} overview={overview} onOpenSchema={openTarget}
                      onView={(r) => setDrawer({ database: r.database_name, schema: r.schema_name, table: r.table_name })} />
      )}

      {mode === "snowflake" && checked.length > 0 && (
        <div className="fixed inset-x-0 bottom-6 z-40 flex justify-center px-4">
          <div role="toolbar" aria-label="Selected tables"
               className="flex max-w-4xl flex-wrap items-center gap-2 rounded-2xl border bg-card/95 px-4 py-3 shadow-2xl backdrop-blur">
            <span className="grid h-7 min-w-7 place-items-center rounded-full bg-primary px-2 text-xs font-semibold text-primary-foreground">
              {checked.length}
            </span>
            <span className="text-sm font-medium" aria-live="polite">selected</span>
            {notReady.length > 0 && <span className="text-xs text-muted-foreground">· {notReady.length} not ready for modeling</span>}
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
        <ConnectSource initial={connect.managed} startAtConnectors={connect.external} onClose={closeConnect}
                       onSnowflake={() => { setModeState("snowflake"); syncUrl("snowflake", hasSchema ? { database, schema } : null); setBrowserOpen(true); }}
                       onOpenSchema={(t) => openTarget(t)} />
      )}
      {modeling && (
        <ModelPanel database={database} schema={schema} tables={modeling} domains={domains} onClose={closeModeling} />
      )}
    </div>
  );
}
