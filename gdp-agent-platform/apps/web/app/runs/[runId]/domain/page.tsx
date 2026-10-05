import { api, getRun } from "@/lib/api";
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
  const canScore = state.current_state === "PROFILING_COMPLETE";
  const accepted = recommendations.find((r) => r.status === "ACCEPTED");
  const packName = state.run.domain_name ?? accepted?.domain_name ?? null;
  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Knowledge pack</CardTitle>
          <CardDescription>
            This is not a factory step. The pack is stamped from the target you picked at onboarding
            {packName ? ` — currently ${packName}` : ""}.
            Scoring after profiling is an audit trail so you can see why the source looks like that pack.
            Mapping, glossary, and Soda patterns stay scoped to the stamped pack.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-muted-foreground">Run pack</span>
            <Badge variant={packName ? "success" : "outline"}>
              {packName ?? "Not stamped yet"}
            </Badge>
            {state.run.target_model && (
              <span className="text-muted-foreground">from {state.run.target_model}</span>
            )}
          </div>
          {canScore && (
            <StageAction
              label="Score source against packs"
              pendingLabel="Scoring domains…"
              action={identifyDomain.bind(null, params.runId)}
            />
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Scoring trail</CardTitle>
          <CardDescription>
            Token overlap and Cortex Search after profiling. This does not block Mapping.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {recommendations.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {canScore
                ? "Profiling is complete. Score the source to record pack confidence."
                : "Scores appear automatically after profiling."}
            </p>
          ) : (
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
          )}
        </CardContent>
      </Card>
    </>
  );
}
