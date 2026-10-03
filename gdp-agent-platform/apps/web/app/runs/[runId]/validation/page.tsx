import { api, getRun } from "@/lib/api";
import { StageGate } from "@/components/stage-gate";
import { StageAction } from "@/components/stage-action";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { validateDbt } from "../pipeline-actions";

export default async function ValidationPage({ params }: { params: { runId: string } }) {
  const [state, { runs }] = await Promise.all([
    getRun(params.runId),
    api<{ runs: {
      validation_id: string; validation_type: string; status: string; error_count: number;
      warning_count: number; result_json: unknown; started_at: string;
    }[] }>(`/api/runs/${params.runId}/validation`),
  ]);
  const canRun = ["VALIDATION_PENDING", "VALIDATION_FAILED"].includes(state.current_state);
  return (
    <StageGate state={state} stage="VALIDATION">
      <Card>
        <CardHeader>
          <CardTitle>Validation</CardTitle>
          <CardDescription>
            Naming, required columns, STTM consistency, schema.yml and SodaCL are checked in process. dbt compile
            runs with WRITEBACK=FALSE when dbt project objects are available; if they are not, that check is recorded
            as ERROR and the deterministic results still decide the stage.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {canRun && (
            <StageAction
              label="Run validation"
              pendingLabel="Validating…"
              action={validateDbt.bind(null, params.runId)}
            />
          )}
        </CardContent>
      </Card>
      {runs.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Results</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <THead><TR><TH>Type</TH><TH>Status</TH><TH>Errors</TH><TH>Warnings</TH><TH>When</TH></TR></THead>
              <TBody>
                {runs.map((r) => (
                  <TR key={r.validation_id}>
                    <TD className="font-medium">{r.validation_type}</TD>
                    <TD><Badge variant={r.status === "PASSED" ? "success" : r.status === "ERROR" ? "outline" : "destructive"}>{r.status}</Badge></TD>
                    <TD>{r.error_count}</TD>
                    <TD>{r.warning_count}</TD>
                    <TD className="text-muted-foreground">{r.started_at?.slice(0, 19)}</TD>
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
