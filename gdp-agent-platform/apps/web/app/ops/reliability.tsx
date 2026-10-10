"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { Select } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { Alert, useSeq } from "../qa/qa-shared";
import { reliability, type Reliability } from "./actions";
import { dagHref } from "./ops-board";
import { explain, pct, pctTone, When } from "./ops-shared";

const PERIODS = [7, 30];
type Option = { id: string; name: string };

/** Minutes as "45 min", "3 h 10 min" or "2 d 4 h". */
function minutes(m: number | null | undefined): string {
  if (m === null || m === undefined || !Number.isFinite(m)) return "-";
  const v = Math.max(0, Math.round(m));
  if (v < 60) return `${v} min`;
  if (v < 1440) return `${Math.floor(v / 60)} h${v % 60 ? ` ${v % 60} min` : ""}`;
  const h = Math.round((v % 1440) / 60);
  return `${Math.floor(v / 1440)} d${h ? ` ${h} h` : ""}`;
}

const usd = (v: number | null | undefined) => (v === null || v === undefined || !Number.isFinite(v) ? "-" : `$${v.toFixed(v < 10 ? 2 : 0)}`);

/** Reliability per team over 7 or 30 days: incidents, MTTR, MTTA, repeats, the DAGs that fail most and AI spend. */
export function ReliabilityView({ initialDays, initialTeam, initial, initialError, teams }: {
  initialDays: number; initialTeam: string; initial: Reliability | null; initialError: string | null; teams: Option[];
}) {
  const [days, setDays] = useState(initialDays);
  const [team, setTeam] = useState(initialTeam);
  const [data, setData] = useState(initial);
  const [error, setError] = useState(initialError ?? "");
  const [loading, setLoading] = useState(false);
  const seq = useSeq();
  const first = useRef(true);

  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    p.set("view", "reliability");
    p.set("days", String(days));
    if (team) p.set("team", team); else p.delete("team");
    window.history.replaceState(window.history.state, "", `${window.location.pathname}?${p}`);
    if (first.current) { first.current = false; return; }
    const ticket = seq.next();
    setLoading(true);
    reliability(days, team).then((r) => {
      if (!seq.current(ticket)) return;
      setLoading(false);
      if (r.ok) { setData(r.data); setError(""); } else setError(explain(r.error));
    }, (e: unknown) => {
      if (!seq.current(ticket)) return;
      setLoading(false);
      setError(e instanceof Error ? e.message : "Could not load the reliability report.");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days, team]);

  const teamName = (id: string | null, name: string | null) => name || teams.find((t) => t.id === id)?.name || (id ? id : "Unrouted");
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg border p-0.5 text-xs" role="group" aria-label="Period">
          {PERIODS.map((d) => (
            <button key={d} type="button" onClick={() => setDays(d)} aria-pressed={days === d}
                    className={cn("rounded-md px-2.5 py-1", days === d ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}>
              {d} days</button>
          ))}
        </div>
        <Select value={team} onChange={(e) => setTeam(e.target.value)} aria-label="Team" className="w-48 text-xs">
          <option value="">All teams</option>
          {[...teams, ...(team && !teams.some((t) => t.id === team) ? [{ id: team, name: team }] : [])]
            .map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </Select>
        {loading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-label="Loading" />}
        {data?.period && (
          <span className="ml-auto text-xs text-muted-foreground">
            <When iso={data.period.from} /> to <When iso={data.period.to} />
          </span>
        )}
      </div>
      {error && <Alert onDismiss={() => setError("")}>{/not found|404/i.test(error) ? "The reliability report needs the latest API deploy." : error}</Alert>}

      {data && (
        <>
          <section className="surface overflow-hidden">
            <header className="border-b px-4 py-3"><h2 className="text-sm font-semibold">By team</h2></header>
            {data.teams.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                    <tr className="border-b">
                      <th className="px-4 py-2 font-medium">Team</th>
                      <th className="px-3 py-2 text-right font-medium">Incidents</th>
                      <th className="px-3 py-2 text-right font-medium">P1</th>
                      <th className="px-3 py-2 text-right font-medium" title="Mean time to resolve">MTTR</th>
                      <th className="px-3 py-2 text-right font-medium" title="Mean time to acknowledge">MTTA</th>
                      <th className="px-4 py-2 text-right font-medium" title="Incidents whose fingerprint happened before in the period">Repeats</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {data.teams.map((t) => (
                      <tr key={t.team_id ?? "unrouted"}>
                        <td className="px-4 py-2 text-xs">
                          {t.team_id
                            ? <Link href={`/incidents?${new URLSearchParams({ team: t.team_id })}`} className="text-primary hover:underline">{teamName(t.team_id, t.name)}</Link>
                            : <span className="text-amber-700">{teamName(t.team_id, t.name)}</span>}
                        </td>
                        <td className="px-3 py-2 text-right text-xs tabular-nums">{t.incidents}</td>
                        <td className={cn("px-3 py-2 text-right text-xs tabular-nums", t.p1 ? "font-semibold text-rose-700" : "text-muted-foreground")}>{t.p1}</td>
                        <td className="px-3 py-2 text-right text-xs tabular-nums">{minutes(t.mttr_min)}</td>
                        <td className="px-3 py-2 text-right text-xs tabular-nums">{minutes(t.mtta_min)}</td>
                        <td className={cn("px-4 py-2 text-right text-xs tabular-nums", t.repeats ? "text-amber-700" : "text-muted-foreground")}>{t.repeats}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="px-4 py-8 text-center text-sm text-muted-foreground">No incidents in this period.</p>}
          </section>

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="surface overflow-hidden">
              <header className="border-b px-4 py-3"><h2 className="text-sm font-semibold">Top failing DAGs</h2></header>
              {data.top_dags.length ? (
                <ul className="divide-y text-xs">
                  {data.top_dags.map((d) => (
                    <li key={`${d.env_id}:${d.dag_id}`} className="flex items-center gap-3 px-4 py-2">
                      <Link href={dagHref(d.env_id, d.dag_id)} className="min-w-0 flex-1 truncate font-mono text-primary hover:underline" title={d.dag_id}>{d.dag_id}</Link>
                      <span className="shrink-0 tabular-nums text-rose-700">{d.failures} failure{d.failures === 1 ? "" : "s"}</span>
                      <span className={cn("w-12 shrink-0 text-right tabular-nums", pctTone(d.success_rate))} title="Success rate">{pct(d.success_rate)}</span>
                    </li>
                  ))}
                </ul>
              ) : <p className="px-4 py-8 text-center text-sm text-muted-foreground">No failed runs in this period.</p>}
            </section>

            <section className="surface overflow-hidden">
              <header className="border-b px-4 py-3"><h2 className="text-sm font-semibold">Repeat failures</h2></header>
              {data.repeats.length ? (
                <ul className="divide-y text-xs">
                  {data.repeats.map((r) => (
                    <li key={r.fingerprint} className="flex items-center gap-3 px-4 py-2">
                      <Link href={`/incidents?${new URLSearchParams({ q: r.title })}`} className="min-w-0 flex-1 truncate text-primary hover:underline" title={r.title}>{r.title}</Link>
                      <span className="shrink-0 tabular-nums text-amber-700">{r.count}×</span>
                    </li>
                  ))}
                </ul>
              ) : <p className="px-4 py-8 text-center text-sm text-muted-foreground">No failure repeated in this period.</p>}
            </section>
          </div>

          <section className="surface flex flex-wrap items-center gap-x-6 gap-y-1 px-4 py-3 text-xs">
            <h2 className="text-sm font-semibold">AI diagnoses</h2>
            <span><span className="text-muted-foreground">Runs </span><span className="tabular-nums">{data.ai?.diagnoses ?? 0}</span></span>
            <span><span className="text-muted-foreground">Cost </span><span className="tabular-nums">{usd(data.ai?.cost_usd)}</span></span>
          </section>
        </>
      )}
    </div>
  );
}
