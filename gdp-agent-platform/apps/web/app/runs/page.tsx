import { api } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import type { RunSummary } from "@/lib/types";
import { RunTable } from "@/components/run-table";
import { parseStatusFilter } from "@/lib/run-filters";

export default async function Runs({ searchParams }: { searchParams: { status?: string } }) {
  const filter = parseStatusFilter(searchParams.status);
  const { runs } = await api<{ runs: RunSummary[] }>(`/api/runs?status=${filter}`);
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Work" title="Runs"
                  description="Every modeling run from source to reviewed code. Archived runs are hidden from the other filters; deleting a run keeps its audit trail and the staged table profiles." />
      <RunTable key={filter} runs={runs} filter={filter} />
    </div>
  );
}
