"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import {
  AlertTriangle, ArrowRight, Ban, CheckCircle2, Clock, Database, GitMerge, KeyRound, Layers, Loader2, Plus, RefreshCcw,
  RotateCcw, ShieldCheck, Square, X, XCircle,
} from "lucide-react";
import type { IngestJob, IngestJobTable, OracleColumn, OracleTable } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { oracleColumns, oracleIngest, type IngestOptions } from "./actions";
import { fmtBytes, fmtDuration, fmtRows, Segmented } from "./oracle-ui";

type Mode = "replace" | "append" | "merge";

const MODES: { id: Mode; title: string; body: string; icon: typeof RefreshCcw }[] = [
  { id: "replace", title: "Full refresh", icon: RefreshCcw,
    body: "Truncate and reload every row. Always consistent; reads the whole table each time." },
  { id: "append", title: "Append new rows", icon: Plus,
    body: "Reads only rows past the watermark and appends them. Fast for logs and events; updates to old rows are not seen." },
  { id: "merge", title: "Merge changes", icon: GitMerge,
    body: "Upserts on the key: new and changed rows (by watermark) replace their old version. No duplicates, safe to rerun." },
];

/** Choose how the selected tables land: mode, watermark and keys per table, storage, and a preflight summary. */
export function IngestDialog({ sourceId, tables, landing, onClose, onStarted }: {
  sourceId: string; tables: OracleTable[]; landing: { database: string; schema: string };
  onClose: () => void; onStarted: (jobId: string, tables: string[], profileAfter: boolean) => void;
}) {
  const [columns, setColumns] = useState<Record<string, OracleColumn[]> | null>(null);
  const [mode, setMode] = useState<Mode>(() => (tables.every((t) => t.load) && tables.every((t) => t.primary_key.length) ? "merge" : "replace"));
  const [storage, setStorage] = useState<"MANAGED" | "ICEBERG">("MANAGED");
  const [profileAfter, setProfileAfter] = useState(true);
  const [lookback, setLookback] = useState("15");
  const [watermarks, setWatermarks] = useState<Record<string, string>>(() =>
    Object.fromEntries(tables.map((t) => [t.table, t.load?.watermark_column ?? t.watermark?.column ?? ""])));
  // chosen merge keys; empty means "use the primary key" (only tables without one need a choice)
  const [keys, setKeys] = useState<Record<string, string[]>>({});
  const [error, setError] = useState("");
  const [pending, start] = useTransition();

  useEffect(() => {
    oracleColumns(sourceId, tables.map((t) => t.table)).then((r) => {
      if (r.ok) setColumns(r.data.columns); else setError(r.error);
    });
  }, [sourceId, tables]);

  const incremental = mode !== "replace";
  const issues = useMemo(() => {
    const out: { table: string; level: "block" | "warn"; text: string }[] = [];
    for (const t of tables) {
      if (mode === "append" && !watermarks[t.table])
        out.push({ table: t.table, level: "warn", text: "no watermark: every row is read and appended again (duplicates)" });
      if (mode === "merge" && !(keys[t.table]?.length || t.primary_key.length))
        out.push({ table: t.table, level: "block", text: "no primary key: choose the key columns to merge on" });
      if (mode === "merge" && !watermarks[t.table])
        out.push({ table: t.table, level: "warn", text: "no watermark: the whole table is read each time (still no duplicates)" });
      if (mode === "replace" && (t.estimated_rows ?? 0) > 50_000_000)
        out.push({ table: t.table, level: "warn", text: `${fmtRows(t.estimated_rows)} rows on every refresh; merge with a watermark reads far less` });
    }
    return out;
  }, [tables, mode, watermarks, keys]);
  const blocked = issues.some((i) => i.level === "block");
  const totalRows = tables.reduce((n, t) => n + (t.estimated_rows ?? 0), 0);
  const totalBytes = tables.reduce((n, t) => n + (t.estimated_bytes ?? 0), 0);
  const skipped = tables.reduce((n, t) => n + t.skipped_columns.length, 0);

  const startLoad = () => start(async () => {
    setError("");
    const body: IngestOptions = {
      tables: tables.map((t) => t.table), mode, storage, profile_after_landing: profileAfter,
      watermark_columns: incremental ? Object.fromEntries(Object.entries(watermarks).filter(([, c]) => c)) : {},
      merge_keys: mode === "merge" ? Object.fromEntries(Object.entries(keys).filter(([, k]) => k.length)) : {},
      lookback_minutes: mode === "merge" ? Math.max(0, Number(lookback) || 0) : 0,
    };
    const r = await oracleIngest(sourceId, body);
    if (!r.ok) { setError(r.error); return; }
    onStarted(r.data.job_id, body.tables, profileAfter);
  });

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto bg-black/40 p-6">
      <div role="dialog" aria-label="Extract and land" className="w-full max-w-4xl rounded-2xl border bg-background shadow-2xl">
        <header className="flex items-center gap-3 border-b px-6 py-4">
          <span className="rounded-lg bg-primary/10 p-2 text-primary"><Database className="h-4 w-4" /></span>
          <div>
            <h3 className="text-base font-semibold">Extract &amp; land {tables.length} table{tables.length === 1 ? "" : "s"}</h3>
            <p className="text-xs text-muted-foreground">Into <span className="font-mono">{landing.database}.{landing.schema}</span>, verified row by row count, then profiled for modeling.</p>
          </div>
          <button type="button" aria-label="Close" onClick={onClose} className="ml-auto rounded-md p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
        </header>

        <div className="space-y-5 px-6 py-5">
          <div className="grid gap-2 sm:grid-cols-3">
            {MODES.map((m) => (
              <button key={m.id} type="button" aria-pressed={mode === m.id} onClick={() => setMode(m.id)}
                      className={cn("rounded-xl border p-3 text-left transition-all",
                        mode === m.id ? "border-primary bg-primary/5 ring-2 ring-primary/20" : "hover:border-primary/40")}>
                <span className="flex items-center gap-2 text-sm font-semibold"><m.icon className="h-4 w-4 text-primary" /> {m.title}</span>
                <span className="mt-1 block text-[11px] text-muted-foreground">{m.body}</span>
              </button>
            ))}
          </div>

          <div className="overflow-x-auto rounded-xl border">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">Table</th>
                  <th className="px-3 py-2 text-right">Est. rows</th>
                  {incremental && <th className="px-3 py-2">Watermark column</th>}
                  {mode === "merge" && <th className="px-3 py-2">Merge key</th>}
                  <th className="px-3 py-2">Notes</th>
                </tr>
              </thead>
              <tbody>
                {tables.map((t) => {
                  const cols = columns?.[t.table] ?? [];
                  const wmOptions = cols.filter((c) => !c.skip_reason && ["TIMESTAMP", "NUMBER"].includes(c.family));
                  const keyCols = keys[t.table] ?? [];
                  return (
                    <tr key={t.table} className="border-t align-top">
                      <td className="px-3 py-2">
                        <span className="font-mono text-xs font-medium">{t.table}</span>
                        {t.load && <span className="block text-[11px] text-muted-foreground">landed {fmtRows(t.load.rows)} rows · {t.load.mode}</span>}
                      </td>
                      <td className="px-3 py-2 text-right text-xs tabular-nums">{fmtRows(t.estimated_rows)}</td>
                      {incremental && (
                        <td className="px-3 py-2">
                          <select value={watermarks[t.table] ?? ""} aria-label={`Watermark for ${t.table}`} disabled={!columns}
                                  onChange={(e) => setWatermarks((w) => ({ ...w, [t.table]: e.target.value }))}
                                  className="h-8 w-full min-w-44 rounded-md border bg-card px-2 font-mono text-xs">
                            <option value="">None (read every row)</option>
                            {wmOptions.map((c) => (
                              <option key={c.column_name} value={c.column_name}>
                                {c.column_name} · {c.oracle_type}{t.watermark?.column === c.column_name ? "  (suggested)" : ""}
                              </option>
                            ))}
                          </select>
                          {t.load?.watermark && t.load.watermark_column === watermarks[t.table] && (
                            <span className="mt-0.5 block text-[10px] text-muted-foreground">continues after {t.load.watermark}</span>
                          )}
                          {!t.load?.watermark && t.watermark && watermarks[t.table] === t.watermark.column && (
                            <span className="mt-0.5 block text-[10px] text-muted-foreground">{t.watermark.reason}</span>
                          )}
                        </td>
                      )}
                      {mode === "merge" && (
                        <td className="px-3 py-2">
                          {t.primary_key.length > 0 ? (
                            <span className="flex flex-wrap items-center gap-1 text-xs">
                              <KeyRound className="h-3.5 w-3.5 text-primary" />
                              {t.primary_key.map((k) => <span key={k} className="rounded bg-muted px-1.5 font-mono text-[11px]">{k}</span>)}
                              <span className="text-[10px] text-muted-foreground">primary key</span>
                            </span>
                          ) : (
                            <select multiple value={keyCols} aria-label={`Merge key for ${t.table}`} disabled={!columns}
                                    onChange={(e) => setKeys((k) => ({ ...k, [t.table]: Array.from(e.target.selectedOptions).map((o) => o.value) }))}
                                    className="h-16 w-full min-w-44 rounded-md border bg-card px-1 font-mono text-xs">
                              {cols.filter((c) => !c.skip_reason && !c.lob).map((c) => <option key={c.column_name} value={c.column_name}>{c.column_name}</option>)}
                            </select>
                          )}
                        </td>
                      )}
                      <td className="px-3 py-2 text-[11px] text-muted-foreground">
                        {t.type !== "TABLE" && <Badge variant="outline" className="mr-1 text-[10px]">{t.type.toLowerCase()}</Badge>}
                        {t.skipped_columns.length > 0 && (
                          <span title={t.skipped_columns.map((s) => `${s.column}: ${s.reason}`).join("\n")}>
                            {t.skipped_columns.length} column{t.skipped_columns.length === 1 ? "" : "s"} skipped
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {issues.length > 0 && (
            <ul className="space-y-1">
              {issues.map((i, n) => (
                <li key={n} className={cn("flex items-start gap-2 text-xs", i.level === "block" ? "text-destructive" : "text-warning")}>
                  {i.level === "block" ? <Ban className="mt-0.5 h-3.5 w-3.5" /> : <AlertTriangle className="mt-0.5 h-3.5 w-3.5" />}
                  <span><span className="font-mono">{i.table}</span>: {i.text}</span>
                </li>
              ))}
            </ul>
          )}

          <div className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-1">
              <p className="text-xs font-medium">Storage</p>
              <Segmented label="Storage" value={storage} onChange={setStorage}
                         options={[["MANAGED", "Snowflake table"], ["ICEBERG", "Iceberg"]] as const} />
              <p className="text-[11px] text-muted-foreground">{storage === "ICEBERG" ? "Open format on the admin's external volume; applies when a table is first created." : "Native managed table."}</p>
            </div>
            {mode === "merge" && (
              <label className="block text-xs font-medium">Re-read window (minutes)
                <Input value={lookback} onChange={(e) => setLookback(e.target.value.replace(/[^0-9]/g, ""))} className="mt-1 h-8 w-24" />
                <span className="mt-1 block text-[11px] font-normal text-muted-foreground">Catches rows committed late with an older timestamp; merge removes the overlap.</span>
              </label>
            )}
            <label className="flex items-start gap-2 text-xs">
              <input type="checkbox" checked={profileAfter} onChange={(e) => setProfileAfter(e.target.checked)} className="mt-0.5" />
              <span><span className="font-medium">Profile after landing</span><span className="block text-[11px] text-muted-foreground">Makes the tables ready to model.</span></span>
            </label>
          </div>

          <div className="flex flex-wrap items-center gap-x-6 gap-y-1 rounded-xl bg-muted/40 px-4 py-3 text-xs">
            <span className="font-medium">Preflight</span>
            <span>{tables.length} table{tables.length === 1 ? "" : "s"}</span>
            <span>~{fmtRows(totalRows)} rows{incremental ? " at most" : ""}</span>
            <span>~{fmtBytes(totalBytes || null)} in Oracle</span>
            {skipped > 0 && <span className="text-warning">{skipped} unreadable column{skipped === 1 ? "" : "s"} left out</span>}
            <span className="flex items-center gap-1 text-success"><ShieldCheck className="h-3.5 w-3.5" /> Row counts verified after each load</span>
          </div>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        </div>

        <footer className="flex items-center gap-2 border-t px-6 py-3">
          {!columns && !error && <span className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Reading columns…</span>}
          <Button variant="ghost" className="ml-auto" onClick={onClose}>Cancel</Button>
          <Button disabled={pending || blocked || !columns} onClick={startLoad}>
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />} Start load
          </Button>
        </footer>
      </div>
    </div>
  );
}

const STEPS_INGEST = [["EXTRACTING", "Extract"], ["UPLOADING", "Upload"], ["LANDING", "Land"], ["VERIFY", "Verify"], ["PROFILING", "Profile"]] as const;
const ORDER = ["QUEUED", "EXTRACTING", "UPLOADING", "LANDING", "VERIFY", "PROFILING", "DONE"];

function stepState(s: IngestJobTable, step: string, profileAfter: boolean): "done" | "active" | "todo" | "skip" | "failed" {
  if (step === "PROFILING" && !profileAfter) return "skip";
  const terminal = ["DONE", "FAILED", "CANCELLED"].includes(s.phase);
  if (step === "VERIFY") {
    if (s.verification) return s.verification.verified ? "done" : "failed";
    return terminal && s.phase !== "DONE" ? "todo" : ORDER.indexOf(s.phase) > ORDER.indexOf("LANDING") ? "done" : "todo";
  }
  if (s.phase === "DONE") return step === "PROFILING" && s.profile_error ? "failed" : "done";
  if (terminal) return "todo";  // failed or stopped: the badge says so; finished steps are not tracked per phase
  const at = ORDER.indexOf(s.phase);
  const me = ORDER.indexOf(step);
  return me < at ? "done" : me === at ? "active" : "todo";
}

function Elapsed({ from, to }: { from?: number; to?: number | null }) {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    if (to) return;
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, [to]);
  if (!from) return null;
  return <span className="tabular-nums">{fmtDuration((to ?? now) - from)}</span>;
}

/** Live progress of a profile or load job: per table phases, rows and speed, verification, drift, merge counts and
 *  explained errors; cancel while running, retry what failed afterwards. */
export function JobCard({ job, profileAfter, onCancel, onRetry, onOpenLanded, onDismiss }: {
  job: IngestJob; profileAfter: boolean; onCancel: () => void; onRetry: (tables: string[]) => void;
  onOpenLanded: () => void; onDismiss: () => void;
}) {
  const entries = Object.entries(job.tables);
  const finished = entries.filter(([, s]) => ["DONE", "FAILED", "CANCELLED"].includes(s.phase)).length;
  const failed = entries.filter(([, s]) => ["FAILED", "CANCELLED"].includes(s.phase)).map(([t]) => t);
  const running = job.status === "RUNNING";
  const rows = entries.reduce((n, [, s]) => n + (s.rows_loaded ?? 0), 0);
  const verified = entries.filter(([, s]) => s.verification?.verified).length;
  const statusTone = running ? "text-primary" : job.status === "DONE" ? "text-success" : job.status === "CANCELLED" ? "text-muted-foreground" : "text-destructive";

  return (
    <section className="space-y-3 rounded-2xl border bg-card p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-3">
        <span className={cn("flex items-center gap-1.5 text-sm font-semibold", statusTone)}>
          {running ? <Loader2 className="h-4 w-4 animate-spin" /> : job.status === "DONE" ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
          {job.kind === "ingest" ? "Extract & land" : "Profile in Oracle"}
          {" · "}{running ? (job.cancel_requested ? "stopping…" : "running") : job.status.toLowerCase()}
        </span>
        <span className="flex items-center gap-1 text-xs text-muted-foreground"><Clock className="h-3.5 w-3.5" /><Elapsed from={job.started_at} to={job.finished_at} /></span>
        {job.kind === "ingest" && (
          <span className="text-xs text-muted-foreground">{rows.toLocaleString()} rows landed · {verified}/{entries.length} verified</span>
        )}
        <div className="ml-auto flex gap-2">
          {running && <Button size="sm" variant="outline" disabled={job.cancel_requested} onClick={onCancel}><Square className="h-3.5 w-3.5" /> Stop</Button>}
          {!running && failed.length > 0 && <Button size="sm" variant="outline" onClick={() => onRetry(failed)}><RotateCcw className="h-3.5 w-3.5" /> Retry {failed.length} failed</Button>}
          {!running && job.kind === "ingest" && finished > failed.length && <Button size="sm" onClick={onOpenLanded}>Open landed tables <ArrowRight className="h-3.5 w-3.5" /></Button>}
          {!running && <Button size="sm" variant="ghost" aria-label="Dismiss" onClick={onDismiss}><X className="h-3.5 w-3.5" /></Button>}
        </div>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div className={cn("h-full rounded-full transition-all", failed.length && !running ? "bg-warning" : "bg-primary")}
             style={{ width: `${entries.length ? Math.max(4, (finished / entries.length) * 100) : 0}%` }} />
      </div>
      <ul className="divide-y">
        {entries.map(([table, s]) => (
          <li key={table} className="py-2.5">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="w-56 truncate font-mono text-xs font-medium" title={table}>{table}</span>
              {job.kind === "ingest" ? (
                <ol className="flex items-center gap-1">
                  {STEPS_INGEST.map(([id, label]) => {
                    const st = stepState(s, id, profileAfter);
                    if (st === "skip") return null;
                    return (
                      <li key={id} className={cn("flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium",
                        st === "done" ? "bg-success/10 text-success" : st === "active" ? "bg-primary/10 text-primary"
                          : st === "failed" ? "bg-destructive/10 text-destructive" : "bg-muted text-muted-foreground")}>
                        {st === "active" && <Loader2 className="h-2.5 w-2.5 animate-spin" />}
                        {st === "done" && <CheckCircle2 className="h-2.5 w-2.5" />}
                        {st === "failed" && <XCircle className="h-2.5 w-2.5" />}
                        {label}
                      </li>
                    );
                  })}
                </ol>
              ) : (
                <Badge variant={s.phase === "DONE" ? "success" : s.phase === "FAILED" ? "destructive" : "secondary"} className="text-[10px]">
                  {s.phase === "PROFILING" && <Loader2 className="h-2.5 w-2.5 animate-spin" />}{s.phase.toLowerCase()}
                </Badge>
              )}
              {s.phase === "FAILED" && <Badge variant="destructive" className="text-[10px]">failed</Badge>}
              {s.phase === "CANCELLED" && <Badge variant="secondary" className="text-[10px]">stopped</Badge>}
              <span className="ml-auto flex flex-wrap items-center gap-x-3 text-[11px] text-muted-foreground">
                {s.phase === "EXTRACTING" && s.rows != null && <span className="tabular-nums">{s.rows.toLocaleString()} rows read{s.rows_per_s ? ` · ${fmtRows(s.rows_per_s)}/s` : ""}</span>}
                {s.verification && (
                  <span className={cn("flex items-center gap-1", s.verification.verified ? "text-success" : "text-destructive")}
                        title={`read ${s.verification.read_from_oracle}, copied ${s.verification.copied}, in table ${s.verification.in_table}`}>
                    <ShieldCheck className="h-3.5 w-3.5" />
                    {s.verification.verified ? `${s.verification.in_table.toLocaleString()} rows verified` : "count mismatch"}
                  </span>
                )}
                {s.merged && <span className="flex items-center gap-1"><GitMerge className="h-3 w-3" />+{s.merged.inserted.toLocaleString()} new · {s.merged.updated.toLocaleString()} updated</span>}
                {s.drift?.changed && (
                  <span className="flex items-center gap-1 text-warning" title={[
                    ...Object.keys(s.drift.added).map((c) => `added ${c}`), ...s.drift.missing.map((c) => `no longer in source: ${c}`),
                    ...Object.entries(s.drift.retyped).map(([c, v]) => `${c}: ${v.was} -> ${v.now}`)].join("\n")}>
                    <Layers className="h-3 w-3" /> schema changed{Object.keys(s.drift.added).length ? ` (+${Object.keys(s.drift.added).length} columns)` : ""}
                  </span>
                )}
                {(s.skipped?.length ?? 0) > 0 && <span title={s.skipped!.map((x) => `${x.column}: ${x.reason}`).join("\n")}>{s.skipped!.length} skipped</span>}
                {s.note && <span>{s.note}</span>}
                {s.row_count != null && job.kind === "profile" && <span>{s.row_count.toLocaleString()} rows profiled</span>}
                {s.duration_s != null && <span className="tabular-nums">{fmtDuration(s.duration_s)}</span>}
              </span>
            </div>
            {(s.explain || s.error) && s.phase !== "CANCELLED" && (
              <div className="mt-1.5 rounded-lg bg-destructive/5 px-3 py-2 text-xs">
                <p className="font-medium text-destructive">{s.explain?.title ?? s.error}</p>
                {s.explain?.fix && <p className="mt-0.5">{s.explain.fix}</p>}
                {s.explain && s.error && (
                  <details className="mt-1 text-[11px] text-muted-foreground"><summary className="cursor-pointer">Technical detail</summary>
                    <pre className="whitespace-pre-wrap break-all font-mono">{s.error}</pre></details>
                )}
              </div>
            )}
            {s.profile_error && <p className="mt-1 text-[11px] text-warning">Landed, but profiling failed: {s.profile_error}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}
