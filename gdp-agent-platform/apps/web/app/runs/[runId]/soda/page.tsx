import { api, getRun } from "@/lib/api";
import { StageGate } from "@/components/stage-gate";
import { StageAction } from "@/components/stage-action";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { generateSoda } from "../pipeline-actions";
import { SodaBoard } from "./soda-board";

type SodaPayload = {
  checks: {
    expectation_id: string; target_table: string; target_column: string | null; check_type: string;
    check_definition?: Record<string, unknown> | null; severity: string; origin: string;
    client_requirement: string | null; status: string; version: number; sodacl?: string;
  }[];
  yaml: string;
  brief: { title: string; content: string } | null;
  status: { total: number; proposed: number; approved: number; rejected: number };
};

export default async function SodaPage({ params }: { params: { runId: string } }) {
  const [state, soda] = await Promise.all([
    getRun(params.runId),
    api<SodaPayload>(`/api/runs/${params.runId}/soda`),
  ]);
  const canGenerate = ["STTM_APPROVED", "SODA_PENDING"].includes(state.current_state);
  const canImport = ["STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW"].includes(state.current_state);
  const canReview = state.current_state === "SODA_REVIEW";
  return (
    <StageGate state={state} stage="SODA">
      <Card>
        <CardHeader>
          <CardTitle>Soda checks</CardTitle>
          <CardDescription>
            Checks come from the approved STTM plus the client brief. Cortex writes official SodaCL
            — missing, validity, uniqueness, freshness, schema — and you confirm each one against
            the business need. Feedback becomes a Soda pattern for the next run.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {canGenerate && (
            <StageAction
              label="Generate Soda checks"
              pendingLabel="Building SodaCL from the STTM and client brief…"
              action={generateSoda.bind(null, params.runId)}
            />
          )}
          {soda.status.total > 0 && (
            <p className="text-sm text-muted-foreground">
              {soda.status.approved} approved · {soda.status.proposed} still to confirm · {soda.status.rejected} rejected
            </p>
          )}
          <SodaBoard
            runId={params.runId}
            checks={soda.checks}
            yaml={soda.yaml}
            brief={soda.brief}
            canImport={canImport}
            canReview={canReview}
          />
        </CardContent>
      </Card>
    </StageGate>
  );
}
