import Link from "next/link";
import {
  Activity, AlertTriangle, ArrowRight, ArrowUpRight, Boxes, CheckCircle2, CircleDashed, Clock, Database, Eye,
  FolderTree, Layers, PlayCircle, Sparkles, XCircle,
} from "lucide-react";
import { api, whoami } from "@/lib/api";
import type { AuditEvent, ProfileStoreRow, RunSummary } from "@/lib/types";
import { RunTable } from "@/components/run-table";
import { PageHeader } from "@/components/page-header";
import { buttonVariants } from "@/components/ui/button";
import { displayDomain } from "@/lib/catalog-display";
import { FUNNEL, ago, isCancelled, needsReview, runHref, summarize } from "@/lib/run-insights";
import { cn } from "@/lib/utils";

type DomainRow = { domain_id: string; domain_name: string; knowledge_items: number; target_tables: number };

type Metrics = {
  total: number; lifecycle: Record<string, number>; by_stage: Record<string, number>; needs_review: number;
  failed: number; cancelled: number; archived: number;
  cost_30d: { calls: number; tokens: number; estimated_cost: number; credits?: number; actual_credits?: number; estimated_credits?: number } | null;
};

const STATE_LABEL: Record<string, string> = {
  MAPPING_REVIEW: "Mapping waits for approval", STTM_REVIEW: "STTM waits for approval",
  SODA_REVIEW: "Data quality checks to confirm", DBT_REVIEW: "Generated dbt code to review",
  VALIDATION_FAILED: "Validation failed: fix and rerun",
};

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

// soft tints per state: chip, the faint glow in the corner, and the accent line of a highlighted card
const TONES = {
  blue: { chip: "bg-indigo-50 text-indigo-600 ring-indigo-100", glow: "bg-indigo-400", line: "from-indigo-400 to-violet-400" },
  rose: { chip: "bg-rose-50 text-rose-600 ring-rose-100", glow: "bg-rose-400", line: "from-rose-400 to-pink-400" },
  slate: { chip: "bg-slate-100 text-slate-600 ring-slate-200", glow: "bg-slate-400", line: "from-slate-300 to-slate-400" },
  emerald: { chip: "bg-emerald-50 text-emerald-600 ring-emerald-100", glow: "bg-emerald-400", line: "from-emerald-400 to-teal-400" },
  amber: { chip: "bg-amber-50 text-amber-600 ring-amber-100", glow: "bg-amber-400", line: "from-amber-400 to-orange-400" },
} as const;

function Kpi({ href, label, value, hint, icon: Icon, tone, highlight }: {
  href: string; label: string; value: number; hint: string; icon: typeof Activity; tone: keyof typeof TONES; highlight?: boolean;
}) {
  const t = TONES[tone];
  return (
    <Link href={href}
          className={cn("surface group relative overflow-hidden p-4 transition duration-200 hover:border-primary/25 hover:shadow-hover",
            highlight && "border-rose-200 bg-gradient-to-br from-rose-50/70 via-card to-card")}>
      {highlight && <div className={cn("absolute inset-x-0 top-0 h-0.5 bg-gradient-to-r", t.line)} />}
      <div className={cn("absolute -right-10 -top-10 h-28 w-28 rounded-full opacity-[0.07] blur-xl", t.glow)} />
      <div className="flex items-center justify-between">
        <span className={cn("grid h-9 w-9 place-items-center rounded-xl ring-1 ring-inset", t.chip)}><Icon className="h-4 w-4" /></span>
        <ArrowUpRight className="h-4 w-4 text-muted-foreground/60 opacity-0 transition group-hover:opacity-100" />
      </div>
      <p className="mt-3 text-[28px] font-semibold leading-none tracking-tight tabular-nums">{value}</p>
      <p className="mt-1.5 text-sm font-medium">{label}</p>
      <p className="text-xs text-muted-foreground">{hint}</p>
    </Link>
  );
}

function Panel({ title, icon: Icon, action, children, className }: {
  title: string; icon: typeof Activity; action?: React.ReactNode; children: React.ReactNode; className?: string;
}) {
  return (
    <section className={cn("surface flex flex-col p-5", className)}>
      <div className="mb-4 flex items-center gap-2">
        <span className="grid h-7 w-7 place-items-center rounded-lg bg-accent text-accent-foreground ring-1 ring-inset ring-primary/10"><Icon className="h-3.5 w-3.5" /></span>
        <h3 className="text-sm font-semibold">{title}</h3>
        <div className="ml-auto">{action}</div>
      </div>
      {children}
    </section>
  );
}

export default async function Dashboard() {
  const [me, { runs }, metrics, review, failing, overview, store, domains, audit] = await Promise.all([
    whoami(),
    api<{ runs: RunSummary[] }>("/api/runs?limit=8"),
    api<Metrics>("/api/metrics/summary").catch(() => null),
    api<{ runs: RunSummary[] }>("/api/runs?needs_review=true&limit=6&sort=updated").catch(() => ({ runs: [] as RunSummary[] })),
    api<{ runs: RunSummary[] }>("/api/runs?status=failed&limit=10&sort=updated").catch(() => ({ runs: [] as RunSummary[] })),
    api<{ sources: unknown[] }>("/api/sources").catch(() => null),
    api<{ profiles: ProfileStoreRow[] }>("/api/profiles/store").catch(() => ({ profiles: [] as ProfileStoreRow[] })),
    api<{ domains: DomainRow[] }>("/api/domains").catch(() => ({ domains: [] as DomainRow[] })),
    api<{ events: AuditEvent[] }>("/api/audit?limit=7").catch(() => ({ events: [] as AuditEvent[] })),
  ]);
  // Counts come from the server (correct at any number of runs); the run lists below are small, targeted fetches.
  const s = metrics
    ? { total: metrics.total, running: metrics.lifecycle.RUNNING ?? 0, drafts: metrics.lifecycle.DRAFT ?? 0,
        review: metrics.needs_review, completed: metrics.lifecycle.COMPLETED ?? 0, failed: metrics.failed,
        cancelled: metrics.cancelled, stages: metrics.by_stage }
    : summarize(runs);
  const attention = review.runs.filter((r) => needsReview(r)).slice(0, 6);
  const failed = failing.runs.filter((r) => !r.is_archived && !isCancelled(r)).slice(0, 3);
  const cost = metrics?.cost_30d;
  const funnelMax = Math.max(1, ...FUNNEL.map((f) => s.stages[f.stage] ?? 0));
  const profiles = store.profiles;
  const staged = profiles.filter((p) => p.status !== "PROFILING" && p.status !== "FAILED").length;
  const profilingNow = profiles.filter((p) => p.status === "PROFILING").length;
  const bySchema = new Map<string, number>();
  for (const p of profiles) bySchema.set(`${p.database_name}.${p.schema_name}`, (bySchema.get(`${p.database_name}.${p.schema_name}`) ?? 0) + 1);
  const topSchemas = Array.from(bySchema.entries()).sort((a, b) => b[1] - a[1]).slice(0, 4);
  const visibleDomains = domains.domains.filter((d) => displayDomain(d.domain_name));
  const user = (me?.user ?? "").split(/[._@ ]/)[0];
  const name = user ? user.charAt(0) + user.slice(1).toLowerCase() : "";

  return (
    <div className="space-y-6">
      <PageHeader eyebrow={new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })}
                  title={`${greeting()}${name ? `, ${name}` : ""}`}
                  description={s.review
                    ? `${s.review} run${s.review === 1 ? " is" : "s are"} waiting on a review. ${s.running} running, ${s.drafts} in draft.`
                    : `${s.running} run${s.running === 1 ? "" : "s"} in flight and nothing waiting on you.`}
                  actions={<>
                    <Link href="/runs" className={buttonVariants({ variant: "outline" })}>All runs</Link>
                    <Link href="/sources" className={buttonVariants()}><Sparkles className="h-4 w-4" /> Profile & model a source</Link>
                  </>} />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <Kpi href="/runs?status=active" label="Running" value={s.running} hint="Moving through the pipeline" icon={PlayCircle} tone="blue" />
        <Kpi href="/runs?needs_review=1" label="Needs review" value={s.review} hint="Mapping, STTM, checks or code" icon={Eye} tone="rose" highlight={s.review > 0} />
        <Kpi href="/runs?status=draft" label="Drafts" value={s.drafts} hint="Created, source not chosen yet" icon={CircleDashed} tone="slate" />
        <Kpi href="/runs?status=completed" label="Completed" value={s.completed} hint="Approved and delivered" icon={CheckCircle2} tone="emerald" />
        <Kpi href="/runs?status=failed" label="Failed" value={s.failed} hint={`${s.cancelled} more cancelled`} icon={XCircle} tone="amber" />
      </div>
      {cost && (
        <Link href="/audit?tab=cost" className="flex flex-wrap items-center gap-3 rounded-xl border border-indigo-100 bg-gradient-to-r from-indigo-50/80 via-card to-card px-4 py-3 text-sm shadow-xs transition hover:border-indigo-200 hover:shadow-card">
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-indigo-100 text-indigo-600"><Sparkles className="h-3.5 w-3.5" /></span>
          <span className="font-medium">AI usage, last 30 days</span>
          <span className="text-muted-foreground">{cost.calls.toLocaleString()} calls · {cost.tokens.toLocaleString()} tokens · {(cost.credits ?? cost.estimated_cost).toFixed(2)} credits{cost.actual_credits ? ` (${cost.actual_credits.toFixed(2)} billed)` : " estimated"}</span>
          <ArrowRight className="ml-auto h-4 w-4 text-muted-foreground" />
        </Link>
      )}

      <div className="grid gap-4 xl:grid-cols-5">
        <Panel title="Pipeline: where running work sits" icon={Activity} className="xl:col-span-3"
               action={<span className="text-xs text-muted-foreground">{s.running} running</span>}>
          <div className="space-y-2.5">
            {FUNNEL.map((f, i) => {
              const n = s.stages[f.stage] ?? 0;
              return (
                <div key={f.stage} className="grid grid-cols-[110px_1fr_32px] items-center gap-3 text-sm">
                  <span className="flex items-center gap-2 text-muted-foreground">
                    <span className="grid h-5 w-5 place-items-center rounded-full bg-muted text-[10px] font-semibold">{i + 1}</span>
                    {f.label}
                  </span>
                  <div className="h-2.5 overflow-hidden rounded-full bg-muted">
                    <div className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-violet-400 transition-all"
                         style={{ width: n ? `${Math.max(6, (100 * n) / funnelMax)}%` : "0%" }} />
                  </div>
                  <span className="text-right font-semibold tabular-nums">{n}</span>
                </div>
              );
            })}
          </div>
          {s.running === 0 && <p className="mt-3 text-xs text-muted-foreground">No runs in progress.</p>}
        </Panel>

        <Panel title="Needs your attention" icon={AlertTriangle} className="xl:col-span-2"
               action={<Link href="/runs?status=active" className="text-xs font-medium text-primary hover:underline">View runs</Link>}>
          {attention.length === 0 && failed.length === 0 ? (
            <div className="grid flex-1 place-items-center py-6 text-center">
              <CheckCircle2 className="h-8 w-8 text-success" />
              <p className="mt-2 text-sm font-medium">You are all caught up</p>
              <p className="text-xs text-muted-foreground">No mapping, STTM, check or code review is waiting.</p>
            </div>
          ) : (
            <ul className="space-y-2">
              {[...attention, ...failed].map((r) => (
                <li key={r.run_id}>
                  <Link href={runHref(r)} className="group flex items-center gap-3 rounded-xl border border-border/80 bg-card p-3 transition hover:border-primary/30 hover:bg-accent/50">
                    <span className={cn("h-2 w-2 shrink-0 rounded-full", needsReview(r) ? "bg-rose-500" : "bg-amber-500")} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{r.run_name}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {STATE_LABEL[r.current_state] ?? `Failed at ${r.current_state.replace(/_/g, " ").toLowerCase()}`}
                        {r.updated_at ? ` · ${ago(r.updated_at)}` : ""}
                      </span>
                    </span>
                    <ArrowRight className="h-4 w-4 text-muted-foreground transition group-hover:translate-x-0.5 group-hover:text-primary" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Profile store" icon={Layers}
               action={<Link href="/sources" className="text-xs font-medium text-primary hover:underline">Open Sources</Link>}>
          <div className="flex items-end gap-6">
            <div>
              <p className="text-3xl font-semibold tabular-nums">{staged}</p>
              <p className="text-xs text-muted-foreground">tables staged for reuse</p>
            </div>
            <div>
              <p className="text-xl font-semibold tabular-nums">{bySchema.size}</p>
              <p className="text-xs text-muted-foreground">schemas</p>
            </div>
            <div>
              <p className={cn("text-xl font-semibold tabular-nums", profilingNow && "text-amber-600")}>{profilingNow}</p>
              <p className="text-xs text-muted-foreground">profiling now</p>
            </div>
          </div>
          <ul className="mt-4 space-y-1.5">
            {topSchemas.map(([schema, n]) => (
              <li key={schema} className="flex items-center gap-2 text-xs">
                <FolderTree className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="truncate font-mono">{schema}</span>
                <span className="ml-auto tabular-nums text-muted-foreground">{n}</span>
              </li>
            ))}
            {topSchemas.length === 0 && <li className="text-xs text-muted-foreground">Nothing profiled yet.</li>}
          </ul>
          {overview && (
            <p className="mt-3 border-t pt-3 text-xs text-muted-foreground">
              <Database className="mr-1 inline h-3.5 w-3.5" />
              {overview.sources.length} registered source{overview.sources.length === 1 ? "" : "s"}
            </p>
          )}
        </Panel>

        <Panel title="Domain knowledge" icon={Boxes}
               action={<Link href="/domains" className="text-xs font-medium text-primary hover:underline">Domains</Link>}>
          <ul className="space-y-2">
            {visibleDomains.map((d) => (
              <li key={d.domain_id} className="flex items-center gap-3 rounded-xl border border-border/80 px-3 py-2 transition hover:bg-accent/40">
                <span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-indigo-50 to-violet-100 text-xs font-bold text-indigo-600 ring-1 ring-inset ring-indigo-100">
                  {d.domain_name.slice(0, 2)}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{d.domain_name}</span>
                  <span className="text-xs text-muted-foreground">{d.target_tables} target model{d.target_tables === 1 ? "" : "s"}</span>
                </span>
                <span className="text-xs tabular-nums text-muted-foreground">{d.knowledge_items} items</span>
              </li>
            ))}
            {visibleDomains.length === 0 && <li className="text-xs text-muted-foreground">No domain packs registered.</li>}
          </ul>
        </Panel>

        <Panel title="Recent activity" icon={Clock}
               action={<Link href="/audit" className="text-xs font-medium text-primary hover:underline">Audit</Link>}>
          <ol className="relative space-y-3 before:absolute before:left-[5px] before:top-1 before:h-[calc(100%-8px)] before:w-px before:bg-border">
            {audit.events.slice(0, 7).map((e) => (
              <li key={e.event_id} className="relative pl-5">
                <span className={cn("absolute left-0 top-1.5 h-[11px] w-[11px] rounded-full border-2 border-card",
                  e.actor_type === "HUMAN" ? "bg-indigo-500" : "bg-emerald-500")} />
                <p className="truncate text-xs">
                  <span className="font-medium">{e.run_name ?? "run"}</span>
                  <span className="text-muted-foreground"> · {e.to_state.replace(/_/g, " ").toLowerCase()}</span>
                </p>
                <p className="truncate text-[11px] text-muted-foreground">{e.actor} · {ago(e.created_at)}</p>
              </li>
            ))}
            {audit.events.length === 0 && <li className="text-xs text-muted-foreground">No activity yet.</li>}
          </ol>
        </Panel>
      </div>

      <section className="space-y-3">
        <div className="flex items-center">
          <h3>Latest runs</h3>
          <Link href="/runs" className="ml-auto text-sm font-medium text-primary hover:underline">See all runs</Link>
        </div>
        <RunTable runs={runs.filter((r) => !r.is_archived).slice(0, 8)} selectable={false} />
      </section>
    </div>
  );
}
