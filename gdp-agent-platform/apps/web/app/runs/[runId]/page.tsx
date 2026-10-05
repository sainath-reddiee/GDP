import { getRun } from "@/lib/api";
import { SttmFork } from "@/components/sttm-fork";
import { RunConsole } from "./console";
import { AgentPanel } from "@/components/agent-panel";

export default async function RunOverview({ params }: { params: { runId: string } }) {
  const state = await getRun(params.runId);
  const showFork = [
    "STTM_REVIEW", "STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED",
    "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_RUNNING",
    "VALIDATION_PASSED", "VALIDATION_FAILED", "DBT_REVIEW",
  ].includes(state.current_state);
  return (
    <>
      {showFork && <SttmFork runId={params.runId} currentState={state.current_state} />}
      <RunConsole state={state} />
      <AgentPanel runId={params.runId} />
    </>
  );
}
