"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle2, Loader2, ShieldAlert, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { signOffQa, type QaSignoff } from "./qa-actions";

/** QA is its own lane: a tester approves or rejects the tests for the current STTM version. Code review stays
 *  closed until this is approved (with Data Quality and Validation). */
export function QaSignoffCard({ runId, signoff, canSign }: { runId: string; signoff: QaSignoff | null; canSign: boolean }) {
  const router = useRouter();
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();

  const decide = (decision: "APPROVED" | "REJECTED") => start(async () => {
    setError("");
    const r = await signOffQa(runId, decision, note);
    if (!r.ok) { setError(r.error); return; }
    setNote("");
    router.refresh();
  });

  const approved = signoff?.decision === "APPROVED";
  const rejected = signoff?.decision === "REJECTED";

  return (
    <section className="rounded-2xl border bg-card p-5 shadow-sm">
      <div className="flex flex-wrap items-start gap-3">
        {approved ? <CheckCircle2 className="mt-0.5 h-5 w-5 text-success" />
          : rejected ? <XCircle className="mt-0.5 h-5 w-5 text-destructive" />
          : <ShieldAlert className="mt-0.5 h-5 w-5 text-warning" />}
        <div className="min-w-0 flex-1">
          <h3 className="text-base font-semibold">QA sign-off</h3>
          <p className="text-sm text-muted-foreground">
            {approved ? `Approved by ${signoff.decided_by} on ${signoff.decided_at}.`
              : rejected ? `Rejected by ${signoff.decided_by} on ${signoff.decided_at}.`
              : "Run the tests below against the built model, then approve or reject them. Code review opens once QA, Data Quality and Validation are all done."}
          </p>
          {signoff?.note && <p className="mt-1 text-sm">“{signoff.note}”</p>}
        </div>
      </div>
      {canSign && (
        <div className="mt-4 space-y-2">
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} aria-label="QA sign-off note"
                    placeholder={approved ? "Note for a new decision (optional)" : "What you ran and what you found (required to reject)"}
                    className="w-full rounded-md border bg-background p-2 text-sm" />
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={pending} onClick={() => decide("APPROVED")}>
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
              Approve QA
            </Button>
            <Button size="sm" variant="outline" disabled={pending || !note.trim()} onClick={() => decide("REJECTED")}>
              <XCircle className="h-4 w-4" /> Reject
            </Button>
          </div>
        </div>
      )}
      {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
    </section>
  );
}
