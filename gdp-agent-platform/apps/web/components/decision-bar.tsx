"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Check, Loader2, Undo2, UserCheck } from "lucide-react";
import { approveStage } from "@/app/runs/[runId]/actions";
import { useAccess } from "@/components/access";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { stateLabel } from "@/lib/run-labels";
import { cn } from "@/lib/utils";

/**
 * The human approval gate as one bar pinned to the bottom of the screen, so the decision is always in reach while the
 * reviewer reads the evidence above it. Approve moves the run to `approveTo`; Send back moves it to one of `sendBack`
 * with REQUEST_CHANGES. Both need a written justification (the API records it with the reviewer). The agent never
 * gets here: only a person with the gate's privilege can decide, anything else becomes a change request.
 */
export function DecisionBar({ runId, gate, role, approveTo, approveLabel = "Approve", sendBack = [], defaultNote = "",
  reviewHref, afterApprove, detail }: {
  runId: string;
  gate: string;
  role?: string;
  approveTo: string;
  approveLabel?: string;
  sendBack?: string[];
  defaultNote?: string;
  /** a link to where the evidence is reviewed, when the bar is shown somewhere else (the run overview) */
  reviewHref?: string;
  /** where to go after approving; stays on the page (refreshed) otherwise */
  afterApprove?: string;
  detail?: string;
}) {
  const router = useRouter();
  const { readOnly } = useAccess();
  const [mode, setMode] = useState<"approve" | "send">("approve");
  const [note, setNote] = useState(defaultNote);
  const [back, setBack] = useState(sendBack[0] ?? "");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();

  const submit = () => start(async () => {
    setError("");
    const approving = mode === "approve";
    const r = await approveStage(runId, approving ? approveTo : back, note.trim(), approving ? "APPROVE" : "REQUEST_CHANGES");
    if (!r.ok) { setError(r.error); return; }
    if (approving && afterApprove) router.push(afterApprove);
    else router.refresh();
  });

  return (
    <div className="sticky bottom-4 z-20" data-testid="decision-bar" data-decision-bar>
      <div className="overflow-hidden rounded-2xl border border-human/40 bg-card shadow-[0_18px_50px_-18px_hsl(var(--human)/0.55)]">
        <div className="flex flex-wrap items-center gap-3 border-l-4 border-human px-4 py-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-human/10 text-human"><UserCheck className="h-[18px] w-[18px]" /></span>
          <div className="min-w-[12rem] flex-1">
            <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-human">
              {mode === "approve" ? "Awaiting human approval" : "Send back for changes"}
            </p>
            <p className="text-sm font-semibold">{gate}{role && <span className="font-normal text-muted-foreground"> · {role}</span>}</p>
            {detail && <p className="text-xs text-muted-foreground">{detail}</p>}
          </div>
          {reviewHref && (
            <Link href={reviewHref} className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline">
              Review the evidence <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          )}
        </div>
        <div className="flex flex-wrap items-end gap-2 border-t bg-muted/30 px-4 py-3">
          <label className="min-w-[16rem] flex-1 space-y-1 text-xs font-medium">
            {mode === "approve" ? "Business justification" : "What needs to change"}
            <Textarea rows={1} value={note} onChange={(e) => setNote(e.target.value)} className="min-h-[38px] resize-y text-sm"
                      placeholder={mode === "approve" ? "Why this is ready to approve" : "Tell the next run what to fix"} />
          </label>
          {mode === "send" && sendBack.length > 1 && (
            <label className="space-y-1 text-xs font-medium">To
              <select value={back} onChange={(e) => setBack(e.target.value)} className="flex h-[38px] rounded-lg border bg-card px-2 text-sm">
                {sendBack.map((s) => <option key={s} value={s}>{stateLabel(s)}</option>)}
              </select>
            </label>
          )}
          {mode === "approve" ? (
            <>
              {sendBack.length > 0 && (
                <Button variant="outline" disabled={pending} onClick={() => { setMode("send"); setNote(""); setError(""); }}>
                  <Undo2 className="h-4 w-4" />Send back
                </Button>
              )}
              <Button onClick={submit} disabled={pending || readOnly || !note.trim()} title={readOnly ? "View-only access" : undefined}
                      className="bg-human text-white hover:bg-human/90">
                {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}{approveLabel}
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" disabled={pending} onClick={() => { setMode("approve"); setNote(defaultNote); setError(""); }}>Cancel</Button>
              <Button onClick={submit} disabled={pending || readOnly || !note.trim() || !back} variant="outline"
                      className={cn("border-human/50 text-human hover:bg-human/10")}>
                {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Undo2 className="h-4 w-4" />}Send back
              </Button>
            </>
          )}
        </div>
        {error && <p role="alert" className={cn("border-t px-4 py-2 text-sm", error.startsWith("Sent to ") ? "text-violet-700 dark:text-violet-300" : "text-destructive")}>{error}</p>}
      </div>
    </div>
  );
}
