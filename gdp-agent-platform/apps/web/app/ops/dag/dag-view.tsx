"use client";

import Link from "next/link";
import { Fragment, useEffect, useMemo, useState, useTransition } from "react";
import { Check, ExternalLink, FileText, Loader2, RefreshCw, Settings2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { CopyButton } from "../../runs/[runId]/dbt/studio-ui";
import { Alert, Notice, Pending, useSeq } from "../../qa/qa-shared";
import {
  dagDetail, pollEnv, runDetail, saveDagSettings, taskLog,
  type Criticality, type DagDetail, type DagRun, type TaskLog, type TaskRun,
} from "../actions";
import { duration, explain, parseTs, pct, pctTone, StateBadge, stateTone, When } from "../ops-shared";

type Option = { id: string; name: string };
const CRITICALITY: Criticality[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];
const ERROR_LINE = /error|exception|traceback|failed/i;

const time = (r: DagRun) => parseTs(r.start ?? r.logical_date)?.getTime() ?? 0;

/** One DAG's page: header with Airflow link, settings, runs strip and table, a run's tasks and a task's log. */
export function DagView({ envId, envName, envEnabled, initial, initialRun, canOperate, domains, repos, lookupErrors }: {
  envId: string; envName: string; envEnabled: boolean; initial: { dag: DagDetail; runs: DagRun[] }; initialRun: string;
  canOperate: boolean; domains: Option[]; repos: Option[]; lookupErrors: string[];
}) {
  const [data, setData] = useState(initial);
  const [runId, setRunId] = useState(initialRun);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState(false);
  const [polling, startPoll] = useTransition();
  const refreshSeq = useSeq();
  const { dag, runs } = data;

  const select = (id: string) => {
    setRunId(id);
    const p = new URLSearchParams(window.location.search);
    if (id) p.set("run", id); else p.delete("run");
    window.history.replaceState(window.history.state, "", `${window.location.pathname}?${p}`);
  };

  const refresh = async () => {
    const ticket = refreshSeq.next();
    const r = await dagDetail(envId, dag.dag_id);
    if (!refreshSeq.current(ticket)) return;
    if (r.ok) setData(r.data); else setError(explain(r.error));
  };

  const poll = () => startPoll(async () => {
    setError(""); setNotice("");
    const r = await pollEnv(envId);
    if (!r.ok) { setError(explain(r.error)); return; }
    setNotice(r.data.detail || (r.data.started ? `Polling ${envName} now. New runs show up in a minute.` : `A poll of ${envName} is already running.`));
    await refresh();
  });

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-4">
        <div className="min-w-[min(100%,24rem)] flex-1">
          <p className="eyebrow">
            <Link href={`/ops?${new URLSearchParams({ env: envId })}`} className="hover:underline">Pipelines</Link>
            <span className="mx-1.5 text-muted-foreground">/</span>
            <span className="normal-case tracking-normal text-muted-foreground">{envName}</span>
          </p>
          <h1 className="mt-1 break-all font-mono text-xl">{dag.dag_id}</h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <StateBadge state={dag.last_state} />
            {dag.is_paused && <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">paused</span>}
            {!dag.is_active && <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">inactive</span>}
            {dag.criticality && <span className="rounded-full bg-violet-50 px-1.5 py-0.5 text-[10px] text-violet-700">{dag.criticality.toLowerCase()}</span>}
            <span>Schedule <span className="font-mono">{dag.schedule || "none"}</span></span>
            <span>· Owners {dag.owners?.join(", ") || "none"}</span>
            {dag.tags?.length > 0 && <span>· Tags {dag.tags.join(", ")}</span>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {dag.airflow_url && (
            <a href={dag.airflow_url} target="_blank" rel="noreferrer"
               className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-3 py-1.5 text-sm font-medium shadow-xs hover:bg-muted">
              <ExternalLink className="h-4 w-4" />Open in Airflow
            </a>
          )}
          {canOperate && (
            <>
              <Button variant="outline" onClick={() => setEditing((v) => !v)} aria-expanded={editing}><Settings2 className="h-4 w-4" />Settings</Button>
              <Button variant="outline" disabled={polling || !envEnabled} onClick={poll} title={envEnabled ? undefined : "The environment is disabled"}>
                {polling ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}Poll now
              </Button>
            </>
          )}
        </div>
      </div>

      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
      {notice && <Notice onDismiss={() => setNotice("")}>{notice}</Notice>}
      {lookupErrors.map((e) => <Alert key={e}>{e}</Alert>)}

      <dl className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {[
          ["Success 7 d", <span key="7" className={pctTone(dag.success_7d)}>{pct(dag.success_7d)}</span>],
          ["Success 30 d", <span key="30" className={pctTone(dag.success_30d)}>{pct(dag.success_30d)}</span>],
          ["p50 / p95", `${duration(dag.p50_s)} / ${duration(dag.p95_s)}`],
          ["Runs 7 d", dag.runs_7d],
          ["Last run", <When key="lr" iso={dag.last_run_at} rel />],
        ].map(([label, value]) => (
          <div key={String(label)} className="surface px-4 py-3">
            <dt className="text-[11px] text-muted-foreground">{label}</dt>
            <dd className="text-base font-semibold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      {dag.fileloc && <p className="text-xs text-muted-foreground">File <span className="break-all font-mono">{dag.fileloc}</span></p>}

      {editing && canOperate && (
        <SettingsForm dag={dag} envId={envId} domains={domains} repos={repos} onClose={() => setEditing(false)}
                      onSaved={async () => { setEditing(false); setNotice("DAG settings saved."); await refresh(); }} />
      )}

      <section className="surface overflow-hidden">
        <header className="flex items-center gap-2 border-b px-4 py-3">
          <h2 className="text-sm font-semibold">Runs</h2>
          <span className="text-xs text-muted-foreground">{runs.length} most recent</span>
        </header>
        {runs.length ? (
          <>
            <RunStrip runs={runs} selected={runId} onSelect={select} />
            <RunsTable runs={runs} selected={runId} onSelect={select} />
          </>
        ) : <p className="px-4 py-10 text-center text-sm text-muted-foreground">No runs recorded yet.</p>}
      </section>

      {runId && <RunPanel key={runId} envId={envId} dagId={dag.dag_id} runId={runId} onClose={() => select("")} />}
    </div>
  );
}

/** Oldest to newest, left to right; bar height is the run's duration against the longest one shown. */
function RunStrip({ runs, selected, onSelect }: { runs: DagRun[]; selected: string; onSelect: (id: string) => void }) {
  const ordered = useMemo(() => [...runs].sort((a, b) => time(a) - time(b)), [runs]);
  const max = Math.max(1, ...ordered.map((r) => r.duration_s ?? 0));
  return (
    <div className="border-b px-4 py-3">
      <div className="flex h-16 items-end gap-[3px] overflow-x-auto" role="list" aria-label="Runs timeline">
        {ordered.map((r) => {
          const h = r.duration_s ? Math.max(12, Math.round((r.duration_s / max) * 100)) : 12;
          return (
            <button key={r.run_id} type="button" role="listitem" onClick={() => onSelect(r.run_id)}
                    title={`${r.run_id}\n${(r.state ?? "unknown").replace(/_/g, " ")} · ${duration(r.duration_s)}`}
                    aria-label={`Run ${r.run_id}, ${r.state ?? "unknown"}, ${duration(r.duration_s)}`}
                    aria-pressed={selected === r.run_id}
                    className={cn("w-2.5 min-w-[10px] shrink-0 rounded-sm transition hover:opacity-80", stateTone(r.state).bar,
                      selected === r.run_id && "ring-2 ring-primary ring-offset-1")}
                    style={{ height: `${h}%` }} />
          );
        })}
      </div>
      <p className="mt-1 flex justify-between text-[10px] text-muted-foreground"><span>older</span><span>newer</span></p>
    </div>
  );
}

function RunsTable({ runs, selected, onSelect }: { runs: DagRun[]; selected: string; onSelect: (id: string) => void }) {
  const ordered = useMemo(() => [...runs].sort((a, b) => time(b) - time(a)), [runs]);
  return (
    <div className="max-h-[22rem] overflow-auto">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-card text-left text-[11px] uppercase tracking-wide text-muted-foreground">
          <tr className="border-b">
            <th className="px-4 py-2 font-medium">Run</th>
            <th className="px-3 py-2 font-medium">Type</th>
            <th className="px-3 py-2 font-medium">State</th>
            <th className="px-3 py-2 font-medium">Logical date</th>
            <th className="px-3 py-2 font-medium">Started</th>
            <th className="px-3 py-2 text-right font-medium">Duration</th>
            <th className="px-4 py-2 text-right font-medium">Failed tasks</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {ordered.map((r) => (
            <tr key={r.run_id} onClick={() => onSelect(r.run_id)}
                className={cn("cursor-pointer hover:bg-muted/40", selected === r.run_id && "bg-primary/5")}>
              <td className="max-w-[18rem] px-4 py-2">
                <button type="button" onClick={(e) => { e.stopPropagation(); onSelect(r.run_id); }}
                        className="block max-w-full truncate font-mono text-xs text-primary hover:underline" title={r.run_id}>{r.run_id}</button>
              </td>
              <td className="px-3 py-2 text-xs">{(r.run_type ?? "-").replace(/_/g, " ")}{r.external_trigger ? " (manual)" : ""}</td>
              <td className="px-3 py-2"><StateBadge state={r.state} /></td>
              <td className="whitespace-nowrap px-3 py-2 text-xs"><When iso={r.logical_date} empty="-" /></td>
              <td className="whitespace-nowrap px-3 py-2 text-xs"><When iso={r.start} empty="not started" /></td>
              <td className="px-3 py-2 text-right text-xs tabular-nums">{duration(r.duration_s)}</td>
              <td className={cn("px-4 py-2 text-right text-xs tabular-nums", r.failed_tasks ? "font-semibold text-rose-700" : "text-muted-foreground")}>{r.failed_tasks ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type LogKey = { task: TaskRun; key: string };

function RunPanel({ envId, dagId, runId, onClose }: { envId: string; dagId: string; runId: string; onClose: () => void }) {
  const [tasks, setTasks] = useState<TaskRun[] | null>(null);
  const [error, setError] = useState("");
  const [log, setLog] = useState<LogKey | null>(null);
  useEffect(() => {
    let live = true;
    runDetail(envId, dagId, runId).then((r) => {
      if (!live) return;
      if (r.ok) setTasks(r.data.tasks); else { setError(explain(r.error)); setTasks([]); }
    }, (e: unknown) => { if (live) { setError(e instanceof Error ? e.message : "Could not load the run."); setTasks([]); } });
    return () => { live = false; };
  }, [envId, dagId, runId]);
  const ordered = useMemo(() => [...(tasks ?? [])].sort((a, b) =>
    (parseTs(a.start)?.getTime() ?? Infinity) - (parseTs(b.start)?.getTime() ?? Infinity) || a.task_id.localeCompare(b.task_id)), [tasks]);
  const keyOf = (t: TaskRun) => `${t.task_id}:${t.map_index}:${t.try_number}`;
  return (
    <section className="surface overflow-hidden">
      <header className="flex items-center gap-2 border-b px-4 py-3">
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">Tasks in <span className="font-mono font-normal">{runId}</span></h2>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close run"><X className="h-4 w-4" /></Button>
      </header>
      {error && <div className="px-4 pt-3"><Alert>{error}</Alert></div>}
      {tasks === null ? <div className="px-4"><Pending text="Loading tasks" /></div> : !ordered.length ? (
        !error && <p className="px-4 py-8 text-center text-sm text-muted-foreground">No task instances recorded for this run.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
              <tr className="border-b">
                <th className="px-4 py-2 font-medium">Task</th>
                <th className="px-3 py-2 font-medium">Try</th>
                <th className="px-3 py-2 font-medium">State</th>
                <th className="px-3 py-2 font-medium">Operator</th>
                <th className="px-3 py-2 text-right font-medium">Duration</th>
                <th className="px-3 py-2 font-medium">Error</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y">
              {ordered.map((t) => {
                const key = keyOf(t);
                const open = log?.key === key;
                return (
                  <Fragment key={key}>
                    <tr className={cn(open && "bg-primary/5")}>
                      <td className="max-w-[16rem] px-4 py-2 font-mono text-xs" title={t.hostname ? `on ${t.hostname}` : undefined}>
                        <span className="block truncate">{t.task_id}{t.map_index >= 0 ? ` [${t.map_index}]` : ""}</span>
                      </td>
                      <td className="px-3 py-2 text-xs tabular-nums">{t.try_number}</td>
                      <td className="px-3 py-2"><StateBadge state={t.state} /></td>
                      <td className="px-3 py-2 text-xs text-muted-foreground">{t.operator ?? "-"}</td>
                      <td className="px-3 py-2 text-right text-xs tabular-nums">{duration(t.duration_s)}</td>
                      <td className="max-w-[24rem] px-3 py-2 text-xs text-rose-700">
                        {t.error_excerpt ? <span className="line-clamp-2 break-words font-mono text-[11px]" title={t.error_excerpt}>{t.error_excerpt}</span> : <span className="text-muted-foreground">-</span>}
                      </td>
                      <td className="px-4 py-2 text-right">
                        <Button variant={open ? "secondary" : "outline"} size="sm" onClick={() => setLog(open ? null : { task: t, key })} aria-expanded={open}>
                          <FileText className="h-3.5 w-3.5" />{open ? "Hide log" : "Log"}
                        </Button>
                      </td>
                    </tr>
                    {open && (
                      <tr><td colSpan={7} className="bg-muted/30 px-4 py-3">
                        <LogViewer envId={envId} dagId={dagId} runId={runId} task={t} />
                      </td></tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** Fetched on demand. Lines that look like errors are highlighted; the API has already redacted secrets. */
function LogViewer({ envId, dagId, runId, task }: { envId: string; dagId: string; runId: string; task: TaskRun }) {
  const [log, setLog] = useState<TaskLog | null>(null);
  const [error, setError] = useState("");
  const [onlyErrors, setOnlyErrors] = useState(false);
  const [reload, setReload] = useState(0);
  const seq = useSeq();
  useEffect(() => {
    const ticket = seq.next();
    setLog(null); setError("");
    taskLog(envId, dagId, runId, task.task_id, task.try_number, task.map_index).then((r) => {
      if (!seq.current(ticket)) return;
      if (r.ok) setLog(r.data); else setError(explain(r.error));
    }, (e: unknown) => { if (seq.current(ticket)) setError(e instanceof Error ? e.message : "Could not load the log."); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [envId, dagId, runId, task.task_id, task.try_number, task.map_index, reload]);
  const lines = useMemo(() => (log?.text ?? "").split(/\r?\n/), [log]);
  const hits = lines.filter((l) => ERROR_LINE.test(l)).length;
  if (error) {
    return <Alert>{error} <button type="button" className="ml-2 underline" onClick={() => setReload((n) => n + 1)}>Try again</button></Alert>;
  }
  if (!log) return <Pending text={`Fetching the log of ${task.task_id}, try ${task.try_number}`} />;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span className="font-mono">{task.task_id} · try {task.try_number}</span>
        {log.source && <span>· from {log.source}</span>}
        <span>· {hits} error line{hits === 1 ? "" : "s"}</span>
        <label className="ml-auto flex items-center gap-1.5">
          <input type="checkbox" checked={onlyErrors} onChange={(e) => setOnlyErrors(e.target.checked)} disabled={!hits} />Only error lines
        </label>
        <CopyButton text={log.text} label="Copy log" />
      </div>
      {(log.truncated || log.redacted || log.detail) && (
        <ul className="space-y-0.5 text-[11px] text-amber-800">
          {log.truncated && <li>The log is long, so only part of it is shown (the API caps it at 256 KB). Open it in Airflow for the full text.</li>}
          {log.redacted && <li>Secrets and personal data were masked before the log reached the browser.</li>}
          {log.detail && <li>{log.detail}</li>}
        </ul>
      )}
      {log.text ? (
        <pre className="max-h-[28rem] overflow-auto rounded-lg bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-200">
          {lines.map((line, i) => {
            const bad = ERROR_LINE.test(line);
            if (onlyErrors && !bad) return null;
            return (
              <div key={i} className={cn("flex gap-3 whitespace-pre-wrap break-all", bad && "bg-rose-500/20 text-rose-100")}>
                <span className="w-10 shrink-0 select-none text-right text-slate-500">{i + 1}</span><span className="min-w-0 flex-1">{line || " "}</span>
              </div>
            );
          })}
        </pre>
      ) : <p className="text-xs text-muted-foreground">The log is empty.</p>}
    </div>
  );
}

function SettingsForm({ dag, envId, domains, repos, onClose, onSaved }: {
  dag: DagDetail; envId: string; domains: Option[]; repos: Option[]; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const [criticality, setCriticality] = useState<string>(dag.criticality ?? "");
  const [cron, setCron] = useState(dag.expected_by_cron ?? "");
  const [maxMin, setMaxMin] = useState(dag.max_duration_min === null || dag.max_duration_min === undefined ? "" : String(dag.max_duration_min));
  const [domainId, setDomainId] = useState(dag.domain_id ?? "");
  const [repoId, setRepoId] = useState(dag.repo_id ?? "");
  const [repoPath, setRepoPath] = useState(dag.repo_path ?? "");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const minutes = maxMin.trim() ? Number(maxMin) : null;
  const badMinutes = minutes !== null && (!Number.isInteger(minutes) || minutes < 1 || minutes > 10080);
  const save = () => start(async () => {
    setError("");
    const r = await saveDagSettings(envId, dag.dag_id, {
      criticality: (criticality || null) as Criticality | null, expected_by_cron: cron.trim() || null, max_duration_min: minutes,
      domain_id: domainId || null, repo_id: repoId || null, repo_path: repoPath.trim() || null,
    });
    if (r.ok) await onSaved(); else setError(explain(r.error));
  });
  // a saved id the lists no longer contain still shows, so saving never clears it silently
  const domainOptions = domainId && !domains.some((d) => d.id === domainId) ? [...domains, { id: domainId, name: domainId }] : domains;
  const repoOptions = repoId && !repos.some((r) => r.id === repoId) ? [...repos, { id: repoId, name: repoId }] : repos;
  return (
    <section className="surface space-y-3 p-4">
      <h2 className="text-sm font-semibold">DAG settings</h2>
      <div className="grid gap-3 md:grid-cols-3">
        <label className="space-y-1 text-xs font-medium">Criticality
          <Select value={criticality} onChange={(e) => setCriticality(e.target.value)} className="text-xs">
            <option value="">Not set</option>
            {CRITICALITY.map((c) => <option key={c} value={c}>{c.charAt(0) + c.slice(1).toLowerCase()}</option>)}
          </Select>
        </label>
        <label className="space-y-1 text-xs font-medium">Expected by (cron)
          <Input value={cron} onChange={(e) => setCron(e.target.value)} placeholder="0 7 * * * UTC" className="font-mono text-xs" />
          <span className="block font-normal text-muted-foreground">A successful run should exist by this time.</span>
        </label>
        <label className="space-y-1 text-xs font-medium">Max duration (minutes)
          <Input value={maxMin} onChange={(e) => setMaxMin(e.target.value)} inputMode="numeric" placeholder="e.g. 90" className="text-xs"
                 aria-invalid={badMinutes} />
          {badMinutes && <span role="alert" className="block font-normal text-destructive">A whole number from 1 to 10080.</span>}
        </label>
        <label className="space-y-1 text-xs font-medium">Domain
          <Select value={domainId} onChange={(e) => setDomainId(e.target.value)} className="text-xs">
            <option value="">None</option>
            {domainOptions.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </Select>
        </label>
        <label className="space-y-1 text-xs font-medium">Repository
          <Select value={repoId} onChange={(e) => setRepoId(e.target.value)} className="text-xs">
            <option value="">None</option>
            {repoOptions.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </Select>
        </label>
        <label className="space-y-1 text-xs font-medium">Path in the repository
          <Input value={repoPath} onChange={(e) => setRepoPath(e.target.value)} placeholder="dags/orders_daily.py" className="font-mono text-xs" />
        </label>
      </div>
      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose} disabled={pending}>Cancel</Button>
        <Button onClick={save} disabled={pending || badMinutes}>{pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}Save settings</Button>
      </div>
    </section>
  );
}
