import { api, getRun } from "@/lib/api";
import { StageGate } from "@/components/stage-gate";
import { StageAction } from "@/components/stage-action";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { generateSoda } from "../pipeline-actions";
import { ImportSodaForm } from "./import-form";

export default async function SodaPage({ params }: { params: { runId: string } }) {
  const [state, { checks }] = await Promise.all([
    getRun(params.runId),
    api<{ checks: {
      expectation_id: string; target_table: string; target_column: string | null; check_type: string;
      severity: string; origin: string; client_requirement: string | null; status: string; version: number;
    }[] }>(`/api/runs/${params.runId}/soda`),
  ]);
  const canGenerate = ["STTM_APPROVED", "SODA_PENDING"].includes(state.current_state);
  const canImport = ["STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW"].includes(state.current_state);
  return (
    <StageGate state={state} stage="SODA">
      <Card>
        <CardHeader>
          <CardTitle>Soda expectations</CardTitle>
          <CardDescription>
            Checks are derived from the approved STTM (row count, uniqueness, not-null, accepted values) plus any
            client rows you import. Nothing is published to Soda Cloud in Phase 1.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {canGenerate && (
            <StageAction
              label="Generate Soda checks"
              pendingLabel="Building expectations…"
              action={generateSoda.bind(null, params.runId)}
            />
          )}
          {canImport && <ImportSodaForm runId={params.runId} />}
        </CardContent>
      </Card>
      {checks.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Current checks</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <THead><TR><TH>Table</TH><TH>Column</TH><TH>Check</TH><TH>Severity</TH><TH>Origin</TH><TH>Status</TH><TH>Requirement</TH></TR></THead>
              <TBody>
                {checks.map((c) => (
                  <TR key={c.expectation_id}>
                    <TD>{c.target_table}</TD>
                    <TD>{c.target_column ?? "—"}</TD>
                    <TD>{c.check_type}</TD>
                    <TD><Badge variant={c.severity === "FAIL" ? "destructive" : "outline"}>{c.severity}</Badge></TD>
                    <TD>{c.origin}</TD>
                    <TD>{c.status}</TD>
                    <TD className="text-sm text-muted-foreground">{c.client_requirement}</TD>
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
