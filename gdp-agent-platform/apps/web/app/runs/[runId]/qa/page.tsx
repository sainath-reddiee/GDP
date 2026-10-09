import { api, ApiError, getRun } from "@/lib/api";
import type { QaResults, QaSignoff, QaSuite } from "./qa-actions";
import { QaSignoffCard } from "./qa-signoff";
import { QaWorkbench } from "./qa-workbench";

const SIGNABLE = new Set([
  "STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED", "DBT_PENDING", "DBT_GENERATING",
  "VALIDATION_PENDING", "VALIDATION_RUNNING", "VALIDATION_PASSED", "VALIDATION_FAILED", "DBT_REVIEW",
]);

export default async function QaPage({ params }: { params: { runId: string } }) {
  let suite: QaSuite | null = null;
  let error = "";
  const [state, signoff, results] = await Promise.all([
    getRun(params.runId),
    api<{ signoff: QaSignoff | null }>(`/api/runs/${params.runId}/qa/signoff`).catch(() => ({ signoff: null })),
    api<QaResults>(`/api/runs/${params.runId}/qa/results`)
      .catch((): QaResults => ({ run: null, results: [], history: {}, runs: [], ready: false })),
  ]);
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
  const canSign = !state.is_archived && SIGNABLE.has(state.current_state);
  return (
    <div className="space-y-5">
      <QaWorkbench runId={params.runId} suite={suite} results={results} canRun={!state.is_archived && results.ready} />
      <QaSignoffCard runId={params.runId} signoff={signoff.signoff} canSign={canSign} results={results} />
    </div>
  );
}
