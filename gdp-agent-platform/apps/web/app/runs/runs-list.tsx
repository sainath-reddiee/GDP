import Link from "next/link";
import { Plus } from "lucide-react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import { PipelineTabs } from "@/components/pipeline-tabs";
import { RunTable } from "@/components/run-table";
import { displayDomain } from "@/lib/catalog-display";
import { parseStatusFilter } from "@/lib/run-filters";
import { PIPELINES, type Pipeline } from "@/lib/stages";
import type { RunSummary } from "@/lib/types";
import { NewGoldRun } from "../gold/new-run";
import { RunsToolbar } from "./runs-toolbar";

const LIMIT = 50;

export type RunsSearch = {
  status?: string; q?: string; domain_id?: string; stage?: string; needs_review?: string; sort?: string; offset?: string; tag?: string;
};

const DESCRIPTION: Record<Pipeline, string> = {
  SILVER: "The Silver Pipeline takes a source to reviewed Silver (pre-MDM) code: Source Ingestion, Profiling, Modeling "
    + "(field mapping, then transformations and joins), QA tests, Data Quality, Silver Code Generation, Validation and Code review.",
  GOLD: "The Gold Pipeline takes Silver tables, read in place, to reviewed Gold code with the same steps and gates: Silver "
    + "Selection, Profiling, Modeling (the gold model of facts, dimensions and bridges, field mapping, then transformations), "
    + "QA tests, Data Quality, Gold Code Generation, Validation and Code review. Company and Property keys resolve through Reltio's cross-reference.",
};

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
  const [{ runs, total }, { domains }, tagList] = await Promise.all([
    api<{ runs: RunSummary[]; total?: number }>(`/api/runs?${query.toString()}`),
    api<{ domains: { domain_id: string; domain_name: string; active_flag?: boolean }[] }>("/api/domains")
      .catch(() => ({ domains: [] })),
    api<{ tags: { tag: string; count: number }[] }>("/api/tags?entity_type=RUN").catch(() => ({ tags: [] })),
  ]);
  const options = domains.filter((d) => d.active_flag !== false && displayDomain(d.domain_name))
    .map((d) => ({ domain_id: d.domain_id, domain_name: displayDomain(d.domain_name) as string }));
  const action = pipeline === "GOLD"
    ? <NewGoldRun domains={options} />
    : (
      <Link href="/dashboard/onboarding" className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground">
        <Plus className="h-4 w-4" />New {PIPELINES.SILVER.run}</Link>
    );
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Model and build" title="Pipelines" description={DESCRIPTION[pipeline]} actions={action} />
      <PipelineTabs active={pipeline} />
      <RunsToolbar domains={options} tags={tagList.tags.map((t) => t.tag)} total={total ?? runs.length} offset={offset}
                   limit={LIMIT} pipeline={pipeline} />
      {!runs.length && pipeline === "GOLD" && !searchParams.q && (
        <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">
          No Gold runs yet. Start one for a domain that consumes Company or Property (set on the domain&apos;s Lineage tab): pick its
          Silver tables and the run takes them through the same steps as a Silver run.</p>
      )}
      <RunTable key={`${pipeline}:${filter}:${query.toString()}`} runs={runs} filter={filter} />
    </div>
  );
}
