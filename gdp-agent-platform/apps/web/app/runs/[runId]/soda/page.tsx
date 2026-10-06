import { api, getRun } from "@/lib/api";
import { StageGate } from "@/components/stage-gate";
import { StageAction } from "@/components/stage-action";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { generateSoda } from "../pipeline-actions";
import { SodaBoard } from "./soda-board";
import { SodaGate } from "./soda-gate";
import type { SttmLine } from "../sttm/sttm-board";

type SodaPayload = {
  checks: {
    expectation_id: string; target_table: string; target_column: string | null; check_type: string;
    check_definition?: Record<string, unknown> | null; severity: string; origin: string;
    client_requirement: string | null; status: string; version: number; sodacl?: string;
    evidence?: string | null;
    backtest?: { status: "PASS" | "FAIL" | "NOT_EVALUATED"; observed?: number; percent?: number; detail?: string } | null;
  }[];
  yaml: string;
  gx_suite?: Record<string, unknown> | null;
  brief: { title: string; content: string } | null;
  status: { total: number; proposed: number; approved: number; rejected: number };
};

export default async function SodaPage({ params }: { params: { runId: string } }) {
  const [state, soda, contract] = await Promise.all([
    getRun(params.runId),
    api<SodaPayload>(`/api/runs/${params.runId}/soda`),
    api<{ lines: SttmLine[] }>(`/api/runs/${params.runId}/sttm`).catch(() => ({ lines: [] })),
  ]);
  const canGenerate = ["STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "DBT_PENDING", "DBT_GENERATING"].includes(state.current_state);
  const canImport = ["STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED", "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING"].includes(state.current_state);
  const canReview = ["SODA_REVIEW", "SODA_PENDING", "STTM_APPROVED", "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_FAILED"].includes(state.current_state);
  return (
    <StageGate state={state} stage="SODA">
      <Card>
        <CardHeader>
          <CardTitle>Data Quality</CardTitle>
          <CardDescription>
            Checks come from the approved STTM, the client brief and the data profile of each source column,
            with the evidence for every threshold. Backtest them on today&apos;s data, confirm each one, then
            download SodaCL or a Great Expectations suite. Approving a check is not the pack gate.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {canGenerate && (
            <StageAction
              label="Generate Data Quality checks"
              pendingLabel="Building SodaCL from the STTM and client brief…"
              action={generateSoda.bind(null, params.runId)}
            />
          )}
          {soda.status.total > 0 && (
            <p className="text-sm text-muted-foreground">
              {soda.status.approved} approved · {soda.status.proposed} still to confirm · {soda.status.rejected} rejected
            </p>
          )}
          <SodaGate
            runId={params.runId}
            currentState={state.current_state}
            complete={soda.status.total > 0 && soda.status.proposed === 0}
            proposed={soda.status.proposed}
            approved={soda.status.approved}
            rejected={soda.status.rejected}
          />
          <SodaBoard
            runId={params.runId}
            checks={soda.checks}
            yaml={soda.yaml}
            gxSuite={soda.gx_suite}
            brief={soda.brief}
            sttmLines={contract.lines}
            canImport={canImport}
            canReview={canReview}
          />
        </CardContent>
      </Card>
    </StageGate>
  );
}
