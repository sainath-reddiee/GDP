import Link from "next/link";
import {
  AlertTriangle, ArrowRight, BarChart3, CheckCircle2, ClipboardCheck, Clock, Database, Eye, GitCompareArrows, Network, PlugZap,
  Siren, SquareTerminal, XCircle,
} from "lucide-react";
import { api, whoami } from "@/lib/api";
import { can, type AuditEvent, type RunSummary } from "@/lib/types";
import type { Overview } from "@/lib/store-types";
import { getDomainScope } from "@/lib/domain-scope";
import { isCancelled, needsReview, ago } from "@/lib/run-insights";
import type { IncidentSummary } from "../incidents/actions";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/hub/kpi";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { fmt, Meter, PipelineTable, RibbonMetric, Sparkline, StoreCard } from "./command-center";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

const pct = (part: number, whole: number) => (whole ? (100 * part) / whole : null);

/** The Command Center: what the platform holds (six asset stores), how healthy it is, and what is in flight. */
export default async function Dashboard() {
  const scope = getDomainScope();
  const me = await whoami();
  const overview = await api<Overview>(`/api/hub/overview${scope ? `?domain=${encodeURIComponent(scope)}` : ""}`).catch(() => null);
  const domainId = overview?.domain?.domain_id;
  const runQs = (extra: string) => `/api/runs?${extra}${domainId ? `&domain_id=${domainId}` : ""}`;
  const [active, review, failing, audit, incidents] = await Promise.all([
    api<{ runs: RunSummary[] }>(runQs("status=active&limit=10&sort=updated")).catch(() => ({ runs: [] as RunSummary[] })),
    api<{ runs: RunSummary[]; total?: number }>(runQs("needs_review=true&limit=1")).catch(() => null),
    api<{ runs: RunSummary[]; total?: number }>(runQs("status=failed&limit=20&sort=updated")).catch(() => null),
    api<{ events: AuditEvent[] }>("/api/audit?limit=6").catch(() => ({ events: [] as AuditEvent[] })),
    can(me, "OPS.VIEW") ? api<IncidentSummary>("/api/ops/incidents/summary").catch(() => null) : Promise.resolve(null),
  ]);
  const o = overview;
  const user = (me?.user ?? "").split(/[._@ ]/)[0];
  const name = user ? user.charAt(0) + user.slice(1).toLowerCase() : "";
  const inFlight = active.runs.filter((r) => !r.is_archived);
  const reviewCount = review?.total ?? inFlight.filter(needsReview).length;
  const failedCount = (failing?.runs ?? []).filter((r) => !r.is_archived && !isCancelled(r)).length;
  const openIncidents = incidents ? incidents.open + incidents.ack : null;
  const dqFailing = o?.dq?.failed ?? 0;
  const attention = [
    { show: reviewCount > 0, href: "/runs?needs_review=1", icon: Eye, text: `${reviewCount} run${reviewCount === 1 ? "" : "s"} need review`, tone: "text-rose-700 bg-rose-50 ring-rose-200" },
    { show: failedCount > 0, href: "/runs?status=failed", icon: XCircle, text: `${failedCount} failed run${failedCount === 1 ? "" : "s"}`, tone: "text-amber-800 bg-amber-50 ring-amber-200" },
    { show: !!openIncidents, href: "/incidents", icon: Siren, text: `${openIncidents} open incident${openIncidents === 1 ? "" : "s"}`, tone: "text-rose-700 bg-rose-50 ring-rose-200" },
    { show: !!o?.qa?.pending, href: "/dashboard/qa-store", icon: SquareTerminal, text: `${o?.qa?.pending} QA pack${o?.qa?.pending === 1 ? "" : "s"} awaiting approval`, tone: "text-violet-700 bg-violet-50 ring-violet-200" },
    { show: dqFailing > 0, href: "/dashboard/dq-store", icon: ClipboardCheck, text: `${dqFailing} data quality check${dqFailing === 1 ? "" : "s"} failing`, tone: "text-rose-700 bg-rose-50 ring-rose-200" },
    { show: !!o?.profiling?.drift, href: "/dashboard/profiling", icon: BarChart3, text: `${o?.profiling?.drift} drifting column${o?.profiling?.drift === 1 ? "" : "s"}`, tone: "text-amber-800 bg-amber-50 ring-amber-200" },
  ].filter((a) => a.show);
  const daily = o?.ingested?.daily ?? [];

  return (
    <div className="space-y-6">
      <PageHeader eyebrow={o?.domain ? `${o.domain.domain_name} domain` : "All domains"}
                  title={`${greeting()}${name ? `, ${name}` : ""}`}
                  description={`${inFlight.length} run${inFlight.length === 1 ? "" : "s"} in flight${reviewCount ? `, ${reviewCount} waiting on review` : ", nothing waiting on you"}. Your data estate at a glance.`}
                  actions={<>
                    <Link href="/runs" className={buttonVariants({ variant: "outline" })}>All runs</Link>
                    <Link href="/dashboard/sources/new" className={buttonVariants()}><PlugZap className="h-4 w-4" /> Onboard source</Link>
                  </>} />

      {/* hero ribbon */}
      <section className="glass relative overflow-hidden p-1" data-testid="hero-ribbon">
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-r from-indigo-500/[0.07] via-transparent to-cyan-500/[0.07]" />
        <div className="relative grid divide-y divide-border/60 sm:grid-cols-2 sm:divide-y-0 xl:grid-cols-4 xl:divide-x">
          <RibbonMetric label="Live ingested" value={o?.ingested ? fmt(o.ingested.gb) : "-"} unit="GB" href="/dashboard/sources"
                        hint={o?.ingested ? `${fmt(o.ingested.tables)} landed tables · ${fmt(o.ingested.rows)} rows` : "landing registry not available"}>
            <Sparkline values={daily.map((d) => d.gb)} />
          </RibbonMetric>
          <RibbonMetric label="Profiling health" value={o?.profiling?.health == null ? "-" : Math.round(o.profiling.health)} unit={o?.profiling?.health == null ? undefined : "%"}
                        href="/dashboard/profiling"
                        hint={o?.profiling ? `${fmt(o.profiling.tables)} tables · ${fmt(o.profiling.columns)} columns profiled` : "no profiles yet"}>
            <Meter value={o?.profiling?.health} />
            <p className="mt-1.5 text-[11px] text-muted-foreground">Columns under 20% nulls with no drift since the previous pass</p>
          </RibbonMetric>
          <RibbonMetric label="STTM published" value={o?.sttm ? fmt(o.sttm.verified) : "-"} href="/dashboard/sttm"
                        hint={o?.sttm ? `of ${fmt(o.sttm.mappings)} mappings · ${fmt(o.sttm.pending)} pending review` : "catalog not available"}>
            <Meter value={o?.sttm ? pct(o.sttm.verified, o.sttm.mappings) : null} tone="primary" />
          </RibbonMetric>
          <RibbonMetric label="Active DQ checks" value={o?.dq ? fmt(o.dq.checks) : "-"} href="/dashboard/dq-store"
                        hint={o?.dq ? `${o.dq.pass_rate == null ? "no results yet" : `${o.dq.pass_rate}% passing`} · ${o.dq.coverage == null ? "-" : `${o.dq.coverage}%`} coverage` : "DQ Store not deployed"}>
            <Meter value={o?.dq?.pass_rate} />
          </RibbonMetric>
        </div>
      </section>

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
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <StoreCard testId="store-source" title="Source Store" subtitle="Shares, databases and external pipelines" icon={Database} tone="indigo" missing={!o?.sources}
                   metrics={[{ label: "Shares", value: fmt(o?.sources?.shares) }, { label: "Databases", value: fmt(o?.sources?.databases) },
                             { label: "Pipelines", value: fmt(o?.sources?.external) }]}
                   links={[{ label: "Onboard source", href: "/dashboard/sources/new" }, { label: "All sources", href: "/dashboard/sources" }]} />
        <StoreCard testId="store-profiling" title="Profiling Store" subtitle="Distributions, nulls, cardinality and drift" icon={BarChart3} tone="sky" missing={!o?.profiling}
                   metrics={[{ label: "Tables profiled", value: fmt(o?.profiling?.tables) },
                             { label: "Drift alerts", value: fmt(o?.profiling?.drift), tone: o?.profiling?.drift ? "text-amber-600" : undefined },
                             { label: "High cardinality", value: fmt(o?.profiling?.high_cardinality) }]}
                   meter={{ label: "Profiling health", value: o?.profiling?.health }}
                   links={[{ label: "Browse profiles", href: "/dashboard/profiling" }, { label: "Profile a source", href: "/dashboard/sources" }]} />
        <StoreCard testId="store-models" title="Modeling Store" subtitle="Domain models published to feature branches" icon={Network} tone="violet" missing={!o?.models}
                   metrics={[{ label: "Published", value: fmt(o?.models?.published) }, { label: "Branches", value: fmt(o?.models?.branches) },
                             { label: "In design", value: fmt(o?.models?.in_design) }]}
                   links={[{ label: "New model branch", href: "/dashboard/modeling" }, { label: "Domains", href: "/domains" }]} />
        <StoreCard testId="store-sttm" title="STTM Store" subtitle="Versioned source to target mappings" icon={GitCompareArrows} tone="emerald" missing={!o?.sttm}
                   metrics={[{ label: "Verified", value: fmt(o?.sttm?.verified), tone: "text-emerald-600" }, { label: "Pending review", value: fmt(o?.sttm?.pending) },
                             { label: "Mappings", value: fmt(o?.sttm?.mappings) }]}
                   meter={{ label: "Verified share", value: o?.sttm ? pct(o.sttm.verified, o.sttm.mappings) : null, tone: "primary" }}
                   links={[{ label: "Open catalog", href: "/dashboard/sttm" }, { label: "Pending review", href: "/dashboard/sttm?status=REVIEW" }]} />
        <StoreCard testId="store-qa" title="QA Store" subtitle={o?.qa?.top_tags?.length ? o.qa.top_tags.slice(0, 3).map((t) => `#${t}`).join(" ") : "Deterministic domain SQL packs"}
                   icon={SquareTerminal} tone="amber" missing={!o?.qa}
                   metrics={[{ label: "Approved suites", value: fmt(o?.qa?.approved_packs) }, { label: "Live rules", value: fmt(o?.qa?.rules) },
                             { label: "Pass rate", value: o?.qa?.pass_rate == null ? "-" : `${Math.round(o.qa.pass_rate)}%` }]}
                   links={[{ label: "Add QA query", href: "/dashboard/qa-store?new=1" }, { label: "Open QA Store", href: "/dashboard/qa-store" }]} />
        <StoreCard testId="store-dq" title="Data Quality Store" subtitle="Soda and Great Expectations attribute checks" icon={ClipboardCheck} tone="rose" missing={!o?.dq}
                   metrics={[{ label: "Active checks", value: fmt(o?.dq?.checks) },
                             { label: "Failing", value: fmt(o?.dq?.failed), tone: o?.dq?.failed ? "text-rose-600" : undefined },
                             { label: "Scheduled tables", value: fmt(o?.dq?.scheduled_tables) }]}
                   meter={{ label: "Coverage of profiled tables", value: o?.dq?.coverage, tone: "primary" }}
                   links={[{ label: "Open DQ Store", href: "/dashboard/dq-store" }, { label: "Seed from profiles", href: "/dashboard/dq-store" }]} />
      </div>

      <div className="grid gap-4 2xl:grid-cols-3">
        <section className="surface overflow-hidden 2xl:col-span-2" data-testid="pipeline-table">
          <div className="flex items-center gap-2 px-5 pb-3 pt-4">
            <h3 className="text-sm font-semibold">Active pipeline runs and SLA</h3>
            <span className="rounded-full bg-muted px-2 py-0.5 text-xs tabular-nums text-muted-foreground">{inFlight.length}</span>
            <Link href="/runs?status=active" className="ml-auto text-xs font-medium text-primary hover:underline">All active runs</Link>
          </div>
          <PipelineTable runs={inFlight.slice(0, 8)} />
        </section>

        <Panel title="Recent activity" icon={Clock} action={<Link href="/audit" className="text-xs font-medium text-primary hover:underline">Audit</Link>}>
          <ol className="relative space-y-3 before:absolute before:left-[5px] before:top-1 before:h-[calc(100%-8px)] before:w-px before:bg-border">
            {audit.events.map((e) => (
              <li key={e.event_id} className="relative pl-5">
                <span className={cn("absolute left-0 top-1.5 h-[11px] w-[11px] rounded-full border-2 border-card", e.actor_type === "HUMAN" ? "bg-indigo-500" : "bg-emerald-500")} />
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
