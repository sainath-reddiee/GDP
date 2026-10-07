"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState, useTransition } from "react";
import {
  ArrowRight, CheckCircle2, ChevronDown, ChevronRight, Database, KeyRound, Loader2, Lock, PlugZap, RefreshCw,
  ScanSearch, Search, ShieldAlert, Snowflake, XCircle,
} from "lucide-react";
import type { IngestJob, OracleCatalog, OracleColumn, OracleProfileDoc, OracleTest } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  ingestJob, oracleCatalog, oracleColumns, oracleIngest, oracleProfile, oracleProfileDoc, oracleSetup, oracleTest,
} from "./actions";

type ProfileDoc = OracleProfileDoc;

const PHASES = ["EXTRACTING", "UPLOADING", "LANDING", "PROFILING", "DONE"];

function fmtRows(n: number | null | undefined) {
  if (n == null) return "–";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function PhasePill({ phase }: { phase?: string }) {
  const tone = phase === "DONE" ? "bg-success/10 text-success" : phase === "FAILED" ? "bg-destructive/10 text-destructive"
    : phase && phase !== "QUEUED" ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground";
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium", tone)}>
      {phase && !["DONE", "FAILED", "QUEUED"].includes(phase) && <Loader2 className="h-3 w-3 animate-spin" />}
      {phase === "DONE" && <CheckCircle2 className="h-3 w-3" />}
      {phase === "FAILED" && <XCircle className="h-3 w-3" />}
      {(phase ?? "queued").toLowerCase()}
    </span>
  );
}

/** Oracle source: one-time Snowflake setup, connection test, catalog, in-place profiling and extract & land. */
export function OraclePanel({ sourceId, name, onOpenSchema }: {
  sourceId: string; name: string; onOpenSchema: (target: { database: string; schema: string }) => void;
}) {
  const [catalog, setCatalog] = useState<OracleCatalog | null>(null);
  const [test, setTest] = useState<OracleTest | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [password, setPassword] = useState("");
  const [eai, setEai] = useState("");
  const [setupLog, setSetupLog] = useState<{ sql: string; ok: boolean; error?: string }[] | null>(null);
  const [needsSetup, setNeedsSetup] = useState(false);
  const [ingesting, setIngesting] = useState(false);
  const [mode, setMode] = useState<"replace" | "append">("replace");
  const [storage, setStorage] = useState<"MANAGED" | "ICEBERG">("MANAGED");
  const [profileAfter, setProfileAfter] = useState(true);
  const [columns, setColumns] = useState<Record<string, OracleColumn[]>>({});
  const [watermarks, setWatermarks] = useState<Record<string, string>>({});
  const [job, setJob] = useState<IngestJob | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [doc, setDoc] = useState<ProfileDoc | null>(null);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadCatalog = useCallback(() => start(async () => {
    setError("");
    const r = await oracleCatalog(sourceId);
    if (!r.ok) {
      if (/set up the snowflake connection/i.test(r.error)) setNeedsSetup(true); else setError(r.error);
      return;
    }
    setNeedsSetup(!r.data.ready);
    setCatalog(r.data);
  }), [sourceId]);

  useEffect(() => { loadCatalog(); }, [loadCatalog]);
  useEffect(() => () => { if (timer.current) clearInterval(timer.current); }, []);

  const runSetup = () => start(async () => {
    setError(""); setSetupLog(null);
    const r = await oracleSetup(sourceId, password, eai);
    setPassword("");
    if (!r.ok) { setError(r.error); return; }
    setSetupLog(r.data.log);
    if (!r.data.ready) { setError(r.data.detail ?? "Setup did not complete."); return; }
    setNeedsSetup(false);
    const t = await oracleTest(sourceId);
    if (t.ok) setTest(t.data); else setError(t.error);
    loadCatalog();
  });

  const runTest = () => start(async () => {
    setError(""); setTest(null);
    const r = await oracleTest(sourceId);
    if (r.ok) setTest(r.data); else setError(r.error);
  });

  const poll = (jobId: string) => {
    if (timer.current) clearInterval(timer.current);
    timer.current = setInterval(async () => {
      const r = await ingestJob(jobId);
      if (!r.ok) return;
      setJob(r.data);
      if (r.data.status !== "RUNNING") {
        if (timer.current) clearInterval(timer.current);
        loadCatalog();
      }
    }, 2000);
  };

  const profile = () => start(async () => {
    setError("");
    const r = await oracleProfile(sourceId, selected);
    if (!r.ok) { setError(r.error); return; }
    setJob({ job_id: r.data.job_id, source_id: sourceId, kind: "profile", status: "RUNNING",
             tables: Object.fromEntries(selected.map((t) => [t, { phase: "QUEUED" }])), result: null, error: null });
    poll(r.data.job_id);
  });

  const openIngest = () => start(async () => {
    setError("");
    setIngesting(true);
    const missing = selected.filter((t) => !columns[t]);
    if (missing.length) {
      const r = await oracleColumns(sourceId, missing);
      if (r.ok) setColumns((c) => ({ ...c, ...r.data.columns }));
    }
  });

  const ingest = () => start(async () => {
    setError("");
    const r = await oracleIngest(sourceId, {
      tables: selected, mode, storage, profile_after_landing: profileAfter,
      watermark_columns: mode === "append" ? Object.fromEntries(Object.entries(watermarks).filter(([t, c]) => c && selected.includes(t))) : {},
    });
    if (!r.ok) { setError(r.error); return; }
    setIngesting(false);
    setJob({ job_id: r.data.job_id, source_id: sourceId, kind: "ingest", status: "RUNNING",
             tables: Object.fromEntries(selected.map((t) => [t, { phase: "QUEUED" }])), result: null, error: null });
    poll(r.data.job_id);
  });

  const toggleDoc = (table: string) => start(async () => {
    if (open === table) { setOpen(null); return; }
    setOpen(table); setDoc(null);
    const r = await oracleProfileDoc(sourceId, table);
    if (r.ok) setDoc(r.data); else setError(r.error);
  });

  const tables = useMemo(() => (catalog?.tables ?? []).filter((t) =>
    !query || t.table.toLowerCase().includes(query.toLowerCase()) || (t.comment ?? "").toLowerCase().includes(query.toLowerCase())),
  [catalog, query]);
  const allChecked = tables.length > 0 && tables.every((t) => selected.includes(t.table));
  const running = job?.status === "RUNNING";

  return (
    <div className="space-y-4">
      {needsSetup && (
        <section className="space-y-3 rounded-xl border border-primary/30 bg-primary/5 p-4">
          <div className="flex items-start gap-2">
            <Snowflake className="mt-0.5 h-4 w-4 text-primary" />
            <div>
              <p className="text-sm font-semibold">Connect Snowflake to {name}</p>
              <p className="text-xs text-muted-foreground">
                Creates a network rule to the Oracle listener, a Snowflake secret for the password and an external access
                integration, then the procedure that reads Oracle. The password goes straight into the Snowflake secret and is
                not stored anywhere else. Needs CREATE NETWORK RULE, SECRET and INTEGRATION; otherwise the SQL is shown for an admin.
              </p>
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
            <label className="text-xs font-medium">Oracle password
              <Input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} className="mt-1" />
            </label>
            <label className="text-xs font-medium">External access integration (optional)
              <Input value={eai} onChange={(e) => setEai(e.target.value.toUpperCase())} placeholder={`${name}_ORACLE_ACCESS`} className="mt-1 font-mono" />
            </label>
            <Button disabled={pending || !password} onClick={runSetup}>
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />} Connect
            </Button>
          </div>
          {setupLog && (
            <ol className="space-y-1 rounded-lg border bg-card p-2 font-mono text-[11px]">
              {setupLog.map((l, i) => (
                <li key={i} className={l.ok ? "text-muted-foreground" : "text-destructive"}>
                  {l.ok ? "✓" : "✗"} {l.sql.split("\n")[0].slice(0, 140)}{l.error ? ` · ${l.error}` : ""}
                </li>
              ))}
            </ol>
          )}
        </section>
      )}

      <section className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" disabled={pending || needsSetup} onClick={runTest}>
          <PlugZap className="h-4 w-4" /> Test connection
        </Button>
        <Button size="sm" variant="ghost" disabled={pending || needsSetup} onClick={loadCatalog}>
          <RefreshCw className="h-4 w-4" /> Refresh tables
        </Button>
        {catalog && (
          <span className="text-xs text-muted-foreground">
            Runs {catalog.runtime === "snowflake" ? "inside Snowflake" : "on the API host"} · lands in{" "}
            <span className="font-mono">{catalog.landing.database}.{catalog.landing.schema}</span>
          </span>
        )}
        {catalog?.tables.some((t) => t.load) && (
          <Button size="sm" variant="ghost" className="ml-auto" onClick={() => onOpenSchema(catalog.landing)}>
            Open landed tables <ArrowRight className="h-4 w-4" />
          </Button>
        )}
      </section>

      {test && (
        <div className={cn("flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border p-3 text-sm",
          test.warning ? "border-warning/40 bg-warning/5" : "border-success/30 bg-success/5")}>
          {test.warning ? <ShieldAlert className="h-4 w-4 text-warning" /> : <CheckCircle2 className="h-4 w-4 text-success" />}
          <span className="font-medium">Connected as {test.user}</span>
          <span className="text-muted-foreground">{test.version}</span>
          <span className="text-muted-foreground">{test.visible_tables} tables · {test.visible_views} views in {test.schema_owner}</span>
          {test.elapsed_ms != null && <span className="text-muted-foreground">{test.elapsed_ms} ms</span>}
          {test.warning && <span className="w-full text-xs text-warning">{test.warning}</span>}
        </div>
      )}

      {catalog && (
        <section className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search className="absolute left-2 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
              <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search tables or comments"
                     className="h-8 w-64 pl-7 text-xs" aria-label="Search Oracle tables" />
            </div>
            <span className="text-xs text-muted-foreground">{selected.length} of {catalog.tables.length} selected</span>
            <div className="ml-auto flex gap-2">
              <Button size="sm" variant="outline" disabled={pending || running || !selected.length} onClick={profile}>
                <ScanSearch className="h-4 w-4" /> Profile in Oracle
              </Button>
              <Button size="sm" disabled={pending || running || !selected.length} onClick={openIngest}>
                <Database className="h-4 w-4" /> Extract &amp; land
              </Button>
            </div>
          </div>
          <div className="max-h-[420px] overflow-y-auto rounded-xl border">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-muted/80 text-left text-[11px] uppercase tracking-wide text-muted-foreground backdrop-blur">
                <tr>
                  <th className="w-8 px-3 py-2">
                    <input type="checkbox" aria-label="Select all" checked={allChecked}
                           onChange={() => setSelected(allChecked ? [] : tables.map((t) => t.table))} />
                  </th>
                  <th className="px-2 py-2">Table</th>
                  <th className="px-2 py-2 text-right">Est. rows</th>
                  <th className="px-2 py-2">Profile</th>
                  <th className="px-2 py-2">Landed</th>
                  <th className="px-2 py-2">Progress</th>
                </tr>
              </thead>
              <tbody>
                {tables.map((t) => (
                  <Fragment key={t.table}>
                    <tr className={cn("border-t", selected.includes(t.table) && "bg-primary/5")}>
                      <td className="px-3 py-2">
                        <input type="checkbox" aria-label={`select ${t.table}`} checked={selected.includes(t.table)}
                               onChange={() => setSelected((s) => (s.includes(t.table) ? s.filter((x) => x !== t.table) : [...s, t.table]))} />
                      </td>
                      <td className="px-2 py-2">
                        <span className="flex items-center gap-1.5 font-mono text-xs">
                          {t.table}
                          {t.type === "VIEW" && <Badge variant="outline" className="text-[10px]">view</Badge>}
                          {t.partitioned && <Badge variant="outline" className="text-[10px]">partitioned</Badge>}
                        </span>
                        {t.comment && <span className="block truncate text-[11px] text-muted-foreground">{t.comment}</span>}
                      </td>
                      <td className="px-2 py-2 text-right tabular-nums text-xs" title={t.last_analyzed ? `stats from ${t.last_analyzed}` : "no optimizer statistics"}>
                        {fmtRows(t.estimated_rows)}
                      </td>
                      <td className="px-2 py-2 text-xs">
                        {t.profile && t.profile.status !== "PROFILING" ? (
                          <button type="button" onClick={() => toggleDoc(t.table)} className="flex items-center gap-1 text-primary hover:underline">
                            {open === t.table ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                            {fmtRows(t.profile.row_count)} rows
                            {(t.profile.pii_columns ?? 0) > 0 && <Lock className="ml-1 h-3 w-3 text-destructive" />}
                          </button>
                        ) : <span className="text-muted-foreground">–</span>}
                      </td>
                      <td className="px-2 py-2 text-xs">
                        {t.load ? <span title={t.load.landed_as}>{fmtRows(t.load.rows)} · {t.load.at.slice(0, 16)}</span>
                          : <span className="text-muted-foreground">–</span>}
                      </td>
                      <td className="px-2 py-2">{job?.tables[t.table] && <PhasePill phase={job.tables[t.table].phase} />}</td>
                    </tr>
                    {open === t.table && (
                      <tr className="border-t bg-muted/20">
                        <td colSpan={6} className="px-4 py-3">
                          {!doc ? <Loader2 className="h-4 w-4 animate-spin" /> : (
                            <div className="space-y-2">
                              <p className="text-xs text-muted-foreground">
                                {doc.profile.row_count.toLocaleString()} rows{doc.profile.approximate ? " (sampled in Oracle)" : ""}
                                {doc.scorecard?.grade ? ` · quality ${doc.scorecard.grade}` : ""}
                              </p>
                              <div className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
                                {doc.profile.columns.map((c) => (
                                  <div key={c.column_name} className="flex items-center gap-2 rounded-md border bg-card px-2 py-1 text-[11px]">
                                    <span className="font-mono font-medium">{c.column_name}</span>
                                    <span className="text-muted-foreground">{c.source_type ?? c.data_type}</span>
                                    <span className="ml-auto text-muted-foreground">{Math.round(c.statistics.null_percentage ?? 0)}% null</span>
                                    {c.potential_key && <KeyRound className="h-3 w-3 text-primary" />}
                                    {c.pii_classification !== "NONE" && <Badge variant="destructive" className="text-[10px]">{c.pii_classification}</Badge>}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
                {tables.length === 0 && (
                  <tr><td colSpan={6} className="p-6 text-center text-sm text-muted-foreground">No tables match.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {!catalog && pending && !needsSetup && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Reading the Oracle catalog…</p>
      )}

      {ingesting && (
        <section className="space-y-3 rounded-xl border p-4">
          <p className="text-sm font-semibold">Extract &amp; land {selected.length} table{selected.length === 1 ? "" : "s"}</p>
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="text-xs font-medium">Load
              <select value={mode} onChange={(e) => setMode(e.target.value as "replace" | "append")} className="mt-1 h-9 w-full rounded-md border bg-card px-2 text-sm">
                <option value="replace">Replace (full refresh)</option>
                <option value="append">Append new rows (incremental)</option>
              </select>
            </label>
            <label className="text-xs font-medium">Storage
              <select value={storage} onChange={(e) => setStorage(e.target.value as "MANAGED" | "ICEBERG")} className="mt-1 h-9 w-full rounded-md border bg-card px-2 text-sm">
                <option value="MANAGED">Snowflake managed table</option>
                <option value="ICEBERG">Iceberg (external volume)</option>
              </select>
            </label>
            <label className="flex items-center gap-2 self-end pb-2 text-xs">
              <input type="checkbox" checked={profileAfter} onChange={(e) => setProfileAfter(e.target.checked)} />
              Profile after landing (ready to model)
            </label>
          </div>
          {mode === "append" && (
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">Incremental loads read only rows above the last value of a column that only grows (a timestamp or id). Leave empty to append everything.</p>
              {selected.map((t) => (
                <label key={t} className="flex items-center gap-2 text-xs">
                  <span className="w-48 truncate font-mono">{t}</span>
                  <select value={watermarks[t] ?? ""} onChange={(e) => setWatermarks((w) => ({ ...w, [t]: e.target.value }))}
                          className="h-8 flex-1 rounded-md border bg-card px-2">
                    <option value="">No watermark</option>
                    {(columns[t] ?? []).filter((c) => ["TIMESTAMP", "NUMBER"].includes(c.family)).map((c) => (
                      <option key={c.column_name} value={c.column_name}>{c.column_name} · {c.oracle_type}</option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
          )}
          <p className="text-[11px] text-muted-foreground">
            Oracle DATE keeps its time (TIMESTAMP_NTZ); NUMBER precision is kept exactly; each row records its source file, batch and
            load time. Tables land in <span className="font-mono">{catalog?.landing.database}.{catalog?.landing.schema}</span>.
          </p>
          <div className="flex gap-2">
            <Button disabled={pending} onClick={ingest}>
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />} Start
            </Button>
            <Button variant="ghost" onClick={() => setIngesting(false)}>Cancel</Button>
          </div>
        </section>
      )}

      {job && (
        <section className="space-y-2 rounded-xl border p-4">
          <div className="flex items-center gap-2">
            <p className="text-sm font-semibold">{job.kind === "ingest" ? "Extract & land" : "Profile in Oracle"}</p>
            <PhasePill phase={job.status === "RUNNING" ? "RUNNING" : job.status === "DONE" ? "DONE" : "FAILED"} />
          </div>
          {job.kind === "ingest" && (
            <div className="flex gap-1">
              {PHASES.map((p) => <span key={p} className="flex-1 rounded-full bg-muted px-2 py-0.5 text-center text-[10px] text-muted-foreground">{p.toLowerCase()}</span>)}
            </div>
          )}
          <ul className="space-y-1 text-xs">
            {Object.entries(job.tables).map(([t, s]) => (
              <li key={t} className="flex items-center gap-2">
                <span className="w-48 truncate font-mono">{t}</span>
                <PhasePill phase={s.phase} />
                <span className="text-muted-foreground">
                  {s.rows != null && s.phase === "EXTRACTING" ? `${s.rows.toLocaleString()} rows read` : ""}
                  {s.rows_loaded != null ? `${s.rows_loaded.toLocaleString()} rows landed` : ""}
                  {s.row_count != null ? `${s.row_count.toLocaleString()} rows profiled` : ""}
                </span>
                {s.error && <span className="truncate text-destructive" title={s.error}>{s.error}</span>}
              </li>
            ))}
          </ul>
          {job.status !== "RUNNING" && job.kind === "ingest" && catalog && (
            <Button size="sm" variant="outline" onClick={() => onOpenSchema(catalog.landing)}>
              Open landed tables to model <ArrowRight className="h-4 w-4" />
            </Button>
          )}
        </section>
      )}

      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </div>
  );
}
