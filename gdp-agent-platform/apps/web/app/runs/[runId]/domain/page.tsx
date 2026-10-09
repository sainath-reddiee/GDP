import { api, getRun } from "@/lib/api";
import { StageAction } from "@/components/stage-action";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { ModelEr } from "@/components/model-er";
import { AiSuggestions } from "@/components/ai-suggestions";
import type { ModelGraph } from "@/app/onboarding/intent-types";
import { displayDomain, isHiddenTarget } from "@/lib/catalog-display";
import { confirmDomain, identifyDomain } from "../pipeline-actions";

export default async function DomainPage({ params }: { params: { runId: string } }) {
  const [state, { recommendations }, graph] = await Promise.all([
    getRun(params.runId),
    api<{ recommendations: {
      recommendation_id: string; domain_id: string; domain_name: string; confidence: number; recommendation: string;
      status: string; decided_by: string | null;
    }[] }>(`/api/runs/${params.runId}/domain`),
    api<ModelGraph>(`/api/runs/${params.runId}/model-graph`).catch(() => null),
  ]);
  const canScore = state.current_state === "PROFILING_COMPLETE";
  const canPick = ["PROFILING_COMPLETE", "DOMAIN_IDENTIFIED", "MAPPING_PENDING", "FAILED"].includes(state.current_state);
  const accepted = recommendations.find((r) => r.status === "ACCEPTED");
  const packName = state.run.domain_name ?? accepted?.domain_name ?? null;
  return (
    <>
      <AiSuggestions runId={params.runId} stage="DOMAIN" canAct={canPick} />
      <Card>
        <CardHeader>
          <CardTitle>Knowledge pack</CardTitle>
          <CardDescription>
            This is not a factory step. The pack is stamped from the model you picked at onboarding.
            Scoring after profiling is an audit trail so you can see why the source looks like that pack.
            Mapping, glossary, and Data Quality patterns stay scoped to the stamped pack.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-muted-foreground">Run pack</span>
            <Badge variant={packName ? "success" : "outline"}>
              {displayDomain(packName) ?? (packName ? "Stamped" : "Not stamped yet")}
            </Badge>
            {state.run.target_model && !isHiddenTarget({
              fqn: state.run.target_model,
              target_database: state.run.target_model.split(".")[0],
              target_schema: state.run.target_model.split(".")[1],
              target_table: state.run.target_model.split(".").pop(),
            }) && (
              <span className="text-muted-foreground">from {state.run.target_model.split(".").pop()}</span>
            )}
          </div>
          {graph?.intent?.path === "map_existing" && (
            <p className="text-sm text-muted-foreground">
              This source was marked as already modeled. Mapping uses the selected target tables — a new
              model is not generated.
            </p>
          )}
          {canScore && (
            <StageAction
              label="Score source against packs"
              pendingLabel="Scoring domains…"
              action={identifyDomain.bind(null, params.runId)}
            />
          )}
        </CardContent>
      </Card>
      {graph && (graph.sources.length > 0 || graph.targets.length > 0) && (
        <Card>
          <CardHeader>
            <CardTitle>How the mapping looks</CardTitle>
            <CardDescription>
              Source tables on the left, selected or suggested target models on the right.
              Solid lines are approved mappings; dashed lines are the plan from onboarding.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ModelEr graph={graph} runName={state.run.run_name} />
          </CardContent>
        </Card>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Scoring trail</CardTitle>
          <CardDescription>
            Detection signals, term overlap and Cortex Search after profiling. When no pack is confident, none is applied: pick the right one here before mapping, or map without a pack.
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
              <THead><TR><TH>Domain</TH><TH>Confidence</TH><TH>Status</TH><TH>Evidence</TH>{canPick && <TH />}</TR></THead>
              <TBody>
                {recommendations.map((r) => (
                  <TR key={r.recommendation_id}>
                    <TD className="font-medium">{displayDomain(r.domain_name) || "Default pack"}</TD>
                    <TD>{Number(r.confidence).toFixed(2)}</TD>
                    <TD><Badge variant={r.status === "ACCEPTED" ? "success" : "outline"}>{r.status}</Badge></TD>
                    <TD className="text-sm text-muted-foreground">{r.recommendation}</TD>
                    {canPick && (
                      <TD className="text-right">
                        {r.status !== "ACCEPTED" && (
                          <StageAction label="Use this pack" pendingLabel="Applying…"
                                       action={confirmDomain.bind(null, params.runId, r.domain_id)} />
                        )}
                      </TD>
                    )}
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
