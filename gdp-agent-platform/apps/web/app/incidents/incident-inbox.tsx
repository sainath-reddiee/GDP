"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import { CheckCheck, CircleCheck, Loader2, Search, Siren, UserPlus, Workflow, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { Alert, Empty, Notice, useSeq } from "../qa/qa-shared";
import type { AirflowEnv } from "../ops/actions";
import { When } from "../ops/ops-shared";
import {
  bulkIncidents, incidentSummary, listIncidents,
  type BulkAction, type BulkResult, type Incident, type IncidentSummary, type OpsTeam,
} from "./actions";
import { DEFAULT_STATUS, navParams, toFilters, type InboxNav } from "./inbox-nav";
import { dagRunHref, incidentHref, IncidentStatusPill, KindPill, SEVERITIES, SeverityPill, statusLabel, STATUSES } from "./incident-shared";

const toneOf = (e: string): "info" | "error" => (/approval|request/i.test(e) ? "info" : "error");
const sameStatus = (a: string[], b: string[]) => a.length === b.length && a.every((s) => b.includes(s));

/** The incidents inbox: summary chips, URL synced filters, a list and bulk acknowledge, assign and resolve. */
export function IncidentInbox({
  initial, initialRows, initialTotal, initialError, summary: initialSummary, summaryError, envs, envsError, teams, teamsError,
  canOperate, canManage, pageSize,
}: {
  initial: InboxNav; initialRows: Incident[] | null; initialTotal: number; initialError: string | null;
  summary: IncidentSummary | null; summaryError: string | null; envs: AirflowEnv[] | null; envsError: string | null;
  teams: OpsTeam[]; teamsError: string | null; canOperate: boolean; canManage: boolean; pageSize: number;
}) {
  const [nav, setNav] = useState<InboxNav>(initial);
  const [rows, setRows] = useState<Incident[] | null>(initialRows);
  const [total, setTotal] = useState(initialTotal);
  const [error, setError] = useState(initialError ?? "");
  const [notice, setNotice] = useState<{ tone: "ok" | "info"; text: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [summary, setSummary] = useState(initialSummary);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [results, setResults] = useState<Record<string, BulkResult>>({});
  const [reload, setReload] = useState(0);
  const seq = useSeq();
  const summarySeq = useSeq();
  const first = useRef(true);

  const go = (patch: Partial<InboxNav>) => setNav((n) => {
    const next = { ...n, ...patch };
    const p = new URLSearchParams(window.location.search);
    for (const [k, v] of Object.entries(navParams(next))) { if (v) p.set(k, v); else p.delete(k); }
    window.history.replaceState(window.history.state, "", `${window.location.pathname}?${p}`);
    return next;
  });

  const fetchPage = (offset: number, ticket: number) => listIncidents({ ...toFilters(nav), limit: pageSize, offset }).then((r) => {
    if (!seq.current(ticket)) return;
    setLoading(false);
    if (!r.ok) { setError(r.error); return; }
    setError("");
    setTotal(r.data.total);
    setRows((prev) => offset && prev ? [...prev, ...r.data.incidents.filter((i) => !prev.some((p) => p.incident_id === i.incident_id))] : r.data.incidents);
  }, (e: unknown) => {
    if (!seq.current(ticket)) return;
    setLoading(false);
    setError(e instanceof Error ? e.message : "Could not load the incidents.");
  });

  const refreshSummary = () => {
    const ticket = summarySeq.next();
    incidentSummary().then((r) => { if (summarySeq.current(ticket) && r.ok) setSummary(r.data); }, () => undefined);
  };

  useEffect(() => {
    if (first.current) { first.current = false; return; }
    const ticket = seq.next();
    setLoading(true);
    setSelected(new Set());
    const timer = setTimeout(() => { void fetchPage(0, ticket); }, nav.q ? 300 : 0);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nav.status.join(","), nav.severity, nav.team, nav.env, nav.mine, nav.q, nav.dag, reload]);

  const more = () => {
    const ticket = seq.next();
    setLoading(true);
    void fetchPage(rows?.length ?? 0, ticket);
  };

  // a DAG filter travels as search text, so keep only the exact DAG
  const shown = useMemo(() => (rows ?? []).filter((i) => !nav.dag || i.dag_id === nav.dag), [rows, nav.dag]);
  const envName = useMemo(() => new Map((envs ?? []).map((e) => [e.env_id, e.name])), [envs]);

  if (envs !== null && envs.length === 0 && !rows?.length) {
    return (
      <Empty icon={<Workflow className="h-6 w-6" />} title="No Airflow environment connected"
             text="Incidents are raised from Airflow runs. Connect Amazon MWAA in Admin, Integrations, Airflow first."
             action={canManage
               ? <Link href="/admin?section=integrations&view=airflow" className="text-sm text-primary hover:underline">Open Airflow setup</Link>
               : <span className="text-xs text-muted-foreground">Ask an admin with the INTEGRATION.MANAGE privilege to connect it.</span>} />
    );
  }

  const filtered = !sameStatus(nav.status, DEFAULT_STATUS) || !!(nav.severity || nav.team || nav.env || nav.mine || nav.q || nav.dag);
  const toggleStatus = (s: string) => go({ status: nav.status.includes(s) ? nav.status.filter((x) => x !== s) : [...nav.status, s] });
  const chips: { label: string; value: number | undefined; tone: string; active: boolean; apply?: Partial<InboxNav> }[] = [
    { label: "Open", value: summary?.open, tone: "text-rose-700", active: sameStatus(nav.status, ["OPEN"]) && !nav.severity && !nav.mine,
      apply: { status: ["OPEN"], severity: "", mine: false } },
    { label: "Acknowledged", value: summary?.ack, tone: "text-amber-700", active: sameStatus(nav.status, ["ACK"]) && !nav.severity && !nav.mine,
      apply: { status: ["ACK"], severity: "", mine: false } },
    { label: "P1 open", value: summary?.p1_open, tone: "text-rose-700", active: sameStatus(nav.status, ["OPEN"]) && nav.severity === "P1",
      apply: { status: ["OPEN"], severity: "P1", mine: false } },
    { label: "Mine", value: summary?.mine_open, tone: "text-indigo-700", active: nav.mine, apply: { mine: !nav.mine } },
    { label: "Unrouted", value: summary?.unrouted, tone: summary?.unrouted ? "text-amber-700" : "text-muted-foreground", active: false },
  ];

  const allSelected = shown.length > 0 && shown.every((i) => selected.has(i.incident_id));
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(shown.map((i) => i.incident_id)));
  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const onBulkDone = (r: BulkResult[], action: BulkAction["action"]) => {
    const map: Record<string, BulkResult> = {};
    for (const x of r) map[x.incident_id] = x;
    setResults(map);
    const ok = r.filter((x) => x.ok).length;
    const failed = r.length - ok;
    const verb = action === "ack" ? "acknowledged" : action === "assign" ? "assigned" : "resolved";
    setNotice({ tone: failed ? "info" : "ok", text: `${ok} ${verb}${failed ? `, ${failed} not changed (see the rows)` : ""}.` });
    setSelected(new Set(r.filter((x) => !x.ok).map((x) => x.incident_id)));
    setReload((n) => n + 1);
    refreshSummary();
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2" aria-label="Summary">
        {chips.map((c) => {
          const body = (
            <>
              <span className={cn("text-lg font-semibold tabular-nums", c.tone)}>{c.value ?? "-"}</span>
              <span className="text-xs text-muted-foreground">{c.label}</span>
            </>
          );
          return c.apply ? (
            <button key={c.label} type="button" onClick={() => go(c.apply!)} aria-pressed={c.active}
                    className={cn("surface flex items-baseline gap-2 px-3 py-2 transition hover:border-primary/30",
                      c.active && "border-primary/40 bg-primary/5")}>{body}</button>
          ) : <div key={c.label} className="surface flex items-baseline gap-2 px-3 py-2" title="Incidents with no team: add a routing rule in Admin">{body}</div>;
        })}
      </div>
      {summaryError && <Alert>The summary did not load: {summaryError}</Alert>}
      {envsError && <Alert>Airflow environments did not load: {envsError}</Alert>}
      {teamsError && <Alert>Teams did not load: {teamsError}</Alert>}

      <section className="surface overflow-hidden">
        <header className="space-y-2 border-b px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input value={nav.q} onChange={(e) => go({ q: e.target.value })} placeholder="Search title, DAG, task" className="w-60 pl-8 text-xs" aria-label="Search incidents" />
            </div>
            <Select value={nav.severity} onChange={(e) => go({ severity: e.target.value })} aria-label="Severity" className="w-32 text-xs">
              <option value="">Any severity</option>
              {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
            </Select>
            <Select value={nav.team} onChange={(e) => go({ team: e.target.value })} aria-label="Team" className="w-40 text-xs">
              <option value="">Any team</option>
              {teams.map((t) => <option key={t.team_id} value={t.team_id}>{t.name}</option>)}
              {nav.team && !teams.some((t) => t.team_id === nav.team) && <option value={nav.team}>{nav.team}</option>}
            </Select>
            {(envs?.length ?? 0) > 1 || nav.env ? (
              <Select value={nav.env} onChange={(e) => go({ env: e.target.value })} aria-label="Environment" className="w-40 text-xs">
                <option value="">Any environment</option>
                {(envs ?? []).map((e) => <option key={e.env_id} value={e.env_id}>{e.name}</option>)}
                {nav.env && !(envs ?? []).some((e) => e.env_id === nav.env) && <option value={nav.env}>{nav.env}</option>}
              </Select>
            ) : null}
            <label className="flex items-center gap-1.5 text-xs">
              <input type="checkbox" checked={nav.mine} onChange={(e) => go({ mine: e.target.checked })} />Mine
            </label>
            {filtered && (
              <Button variant="ghost" size="sm" onClick={() => go({ status: DEFAULT_STATUS, severity: "", team: "", env: "", mine: false, q: "", dag: "" })}>Reset</Button>
            )}
            {loading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-label="Loading" />}
          </div>
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Status">
            <span className="mr-1 text-[11px] text-muted-foreground">Status</span>
            {STATUSES.map((s) => (
              <button key={s} type="button" onClick={() => toggleStatus(s)} aria-pressed={nav.status.includes(s)}
                      className={cn("rounded-full border px-2 py-0.5 text-[11px] transition",
                        nav.status.includes(s) ? "border-primary/40 bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:bg-muted")}>
                {statusLabel(s)}
              </button>
            ))}
            {!nav.status.length && <span className="text-[11px] text-muted-foreground">(any)</span>}
            {nav.dag && (
              <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[11px]">
                DAG <span className="font-mono">{nav.dag}</span>
                <button type="button" aria-label="Clear the DAG filter" onClick={() => go({ dag: "" })}><X className="h-3 w-3" /></button>
              </span>
            )}
          </div>
        </header>
        <div className="space-y-2 px-4 pt-3 empty:hidden">
          {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
          {notice && (notice.tone === "ok"
            ? <Notice onDismiss={() => setNotice(null)}>{notice.text}</Notice>
            : <p role="status" className="flex items-start gap-2 rounded-lg bg-sky-50 px-3 py-2 text-xs text-sky-800">
                <span className="flex-1">{notice.text}</span>
                <button type="button" aria-label="Dismiss" onClick={() => setNotice(null)}><X className="h-3.5 w-3.5" /></button>
              </p>)}
        </div>
        {canOperate && selected.size > 0 && (
          <BulkBar ids={Array.from(selected)} onDone={onBulkDone} onClear={() => setSelected(new Set())}
                   onError={(e) => { if (toneOf(e) === "info") setNotice({ tone: "info", text: e }); else { setNotice(null); setError(e); } }} />
        )}
        {rows === null ? (
          <p className="px-4 py-10 text-center text-sm text-muted-foreground">Incidents did not load.</p>
        ) : !shown.length ? (
          <div className="px-4 py-12 text-center">
            <Siren className="mx-auto h-6 w-6 text-muted-foreground" />
            <p className="mt-2 text-sm font-semibold">{filtered ? "No incident matches these filters" : "No open incidents"}</p>
            <p className="mt-1 text-xs text-muted-foreground">{filtered ? "Change or reset the filters." : "Failed, late and long running DAGs show up here as incidents."}</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                <tr className="border-b">
                  {canOperate && (
                    <th className="w-8 py-2 pl-4"><input type="checkbox" checked={allSelected} onChange={toggleAll} aria-label="Select all shown" /></th>
                  )}
                  <th className={cn("py-2 pr-3 font-medium", !canOperate && "pl-4")}>Incident</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Team</th>
                  <th className="px-3 py-2 font-medium">Assignee</th>
                  <th className="px-3 py-2 text-right font-medium">Seen</th>
                  <th className="px-3 py-2 font-medium">First / last</th>
                  <th className="px-4 py-2 font-medium">Jira</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {shown.map((i) => {
                  const res = results[i.incident_id];
                  return (
                    <tr key={i.incident_id} className={cn("align-top hover:bg-muted/40", selected.has(i.incident_id) && "bg-primary/5")}>
                      {canOperate && (
                        <td className="py-2.5 pl-4">
                          <input type="checkbox" checked={selected.has(i.incident_id)} onChange={() => toggle(i.incident_id)} aria-label={`Select ${i.title}`} />
                        </td>
                      )}
                      <td className={cn("max-w-[30rem] py-2 pr-3", !canOperate && "pl-4")}>
                        <div className="flex items-start gap-2">
                          <SeverityPill severity={i.severity} className="mt-0.5" />
                          <div className="min-w-0">
                            <Link href={incidentHref(i.incident_id)} className="line-clamp-2 font-medium text-primary hover:underline">{i.title || i.dag_id}</Link>
                            <p className="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground">
                              <span>{envName.get(i.env_id) ?? i.env_id}</span>
                              <span>·</span>
                              <Link href={dagRunHref(i.env_id, i.dag_id, i.run_id)} className="font-mono hover:underline">{i.dag_id}</Link>
                              {i.task_id && <><span>·</span><span className="font-mono">{i.task_id}</span></>}
                              <KindPill kind={i.kind} />
                              {i.children > 0 && <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600" title="Downstream failures grouped under this one">+{i.children} grouped</span>}
                              {i.parent_incident_id && <Link href={incidentHref(i.parent_incident_id)} className="text-[10px] hover:underline">child of another incident</Link>}
                            </p>
                            {res && (res.ok
                              ? <p role="status" className="mt-1 text-[11px] text-success">Done.</p>
                              : <p role="alert" className="mt-1 text-[11px] text-destructive">{res.error || "Not changed."}</p>)}
                          </div>
                        </div>
                      </td>
                      <td className="px-3 py-2.5"><IncidentStatusPill status={i.status} /></td>
                      <td className="px-3 py-2.5 text-xs">{i.team_name ?? <span className="text-amber-700">unrouted</span>}</td>
                      <td className="max-w-[10rem] truncate px-3 py-2.5 text-xs" title={i.assignee ?? undefined}>{i.assignee ?? <span className="text-muted-foreground">nobody</span>}</td>
                      <td className="px-3 py-2.5 text-right text-xs tabular-nums" title="Occurrences">{i.occurrences}×</td>
                      <td className="whitespace-nowrap px-3 py-2.5 text-[11px] text-muted-foreground">
                        <When iso={i.first_seen} rel empty="-" /><br /><When iso={i.last_seen} rel empty="-" />
                      </td>
                      <td className="px-4 py-2.5 text-xs">
                        {i.jira_key ? (i.jira_url
                          ? <a href={i.jira_url} target="_blank" rel="noreferrer" className="font-mono text-primary hover:underline">{i.jira_key}</a>
                          : <span className="font-mono">{i.jira_key}</span>)
                          : <span className="text-muted-foreground">{i.jira_state ? i.jira_state.toLowerCase().replace(/_/g, " ") : "-"}</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {rows !== null && (
          <footer className="flex items-center gap-2 border-t px-4 py-2 text-xs text-muted-foreground">
            <span>{shown.length} of {total} shown</span>
            {rows.length < total && (
              <Button variant="outline" size="sm" className="ml-auto" disabled={loading} onClick={more}>
                {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}Load more</Button>
            )}
          </footer>
        )}
      </section>
    </div>
  );
}

function BulkBar({ ids, onDone, onClear, onError }: {
  ids: string[]; onDone: (r: BulkResult[], action: BulkAction["action"]) => void; onClear: () => void; onError: (e: string) => void;
}) {
  const [mode, setMode] = useState<"assign" | "resolve" | null>(null);
  const [assignee, setAssignee] = useState("");
  const [resolution, setResolution] = useState("");
  const [busy, setBusy] = useState<BulkAction["action"] | null>(null);
  const [pending, start] = useTransition();
  const run = (body: BulkAction) => start(async () => {
    setBusy(body.action);
    try {
      const r = await bulkIncidents(body);
      if (!r.ok) { onError(r.error); return; }
      setMode(null); setAssignee(""); setResolution("");
      onDone(r.data.results, body.action);
    } finally {
      setBusy(null);
    }
  });
  const spin = (a: BulkAction["action"]) => busy === a ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null;
  return (
    <div className="mx-4 mt-3 space-y-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{ids.length} selected</span>
        <Button size="sm" variant="outline" disabled={pending} onClick={() => run({ ids, action: "ack" })}>
          {spin("ack") ?? <CheckCheck className="h-3.5 w-3.5" />}Acknowledge</Button>
        <Button size="sm" variant={mode === "assign" ? "secondary" : "outline"} disabled={pending} onClick={() => setMode(mode === "assign" ? null : "assign")}>
          <UserPlus className="h-3.5 w-3.5" />Assign</Button>
        <Button size="sm" variant={mode === "resolve" ? "secondary" : "outline"} disabled={pending} onClick={() => setMode(mode === "resolve" ? null : "resolve")}>
          <CircleCheck className="h-3.5 w-3.5" />Resolve</Button>
        <Button size="sm" variant="ghost" className="ml-auto" disabled={pending} onClick={onClear}>Clear selection</Button>
      </div>
      {mode === "assign" && (
        <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (assignee.trim()) run({ ids, action: "assign", assignee: assignee.trim() }); }}>
          <Input value={assignee} onChange={(e) => setAssignee(e.target.value)} placeholder="Assignee (user name or email)" className="w-64 text-xs" aria-label="Assignee" autoFocus />
          <Button size="sm" type="submit" disabled={pending || !assignee.trim()}>{spin("assign")}Assign {ids.length}</Button>
        </form>
      )}
      {mode === "resolve" && (
        <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (resolution.trim()) run({ ids, action: "resolve", resolution: resolution.trim() }); }}>
          <Input value={resolution} onChange={(e) => setResolution(e.target.value)} placeholder="What fixed it (saved on each incident)" className="min-w-[16rem] flex-1 text-xs" aria-label="Resolution" autoFocus />
          <Button size="sm" type="submit" disabled={pending || !resolution.trim()}>{spin("resolve")}Resolve {ids.length}</Button>
        </form>
      )}
    </div>
  );
}

