import { getRun } from "@/lib/api";
import { RunConsole } from "./console";
import { AgentPanel } from "@/components/agent-panel";

export default async function RunOverview({ params }: { params: { runId: string } }) {
  const state = await getRun(params.runId);
  return (
    <>
      <RunConsole state={state} />
      <AgentPanel runId={params.runId} />
    </>
  );
}
