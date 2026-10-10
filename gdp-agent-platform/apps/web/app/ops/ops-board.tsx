"use client";

import Link from "next/link";
import { useEffect, useRef, useState, useTransition } from "react";
import { Activity, AlertTriangle, Clock3, Loader2, PauseCircle, PlayCircle, RefreshCw, Search, Workflow } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { Alert, Empty, Notice, useSeq } from "../qa/qa-shared";
import { listDags, opsSummary, pollEnv, type AirflowEnv, type DagRow, type OpsSummary } from "./actions";
import { duration, explain, pct, pctTone, StateBadge, When } from "./ops-shared";

export type OpsNav = { env: string; q: string; state: string; owner: string; team: string };

const STATES = ["failed", "running", "success", "queued", "up_for_retry", "upstream_failed"];

export const dagHref = (envId: string, dagId: string) => `/ops/dag?${new URLSearchParams({ env: envId, dag: dagId })}`;

function Card({ label, value, hint, icon: Icon, tone }: {
  label: string; value: React.ReactNode; hint?: React.ReactNode; icon: typeof Activity; tone: string;
}) {
  return (
    <div className="surface flex items-start gap-3 p-4">
      <span className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-lg ring-1 ring-inset", tone)}><Icon className="h-4 w-4" /></span>
      <div className="min-w-0">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="text-xl font-semibold tabular-nums">{value}</p>
        {hint && <p className="truncate text-[11px] text-muted-foreground">{hint}</p>}
      </div>
    </div>
  );
}

/** Pipelines: pick an Airflow environment, see its DAGs' health and filter them. Filters live in the URL. */
export function OpsBoard({ envs, summary: initialSummary, summaryError, initial, initialDags, initialError, access }: {
  envs: AirflowEnv[]; summary: OpsSummary | null; summaryError: string | null; initial: OpsNav;
  initialDags: DagRow[] | null; initialError: string | null; access: { canOperate: boolean; canManage: boolean };
}) {
  const [nav, setNav] = useState<OpsNav>(initial);
  const [dags, setDags] = useState<DagRow[] | null>(initialDags);
  const [error, setError] = useState(initialError ?? "");
  const [loading, setLoading] = useState(false);
  const [summary, setSummary] = useState(initialSummary);
  const [notice, setNotice] = useState("");
  const [polling, startPoll] = useTransition();
  const [reload, setReload] = useState(0);
  const seq = useSeq();
  const first = useRef(true);
  // owners and teams seen so far, so filtering by one keeps the others choosable
  const [seen, setSeen] = useState(() => collect(initialDags ?? [], { owners: new Set<string>(), teams: new Set<string>() }));

  const go = (patch: Partial<OpsNav>) => setNav((n) => {
    const next = { ...n, ...patch };
    const p = new URLSearchParams(window.location.search);
    for (const [k, v] of Object.entries(next)) { if (v) p.set(k, v); else p.delete(k); }
    window.history.replaceState(window.history.state, "", `${window.location.pathname}?${p}`);
    return next;
  });

  useEffect(() => {
    if (first.current) { first.current = false; return; }
    if (!nav.env) return;
    const ticket = seq.next();
    setLoading(true);
    const timer = setTimeout(() => {
      listDags({ env_id: nav.env, q: nav.q.trim(), state: nav.state, owner: nav.owner, team_id: nav.team }).then((r) => {
        if (!seq.current(ticket)) return;
        setLoading(false);
        if (r.ok) { setDags(r.data.dags); setError(""); setSeen((s) => collect(r.data.dags, s)); }
        else setError(explain(r.error));
      }, (e: unknown) => {
        if (!seq.current(ticket)) return;
        setLoading(false);
        setError(e instanceof Error ? e.message : "Could not load the DAGs.");
      });
    }, nav.q ? 300 : 0);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nav.env, nav.q, nav.state, nav.owner, nav.team, reload]);

  const env = envs.find((e) => e.env_id === nav.env) ?? null;
  const poll = () => startPoll(async () => {
    if (!env) return;
    setNotice(""); setError("");
    const r = await pollEnv(env.env_id);
    if (!r.ok) { setError(explain(r.error)); return; }
    setNotice(r.data.detail || (r.data.started ? `Polling ${env.name} now. New runs show up in a minute.` : `A poll of ${env.name} is already running.`));
    const s = await opsSummary();
    if (s.ok) setSummary(s.data);
    setReload((n) => n + 1);
  });

  if (!envs.length) {
    return (
      <Empty icon={<Workflow className="h-6 w-6" />} title="No Airflow environment connected"
             text="Connect Amazon MWAA in Admin, Integrations, Airflow. DAGs, runs and task logs then show up here."
             action={access.canManage
               ? <Link href="/admin?section=integrations&view=airflow" className="text-sm text-primary hover:underline">Open Airflow setup</Link>
               : <span className="text-xs text-muted-foreground">Ask an admin with the INTEGRATION.MANAGE privilege to connect it.</span>} />
    );
  }

  const owners = Array.from(seen.owners).sort();
  const teams = Array.from(seen.teams).sort();
  const filtered = !!(nav.q || nav.state || nav.owner || nav.team);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Card label="DAGs" value={summary?.dags ?? "-"} icon={Workflow} tone="bg-indigo-50 text-indigo-600 ring-indigo-100"
              hint={summary ? `${summary.envs} environment${summary.envs === 1 ? "" : "s"}` : undefined} />
        <Card label="Failing in 24 h" value={summary?.failing_24h ?? "-"} icon={AlertTriangle}
              tone={summary?.failing_24h ? "bg-rose-50 text-rose-600 ring-rose-100" : "bg-emerald-50 text-emerald-600 ring-emerald-100"} />
        <Card label="Running" value={summary?.running ?? "-"} icon={PlayCircle} tone="bg-sky-50 text-sky-600 ring-sky-100" />
        <Card label="Last poll" value={<span className="text-base"><When iso={summary?.last_poll_at} rel /></span>} icon={Clock3}
              tone="bg-slate-100 text-slate-600 ring-slate-200" hint={env?.last_error ? "Last poll reported an error" : undefined} />
      </div>
      {summaryError && <Alert>The summary did not load: {explain(summaryError)}</Alert>}

      <section className="surface overflow-hidden">
        <header className="flex flex-wrap items-center gap-2 border-b px-4 py-3">
          <Select value={nav.env} onChange={(e) => go({ env: e.target.value, owner: "", team: "" })} aria-label="Airflow environment" className="w-56">
            {envs.map((e) => <option key={e.env_id} value={e.env_id}>{e.name}{e.enabled ? "" : " (disabled)"}</option>)}
          </Select>
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input value={nav.q} onChange={(e) => go({ q: e.target.value })} placeholder="Search DAGs" className="w-56 pl-8 text-xs" aria-label="Search DAGs" />
          </div>
          <Select value={nav.state} onChange={(e) => go({ state: e.target.value })} aria-label="Last state" className="w-40 text-xs">
            <option value="">Any state</option>
            {STATES.map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
          </Select>
          <Select value={nav.owner} onChange={(e) => go({ owner: e.target.value })} aria-label="Owner" className="w-40 text-xs">
            <option value="">Any owner</option>
            {owners.map((o) => <option key={o} value={o}>{o}</option>)}
          </Select>
          {(teams.length > 0 || nav.team) && (
            <Select value={nav.team} onChange={(e) => go({ team: e.target.value })} aria-label="Team" className="w-40 text-xs">
              <option value="">Any team</option>
              {[...new Set([...teams, ...(nav.team ? [nav.team] : [])])].map((t) => <option key={t} value={t}>{t}</option>)}
            </Select>
          )}
          {filtered && <Button variant="ghost" size="sm" onClick={() => go({ q: "", state: "", owner: "", team: "" })}>Clear</Button>}
          {loading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-label="Loading" />}
          <span className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
            {env && <>Polled <When iso={env.last_poll_at} rel /></>}
            {access.canOperate && env && (
              <Button variant="outline" size="sm" disabled={polling || !env.enabled} onClick={poll} title={env.enabled ? undefined : "The environment is disabled"}>
                {polling ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}Poll now
              </Button>
            )}
          </span>
        </header>
        <div className="space-y-2 px-4 pt-3 empty:hidden">
          {env?.last_error && <Alert>Last poll of {env.name} failed: {explain(env.last_error)}</Alert>}
          {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
          {notice && <Notice onDismiss={() => setNotice("")}>{notice}</Notice>}
        </div>
        <DagTable dags={dags} filtered={filtered} />
      </section>
    </div>
  );
}

function collect(dags: DagRow[], prev: { owners: Set<string>; teams: Set<string> }) {
  const owners = new Set(prev.owners);
  const teams = new Set(prev.teams);
  for (const d of dags) {
    for (const o of d.owners ?? []) if (o) owners.add(o);
    if (d.team_id) teams.add(d.team_id);
  }
  return { owners, teams };
}

function DagTable({ dags, filtered }: { dags: DagRow[] | null; filtered: boolean }) {
  const rows = dags ?? [];
  if (dags === null) return <p className="px-4 py-10 text-center text-sm text-muted-foreground">DAGs did not load.</p>;
  if (!rows.length) {
    return (
      <p className="px-4 py-10 text-center text-sm text-muted-foreground">
        {filtered ? "No DAG matches these filters." : "No DAGs yet. They appear after the first poll of this environment."}
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
          <tr className="border-b">
            <th className="px-4 py-2 font-medium">DAG</th>
            <th className="px-3 py-2 font-medium">Owners</th>
            <th className="px-3 py-2 font-medium">Schedule</th>
            <th className="px-3 py-2 font-medium">Last run</th>
            <th className="px-3 py-2 text-right font-medium" title="Successful runs over 7 and 30 days">Success 7 d / 30 d</th>
            <th className="px-3 py-2 text-right font-medium" title="Median and 95th percentile run duration">p50 / p95</th>
            <th className="px-4 py-2 text-right font-medium">Runs 7 d</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((d) => (
            <tr key={`${d.env_id}:${d.dag_id}`} className="hover:bg-muted/40">
              <td className="max-w-[22rem] px-4 py-2">
                <Link href={dagHref(d.env_id, d.dag_id)} className="block truncate font-mono text-xs font-medium text-primary hover:underline" title={d.dag_id}>{d.dag_id}</Link>
                <span className="mt-0.5 flex flex-wrap items-center gap-1">
                  {d.is_paused && <span className="inline-flex items-center gap-0.5 rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600"><PauseCircle className="h-3 w-3" />paused</span>}
                  {!d.is_active && <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">inactive</span>}
                  {d.criticality && <span className="rounded-full bg-violet-50 px-1.5 py-0.5 text-[10px] text-violet-700">{d.criticality.toLowerCase()}</span>}
                  {d.open_incidents > 0 && (
                    <Link href={`/incidents?${new URLSearchParams({ dag: d.dag_id, env: d.env_id, status: "OPEN,ACK" })}`}
                          className="rounded-full bg-rose-50 px-1.5 py-0.5 text-[10px] font-semibold text-rose-700 ring-1 ring-inset ring-rose-100 hover:bg-rose-100">
                      {d.open_incidents} open incident{d.open_incidents === 1 ? "" : "s"}</Link>
                  )}
                  {d.tags?.slice(0, 3).map((t) => <span key={t} className="rounded bg-muted px-1 text-[10px] text-muted-foreground">{t}</span>)}
                </span>
              </td>
              <td className="max-w-[10rem] truncate px-3 py-2 text-xs" title={d.owners?.join(", ")}>{d.owners?.join(", ") || "-"}</td>
              <td className="px-3 py-2 font-mono text-[11px] text-muted-foreground">{d.schedule || "none"}</td>
              <td className="whitespace-nowrap px-3 py-2 text-xs">
                <span className="flex items-center gap-1.5"><StateBadge state={d.last_state} />
                  {d.last_run_at && <span className="text-muted-foreground"><When iso={d.last_run_at} rel /></span>}</span>
              </td>
              <td className="whitespace-nowrap px-3 py-2 text-right text-xs tabular-nums">
                <span className={pctTone(d.success_7d)}>{pct(d.success_7d)}</span><span className="text-muted-foreground"> / </span>
                <span className={pctTone(d.success_30d)}>{pct(d.success_30d)}</span>
              </td>
              <td className="whitespace-nowrap px-3 py-2 text-right text-xs tabular-nums">{duration(d.p50_s)} / {duration(d.p95_s)}</td>
              <td className="px-4 py-2 text-right text-xs tabular-nums">{d.runs_7d}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
