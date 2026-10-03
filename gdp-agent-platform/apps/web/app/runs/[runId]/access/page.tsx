import { api, getRun } from "@/lib/api";
import type { SourceOverview } from "@/lib/types";
import { StageGate } from "@/components/stage-gate";
import { ChecksTable } from "@/components/checks-table";
import { StageAction } from "@/components/stage-action";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { executeLanding } from "../source-actions";

export default async function AccessPage({ params }: { params: { runId: string } }) {
  const [state, { checks }] = await Promise.all([
    getRun(params.runId),
    api<SourceOverview>(`/api/runs/${params.runId}/source`),
  ]);
  const attempts = Array.from(new Set(checks.map((c) => c.checked_at)));
  const canLand = state.current_state === "ACCESS_APPROVED";
  return (
    <StageGate state={state} stage="ACCESS">
      <Card>
        <CardHeader>
          <CardTitle>Access validation</CardTitle>
          <CardDescription>
            Checks run as the platform: they prove it can read every selected object before anything is copied.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {canLand && (
            <StageAction
              label="Start landing"
              pendingLabel="Landing objects… this can take a few minutes"
              action={executeLanding.bind(null, params.runId)}
            />
          )}
        </CardContent>
      </Card>
      {attempts.map((at, i) => (
        <Card key={at}>
          <CardHeader>
            <CardTitle>{i === 0 ? "Latest attempt" : "Earlier attempt"}</CardTitle>
            <CardDescription>{at.slice(0, 19)}</CardDescription>
          </CardHeader>
          <CardContent><ChecksTable checks={checks.filter((c) => c.checked_at === at)} /></CardContent>
        </Card>
      ))}
    </StageGate>
  );
}
