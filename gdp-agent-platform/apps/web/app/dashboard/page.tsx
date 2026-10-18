import Link from "next/link";
import {
  AlertTriangle, ArrowRight, BarChart3, CheckCircle2, ClipboardCheck, Clock, Eye, PlugZap, Siren, SquareTerminal, XCircle,
} from "lucide-react";
import { api, whoami } from "@/lib/api";
import { can, type AuditEvent, type RunSummary } from "@/lib/types";
import type { Overview } from "@/lib/store-types";
import { getDomainScope } from "@/lib/domain-scope";
import { ago } from "@/lib/run-insights";
import type { IncidentSummary } from "../incidents/actions";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/hub/kpi";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { fmt, Meter, PipelineTable, RibbonMetric, Sparkline } from "./command-center";
import { StoreCards } from "@/components/hub/store-cards";
import { CountUp } from "@/components/motion/count-up";
import { TicketsCard, type TicketsCardData } from "./tickets-card";
import { ValueTile } from "@/components/value/value-tile";
import type { ValueSummary } from "@/lib/value-types";

/** Below this many finished runs the ribbon shows "n of m finished succeeded" rather than a percentage. */
const FEW = 5;

/** Minutes as "45m", "3h 10m" or "2d 4h". */
function duration(minutes: number) {
  if (minutes < 60) return `${Math.max(0, Math.round(minutes))}m`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h${minutes % 60 ? ` ${Math.round(minutes % 60)}m` : ""}`;
  return `${Math.floor(minutes / 1440)}d${Math.floor((minutes % 1440) / 60) ? ` ${Math.floor((minutes % 1440) / 60)}h` : ""}`;
}

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

const pct = (part: number, whole: number) => (whole ? (100 * part) / whole : null);
const count = (n: number, word: string) => `${fmt(n)} ${word}${n === 1 ? "" : "s"}`;

/** The Command Center: what the platform holds (six asset stores), how healthy it is, and what is in flight. */
export default async function Dashboard() {
  const scope = getDomainScope();
  const me = await whoami();
  const overview = await api<Overview>(`/api/hub/overview${scope ? `?domain=${encodeURIComponent(scope)}` : ""}`).catch(() => null);
  const domainId = overview?.domain?.domain_id;
  const runQs = (extra: string) => `/api/runs?${extra}${domainId ? `&domain_id=${domainId}` : ""}`;
  // the table lists the 8 most recently updated runs in flight; every count comes from the overview (exact, not a
  // list length) so the greeting, the chips, the tiles and the table header agree
  const [active, audit, incidents, jira, value] = await Promise.all([
    api<{ runs: RunSummary[]; total?: number }>(runQs("status=running&limit=8&sort=updated")).catch(() => ({ runs: [] as RunSummary[], total: undefined })),
    api<{ events: AuditEvent[] }>("/api/audit?limit=6").catch(() => ({ events: [] as AuditEvent[] })),
    can(me, "OPS.VIEW") ? api<IncidentSummary>("/api/ops/incidents/summary").catch(() => null) : Promise.resolve(null),
    can(me, "JIRA.READ") || can(me, "CASE.WORK") ? ticketsCard() : Promise.resolve(null),
    can(me, "AUDIT.VIEW") ? api<ValueSummary>("/api/value/summary").catch(() => null) : Promise.resolve(null),
  ]);
  const o = overview;
  const user = (me?.user ?? "").split(/[._@ ]/)[0];
  const name = user ? user.charAt(0) + user.slice(1).toLowerCase() : "";
  const inFlight = active.runs;
  const inFlightCount = o?.runs?.in_flight ?? active.total ?? inFlight.length;
  const reviewCount = o?.runs?.needs_review ?? 0;
  const failedCount = o?.runs?.failed_7d ?? 0;
  const openIncidents = incidents ? incidents.open + incidents.ack : null;
  const dqFailing = o?.dq?.failed ?? 0;
  const attention = [
    { show: reviewCount > 0, href: "/runs?needs_review=1", icon: Eye, text: `${reviewCount} run${reviewCount === 1 ? "" : "s"} awaiting approval`, tone: "text-human bg-human/10 ring-human/30" },
    { show: failedCount > 0, href: "/runs?status=failed", icon: XCircle, text: `${failedCount} run${failedCount === 1 ? "" : "s"} failed this week`, tone: "text-amber-800 bg-amber-50 ring-amber-200" },
    { show: !!openIncidents, href: "/incidents", icon: Siren, text: `${openIncidents} open incident${openIncidents === 1 ? "" : "s"}`, tone: "text-rose-700 bg-rose-50 ring-rose-200" },
    { show: !!o?.qa?.pending, href: "/dashboard/qa-store", icon: SquareTerminal, text: `${o?.qa?.pending} QA pack${o?.qa?.pending === 1 ? "" : "s"} awaiting approval`, tone: "text-blue-700 bg-blue-50 ring-blue-200" },
    { show: dqFailing > 0, href: "/dashboard/dq-store", icon: ClipboardCheck, text: `${dqFailing} data quality check${dqFailing === 1 ? "" : "s"} failing`, tone: "text-rose-700 bg-rose-50 ring-rose-200" },
    { show: !!o?.profiling?.drift, href: "/dashboard/profiling", icon: BarChart3, text: `${o?.profiling?.drift} drifting column${o?.profiling?.drift === 1 ? "" : "s"}`, tone: "text-amber-800 bg-amber-50 ring-amber-200" },
  ].filter((a) => a.show);
  const d = o?.delivery;
  // with only a few finished runs a percentage misleads (one failure reads "0% succeeded"): show the count instead
  const finished = d && d.completed + d.failed > 0 ? d.completed + d.failed : null;
  const w = o?.waiting;
  const f = o?.freshness;
  const x = o?.failing;

  return (
    <div className="space-y-6">
      <PageHeader eyebrow={o?.domain ? `${o.domain.domain_name} domain` : "All domains"}
                  title={`${greeting()}${name ? `, ${name}` : ""}`}
                  description={`${inFlightCount} run${inFlightCount === 1 ? "" : "s"} in flight${reviewCount ? `, ${reviewCount} at a review step` : ", none at a review step"}. Your data estate at a glance.`}
                  actions={<>
                    <Link href="/runs" className={buttonVariants({ variant: "outline" })}>All runs</Link>
                    <Link href="/dashboard/onboarding?type=source" className={buttonVariants()}><PlugZap className="h-4 w-4" /> Onboard source</Link>
                  </>} />

      {/* hero ribbon */}
      <section className="glass aurora-border relative overflow-hidden p-1" data-testid="hero-ribbon">
        <div className="pointer-events-none absolute inset-0 bg-accent" />
        <div className="relative grid divide-y divide-border/60 sm:grid-cols-2 sm:divide-y-0 xl:grid-cols-4 xl:divide-x">
          <RibbonMetric label="Runs, last 7 days" value={<CountUp value={finished == null ? null : finished < FEW ? d!.completed : Math.round(d!.success_rate ?? 0)} />}
                        unit={finished == null ? undefined : finished < FEW ? `of ${finished} finished succeeded` : "% succeeded"}
                        href="/runs" hint={d ? `${fmt(d.completed)} completed · ${fmt(d.failed)} failed · ${fmt(inFlightCount)} in flight now` : "run history not available"}>
            <Sparkline values={(d?.daily ?? []).map((x) => x.completed + x.failed)} />
            <p className="mt-1.5 text-[11px] text-muted-foreground">
              {d?.lead_minutes == null ? "No run completed this week" : `Median lead time ${duration(d.lead_minutes)}, start to completion`}
              {o?.runs?.drafts ? ` · ${fmt(o.runs.drafts)} draft${o.runs.drafts === 1 ? "" : "s"} not started` : ""}
            </p>
          </RibbonMetric>
          <RibbonMetric label="Awaiting human approval" value={<CountUp value={w?.total} />} href={w?.requests ? "/approvals" : "/runs?needs_review=1"}
                        hint={w ? `${count(w.runs, "run")} at review · ${count(w.requests, "approval")} · ${count(w.versions, "version")} in review` : "not available"}>
            <p className="text-[11px] text-muted-foreground">{w?.oldest_minutes == null ? "Nothing is waiting" : `Oldest has waited ${duration(w.oldest_minutes)}`}</p>
          </RibbonMetric>
          <RibbonMetric label="Stale landed tables" value={<CountUp value={f?.stale} />} href="/dashboard/sources"
                        hint={f ? `of ${fmt(f.tables)} landed · ${fmt(f.fresh)} in the last 24 hours · ${fmt(f.aging)} from 1 to 7 days ago` : "landing registry not available"}>
            <Meter value={f && f.tables ? pct(f.fresh, f.tables) : null} tone="primary" />
            <p className="mt-1.5 text-[11px] text-muted-foreground">
              {f ? [`${fmt(f.failed_loads)} table${f.failed_loads === 1 ? "" : "s"} whose last load failed this week`,
                    f.idle_pipelines ? `${fmt(f.idle_pipelines)} pipeline${f.idle_pipelines === 1 ? "" : "s"} not active` : "",
                    f.oldest_hours != null ? `oldest load ${duration(f.oldest_hours * 60)} ago` : ""].filter(Boolean).join(" · ") : ""}
            </p>
          </RibbonMetric>
          <RibbonMetric label="Failing checks" value={<CountUp value={x?.total} />} href="/dashboard/quality"
                        hint={x ? `${fmt(x.dq_checks)} data quality · ${count(x.qa_tests, "QA test")} · ${count(x.tables, "table")} affected` : "no check results readable"}>
            <Meter value={o?.dq?.pass_rate} />
            <p className="mt-1.5 text-[11px] text-muted-foreground">{o?.dq?.pass_rate == null ? "No data quality results yet" : `${o.dq.pass_rate}% of data quality checks passing`}</p>
          </RibbonMetric>
        </div>
      </section>

      {value && <ValueTile data={value} />}

      {attention.length > 0 && (
        <div className="flex flex-wrap items-center gap-2" data-testid="attention">
          <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-[0.1em] text-muted-foreground"><AlertTriangle className="h-3.5 w-3.5" /> Attention</span>
          {attention.map(({ href, icon: Icon, text, tone }) => (
            <Link key={href} href={href} className={cn("inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset transition hover:brightness-95", tone)}>
              <Icon className="h-3.5 w-3.5" /> {text} <ArrowRight className="h-3 w-3" />
            </Link>
          ))}
        </div>
      )}

      {/* six asset stores */}
      <StoreCards o={o} />

      {jira && <TicketsCard data={jira} />}

      <div className="grid gap-4 2xl:grid-cols-3">
        <section className="surface overflow-hidden 2xl:col-span-2" data-testid="pipeline-table">
          <div className="flex items-center gap-2 px-5 pb-3 pt-4">
            <h3 className="text-sm font-semibold">Active pipeline runs and SLA</h3>
            <span className="rounded-full bg-muted px-2 py-0.5 text-xs tabular-nums text-muted-foreground">{inFlightCount}</span>
            {inFlightCount > inFlight.length && <span className="text-xs text-muted-foreground">latest {inFlight.length} shown</span>}
            <Link href="/runs?status=running" className="ml-auto text-xs font-medium text-primary hover:underline">All runs in flight</Link>
          </div>
          <PipelineTable runs={inFlight.slice(0, 8)} />
        </section>

        <Panel title="Recent activity" icon={Clock} action={<Link href="/audit" className="text-xs font-medium text-primary hover:underline">Audit</Link>}>
          <ol className="relative space-y-3 before:absolute before:left-[5px] before:top-1 before:h-[calc(100%-8px)] before:w-px before:bg-border">
            {audit.events.map((e) => (
              <li key={e.event_id} className="relative pl-5">
                <span className={cn("absolute left-0 top-1.5 h-[11px] w-[11px] rounded-full border-2 border-card", e.actor_type === "HUMAN" ? "bg-blue-500" : "bg-emerald-500")} />
                <p className="truncate text-xs"><span className="font-medium">{e.run_name ?? "run"}</span>
                  <span className="text-muted-foreground"> · {(e.to_state ?? "").replace(/_/g, " ").toLowerCase()}</span></p>
                <p className="truncate text-[11px] text-muted-foreground">{e.actor} · {ago(e.created_at)}</p>
              </li>
            ))}
            {audit.events.length === 0 && (
              <li className="flex items-center gap-2 text-xs text-muted-foreground"><CheckCircle2 className="h-4 w-4 text-success" /> No activity yet.</li>
            )}
          </ol>
        </Panel>
      </div>
    </div>
  );
}

/** The caller's open tickets (Jira issues and cases) for the Command Center card; never fails the page. */
async function ticketsCard(): Promise<TicketsCardData> {
  try {
    const r = await api<{ tickets: TicketsCardData["tickets"]; jira: { connected?: boolean; connect?: boolean } }>("/api/tickets?assignee=me");
    return { tickets: r.tickets ?? [], jiraConnect: !!r.jira?.connect };
  } catch {
    return { tickets: [], error: true };
  }
}
