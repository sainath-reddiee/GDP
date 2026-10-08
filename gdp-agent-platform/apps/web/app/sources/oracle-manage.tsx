"use client";

import { useState, useTransition } from "react";
import {
  AlertTriangle, CalendarClock, CheckCircle2, ChevronDown, ChevronRight, GitMerge, KeyRound, Layers, Loader2, Network,
  Plus, RefreshCcw, Save, ShieldCheck, Trash2, XCircle, Zap,
} from "lucide-react";
import type { OracleOverview, OracleTable } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  oracleConnection, oracleEnvCheck, oraclePassword, oracleRemove, oracleSchedule, oracleSetup, oracleUnschedule,
} from "./actions";
import { ConnectionFields, connectionProblems, fmtDuration, fmtRows, Segmented, timeAgo, type OracleFields } from "./oracle-ui";

const MODE_ICON = { replace: RefreshCcw, append: Plus, merge: GitMerge } as Record<string, typeof Plus>;

/** Every landed table with its latest load and history: when, how, how many rows, verified or not. */
export function LoadsTab({ overview }: { overview: OracleOverview }) {
  const [open, setOpen] = useState<string | null>(null);
  const targets = Object.keys({ ...overview.loads, ...overview.history }).sort();
  if (!targets.length) {
    return <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">Nothing landed yet. Select tables and choose Extract &amp; land.</p>;
  }
  return (
    <div className="space-y-2">
      {overview.last_scheduled_run && (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <CalendarClock className="h-3.5 w-3.5" /> Last scheduled run {timeAgo(overview.last_scheduled_run.at)}: {overview.last_scheduled_run.tables} tables,
          {" "}{overview.last_scheduled_run.rows.toLocaleString()} rows{overview.last_scheduled_run.failed.length ? `, failed: ${overview.last_scheduled_run.failed.join(", ")}` : ""}
        </p>
      )}
      <div className="divide-y rounded-xl border">
        {targets.map((target) => {
          const load = overview.loads[target];
          const history = overview.history[target] ?? [];
          const last = history[0];
          const Icon = MODE_ICON[load?.mode ?? last?.mode ?? "replace"] ?? RefreshCcw;
          return (
            <div key={target}>
              <button type="button" onClick={() => setOpen(open === target ? null : target)}
                      className="flex w-full flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 text-left hover:bg-muted/30">
                {open === target ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                <span className="min-w-0">
                  <span className="block font-mono text-xs font-semibold">{target}</span>
                  <span className="block text-[11px] text-muted-foreground">from {load?.oracle_table ?? "Oracle"}</span>
                </span>
                <span className="flex items-center gap-1 text-xs"><Icon className="h-3.5 w-3.5 text-primary" />{load?.mode ?? last?.mode}</span>
                {load && <span className="text-xs tabular-nums">{fmtRows(load.rows)} rows last load</span>}
                {load?.watermark_column && <span className="text-[11px] text-muted-foreground">watermark {load.watermark_column} = {load.watermark ?? "–"}</span>}
                <span className="ml-auto flex items-center gap-2 text-xs">
                  {last?.status === "LANDED" ? (
                    last.verified ? <Badge variant="success" className="text-[10px]"><ShieldCheck className="h-3 w-3" /> verified</Badge>
                      : <Badge variant="warning" className="text-[10px]">not verified</Badge>
                  ) : last ? <Badge variant="destructive" className="text-[10px]">{last.status.toLowerCase()}</Badge> : null}
                  <span className="text-muted-foreground">{timeAgo(last?.at ?? load?.at)}</span>
                </span>
              </button>
              {open === target && (
                <div className="overflow-x-auto border-t bg-muted/20 px-4 py-2">
                  <table className="w-full text-xs">
                    <thead className="text-left text-[10px] uppercase tracking-wide text-muted-foreground">
                      <tr><th className="py-1.5">When</th><th>Mode</th><th>Status</th><th className="text-right">Read</th>
                        <th className="text-right">Landed</th><th>Check</th><th className="text-right">Time</th><th>Notes</th></tr>
                    </thead>
                    <tbody>
                      {history.map((h) => (
                        <tr key={h.batch_id} className="border-t">
                          <td className="py-1.5 tabular-nums">{h.at} UTC {h.scheduled && <CalendarClock className="ml-1 inline h-3 w-3 text-primary" aria-label="scheduled" />}</td>
                          <td>{h.mode}</td>
                          <td className={h.status === "LANDED" ? "text-success" : "text-destructive"}>{h.status.toLowerCase()}</td>
                          <td className="text-right tabular-nums">{h.read == null ? "–" : h.read.toLocaleString()}</td>
                          <td className="text-right tabular-nums">{h.rows.toLocaleString()}</td>
                          <td>{h.verified ? <ShieldCheck className="h-3.5 w-3.5 text-success" /> : h.status === "LANDED" ? <AlertTriangle className="h-3.5 w-3.5 text-warning" /> : "–"}</td>
                          <td className="text-right tabular-nums">{fmtDuration(h.duration_s)}</td>
                          <td className="max-w-xs truncate text-muted-foreground" title={h.error ?? ""}>
                            {h.drift && <span className="mr-2 inline-flex items-center gap-1 text-warning"><Layers className="h-3 w-3" />schema changed</span>}
                            {h.error}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

const PRESETS = [
  ["*/15 * * * * UTC", "Every 15 minutes"], ["0 * * * * UTC", "Hourly"], ["0 */6 * * * UTC", "Every 6 hours"],
  ["0 2 * * * UTC", "Daily at 02:00 UTC"], ["custom", "Custom cron"],
] as const;

/** Incremental loads on a schedule: a Snowflake task calls the source's procedure (Snowflake runtime only). */
export function ScheduleTab({ sourceId, overview, tables, onChanged }: {
  sourceId: string; overview: OracleOverview; tables: OracleTable[]; onChanged: () => void;
}) {
  const current = overview.schedule;
  const landed = tables.filter((t) => t.load).map((t) => t.table);
  const [chosen, setChosen] = useState<string[]>(current?.tables ?? landed);
  const [mode, setMode] = useState<"append" | "merge">(current?.mode === "merge" || !current ? "merge" : "append");
  const [preset, setPreset] = useState<string>(current ? (PRESETS.some(([c]) => c === current.cron) ? current.cron : "custom") : "0 * * * * UTC");
  const [cron, setCron] = useState(current?.cron ?? "0 * * * * UTC");
  const [lookback, setLookback] = useState(String(current?.lookback_minutes ?? 15));
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, start] = useTransition();

  if (overview.connection.runtime === "api_host") {
    return (
      <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
        Schedules run as Snowflake tasks, so they need the Inside Snowflake runtime. This source runs on the API server;
        use Extract &amp; land, or schedule a call to the API from your orchestrator.
      </p>
    );
  }
  const noWatermark = chosen.filter((t) => {
    const table = tables.find((x) => x.table === t);
    return !(table?.load?.watermark_column || table?.watermark);
  });
  const noKey = mode === "merge" ? chosen.filter((t) => !tables.find((x) => x.table === t)?.primary_key.length) : [];

  const save = () => start(async () => {
    setError(""); setNotice("");
    const watermark_columns = Object.fromEntries(chosen.map((t) => {
      const table = tables.find((x) => x.table === t);
      return [t, table?.load?.watermark_column ?? table?.watermark?.column ?? ""];
    }).filter(([, c]) => c));
    const r = await oracleSchedule(sourceId, { tables: chosen, mode, cron: preset === "custom" ? cron : preset,
                                               watermark_columns, merge_keys: {}, lookback_minutes: Number(lookback) || 0 });
    if (!r.ok) { setError(r.error); return; }
    setNotice("Schedule saved. The task runs in Snowflake, even when this app is closed.");
    onChanged();
  });

  return (
    <div className="space-y-4">
      {current && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-success/30 bg-success/5 p-4 text-sm">
          <CalendarClock className="h-5 w-5 text-success" />
          <div>
            <p className="font-semibold">{PRESETS.find(([c]) => c === current.cron)?.[1] ?? current.cron} · {current.mode}</p>
            <p className="text-xs text-muted-foreground">{current.tables.length} tables on warehouse {current.warehouse} · since {current.created_at} UTC</p>
          </div>
          <Button size="sm" variant="outline" className="ml-auto" disabled={pending}
                  onClick={() => start(async () => { const r = await oracleUnschedule(sourceId); if (r.ok) onChanged(); else setError(r.error); })}>
            <Trash2 className="h-3.5 w-3.5" /> Remove schedule
          </Button>
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <div className="space-y-2">
          <p className="text-xs font-medium">Tables</p>
          <div className="max-h-64 overflow-y-auto rounded-xl border">
            {tables.filter((t) => t.extractable).map((t) => (
              <label key={t.table} className="flex items-center gap-2 border-b px-3 py-1.5 text-xs last:border-0">
                <input type="checkbox" checked={chosen.includes(t.table)}
                       onChange={() => setChosen((c) => (c.includes(t.table) ? c.filter((x) => x !== t.table) : [...c, t.table]))} />
                <span className="font-mono">{t.table}</span>
                {t.load && <Badge variant="outline" className="text-[10px]">landed</Badge>}
                <span className="ml-auto text-[10px] text-muted-foreground">{t.load?.watermark_column ?? t.watermark?.column ?? "no watermark"}</span>
              </label>
            ))}
          </div>
        </div>
        <div className="space-y-4">
          <div className="space-y-1">
            <p className="text-xs font-medium">How</p>
            <Segmented label="Schedule mode" value={mode} onChange={setMode} options={[["merge", "Merge changes"], ["append", "Append new rows"]] as const} />
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium">When</p>
            <select value={preset} onChange={(e) => setPreset(e.target.value)} aria-label="Frequency" className="h-9 w-full rounded-md border bg-card px-2 text-sm">
              {PRESETS.map(([c, label]) => <option key={c} value={c}>{label}</option>)}
            </select>
            {preset === "custom" && (
              <Input value={cron} onChange={(e) => setCron(e.target.value)} className="font-mono" aria-label="Cron expression"
                     placeholder="minute hour day month weekday timezone, e.g. 30 6 * * 1-5 Europe/London" />
            )}
          </div>
          {mode === "merge" && (
            <label className="block text-xs font-medium">Re-read window (minutes)
              <Input value={lookback} onChange={(e) => setLookback(e.target.value.replace(/[^0-9]/g, ""))} className="mt-1 h-8 w-24" />
            </label>
          )}
          {noWatermark.length > 0 && <p className="text-[11px] text-warning">Without a watermark these are read in full each run: {noWatermark.join(", ")}</p>}
          {noKey.length > 0 && <p className="text-[11px] text-destructive">No primary key for merge: {noKey.join(", ")}. Land them once with merge keys chosen, or use append.</p>}
          <Button disabled={pending || !chosen.length || noKey.length > 0} onClick={save}>
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4" />} {current ? "Update schedule" : "Create schedule"}
          </Button>
        </div>
      </div>
      {notice && <p className="text-sm text-success">{notice}</p>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </div>
  );
}

/** Connection, credentials and access objects, and removing the source. */
export function SettingsTab({ sourceId, overview, onChanged, onRemoved }: {
  sourceId: string; overview: OracleOverview; onChanged: () => void; onRemoved: () => void;
}) {
  const c = overview.connection;
  const initial: OracleFields = Object.fromEntries(Object.entries({
    host: c.host, port: String(c.port ?? ""), protocol: c.protocol ?? "tcp", ssl_server_dn_match: c.ssl_server_dn_match ?? "true",
    user: c.user, schema_owner: c.schema_owner, ...(c.sid ? { sid: c.sid } : { service_name: c.service_name ?? "" }),
  }).filter(([, v]) => v !== undefined && v !== null)) as OracleFields;
  const [fields, setFields] = useState<OracleFields>(initial);
  const [password, setPassword] = useState("");
  const [confirmName, setConfirmName] = useState("");
  const [dropLanded, setDropLanded] = useState(false);
  const [envPresent, setEnvPresent] = useState<boolean | null>(overview.access.password_env_present);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [pending, start] = useTransition();
  const dirty = JSON.stringify(fields) !== JSON.stringify(initial);
  const problems = connectionProblems(fields);
  const say = (tone: "ok" | "error", text: string) => setMessage({ tone, text });

  return (
    <div className="space-y-6">
      <section className="space-y-3 rounded-xl border p-4">
        <div className="flex items-center gap-2">
          <Network className="h-4 w-4 text-primary" /><p className="text-sm font-semibold">Connection</p>
          {dirty && <Badge variant="warning" className="text-[10px]">unsaved</Badge>}
        </div>
        <ConnectionFields value={fields} onChange={setFields} disabled={pending} />
        <div className="flex items-center gap-2">
          <Button size="sm" disabled={!dirty || problems.length > 0 || pending} onClick={() => start(async () => {
            const r = await oracleConnection(sourceId, fields);
            if (!r.ok) { say("error", r.error); return; }
            say("ok", r.data.note ?? "Connection saved. Run Test connection to confirm.");
            onChanged();
          })}><Save className="h-3.5 w-3.5" /> Save connection</Button>
          {dirty && <Button size="sm" variant="ghost" onClick={() => setFields(initial)}>Discard</Button>}
          {problems[0] && dirty && <span className="text-[11px] text-destructive">{problems[0]}</span>}
        </div>
      </section>

      <section className="space-y-3 rounded-xl border p-4">
        <div className="flex items-center gap-2"><KeyRound className="h-4 w-4 text-primary" /><p className="text-sm font-semibold">Credentials and access</p></div>
        {c.runtime === "snowflake" ? (
          <>
            <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-[160px_1fr]">
              <dt className="text-muted-foreground">Secret</dt>
              <dd className="font-mono">{overview.access.secret ?? "not set"} {overview.access.created.includes("secret") && <Badge variant="outline" className="ml-1 text-[10px]">created here</Badge>}</dd>
              <dt className="text-muted-foreground">External access</dt>
              <dd className="font-mono">{overview.access.integration ?? "not set"} {overview.access.created.includes("integration") && <Badge variant="outline" className="ml-1 text-[10px]">created here</Badge>}</dd>
              <dt className="text-muted-foreground">Procedure</dt><dd className="font-mono">{overview.access.procedure ?? "not created"}</dd>
            </dl>
            {overview.access.secret && (
              <div className="flex flex-wrap items-end gap-2">
                <label className="text-xs font-medium">New Oracle password (after a rotation)
                  <Input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} className="mt-1 w-72" />
                </label>
                <Button size="sm" variant="outline" disabled={!password || pending} onClick={() => start(async () => {
                  const r = await oraclePassword(sourceId, password);
                  setPassword("");
                  if (r.ok) say("ok", `Updated ${r.data.updated}. Run Test connection to confirm.`); else say("error", r.error);
                })}>Update password</Button>
              </div>
            )}
            {overview.access.secret && overview.access.integration && (
              <Button size="sm" variant="ghost" disabled={pending} onClick={() => start(async () => {
                const r = await oracleSetup(sourceId, { secret_mode: "existing", secret: overview.access.secret!, integration_mode: "existing",
                                                        external_access_integration: overview.access.integration! });
                if (!r.ok) say("error", r.error);
                else if (!r.data.ready) say("error", r.data.detail ?? "Setup did not complete");
                else { say("ok", "Procedure refreshed with the current platform code."); onChanged(); }
              })}><RefreshCcw className="h-3.5 w-3.5" /> Refresh the procedure</Button>
            )}
          </>
        ) : (
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <span>Password from <span className="font-mono">{c.password_env}</span> on the API server</span>
            {envPresent === true && <span className="flex items-center gap-1 text-success"><CheckCircle2 className="h-3.5 w-3.5" /> set</span>}
            {envPresent === false && <span className="flex items-center gap-1 text-destructive"><XCircle className="h-3.5 w-3.5" /> not set: set it and restart the API</span>}
            <Button size="sm" variant="outline" onClick={() => c.password_env && oracleEnvCheck(c.password_env).then((r) => r.ok && setEnvPresent(r.data.present))}>Check again</Button>
            {c.wallet_dir && <span>Wallet <span className="font-mono">{c.wallet_dir}</span></span>}
          </div>
        )}
      </section>

      <section className="space-y-3 rounded-xl border border-destructive/30 p-4">
        <p className="text-sm font-semibold text-destructive">Remove this source</p>
        <p className="text-xs text-muted-foreground">
          Drops its schedule and procedure, and the secret and integration only if they were created here (chosen ones are left alone).
          Profiles stay in the profile store.
        </p>
        <label className="flex items-center gap-2 text-xs">
          <input type="checkbox" checked={dropLanded} onChange={(e) => setDropLanded(e.target.checked)} />
          Also drop the landed tables in <span className="font-mono">{overview.landing.database}.{overview.landing.schema}</span>
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <Input value={confirmName} onChange={(e) => setConfirmName(e.target.value)} placeholder={`Type ${overview.name} to confirm`} className="w-64" />
          <Button size="sm" variant="destructive" disabled={confirmName !== overview.name || pending} onClick={() => start(async () => {
            const r = await oracleRemove(sourceId, dropLanded);
            if (!r.ok) { say("error", r.error); return; }
            const failed = r.data.log.filter((l) => !l.ok);
            if (failed.length) say("error", `Removed, but ${failed.length} cleanup statement(s) need an admin: ${failed.map((f) => f.sql).join("; ")}`);
            onRemoved();
          })}><Trash2 className="h-3.5 w-3.5" /> Remove source</Button>
        </div>
      </section>

      {message && <p role="status" className={cn("text-sm", message.tone === "ok" ? "text-success" : "text-destructive")}>{message.text}</p>}
    </div>
  );
}
