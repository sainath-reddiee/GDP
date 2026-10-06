"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label, Textarea } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { approveStage } from "../actions";

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
  const router = useRouter();
  const [note, setNote] = useState("All source columns reviewed. Required target columns are covered.");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();

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
    <Card className="border-primary/30">
      <CardHeader>
        <CardTitle>Approve the mapping pack</CardTitle>
        <CardDescription>
          Column decisions are saved. This gate moves the run to MAPPING_APPROVED and unlocks STTM.
          Data Quality and dbt stay locked until that contract is approved.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Label htmlFor="pack_why">Business justification</Label>
        <Textarea id="pack_why" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <Button
          disabled={pending || !note.trim()}
          onClick={() => start(async () => {
            setError("");
            const result = await approveStage(runId, "MAPPING_APPROVED", note.trim());
            if (!result.ok) {
              setError(result.error);
              return;
            }
            router.push(`/runs/${runId}/sttm`);
          })}
        >
          {pending && <Loader2 className="h-4 w-4 animate-spin" />}
          {pending ? "Approving pack…" : "Approve mapping and continue to STTM"}
        </Button>
      </CardContent>
    </Card>
  );
}
