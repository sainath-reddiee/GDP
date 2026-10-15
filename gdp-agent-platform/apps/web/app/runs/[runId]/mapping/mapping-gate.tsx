"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DecisionBar } from "@/components/decision-bar";

export function MappingGate({
  runId, complete, decided, total, missing, undecided,
}: {
  runId: string;
  complete: boolean;
  decided: number;
  total: number;
  missing: string[];
  undecided: string[];
}) {
  if (!complete) {
    const pct = total ? Math.round((decided / total) * 100) : 0;
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-3">
            Mapping pack is not ready
            <span className="text-sm font-normal text-muted-foreground">{decided}/{total} decided · {pct}%</span>
          </CardTitle>
          <CardDescription>
            STTM unlocks when every source column is approved or set to NULL and every required target has a source.
            Use bulk approve or the AI copilot below to move faster.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <div className="h-2 overflow-hidden rounded-full bg-muted">
            <div className="h-full bg-emerald-500 transition-all" style={{ width: `${pct}%` }} />
          </div>
          {undecided.length > 0 && (
            <p className="text-muted-foreground">
              {undecided.length} still to review: {undecided.slice(0, 8).join(", ")}{undecided.length > 8 ? ` +${undecided.length - 8} more` : ""}
            </p>
          )}
          {missing.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-destructive">Required targets with no source:</span>
              {missing.map((m) => (
                <span key={m} className="rounded-full bg-destructive/10 px-2 py-0.5 text-xs text-destructive">{m}</span>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    );
  }

  return (
    <DecisionBar runId={runId} gate="Field mapping" role="Model approver" approveTo="MAPPING_APPROVED"
                 approveLabel="Approve mapping and continue to STTM" sendBack={["MAPPING_PENDING"]}
                 defaultNote="All source columns reviewed. Required target columns are covered."
                 detail="Every column is decided. Approving unlocks transformations and joins (STTM)."
                 afterApprove={`/runs/${runId}/modeling?tab=transformations`} />
  );
}
