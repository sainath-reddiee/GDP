"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { Bot, Check, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { decideFix, type SkillStep } from "@/app/ops/resolve-actions";

/** The step's plain-words form: "ADD_COLUMN: ..." notes come from the catalogue (services/resolve/actions.py). */
function stepText(s: SkillStep) {
  let input: Record<string, unknown> = {};
  try { input = typeof s.input === "string" ? JSON.parse(s.input) : (s.input ?? {}); } catch { /* shown as is */ }
  const action = (s.note ?? s.action).split(":")[0];
  const parts = Object.entries(input).filter(([, v]) => v !== null && v !== "").map(([k, v]) => `${k} ${Array.isArray(v) ? v.join(", ") : v}`);
  return `${action}${parts.length ? ` · ${parts.join(" · ")}` : ""}`;
}

/** Approve or reject running a known fix on a failure (human approval gate). Rejecting needs a reason. The worker
 *  runs an approved fix within seconds. For an agent proposal (KIND PROPOSAL), approving also publishes the fix, so
 *  the same failure is fixed automatically from then on. */
export function FixDecision({ approvalId, fixTitle, approver, compact = false, proposal }: {
  approvalId: string; fixTitle: string | null | undefined; approver: string | null | undefined; compact?: boolean;
  proposal?: { cause?: string | null; rationale?: string | null; steps?: SkillStep[] | null } | null;
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
    setDone(approve
      ? (proposal ? "Approved and published. It runs within a few seconds, and the same failure is fixed automatically from now on."
        : "Approved. The fix runs within a few seconds.")
      : (proposal ? "Rejected. The draft is retired and the cause goes on the ticket." : "Rejected. The failure is closed and stays on the incident."));
    router.refresh();
  });

  if (done) return <p role="status" className="rounded-lg bg-success/10 px-3 py-2 text-xs text-success">{done}</p>;
  return (
    <div data-testid="fix-decision" className="space-y-2 rounded-xl border border-human/30 bg-human/[0.04] p-3">
      {proposal ? (
        <div className="space-y-1.5 text-xs" data-testid="agent-proposal">
          <p className="flex items-center gap-1.5 font-semibold text-agent"><Bot className="h-3.5 w-3.5" />The agent proposes a fix</p>
          {proposal.cause && <p><span className="font-medium">Cause:</span> {proposal.cause}</p>}
          {!!proposal.steps?.length && (
            <ol className="list-decimal space-y-0.5 pl-4 font-mono text-[11px]">{proposal.steps.map((s) => <li key={s.step_no}>{stepText(s)}</li>)}</ol>
          )}
          {proposal.rationale && <p className="text-muted-foreground"><span className="font-medium text-foreground">Why:</span> {proposal.rationale}</p>}
          <p className="text-muted-foreground">Only safe catalogue actions, checked against Snowflake. Approving publishes it (you are the second person) and runs it now.
            {approver ? <> Routed to <span className="font-medium text-foreground">{approver}</span>.</> : ""}</p>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          Run <span className="font-semibold text-foreground">{fixTitle ?? "the known fix"}</span> on this failure?
          {approver ? <> Routed to <span className="font-medium text-foreground">{approver}</span>.</> : " Any fix approver can decide."}
        </p>
      )}
      {mode && (
        <Textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} className="text-xs" aria-label="Decision note"
                  placeholder={mode === "reject" ? "Why the fix should not run (required)" : "Note (optional)"} />
      )}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" className="bg-human text-white hover:bg-human/90" disabled={pending}
                onClick={() => (mode === "approve" ? submit(true) : setMode("approve"))}>
          {pending && mode === "approve" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
          {proposal ? "Approve, publish and run" : "Approve and run"}
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
