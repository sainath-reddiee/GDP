"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useTransition } from "react";
import {
  ArrowRight, CalendarClock, Clock, Database, Eye, HardDrive, History, KeyRound, Layers, Loader2, Lock, PlugZap,
  RefreshCw, ScanSearch, Search, Server, Settings2, ShieldCheck, Snowflake, Table2, TimerReset, X,
} from "lucide-react";
import type {
  IngestJob, OracleCatalog, OracleColumn, OracleOverview, OraclePreview, OracleProfileDoc, OracleTable, OracleTest,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useScrollLock } from "@/components/use-scroll-lock";
import {
  cancelIngestJob, ingestJob, oracleCatalog, oracleCheckIntegration, oracleColumns, oracleIntegrations, oracleOverview,
  oraclePreview, oracleProfile, oracleProfileDoc, oracleSecrets, oracleSetup, oracleTest, type IntegrationCheck,
  type SetupResult,
} from "./actions";
import { IngestDialog, JobCard } from "./oracle-ingest";
import { LoadsTab, ScheduleTab, SettingsTab } from "./oracle-manage";
import { Checklist, CopyButton, fmtBytes, fmtRows, HealthPill, SecretNote, Segmented, timeAgo } from "./oracle-ui";

type Tab = "tables" | "loads" | "schedule" | "settings";
type Filter = "all" | "tables" | "views" | "landed" | "new" | "attention";

function Kpi({ icon: Icon, label, value, hint }: { icon: typeof Database; label: string; value: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <div className="rounded-xl border bg-card px-4 py-3">
      <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground"><Icon className="h-3.5 w-3.5" />{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums">{value}</p>
      {hint && <p className="truncate text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** First-time Snowflake access for a source registered without it (new or existing secret and integration). */
function SetupCard({ sourceId, overview, onReady }: { sourceId: string; overview: OracleOverview; onReady: () => void }) {
  const c = overview.connection;
  const [secretMode, setSecretMode] = useState<"new" | "existing">("new");
  const [password, setPassword] = useState("");
  const [secrets, setSecrets] = useState<{ name: string }[] | null>(null);
  const [secret, setSecret] = useState("");
  const [eaiMode, setEaiMode] = useState<"new" | "existing">("new");
  const [eais, setEais] = useState<{ name: string; enabled: boolean }[] | null>(null);
  const [eai, setEai] = useState("");
  const [check, setCheck] = useState<IntegrationCheck | null>(null);
  const [result, setResult] = useState<SetupResult | null>(null);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();

  useEffect(() => { if (secretMode === "existing" && !secrets) oracleSecrets().then((r) => r.ok && setSecrets(r.data.secrets)); }, [secretMode, secrets]);
  useEffect(() => { if (eaiMode === "existing" && !eais) oracleIntegrations().then((r) => r.ok && setEais(r.data.integrations)); }, [eaiMode, eais]);
  useEffect(() => {
    setCheck(null);
    if (eaiMode !== "existing" || !eai) return;
    let live = true;  // a slower answer for an earlier choice must not overwrite the current one
    oracleCheckIntegration({ name: eai, host: c.host, port: Number(c.port), secret: secretMode === "existing" ? secret || undefined : undefined })
      .then((r) => { if (live && r.ok) setCheck(r.data); });
    return () => { live = false; };
  }, [eaiMode, eai, secret, secretMode, c.host, c.port]);

  const ready = (secretMode === "new" ? !!password : !!secret) && (eaiMode === "new" || (!!eai && secretMode === "existing" && check?.usable !== false));
  const run = () => start(async () => {
    setError(""); setResult(null);
    const r = await oracleSetup(sourceId, { secret_mode: secretMode, password: secretMode === "new" ? password : undefined,
                                            secret: secretMode === "existing" ? secret : undefined, integration_mode: eaiMode,
                                            external_access_integration: eaiMode === "existing" ? eai : undefined });
    setPassword("");
    if (!r.ok) { setError(r.error); return; }
    setResult(r.data);
    if (r.data.ready) onReady();
  });

  return (
    <section className="space-y-3 rounded-2xl border border-primary/30 bg-primary/5 p-4">
      <p className="flex items-center gap-2 text-sm font-semibold"><Snowflake className="h-4 w-4 text-primary" /> Finish Snowflake access to {c.host}:{c.port}</p>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <Segmented label="Password" value={secretMode} onChange={setSecretMode} options={[["new", "New secret"], ["existing", "Existing secret"]] as const} />
          {secretMode === "new" ? (
            <Input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder={`Password of ${c.user}`} />
          ) : (
            <select value={secret} onChange={(e) => setSecret(e.target.value)} aria-label="Existing secret" className="h-9 w-full rounded-md border bg-card px-2 font-mono text-sm">
              <option value="">{secrets ? "Choose a PASSWORD secret" : "Loading…"}</option>
              {(secrets ?? []).map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          )}
        </div>
        <div className="space-y-2">
          <Segmented label="Network access" value={eaiMode} onChange={setEaiMode} options={[["new", "Create access"], ["existing", "Existing integration"]] as const} />
          {eaiMode === "existing" && (
            <>
              <select value={eai} onChange={(e) => setEai(e.target.value)} aria-label="Existing integration" className="h-9 w-full rounded-md border bg-card px-2 font-mono text-sm">
                <option value="">{eais ? "Choose an integration" : "Loading…"}</option>
                {(eais ?? []).map((i) => <option key={i.name} value={i.name} disabled={!i.enabled}>{i.name}</option>)}
              </select>
              {secretMode === "new" && <p className="text-[11px] text-warning">An existing integration needs an existing secret it already allows.</p>}
              {check && !check.usable && <p className="text-[11px] text-destructive">{eai} does not allow {check.allows_host === false ? `${c.host}:${c.port}` : "this secret"}.</p>}
              {check?.note && <p className="text-[11px] text-muted-foreground">{check.note}</p>}
            </>
          )}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button disabled={!ready || pending} onClick={run}>{pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />} Set up access</Button>
        <SecretNote />
      </div>
      {result && !result.ready && (
        <div className="space-y-2 rounded-xl border border-destructive/30 bg-card p-3 text-xs">
          <p>{result.detail}</p>
          {result.grants && result.grants.length > 0 && <pre className="whitespace-pre-wrap font-mono text-[11px]">{result.grants.join(";\n")};</pre>}
          <CopyButton text={result.grants?.length ? result.grants.join(";\n") + ";"
            : result.log.map((l) => l.sql.replace("'<oracle password>'", "'<the Oracle password>'")).join(";\n\n") + ";"} label="Copy SQL for an admin" />
        </div>
      )}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </section>
  );
}

/** Columns with their Snowflake mapping, a live preview and the in-place profile of one table. */
function TableDrawer({ sourceId, table, onClose, onLoad, onProfile }: {
  sourceId: string; table: OracleTable; onClose: () => void; onLoad: () => void; onProfile: () => void;
}) {
  const [tab, setTab] = useState<"columns" | "preview" | "profile">("columns");
  const [columns, setColumns] = useState<OracleColumn[] | null>(null);
  const [preview, setPreview] = useState<OraclePreview | null>(null);
  const [previewMs, setPreviewMs] = useState<number | null>(null);
  const [doc, setDoc] = useState<OracleProfileDoc | null | "none">(null);
  const [error, setError] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [docError, setDocError] = useState("");
  useScrollLock();

  // answers for a table the user has already left are dropped
  useEffect(() => {
    let live = true;
    setColumns(null); setPreview(null); setDoc(null); setError(""); setPreviewError(""); setDocError("");
    oracleColumns(sourceId, [table.table]).then((r) => { if (live) (r.ok ? setColumns(r.data.columns[table.table] ?? []) : setError(r.error)); });
    return () => { live = false; };
  }, [sourceId, table.table]);
  useEffect(() => {
    let live = true;
    if (tab === "preview" && !preview && !previewError) {
      const t0 = performance.now();
      oraclePreview(sourceId, table.table).then((r) => {
        if (!live) return;
        if (r.ok) { setPreview(r.data); setPreviewMs(Math.round(performance.now() - t0)); } else setPreviewError(r.error);
      });
    }
    if (tab === "profile" && doc === null && !docError) {
      oracleProfileDoc(sourceId, table.table).then((r) => {
        if (!live) return;
        // a 404 "has not been profiled" means no profile yet; anything else is a real failure to show
        if (r.ok) setDoc(r.data); else if (/not been profiled/i.test(r.error)) setDoc("none"); else setDocError(r.error);
      });
    }
    return () => { live = false; };
  }, [tab, preview, previewError, doc, docError, sourceId, table.table]);

  return (
    <div className="fixed inset-y-0 right-0 z-[55] flex w-full max-w-2xl flex-col border-l bg-background shadow-2xl">
      <header className="space-y-2 border-b px-5 py-4">
        <div className="flex items-start gap-2">
          <Table2 className="mt-0.5 h-4 w-4 text-primary" />
          <div className="min-w-0">
            <p className="truncate font-mono text-sm font-semibold">{table.table}</p>
            {table.comment && <p className="text-xs text-muted-foreground">{table.comment}</p>}
          </div>
          <button type="button" aria-label="Close" onClick={onClose} className="ml-auto rounded-md p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
          <Badge variant="outline" className="text-[10px]">{table.type.toLowerCase()}</Badge>
          <span>{fmtRows(table.estimated_rows)} rows</span><span>{fmtBytes(table.estimated_bytes)}</span>
          <span>{table.column_count} columns</span>
          {table.primary_key.length > 0 && <span className="flex items-center gap-1"><KeyRound className="h-3 w-3" />{table.primary_key.join(", ")}</span>}
          {table.last_analyzed && <span>stats {table.last_analyzed.slice(0, 10)}</span>}
          <span className="ml-auto flex gap-2">
            <Button size="sm" variant="outline" disabled={!table.extractable} onClick={onProfile}><ScanSearch className="h-3.5 w-3.5" /> Profile</Button>
            <Button size="sm" disabled={!table.extractable} onClick={onLoad}><Database className="h-3.5 w-3.5" /> Extract &amp; land</Button>
          </span>
        </div>
        <Segmented label="Table details" value={tab} onChange={setTab}
                   options={[["columns", "Columns & mapping"], ["preview", "Preview data"], ["profile", "Profile"]] as const} />
      </header>
      <div className="flex-1 overflow-y-auto px-5 py-4">
        {error && <p role="alert" className="mb-3 text-sm text-destructive">{error}</p>}
        {tab === "columns" && (!columns ? <Loader2 className="h-4 w-4 animate-spin" /> : (
          <table className="w-full text-xs">
            <thead className="text-left text-[10px] uppercase tracking-wide text-muted-foreground">
              <tr><th className="py-1.5">Column</th><th>Oracle</th><th>Snowflake</th><th>Keys</th></tr>
            </thead>
            <tbody>
              {columns.map((col) => (
                <tr key={col.column_name} className={cn("border-t align-top", col.skip_reason && "bg-warning/5")}>
                  <td className="py-1.5 pr-2">
                    <span className="font-mono font-medium">{col.column_name}</span>{!col.nullable && <span className="ml-1 text-[10px] text-muted-foreground">not null</span>}
                    {col.comment && <span className="block text-[10px] text-muted-foreground">{col.comment}</span>}
                    {col.skip_reason && <span className="block text-[10px] text-warning">Skipped: {col.skip_reason}</span>}
                  </td>
                  <td className="pr-2 font-mono text-muted-foreground">{col.oracle_type}</td>
                  <td className="pr-2 font-mono">
                    {col.snowflake_type ?? "–"}
                    {col.lob && !col.skip_reason && <span className="block text-[10px] text-muted-foreground">large value; over 16 MB is cut</span>}
                    {col.free_number && <span className="block text-[10px] text-muted-foreground">free NUMBER: 38,10 or text if larger</span>}
                    {col.expr && col.expr !== `"${col.column_name}"` && <span className="block text-[10px] text-muted-foreground">converted in Oracle</span>}
                  </td>
                  <td className="space-x-1">{col.constraints.map((k) => <Badge key={k} variant="outline" className="text-[10px]">{k}</Badge>)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
        {tab === "preview" && (previewError ? (
          <div className="space-y-2">
            <p role="alert" className="text-sm text-destructive">Could not read a preview: {previewError}</p>
            <Button size="sm" variant="outline" onClick={() => setPreviewError("")}><RefreshCw className="h-3.5 w-3.5" /> Try again</Button>
          </div>
        ) : !preview ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Reading the first rows from Oracle…</p>
        ) : (
          <div className="space-y-2">
            <p className="text-[11px] text-muted-foreground">First {preview.rows.length} rows, read live from Oracle{previewMs ? ` in ${previewMs} ms` : ""}. Values are shown as text; long values are clipped.</p>
            <div className="overflow-x-auto rounded-lg border">
              <table className="text-[11px]">
                <thead className="bg-muted/50"><tr>{preview.columns.map((h) => <th key={h} className="whitespace-nowrap px-2 py-1.5 text-left font-mono font-medium">{h}</th>)}</tr></thead>
                <tbody>
                  {preview.rows.map((row, i) => (
                    <tr key={i} className="border-t">
                      {row.map((v, j) => <td key={j} className="max-w-[220px] truncate whitespace-nowrap px-2 py-1 font-mono" title={v == null ? "" : String(v)}>
                        {v == null ? <span className="italic text-muted-foreground">null</span> : String(v)}</td>)}
                    </tr>
                  ))}
                  {preview.rows.length === 0 && <tr><td className="p-4 text-muted-foreground" colSpan={preview.columns.length}>The table is empty.</td></tr>}
                </tbody>
              </table>
            </div>
            {preview.skipped.length > 0 && <p className="text-[11px] text-warning">Not shown: {preview.skipped.map((s) => s.column).join(", ")}</p>}
          </div>
        ))}
        {tab === "profile" && (docError ? (
          <div className="space-y-2">
            <p role="alert" className="text-sm text-destructive">Could not read the profile: {docError}</p>
            <Button size="sm" variant="outline" onClick={() => setDocError("")}><RefreshCw className="h-3.5 w-3.5" /> Try again</Button>
          </div>
        ) : doc === null ? <Loader2 className="h-4 w-4 animate-spin" /> : doc === "none" ? (
          <div className="space-y-2 rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
            <p>Not profiled yet. Profiling runs inside Oracle; only statistics leave the database.</p>
            <Button size="sm" variant="outline" onClick={onProfile}><ScanSearch className="h-3.5 w-3.5" /> Profile in Oracle</Button>
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">
              {doc.profile.row_count.toLocaleString()} rows{doc.profile.approximate ? " (sampled in Oracle)" : ""}
              {doc.scorecard?.grade ? ` · quality ${doc.scorecard.grade}` : ""}
            </p>
            <div className="grid gap-1 sm:grid-cols-2">
              {doc.profile.columns.map((c) => (
                <div key={c.column_name} className="flex items-center gap-2 rounded-md border bg-card px-2 py-1 text-[11px]">
                  <span className="font-mono font-medium">{c.column_name}</span>
                  <span className="text-muted-foreground">{c.semantic_type}</span>
                  <span className="ml-auto text-muted-foreground">{Math.round(c.statistics.null_percentage ?? 0)}% null</span>
                  {c.potential_key && <KeyRound className="h-3 w-3 text-primary" />}
                  {c.pii_classification !== "NONE" && <Badge variant="destructive" className="text-[10px]">{c.pii_classification}</Badge>}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** An Oracle source: health, tables, loads, schedule and settings. */
export function OraclePanel({ sourceId, name, onOpenSchema, onRemoved }: {
  sourceId: string; name: string; onOpenSchema: (target: { database: string; schema: string }) => void;
  onRemoved?: () => void;
}) {
  const [overview, setOverview] = useState<OracleOverview | null>(null);
  const [catalog, setCatalog] = useState<OracleCatalog | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [tab, setTab] = useState<Tab>("tables");
  const [test, setTest] = useState<OracleTest | null>(null);
  const [testing, setTesting] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<"name" | "rows" | "size">("name");
  const [drawer, setDrawer] = useState<OracleTable | null>(null);
  const [ingestFor, setIngestFor] = useState<OracleTable[] | null>(null);
  const [job, setJob] = useState<IngestJob | null>(null);
  const [profileAfter, setProfileAfter] = useState(true);
  const [error, setError] = useState("");
  const [loading, startLoading] = useTransition();
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadOverview = useCallback(async () => {
    const r = await oracleOverview(sourceId);
    if (r.ok) setOverview(r.data); else setError(r.error);
    return r.ok ? r.data : null;
  }, [sourceId]);

  const loadCatalog = useCallback(() => startLoading(async () => {
    setCatalogError("");
    const r = await oracleCatalog(sourceId);
    if (r.ok) setCatalog(r.data); else setCatalogError(r.error);
  }), [sourceId]);

  const poll = useCallback((jobId: string) => {
    if (timer.current) clearInterval(timer.current);
    const startedAt = Date.now();
    let busy = false;
    timer.current = setInterval(async () => {
      if (busy) return;
      if (Date.now() - startedAt > 60 * 60 * 1000) { if (timer.current) clearInterval(timer.current); return; }
      busy = true;
      const r = await ingestJob(jobId).finally(() => { busy = false; });
      if (!r.ok) {
        // the API keeps jobs in memory: after a restart the job is gone, so stop asking and show the real state
        if (timer.current) clearInterval(timer.current);
        setJob(null);
        loadOverview();
        loadCatalog();
        return;
      }
      setJob(r.data);
      if (r.data.status !== "RUNNING") {
        if (timer.current) clearInterval(timer.current);
        loadOverview();
        loadCatalog();
      }
    }, 1500);
  }, [loadOverview, loadCatalog]);

  useEffect(() => {
    loadOverview().then((o) => {
      if (o?.access.ready) loadCatalog();
      if (o?.running_job) {
        setJob(o.running_job);
        setProfileAfter(o.running_job.options?.profile_after_landing !== false);
        poll(o.running_job.job_id);
      }
    });
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [loadOverview, loadCatalog, poll]);

  const runTest = async () => {
    setTesting(true); setTest(null);
    const r = await oracleTest(sourceId);
    setTesting(false);
    if (r.ok) setTest(r.data); else setError(r.error);
    loadOverview();
  };

  const startJob = (jobId: string, tables: string[], kind: "profile" | "ingest", after: boolean) => {
    setProfileAfter(after);
    setJob({ job_id: jobId, source_id: sourceId, kind, status: "RUNNING", started_at: Date.now() / 1000, finished_at: null,
             tables: Object.fromEntries(tables.map((t) => [t, { phase: "QUEUED" }])), result: null, error: null });
    poll(jobId);
  };

  const profile = async (tables: string[]) => {
    setError("");
    const r = await oracleProfile(sourceId, tables);
    if (!r.ok) { setError(r.error); return; }
    startJob(r.data.job_id, tables, "profile", false);
  };

  const all = catalog?.tables ?? [];
  const byName = useMemo(() => Object.fromEntries(all.map((t) => [t.table, t])), [all]);
  const tables = useMemo(() => {
    const q = query.toLowerCase();
    const rows = all.filter((t) => (!q || t.table.toLowerCase().includes(q) || (t.comment ?? "").toLowerCase().includes(q))
      && (filter === "all" || (filter === "tables" && t.type === "TABLE") || (filter === "views" && t.type !== "TABLE")
        || (filter === "landed" && !!t.load) || (filter === "new" && !t.load)
        || (filter === "attention" && (t.skipped_columns.length > 0 || t.stale_stats || !t.extractable))));
    return [...rows].sort((a, b) => sort === "rows" ? (b.estimated_rows ?? -1) - (a.estimated_rows ?? -1)
      : sort === "size" ? (b.estimated_bytes ?? -1) - (a.estimated_bytes ?? -1) : a.table.localeCompare(b.table));
  }, [all, query, filter, sort]);
  const selectable = tables.filter((t) => t.extractable);
  const allChecked = selectable.length > 0 && selectable.every((t) => selected.includes(t.table));
  const running = job?.status === "RUNNING";

  if (!overview) {
    return error ? <p role="alert" className="text-sm text-destructive">{error}</p>
      : <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Loading {name}…</p>;
  }
  const c = overview.connection;
  const health = test ? { status: test.status, headline: test.headline, at: "now" } : overview.health;
  const landedCount = Object.keys(overview.loads).length;
  const landedRows = Object.values(overview.loads).reduce((n, l) => n + (l.rows ?? 0), 0);
  const lastLoad = Object.values(overview.history).flat().sort((a, b) => b.at.localeCompare(a.at))[0];
  const expiring = overview.health?.password_expires_in_days;

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border bg-gradient-to-br from-red-500/5 via-card to-card p-5">
        <div className="flex flex-wrap items-start gap-4">
          <span className="rounded-xl bg-gradient-to-br from-red-600 to-orange-500 p-3 text-white shadow-sm"><HardDrive className="h-6 w-6" /></span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-semibold">{overview.name}</h2>
              <HealthPill status={health?.status} />
              <Badge variant="outline" className="gap-1 text-[10px]">{c.runtime === "snowflake" ? <Snowflake className="h-3 w-3" /> : <Server className="h-3 w-3" />}
                {c.runtime === "snowflake" ? "Runs in Snowflake" : "Runs on API server"}</Badge>
              {c.protocol === "tcps" && <Badge variant="success" className="gap-1 text-[10px]"><ShieldCheck className="h-3 w-3" /> TLS</Badge>}
            </div>
            <p className="mt-1 font-mono text-xs text-muted-foreground">
              {c.user}@{c.host}:{c.port}/{c.service_name ?? `SID ${c.sid}`} <ArrowRight className="mx-1 inline h-3 w-3" /> schema {c.schema_owner}
            </p>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              {overview.health?.version ? `Oracle ${overview.health.version} · ` : ""}
              {overview.health?.latency_ms != null ? `${overview.health.latency_ms} ms round trip · ` : ""}
              checked {timeAgo(overview.health?.at)} · lands in <span className="font-mono">{overview.landing.database}.{overview.landing.schema}</span>
            </p>
          </div>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" disabled={testing || !overview.access.ready} onClick={runTest}>
              {testing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlugZap className="h-3.5 w-3.5" />} Test connection
            </Button>
            <Button size="sm" variant="ghost" disabled={loading || !overview.access.ready} onClick={loadCatalog}>
              <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} /> Refresh
            </Button>
          </div>
        </div>
        {health?.status && health.status !== "ok" && !test && (
          <p className={cn("mt-3 rounded-lg px-3 py-2 text-xs", health.status === "fail" ? "bg-destructive/10 text-destructive" : "bg-warning/10 text-warning")}>
            {health.headline}. Run Test connection for the details.
          </p>
        )}
        {expiring != null && expiring <= 14 && (
          <p className="mt-3 flex items-center gap-2 rounded-lg bg-warning/10 px-3 py-2 text-xs text-warning">
            <TimerReset className="h-3.5 w-3.5" /> The Oracle password expires in {Math.max(expiring, 0)} day(s). Change it in Oracle, then Settings &gt; Update password.
          </p>
        )}
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Kpi icon={Table2} label="In Oracle" value={catalog ? all.length : "–"}
               hint={catalog ? `${all.filter((t) => t.type === "TABLE").length} tables · ${all.filter((t) => t.type !== "TABLE").length} views` : "browse after setup"} />
          <Kpi icon={Database} label="Landed" value={landedCount} hint={`${fmtRows(landedRows)} rows in the last loads`} />
          <Kpi icon={History} label="Last load" value={lastLoad ? (lastLoad.status === "LANDED" ? (lastLoad.verified ? "Verified" : "Landed") : "Failed") : "–"}
               hint={lastLoad ? `${timeAgo(lastLoad.at)} · ${lastLoad.mode}` : "nothing yet"} />
          <Kpi icon={CalendarClock} label="Schedule" value={overview.schedule ? "On" : "Off"}
               hint={overview.schedule ? `${overview.schedule.cron} · ${overview.schedule.mode}` : c.runtime === "snowflake" ? "incremental loads as a Snowflake task" : "not available on API server"} />
        </div>
      </section>

      {!overview.access.ready && <SetupCard sourceId={sourceId} overview={overview} onReady={() => { loadOverview(); loadCatalog(); runTest(); }} />}

      {(test || testing) && (
        <section className="space-y-2">
          <div className="flex items-center gap-2">
            <p className="text-sm font-semibold">Connection check</p>
            {test?.elapsed_ms != null && <span className="text-xs text-muted-foreground">{(test.elapsed_ms / 1000).toFixed(1)}s</span>}
            {test && <button type="button" className="ml-auto text-xs text-muted-foreground hover:text-foreground" onClick={() => setTest(null)}>Hide</button>}
          </div>
          <Checklist checks={test?.checks ?? null} running={testing} />
        </section>
      )}

      {job && (
        <JobCard job={job} profileAfter={profileAfter}
                 onCancel={() => {
                   const jobId = job.job_id;
                   cancelIngestJob(jobId).then((r) => {
                     if (!r.ok) { setError(r.error); return; }
                     // only mark the job the user cancelled, and only while polling has not already seen it finish
                     setJob((j) => (j && j.job_id === jobId && j.status === "RUNNING" ? { ...j, cancel_requested: true } : j));
                   });
                 }}
                 onRetry={(failed) => setIngestFor(failed.map((t) => byName[t]).filter(Boolean))}
                 onOpenLanded={() => onOpenSchema(overview.landing)} onDismiss={() => setJob(null)} />
      )}

      <div className="flex items-center gap-1 border-b">
        {([["tables", "Tables", Table2], ["loads", "Loads", History], ["schedule", "Schedule", CalendarClock], ["settings", "Settings", Settings2]] as const).map(([id, label, Icon]) => (
          <button key={id} type="button" onClick={() => setTab(id)} aria-current={tab === id ? "page" : undefined}
                  className={cn("-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm",
                    tab === id ? "border-primary font-medium text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}>
            <Icon className="h-4 w-4" />{label}
            {id === "loads" && landedCount > 0 && <span className="rounded-full bg-muted px-1.5 text-[10px]">{landedCount}</span>}
          </button>
        ))}
      </div>

      {tab === "tables" && (
        <section className="space-y-3">
          {catalogError && <p role="alert" className="rounded-lg bg-destructive/10 p-3 text-sm text-destructive">{catalogError}</p>}
          {!catalog && !catalogError && overview.access.ready && (
            <div className="space-y-2">{[0, 1, 2, 3, 4].map((i) => <div key={i} className="h-10 animate-pulse rounded-lg bg-muted" />)}</div>
          )}
          {catalog && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative">
                  <Search className="absolute left-2 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
                  <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search tables or comments" className="h-8 w-56 pl-7 text-xs" aria-label="Search Oracle tables" />
                </div>
                <Segmented label="Filter" value={filter} onChange={setFilter}
                           options={[["all", "All"], ["tables", "Tables"], ["views", "Views"], ["landed", "Landed"], ["new", "Not landed"], ["attention", "Attention"]] as const} />
                <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)} aria-label="Sort" className="h-8 rounded-md border bg-card px-2 text-xs">
                  <option value="name">Name</option><option value="rows">Most rows</option><option value="size">Largest</option>
                </select>
                <div className="ml-auto flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">{selected.length} selected</span>
                  <Button size="sm" variant="outline" disabled={running || !selected.length} onClick={() => profile(selected)}>
                    <ScanSearch className="h-3.5 w-3.5" /> Profile in Oracle
                  </Button>
                  <Button size="sm" disabled={running || !selected.length} onClick={() => setIngestFor(selected.map((t) => byName[t]).filter(Boolean))}>
                    <Database className="h-3.5 w-3.5" /> Extract &amp; land
                  </Button>
                </div>
              </div>
              <div className="max-h-[480px] overflow-auto rounded-xl border">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 z-10 bg-muted/90 text-left text-[10px] uppercase tracking-wide text-muted-foreground backdrop-blur">
                    <tr>
                      <th className="w-8 px-3 py-2"><input type="checkbox" aria-label="Select all" checked={allChecked}
                        onChange={() => setSelected(allChecked ? [] : selectable.map((t) => t.table))} /></th>
                      <th className="px-2 py-2">Table</th>
                      <th className="px-2 py-2 text-right">Rows</th>
                      <th className="px-2 py-2 text-right">Size</th>
                      <th className="px-2 py-2">Key / incremental</th>
                      <th className="px-2 py-2">Profile</th>
                      <th className="px-2 py-2">Landed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tables.map((t) => {
                      const live = job?.tables[t.table];
                      return (
                        <tr key={t.table} className={cn("border-t hover:bg-muted/30", selected.includes(t.table) && "bg-primary/5", !t.extractable && "opacity-60")}>
                          <td className="px-3 py-2">
                            <input type="checkbox" aria-label={`select ${t.table}`} disabled={!t.extractable} title={t.not_extractable_reason ?? undefined}
                                   checked={selected.includes(t.table)}
                                   onChange={() => setSelected((s) => (s.includes(t.table) ? s.filter((x) => x !== t.table) : [...s, t.table]))} />
                          </td>
                          <td className="px-2 py-2">
                            <button type="button" onClick={() => setDrawer(t)} className="flex flex-wrap items-center gap-1.5 text-left font-mono text-xs font-medium text-primary hover:underline">
                              {t.table}
                              {t.type !== "TABLE" && <Badge variant="outline" className="text-[10px] font-sans">{t.type === "MVIEW" ? "mat. view" : t.type.toLowerCase()}</Badge>}
                              {t.partitioned && <Badge variant="outline" className="text-[10px] font-sans">partitioned</Badge>}
                              {t.temporary && <Badge variant="secondary" className="text-[10px] font-sans">temporary</Badge>}
                              {t.skipped_columns.length > 0 && <Badge variant="warning" className="text-[10px] font-sans" title={t.skipped_columns.map((s) => `${s.column}: ${s.reason}`).join("\n")}>{t.skipped_columns.length} skipped</Badge>}
                            </button>
                            {t.comment && <span className="block max-w-md truncate text-[11px] text-muted-foreground">{t.comment}</span>}
                            {!t.extractable && t.not_extractable_reason && <span className="block text-[11px] text-muted-foreground">{t.not_extractable_reason}</span>}
                          </td>
                          <td className="px-2 py-2 text-right text-xs tabular-nums" title={t.last_analyzed ? `optimizer statistics from ${t.last_analyzed}` : "no optimizer statistics"}>
                            {fmtRows(t.estimated_rows)}{t.stale_stats && t.type === "TABLE" && <Clock className="ml-1 inline h-3 w-3 text-warning" aria-label="stale statistics" />}
                          </td>
                          <td className="px-2 py-2 text-right text-xs tabular-nums text-muted-foreground">{fmtBytes(t.estimated_bytes)}</td>
                          <td className="px-2 py-2 text-[11px]">
                            {t.primary_key.length > 0 && <span className="flex items-center gap-1" title={`primary key ${t.primary_key.join(", ")}`}><KeyRound className="h-3 w-3 text-primary" /><span className="max-w-[110px] truncate font-mono">{t.primary_key.join(", ")}</span></span>}
                            {t.watermark && <span className="flex items-center gap-1 text-muted-foreground" title={t.watermark.reason}><Clock className="h-3 w-3" /><span className="max-w-[110px] truncate font-mono">{t.watermark.column}</span></span>}
                          </td>
                          <td className="px-2 py-2 text-xs">
                            {live && job?.kind === "profile" ? <Badge variant="secondary" className="text-[10px]">{live.phase.toLowerCase()}</Badge>
                              : t.profile && t.profile.status !== "PROFILING" ? (
                                <button type="button" onClick={() => setDrawer(t)} className="flex items-center gap-1 text-primary hover:underline">
                                  <Eye className="h-3 w-3" /> {fmtRows(t.profile.row_count)}
                                  {(t.profile.pii_columns ?? 0) > 0 && <Lock className="h-3 w-3 text-destructive" aria-label="contains PII" />}
                                </button>
                              ) : <span className="text-muted-foreground">–</span>}
                          </td>
                          <td className="px-2 py-2 text-xs">
                            {live && job?.kind === "ingest" && live.phase !== "DONE" ? (
                              <Badge variant={live.phase === "FAILED" ? "destructive" : "secondary"} className="gap-1 text-[10px]">
                                {!["FAILED", "CANCELLED", "QUEUED"].includes(live.phase) && <Loader2 className="h-2.5 w-2.5 animate-spin" />}{live.phase.toLowerCase()}
                              </Badge>
                            ) : t.load ? (
                              <span className="flex items-center gap-1" title={`${t.load.landed_as} · ${t.load.at} UTC`}>
                                {t.load.verified ? <ShieldCheck className="h-3.5 w-3.5 text-success" /> : <Layers className="h-3.5 w-3.5 text-muted-foreground" />}
                                {fmtRows(t.load.rows)} · {timeAgo(t.load.at)}
                              </span>
                            ) : <span className="text-muted-foreground">–</span>}
                          </td>
                        </tr>
                      );
                    })}
                    {tables.length === 0 && <tr><td colSpan={7} className="p-8 text-center text-sm text-muted-foreground">No tables match.</td></tr>}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>
      )}
      {tab === "loads" && <LoadsTab overview={overview} />}
      {tab === "schedule" && <ScheduleTab sourceId={sourceId} overview={overview} tables={all} onChanged={loadOverview} />}
      {tab === "settings" && (
        <SettingsTab sourceId={sourceId} overview={overview} onChanged={() => { loadOverview(); setTest(null); }}
                     onRemoved={() => onRemoved?.()} />
      )}

      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}

      {drawer && (
        <TableDrawer key={drawer.table} sourceId={sourceId} table={drawer} onClose={() => setDrawer(null)}
                     onLoad={() => { setIngestFor([drawer]); setDrawer(null); }}
                     onProfile={() => { profile([drawer.table]); setDrawer(null); }} />
      )}
      {ingestFor && catalog && (
        <IngestDialog sourceId={sourceId} tables={ingestFor} landing={catalog.landing} onClose={() => setIngestFor(null)}
                      onStarted={(jobId, tables, after) => { setIngestFor(null); setSelected([]); startJob(jobId, tables, "ingest", after); }} />
      )}
    </div>
  );
}
