"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label, Textarea } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { approveStage } from "../actions";

export function SttmGate({
  runId, currentState, lineCount,
}: {
  runId: string;
  currentState: string;
  lineCount: number;
}) {
  const router = useRouter();
  const [note, setNote] = useState("STTM matches the approved mappings and is ready for Soda checks.");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();

  if (currentState === "STTM_APPROVED" || currentState === "SODA_PENDING") {
    return (
      <Card className="border-primary/30">
        <CardHeader>
          <CardTitle>STTM approved</CardTitle>
          <CardDescription>Soda and dbt generate from this contract in parallel after you start Soda.</CardDescription>
        </CardHeader>
        <CardContent>
          <Button onClick={() => router.push(`/runs/${runId}/soda`)}>Continue to Soda</Button>
        </CardContent>
      </Card>
    );
  }

  if (currentState !== "STTM_REVIEW" || lineCount === 0) return null;

  return (
    <Card className="border-primary/30">
      <CardHeader>
        <CardTitle>Approve the STTM</CardTitle>
        <CardDescription>
          This is the pack gate. Approving moves the run to STTM_APPROVED and unlocks Soda.
          Refine transforms above first if the SQL still needs a change.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Label htmlFor="sttm_why">Business justification</Label>
        <Textarea id="sttm_why" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <Button
          disabled={pending || !note.trim()}
          onClick={() => start(async () => {
            setError("");
            const result = await approveStage(runId, "STTM_APPROVED", note.trim());
            if (!result.ok) {
              setError(result.error);
              return;
            }
            router.push(`/runs/${runId}/soda`);
          })}
        >
          {pending && <Loader2 className="h-4 w-4 animate-spin" />}
          {pending ? "Approving STTM…" : "Approve STTM and continue to Soda"}
        </Button>
      </CardContent>
    </Card>
  );
}
