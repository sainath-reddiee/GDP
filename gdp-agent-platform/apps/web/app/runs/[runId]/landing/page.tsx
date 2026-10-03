import { api, getRun } from "@/lib/api";
import type { SourceOverview } from "@/lib/types";
import { StageGate } from "@/components/stage-gate";
import { StageAction } from "@/components/stage-action";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { executeLanding } from "../source-actions";

export default async function LandingPage({ params }: { params: { runId: string } }) {
  const [state, { landing }] = await Promise.all([
    getRun(params.runId),
    api<SourceOverview>(`/api/runs/${params.runId}/source`),
  ]);
  const canLand = ["ACCESS_APPROVED", "LANDING_PENDING"].includes(state.current_state);
  return (
    <StageGate state={state} stage="LANDING">
      <Card>
        <CardHeader>
          <CardTitle>Landing</CardTitle>
          <CardDescription>
            Each selected share or database table is loaded as-is into LANDING with CREATE TABLE … AS SELECT.
            COPY INTO reads files from a stage, so it is not used for a mounted Snowflake share. Source and landed
            row counts must match. Landed data is readable only by data stewards.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {canLand && (
            <StageAction
              label={state.current_state === "LANDING_PENDING" ? "Retry landing" : "Start landing"}
              pendingLabel="Landing objects… this can take a few minutes"
              action={executeLanding.bind(null, params.runId)}
            />
          )}
          {state.current_state === "FAILED" && state.failure_reason && (
            <p className="text-sm text-destructive">
              Landing failed: {state.failure_reason}. Retry from the overview, then start landing again.
            </p>
          )}
        </CardContent>
      </Card>
      {landing.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Landed tables</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <THead>
                <TR><TH>Source object</TH><TH>Landing table</TH><TH>Status</TH><TH>Source rows</TH><TH>Landed rows</TH><TH>Columns</TH><TH>When</TH></TR>
              </THead>
              <TBody>
                {landing.map((t) => (
                  <TR key={t.landing_id}>
                    <TD className="font-medium">{t.source_table}</TD>
                    <TD className="font-mono text-xs">{t.landing_table}</TD>
                    <TD>
                      <Badge variant={t.ingestion_status === "COMPLETE" ? "success" : "destructive"}>{t.ingestion_status}</Badge>
                      {t.error_message && <div className="mt-1 text-xs text-destructive">{t.error_message}</div>}
                    </TD>
                    <TD>{t.source_row_count ?? "-"}</TD>
                    <TD>{t.row_count ?? "-"}</TD>
                    <TD>{t.columns}</TD>
                    <TD className="text-muted-foreground">{t.created_at.slice(0, 19)}</TD>
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
