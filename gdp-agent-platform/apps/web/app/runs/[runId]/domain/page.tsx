import { api, getRun } from "@/lib/api";
import { StageGate } from "@/components/stage-gate";
import { StageAction } from "@/components/stage-action";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { identifyDomain } from "../pipeline-actions";

export default async function DomainPage({ params }: { params: { runId: string } }) {
  const [state, { recommendations }] = await Promise.all([
    getRun(params.runId),
    api<{ recommendations: {
      recommendation_id: string; domain_name: string; confidence: number; recommendation: string;
      status: string; decided_by: string | null;
    }[] }>(`/api/runs/${params.runId}/domain`),
  ]);
  const canRun = state.current_state === "PROFILING_COMPLETE";
  return (
    <StageGate state={state} stage="DOMAIN">
      <Card>
        <CardHeader>
          <CardTitle>Domain identification</CardTitle>
          <CardDescription>
            The platform scores active domains from profile tokens and Cortex Search. The top match is recorded on
            the run; a human can still override later knowledge, but Phase 1 uses the accepted recommendation.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {canRun && (
            <StageAction
              label="Identify domain"
              pendingLabel="Scoring domains…"
              action={identifyDomain.bind(null, params.runId)}
            />
          )}
        </CardContent>
      </Card>
      {recommendations.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Recommendations</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <THead><TR><TH>Domain</TH><TH>Confidence</TH><TH>Status</TH><TH>Evidence</TH></TR></THead>
              <TBody>
                {recommendations.map((r) => (
                  <TR key={r.recommendation_id}>
                    <TD className="font-medium">{r.domain_name}</TD>
                    <TD>{Number(r.confidence).toFixed(2)}</TD>
                    <TD><Badge variant={r.status === "ACCEPTED" ? "success" : "outline"}>{r.status}</Badge></TD>
                    <TD className="text-sm text-muted-foreground">{r.recommendation}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </StageGate>
  );
}
