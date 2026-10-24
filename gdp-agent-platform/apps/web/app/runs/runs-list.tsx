import Link from "next/link";
import { ChevronRight, Crown, Database, Plus } from "lucide-react";
import { api, whoami } from "@/lib/api";
import { RunTable } from "@/components/run-table";
import { displayDomain } from "@/lib/catalog-display";
import { parseStatusFilter } from "@/lib/run-filters";
import { PIPELINES, type Pipeline } from "@/lib/stages";
import { cn } from "@/lib/utils";
import { can, type RunSummary } from "@/lib/types";
import { NewGoldRun } from "../gold/new-run";
import { RunsToolbar } from "./runs-toolbar";

const LIMIT = 50;

export type RunsSearch = {
  status?: string; q?: string; domain_id?: string; stage?: string; needs_review?: string; sort?: string; offset?: string; tag?: string;
};

type Summary = { deployed: boolean; in_flight?: number; at_gate?: number; completed_30d?: number;
  median_cycle_hours?: number | null; generated_share?: number | null; decisions?: number };

/** One pipeline's runs: the same list, filters and actions for the Silver and the Gold Pipeline. */
export async function RunsList({ pipeline, searchParams }: { pipeline: Pipeline; searchParams: RunsSearch }) {
  const filter = parseStatusFilter(searchParams.status);
  const offset = Math.max(0, Number(searchParams.offset ?? 0) || 0);
  const query = new URLSearchParams({ status: filter, limit: String(LIMIT), offset: String(offset), pipeline });
  for (const key of ["q", "domain_id", "stage", "sort", "tag"] as const) {
    const v = searchParams[key];
    if (v) query.set(key, v);
  }
  if (searchParams.needs_review === "1") query.set("needs_review", "true");
  const p = PIPELINES[pipeline];
  const Icon = pipeline === "GOLD" ? Crown : Database;
  const [{ runs, total }, { domains }, tagList, summary, me] = await Promise.all([
    api<{ runs: RunSummary[]; total?: number }>(`/api/runs?${query.toString()}`),
    api<{ domains: { domain_id: string; domain_name: string; active_flag?: boolean }[] }>("/api/domains")
      .catch(() => ({ domains: [] })),
    api<{ tags: { tag: string; count: number }[] }>("/api/tags?entity_type=RUN").catch(() => ({ tags: [] })),
    api<Summary>(`/api/pipelines/${pipeline}/summary`).catch(() => ({ deployed: false } as Summary)),
    whoami(),
  ]);
  const options = domains.filter((d) => d.active_flag !== false && displayDomain(d.domain_name))
    .map((d) => ({ domain_id: d.domain_id, domain_name: displayDomain(d.domain_name) as string }));
  // a Silver run starts with onboarding its source, the Data OS Administrator's (Enable); others request one on Intake
  const action = pipeline === "GOLD"
    ? <NewGoldRun domains={options} />
    : can(me, "SOURCE.CONNECT") ? (
      <Link href="/os/enable/onboarding?type=source" className={cn("inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm font-medium hover:opacity-90", p.accent.solid)}>
        <Plus className="h-4 w-4" />New {PIPELINES.SILVER.run}</Link>
    ) : (
      <Link href="/os/intake/new" className="inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm font-medium hover:bg-accent">
        <Plus className="h-4 w-4" />Request a source</Link>
    );
  const kpis: [string, string, string?][] = summary.deployed ? [
    ["In flight", String(summary.in_flight ?? 0)],
    ["Waiting on you", String(summary.at_gate ?? 0), (summary.at_gate ?? 0) > 0 ? "text-rose-600" : undefined],
    ["Completed, 30 days", String(summary.completed_30d ?? 0)],
    ["Median cycle", summary.median_cycle_hours != null ? `${summary.median_cycle_hours} h` : "not yet"],
    ["Mappings accepted as generated", summary.generated_share != null ? `${summary.generated_share}%` : "not yet"],
  ] : [];
  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center gap-4">
        <span className={cn("grid h-11 w-11 shrink-0 place-items-center rounded-xl", p.accent.solid)}><Icon className="h-5 w-5" /></span>
        <div className="min-w-[min(100%,18rem)] flex-1">
          <p className="eyebrow">Pipelines</p>
          <h1 className="mt-0.5">{p.name}</h1>
          <p className="text-sm text-muted-foreground">{p.tagline}</p>
        </div>
        {action}
      </header>
      <ol aria-label={`${p.name} steps`} className="flex flex-wrap items-center gap-1 text-xs">
        {p.flow.map((step, i) => (
          <li key={step} className="flex items-center gap-1">
            {i > 0 && <ChevronRight className="h-3 w-3 text-muted-foreground" />}
            <span className={cn("rounded-full border px-2.5 py-1 font-medium", p.accent.border, p.accent.bg, p.accent.text)}>{step}</span>
          </li>
        ))}
      </ol>
      {!!kpis.length && (
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-5" aria-label={`${p.name} at a glance`}>
          {kpis.map(([label, value, cls]) => (
            <div key={label} className="surface px-3 py-2">
              <dt className="text-[11px] text-muted-foreground">{label}</dt>
              <dd className={cn("text-lg font-semibold tabular-nums", cls)}>{value}</dd>
            </div>
          ))}
        </dl>
      )}
      <RunsToolbar domains={options} tags={tagList.tags.map((t) => t.tag)} total={total ?? runs.length} offset={offset}
                   limit={LIMIT} pipeline={pipeline} />
      {!runs.length && pipeline === "GOLD" && !searchParams.q && (
        <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
          No Gold runs yet. Start one for a domain: a consumer&apos;s Silver tables become its facts, and a domain Reltio masters
          (Company, Property) rebuilds its conformed dimension from the post-MDM golden record. Autopilot takes either to the first review.</p>
      )}
      <RunTable key={`${pipeline}:${filter}:${query.toString()}`} runs={runs} filter={filter} pipeline={pipeline} />
    </div>
  );
}
