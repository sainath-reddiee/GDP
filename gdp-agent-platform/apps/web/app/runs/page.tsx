import { api } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import type { RunSummary } from "@/lib/types";
import { RunTable } from "@/components/run-table";
import { displayDomain } from "@/lib/catalog-display";
import { parseStatusFilter } from "@/lib/run-filters";
import { RunsToolbar } from "./runs-toolbar";

const LIMIT = 50;

export default async function Runs({ searchParams }: {
  searchParams: { status?: string; q?: string; domain_id?: string; stage?: string; needs_review?: string; sort?: string; offset?: string; tag?: string };
}) {
  const filter = parseStatusFilter(searchParams.status);
  const offset = Math.max(0, Number(searchParams.offset ?? 0) || 0);
  const query = new URLSearchParams({ status: filter, limit: String(LIMIT), offset: String(offset) });
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
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Work" title="Runs"
                  description="Every modeling run from source to reviewed code. Archived runs are hidden from the other filters; deleting a run keeps its audit trail and the staged table profiles." />
      <RunsToolbar domains={options} tags={tagList.tags.map((t) => t.tag)} total={total ?? runs.length} offset={offset} limit={LIMIT} />
      <RunTable key={`${filter}:${query.toString()}`} runs={runs} filter={filter} />
    </div>
  );
}
