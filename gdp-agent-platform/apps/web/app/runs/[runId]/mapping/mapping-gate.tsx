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
    return (
      <Card>
        <CardHeader>
          <CardTitle>Mapping pack is not ready</CardTitle>
          <CardDescription>
            {decided}/{total} source columns decided. STTM stays locked until every column is approved or left as NULL,
            and every required target has a source.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-1 text-sm text-muted-foreground">
          {undecided.length > 0 && <p>Still to review: {undecided.join(", ")}</p>}
          {missing.length > 0 && <p>Required targets with no source: {missing.join(", ")}</p>}
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
          Soda and dbt stay locked until that contract is approved.
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
