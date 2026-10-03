import { api, getRun } from "@/lib/api";
import { StageGate } from "@/components/stage-gate";
import { StageAction } from "@/components/stage-action";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { generateSttm } from "../pipeline-actions";

export default async function SttmPage({ params }: { params: { runId: string } }) {
  const [state, { sttm, lines }] = await Promise.all([
    getRun(params.runId),
    api<{ sttm: { sttm_id: string; sttm_version: number; status: string; table_design: unknown; created_by: string } | null;
          lines: {
            sttm_line_id: string; source_table: string | null; source_column: string | null;
            target_column: string; target_datatype: string; mapping_type: string;
            transformation: string | null; business_definition: string | null; human_approved: boolean;
          }[];
        }>(`/api/runs/${params.runId}/sttm`),
  ]);
  const canGenerate = ["MAPPING_APPROVED", "STTM_PENDING"].includes(state.current_state);
  const design = (sttm?.table_design && typeof sttm.table_design === "object" ? sttm.table_design : {}) as Record<string, unknown>;
  return (
    <StageGate state={state} stage="STTM">
      <Card>
        <CardHeader>
          <CardTitle>STTM</CardTitle>
          <CardDescription>
            Table-level contract assembled from approved mappings. System-derived columns (surrogate key, record
            source, loaded-at) are filled automatically. Required unmapped columns block generation.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {canGenerate && (
            <StageAction
              label="Generate STTM"
              pendingLabel="Assembling STTM…"
              action={generateSttm.bind(null, params.runId)}
            />
          )}
          {sttm && (
            <div className="mt-4 space-y-1 text-sm">
              <div>Version {sttm.sttm_version} <Badge variant="outline">{sttm.status}</Badge></div>
              <div>Grain: {String(design.grain ?? "—")}</div>
              <div>Business keys: {JSON.stringify(design.business_keys ?? [])}</div>
              <div>SCD: {String(design.scd_type ?? "—")} · incremental {String(design.incremental_strategy ?? "—")}</div>
            </div>
          )}
        </CardContent>
      </Card>
      {lines.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Lines</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <THead><TR><TH>Target</TH><TH>Type</TH><TH>Mapping</TH><TH>Source</TH><TH>Transformation</TH><TH>Approved</TH></TR></THead>
              <TBody>
                {lines.map((l) => (
                  <TR key={l.sttm_line_id}>
                    <TD className="font-medium">{l.target_column}</TD>
                    <TD className="font-mono text-xs">{l.target_datatype}</TD>
                    <TD><Badge variant="outline">{l.mapping_type}</Badge></TD>
                    <TD>{l.source_table ? `${l.source_table}.${l.source_column}` : "—"}</TD>
                    <TD className="font-mono text-xs">{l.transformation}</TD>
                    <TD>{l.human_approved ? "yes" : ""}</TD>
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
