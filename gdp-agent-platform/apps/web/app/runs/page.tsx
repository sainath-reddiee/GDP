import { api } from "@/lib/api";
import type { RunSummary } from "@/lib/types";
import { RunTable } from "@/components/run-table";

export default async function Runs() {
  const { runs } = await api<{ runs: RunSummary[] }>("/api/runs");
  return (
    <div className="space-y-5">
      <h2>Runs</h2>
      <RunTable runs={runs} />
    </div>
  );
}
