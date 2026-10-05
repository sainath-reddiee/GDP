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
  const [note, setNote] = useState("STTM matches the approved mappings and is ready for Data Quality checks.");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();

  if (currentState === "STTM_APPROVED" || currentState === "SODA_PENDING") {
    return (
      <Card className="border-primary/30">
        <CardHeader>
          <CardTitle>STTM approved</CardTitle>
          <CardDescription>Data Quality and dbt both open from this contract. Run them in either order.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Button onClick={() => router.push(`/runs/${runId}/soda`)}>Open Data Quality</Button>
          <Button variant="outline" onClick={() => router.push(`/runs/${runId}/dbt`)}>Open dbt</Button>
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
          This is the pack gate. Approving unlocks Data Quality and dbt in parallel.
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
          {pending ? "Approving STTM…" : "Approve STTM (opens Data Quality and dbt)"}
        </Button>
      </CardContent>
    </Card>
  );
}
