import { api } from "@/lib/api";
import type { RunStatusFilter, RunSummary } from "@/lib/types";
import { RunTable, STATUS_FILTERS } from "@/components/run-table";

export default async function Runs({ searchParams }: { searchParams: { status?: string } }) {
  const requested = (searchParams.status ?? "all").toLowerCase();
  const filter = (STATUS_FILTERS.some((f) => f.value === requested) ? requested : "all") as RunStatusFilter;
  const { runs } = await api<{ runs: RunSummary[] }>(`/api/runs?status=${filter}`);
  return (
    <div className="space-y-5">
      <div>
        <h2>Runs</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Archived runs are hidden from the other filters. Deleting a run keeps its audit trail and the staged
          table profiles.
        </p>
      </div>
      <RunTable key={filter} runs={runs} filter={filter} />
    </div>
  );
}
