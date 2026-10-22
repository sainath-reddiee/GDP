"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Activity, CircleCheck, CircleDashed, ExternalLink, Loader2, Plug, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { savePlatformSetting } from "./actions";
import {
  checkMonitorsNow, testDatadog, type DatadogSetting, type DatadogStatus, type DatadogTest,
} from "../ops/datadog-actions";

type Msg = { tone: "ok" | "info" | "error"; text: string } | null;
const STATES = ["Alert", "Warn", "No Data"] as const;
const TOKENS: [string, string][] = [["{dag_id}", "the incident's DAG"], ["{task_id}", "its failing task"], ["{run_id}", "the DAG run"],
  ["{env_id}", "the platform's Airflow environment id"], ["{try_number}", "the task try"]];
const ENV_LINES = "DATADOG_SITE=\nDATADOG_API_KEY=\nDATADOG_APP_KEY=\nDATADOG_APP_URL=";

function Item({ done, title, children }: { done: boolean; title: string; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      {done ? <CircleCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" /> : <CircleDashed className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />}
      <div className="min-w-0 flex-1 space-y-1.5"><p className="text-sm font-medium">{title}</p>{children}</div>
    </li>
  );
}

function Check({ label, hint, checked, disabled, onChange }: { label: string; hint?: string; checked: boolean; disabled?: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-start gap-2 text-xs">
      <input type="checkbox" className="mt-0.5" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span><span className="font-medium">{label}</span>{hint && <span className="block text-muted-foreground">{hint}</span>}</span>
    </label>
  );
}

/** Admin, Integrations, Datadog: the keys on the API host (never shown, never typed here), a connection test, and what
 *  Operations does with Datadog: incident logs, logs for the AI diagnosis, and monitors that open incidents. */
export function DatadogTab({ status, mayEdit, mayTest, mayOperate, onMsg }: {
  status: DatadogStatus | null; mayEdit: boolean; mayTest: boolean; mayOperate: boolean; onMsg: (m: Msg) => void;
}) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [busy, setBusy] = useState<"test" | "monitors" | null>(null);
  const [test, setTest] = useState<DatadogTest | null>(null);
  const [form, setForm] = useState<DatadogSetting | null>(status?.setting ?? null);
  const [tags, setTags] = useState((status?.setting.monitor_tags ?? []).join(", "));
  if (!status || !form) {
    return <section className="surface p-5 text-sm text-muted-foreground">Datadog needs the latest API build (restart the API after updating).</section>;
  }
  const c = status.connection;
  const set = (patch: Partial<DatadogSetting>) => setForm((f) => (f ? { ...f, ...patch } : f));
  const save = () => start(async () => {
    const value = { ...form, monitor_tags: tags.split(",").map((t) => t.trim()).filter(Boolean) };
    const r = await savePlatformSetting("DATADOG", value);
    onMsg(r.ok ? { tone: "ok", text: value.enabled ? "Datadog settings saved. Incidents now use Datadog." : "Datadog settings saved (Datadog is off)." }
      : { tone: /approval|request/i.test(r.error) ? "info" : "error", text: r.error });
    if (r.ok) router.refresh();
  });
  const doTest = async () => {
    setBusy("test");
    try {
      const r = await testDatadog();
      if (r.ok) setTest(r.data);
      else onMsg({ tone: /approval|request/i.test(r.error) ? "info" : "error", text: r.error });
    } finally { setBusy(null); }
  };
  const doMonitors = async () => {
    setBusy("monitors");
    try {
      const r = await checkMonitorsNow();
      if (!r.ok) { onMsg({ tone: "error", text: r.error }); return; }
      const d = r.data;
      const opened = Object.entries(d.actions ?? {}).map(([k, v]) => `${v} ${k}`).join(", ");
      onMsg(d.disabled ? { tone: "info", text: "Monitors are off: switch Datadog and monitors on, then save." }
        : { tone: d.errors?.length ? "error" : "ok", text: `${d.monitors ?? 0} monitors read, ${d.alerting ?? 0} alerting${opened ? ` (${opened})` : ""}, ${d.resolved ?? 0} resolved.${d.errors?.length ? ` Skipped: ${d.errors.join("; ")}` : ""}` });
    } finally { setBusy(null); }
  };
  const keysReady = c.api_key_set && c.app_key_set && c.configured;
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
      <section className="surface overflow-hidden">
        <header className="flex items-start gap-3 border-b px-5 py-4">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-violet-50 text-violet-600 ring-1 ring-inset ring-violet-100"><Activity className="h-5 w-5" /></span>
          <div className="min-w-0 flex-1">
            <h3 className="flex items-center gap-2 text-base font-semibold">Datadog
              <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ring-inset",
                status.setting.enabled && keysReady ? "bg-emerald-50 text-emerald-700 ring-emerald-100" : keysReady ? "bg-sky-50 text-sky-700 ring-sky-100" : "bg-slate-100 text-slate-600 ring-slate-200")}>
                {status.setting.enabled && keysReady ? "on" : keysReady ? "keys found, off" : "setup needed"}</span></h3>
            <p className="text-xs text-muted-foreground">Pipeline logs for every incident and its AI diagnosis, and Datadog monitors as incidents in the same inbox, routed and ticketed like Airflow failures.</p>
          </div>
        </header>
        <ol className="space-y-5 px-5 py-4">
          <Item done={keysReady} title="1. Keys on the API host">
            <p className="text-xs text-muted-foreground">The keys never go into the platform. Put these lines in <code className="font-mono">docker/api.env</code> (or <code className="font-mono">apps/api/.env</code> when the API runs locally), then restart the API and the worker.
              The application key needs only the scopes <code className="font-mono">logs_read_data</code> and <code className="font-mono">monitors_read</code>.</p>
            <pre className="rounded-lg bg-muted px-3 py-2 font-mono text-[11px]">{ENV_LINES}</pre>
            <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
              <div className="flex gap-2"><dt className="text-muted-foreground">Site</dt><dd className="font-mono">{c.site}</dd></div>
              <div className="flex gap-2"><dt className="text-muted-foreground">Web</dt><dd><a href={c.app_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 font-mono text-primary hover:underline">{c.app_url}<ExternalLink className="h-3 w-3" /></a></dd></div>
              <div className="flex gap-2"><dt className="text-muted-foreground">API key</dt><dd className={c.api_key_set ? "text-emerald-700" : "text-rose-700"}>{c.api_key_set ? "set" : "missing"}</dd></div>
              <div className="flex gap-2"><dt className="text-muted-foreground">Application key</dt><dd className={c.app_key_set ? "text-emerald-700" : "text-rose-700"}>{c.app_key_set ? "set" : "missing"}</dd></div>
            </dl>
            {c.problems.length > 0 && <ul className="list-disc pl-4 text-xs text-rose-700">{c.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
          </Item>
          <Item done={!!test?.ok} title="2. Test the connection">
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" disabled={!mayTest || busy !== null} onClick={doTest}>
                {busy === "test" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plug className="h-3.5 w-3.5" />}Test connection</Button>
              {!mayTest && <span className="self-center text-xs text-muted-foreground">Needs INTEGRATION.MANAGE.</span>}
            </div>
            {test && (
              <div role={test.ok ? "status" : "alert"} className={cn("space-y-0.5 rounded-lg px-3 py-2 text-xs", test.ok ? "bg-success/10 text-success" : "bg-destructive/10 text-destructive")}>
                <p>API key: {test.api_key ? "valid" : test.errors.api_key ?? test.errors.setup ?? "not checked"}</p>
                {test.api_key && <p>Log search: {test.errors.logs ? `${test.errors.logs} (needs logs_read_data)` : `${test.logs ?? 0} entries in the last 15 minutes for ${test.query}`}</p>}
                {test.api_key && <p>Monitors: {test.errors.monitors ? `${test.errors.monitors} (needs monitors_read)` : `${test.monitors ?? 0} visible`}</p>}
              </div>
            )}
          </Item>
          <Item done={status.setting.enabled} title="3. Switch it on">
            <p className="text-xs text-muted-foreground">Save the settings on the right with Datadog on. Monitors are read every two minutes by the worker.</p>
            <Button size="sm" variant="outline" disabled={!mayOperate || busy !== null || !status.setting.enabled} onClick={doMonitors}>
              {busy === "monitors" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}Check monitors now</Button>
          </Item>
        </ol>
      </section>

      <section className="surface space-y-4 p-5">
        <h3 className="text-sm font-semibold">What Operations does with Datadog</h3>
        <Check label="Use Datadog" checked={form.enabled} disabled={!mayEdit} onChange={(v) => set({ enabled: v })}
               hint="Off: nothing is read from Datadog." />
        <fieldset className="space-y-2 rounded-lg border p-3" disabled={!mayEdit}>
          <legend className="px-1 text-xs font-semibold">Logs</legend>
          <label className="block space-y-1 text-xs font-medium">Log search for an incident
            <Input value={form.log_query} onChange={(e) => set({ log_query: e.target.value })} className="font-mono text-xs" /></label>
          <p className="text-[11px] text-muted-foreground">Written in Datadog&apos;s log search syntax, using the tags your pipelines send. Each of these is filled in from the incident; a term whose value is missing is left out:</p>
          <ul className="grid gap-0.5 text-[11px] sm:grid-cols-2">{TOKENS.map(([t, d]) => <li key={t}><code className="font-mono">{t}</code> <span className="text-muted-foreground">{d}</span></li>)}</ul>
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="block space-y-1 text-xs font-medium">Minutes before the failure
              <Input type="number" min={1} max={1440} value={form.log_lookback_minutes} onChange={(e) => set({ log_lookback_minutes: Number(e.target.value) })} /></label>
            <label className="block space-y-1 text-xs font-medium">Most log lines
              <Input type="number" min={10} max={1000} value={form.log_limit} onChange={(e) => set({ log_limit: Number(e.target.value) })} /></label>
          </div>
          <label className="block space-y-1 text-xs font-medium">When a task fails, read its error from
            <Select value={form.failure_logs} onChange={(e) => set({ failure_logs: e.target.value as DatadogSetting["failure_logs"] })}>
              <option value="fallback">Airflow, then Datadog when Airflow&apos;s log cannot be read</option>
              <option value="first">Datadog first, then Airflow</option>
              <option value="off">Airflow only</option>
            </Select></label>
          <p className="text-[11px] text-muted-foreground">The error then goes through the usual flow: a known fix from the knowledge base when it matches one, otherwise the root-cause analysis and a ticket.</p>
          <Check label="Give the AI diagnosis Datadog's logs" checked={form.diagnosis_logs} disabled={!mayEdit} onChange={(v) => set({ diagnosis_logs: v })}
                 hint="When the Airflow task log cannot be read (expired, or no AWS access)." />
        </fieldset>
        <fieldset className="space-y-2 rounded-lg border p-3" disabled={!mayEdit}>
          <legend className="px-1 text-xs font-semibold">Monitors</legend>
          <Check label="Alerting monitors open incidents" checked={form.monitors} disabled={!mayEdit} onChange={(v) => set({ monitors: v })}
                 hint="They resolve themselves when the monitor recovers. Jira and in-app notifications only." />
          <label className="block space-y-1 text-xs font-medium">Only monitors with these tags
            <Input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="team:data, service:airflow" className="font-mono text-xs" /></label>
          <div className="flex flex-wrap gap-3">
            {STATES.map((s) => (
              <Check key={s} label={s} checked={form.monitor_states.includes(s)} disabled={!mayEdit}
                     onChange={(v) => set({ monitor_states: STATES.filter((x) => (x === s ? v : form.monitor_states.includes(x))) })} />
            ))}
          </div>
          <label className="block space-y-1 text-xs font-medium">Severity when the monitor has no priority
            <Select value={form.default_severity} onChange={(e) => set({ default_severity: e.target.value as DatadogSetting["default_severity"] })}>
              {["P1", "P2", "P3", "P4"].map((p) => <option key={p}>{p}</option>)}
            </Select></label>
        </fieldset>
        <div className="flex items-center gap-2">
          <Button size="sm" disabled={!mayEdit || pending} onClick={save}>{pending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}Save</Button>
          {!mayEdit && <span className="text-xs text-muted-foreground">Needs CONFIG.EDIT.</span>}
        </div>
      </section>
    </div>
  );
}
