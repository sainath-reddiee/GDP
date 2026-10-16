"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { Check, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { decideFix } from "@/app/ops/resolve-actions";

/** Approve or reject running a known fix on a failure (human approval gate). Rejecting needs a reason. The worker
 *  runs an approved fix within seconds. */
export function FixDecision({ approvalId, fixTitle, approver, compact = false }: {
  approvalId: string; fixTitle: string | null | undefined; approver: string | null | undefined; compact?: boolean;
}) {
  const router = useRouter();
  const [mode, setMode] = useState<"approve" | "reject" | null>(compact ? null : "approve");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [pending, start] = useTransition();

  const submit = (approve: boolean) => start(async () => {
    setError("");
    const r = await decideFix(approvalId, approve, note.trim());
    if (!r.ok) { setError(r.error); return; }
    setDone(approve ? "Approved. The fix runs within a few seconds." : "Rejected. The failure is closed and stays on the incident.");
    router.refresh();
  });

  if (done) return <p role="status" className="rounded-lg bg-success/10 px-3 py-2 text-xs text-success">{done}</p>;
  return (
    <div data-testid="fix-decision" className="space-y-2 rounded-xl border border-human/30 bg-human/[0.04] p-3">
      <p className="text-xs text-muted-foreground">
        Run <span className="font-semibold text-foreground">{fixTitle ?? "the known fix"}</span> on this failure?
        {approver ? <> Routed to <span className="font-medium text-foreground">{approver}</span>.</> : " Any fix approver can decide."}
      </p>
      {mode && (
        <Textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} className="text-xs" aria-label="Decision note"
                  placeholder={mode === "reject" ? "Why the fix should not run (required)" : "Note (optional)"} />
      )}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" className="bg-human text-white hover:bg-human/90" disabled={pending}
                onClick={() => (mode === "approve" ? submit(true) : setMode("approve"))}>
          {pending && mode === "approve" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Approve and run
        </Button>
        <Button size="sm" variant="outline" disabled={pending || (mode === "reject" && !note.trim())}
                onClick={() => (mode === "reject" ? submit(false) : setMode("reject"))}>
          {pending && mode === "reject" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <X className="h-3.5 w-3.5" />}Reject
        </Button>
      </div>
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
