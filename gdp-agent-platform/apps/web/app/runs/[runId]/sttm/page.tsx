import { api, getRun } from "@/lib/api";
import { AiSuggestions } from "@/components/ai-suggestions";
import { StageGate } from "@/components/stage-gate";
import { StageAction } from "@/components/stage-action";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { SttmFork } from "@/components/sttm-fork";
import { generateSttm } from "../pipeline-actions";
import { SttmBoard, type ProfileCol, type SttmLine } from "./sttm-board";
import { SttmGate } from "./sttm-gate";
import { JoinPlan, type JoinGraph } from "./join-plan";

export default async function SttmPage({ params }: { params: { runId: string } }) {
  const [state, contract, profile] = await Promise.all([
    getRun(params.runId),
    api<{ sttm: { sttm_id: string; sttm_version: number; status: string; table_design: unknown; created_by: string } | null;
          lines: SttmLine[];
        }>(`/api/runs/${params.runId}/sttm`),
    api<{ columns: ProfileCol[] }>(`/api/runs/${params.runId}/profile`).catch(() => ({ columns: [] })),
  ]);
  const canGenerate = ["MAPPING_APPROVED", "STTM_PENDING"].includes(state.current_state);
  const canEdit = ["STTM_REVIEW", "STTM_PENDING"].includes(state.current_state);
  const raw = contract.sttm?.table_design;
  const design = (typeof raw === "string" ? JSON.parse(raw) : raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const graph = (design.join_graph ?? null) as JoinGraph | null;
  const preview = contract.sttm
    ? await api<{ sql: string }>(`/api/runs/${params.runId}/sttm/preview`).catch(() => null)
    : null;
  const tables = Array.from(new Set([
    ...(graph?.driving_table ? [graph.driving_table] : []),
    ...(graph?.joins ?? []).map((j) => j.right_table),
    ...(graph?.unreachable ?? []),
    ...contract.lines.map((l) => String((l as { source_table?: string | null }).source_table ?? "").toUpperCase()).filter(Boolean),
  ]));
  return (
    <StageGate state={state} stage="STTM">
      <Card>
        <CardHeader>
          <CardTitle>STTM</CardTitle>
          <CardDescription>
            Table-level contract assembled from approved mappings. Refine a line in natural language — Cortex
            sees the profile, current SQL, and stored rules. After approval the contract fans out into two
            parallel flows.
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
          {contract.sttm && (
            <div className="mt-4 space-y-1 text-sm">
              <div>Version {contract.sttm.sttm_version} <Badge variant="outline">{contract.sttm.status}</Badge></div>
              <div>Grain: {String(design.grain ?? "—")}</div>
              <div>Business keys: {JSON.stringify(design.business_keys ?? [])}</div>
              <div>SCD: {String(design.scd_type ?? "—")} · incremental {String(design.incremental_strategy ?? "—")}</div>
            </div>
          )}
        </CardContent>
      </Card>
      {graph && (
        <JoinPlan key={JSON.stringify(graph)} runId={params.runId} graph={graph} tables={tables}
                  previewSql={preview?.sql ?? null} canEdit={state.current_state === "STTM_REVIEW" && !state.is_archived} />
      )}
      {(state.current_state === "STTM_REVIEW" || state.current_state === "STTM_APPROVED"
        || state.current_state.startsWith("SODA") || state.current_state.startsWith("DBT")
        || state.current_state.startsWith("VALIDATION")) && (
        <SttmFork runId={params.runId} currentState={state.current_state} />
      )}
      <SttmGate
        runId={params.runId}
        currentState={state.current_state}
        lineCount={contract.lines.length}
      />
      {contract.lines.length > 0 && (
        <SttmBoard
          runId={params.runId}
          lines={contract.lines}
          profiles={profile.columns}
          canEdit={canEdit}
        />
      )}
      <AiSuggestions runId={params.runId} stage="STTM" canAct={canEdit} />
    </StageGate>
  );
}
