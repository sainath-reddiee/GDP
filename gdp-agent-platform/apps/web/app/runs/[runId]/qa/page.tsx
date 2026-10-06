import { api, ApiError } from "@/lib/api";
import type { QaSuite } from "./qa-actions";
import { QaWorkbench } from "./qa-workbench";

export default async function QaPage({ params }: { params: { runId: string } }) {
  let suite: QaSuite | null = null;
  let error = "";
  try {
    suite = await api<QaSuite>(`/api/runs/${params.runId}/qa`);
  } catch (e) {
    error = e instanceof ApiError ? e.message : "Could not prepare the QA suite.";
  }
  if (!suite) {
    return (
      <div className="rounded-2xl border bg-card p-10 text-center shadow-sm">
        <h3 className="text-base font-semibold">QA tests are not available yet</h3>
        <p className="mt-1 text-sm text-muted-foreground">{error}</p>
      </div>
    );
  }
  return <QaWorkbench runId={params.runId} suite={suite} />;
}
