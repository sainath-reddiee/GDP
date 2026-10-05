"use client";

import { useState, useTransition } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label, Textarea } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { approveStage } from "../actions";

export function SodaGate({
  runId, currentState, complete, proposed, approved, rejected,
}: {
  runId: string;
  currentState: string;
  complete: boolean;
  proposed: number;
  approved: number;
  rejected: number;
}) {
  const [note, setNote] = useState("Data Quality checks match the STTM and the client quality need.");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();

  if (currentState === "SODA_APPROVED") {
    return (
      <Card className="border-primary/30">
        <CardHeader>
          <CardTitle>Data Quality pack approved</CardTitle>
          <CardDescription>
            Every current check has a decision. The pack gate is recorded. You can still regenerate
            checks from the STTM if the contract changes.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  if (currentState !== "SODA_REVIEW") return null;

  if (!complete) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Data Quality pack is not ready</CardTitle>
          <CardDescription>
            {approved} approved · {proposed} still to confirm · {rejected} rejected.
            Confirm or reject every proposed check, or mass-approve the remaining set.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card className="border-primary/30">
      <CardHeader>
        <CardTitle>Approve the Data Quality pack</CardTitle>
        <CardDescription>
          Per-check decisions are saved. This gate records the pack approval for this track.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Label htmlFor="soda_pack_why">Business justification</Label>
        <Textarea id="soda_pack_why" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <Button
          disabled={pending || !note.trim()}
          onClick={() => start(async () => {
            setError("");
            const result = await approveStage(runId, "SODA_APPROVED", note.trim());
            if (!result.ok) setError(result.error);
          })}
        >
          {pending && <Loader2 className="h-4 w-4 animate-spin" />}
          {pending ? "Approving Data Quality…" : "Approve Data Quality pack"}
        </Button>
      </CardContent>
    </Card>
  );
}
