"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition, type ReactNode } from "react";
import { BadgeCheck, Check, GitPullRequest, Loader2, RefreshCw, Send, ShieldCheck, Undo2, X } from "lucide-react";
import { approveStage } from "@/app/runs/[runId]/actions";
import { signOffQa } from "@/app/runs/[runId]/qa/qa-actions";
import { publishDbt, validateDbt } from "@/app/runs/[runId]/pipeline-actions";
import { moveLabel } from "@/app/skills/actions";
import { decideInbox, verifyKnowledge } from "@/app/knowledge/actions";
import { stateLabel } from "@/lib/run-labels";
import { cn } from "@/lib/utils";

// The decisions people take in place on the /os screens. Each one calls the server action the classic page uses, so
// the API applies the same governance: a person with the privilege acts; anyone else who may raise change requests
// gets "Sent to <role> for approval" back, shown as routed, not as an error. The server decides which of the two a
// person is (lib/os/access actMode) and passes it as `mode`; a preview of another role disables every control, since
// the API would act as the signed-in admin.

export type Mode = "do" | "request";
type Outcome = { tone: "done" | "routed" | "fail"; text: string } | null;
type Result = { ok: true } | { ok: false; error: string };

const roleName = (r?: string | null) => (r ? r.replace(/_/g, " ").toLowerCase() : "an approver");

function useDecision() {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [outcome, setOutcome] = useState<Outcome>(null);
  const run = (fn: () => Promise<Result>, done: string) => start(async () => {
    setOutcome(null);
    const r = await fn();
    if (r.ok) { setOutcome({ tone: "done", text: done }); router.refresh(); return; }
    // the API routed it to the approver role (a change request), as the classic pages show it
    if (r.error.startsWith("Sent to ")) { setOutcome({ tone: "routed", text: r.error }); router.refresh(); return; }
    setOutcome({ tone: "fail", text: r.error });
  });
  return { pending, outcome, run };
}

function OutcomeLine({ outcome }: { outcome: Outcome }) {
  if (!outcome) return null;
  const cls = outcome.tone === "done" ? "os-banner" : outcome.tone === "routed" ? "os-banner os-banner-blue" : "os-banner os-banner-red";
  return <div role={outcome.tone === "fail" ? "alert" : "status"} className={cn(cls, "mt-3 text-[12px]")}>{outcome.text}</div>;
}

function PreviewNote() {
  return <p className="os-faint mt-2 text-[11px]">Decisions are off while you preview another role: they would run as you.</p>;
}

function Btn({ children, onClick, disabled, variant = "default", title, testId }: {
  children: ReactNode; onClick: () => void; disabled?: boolean; variant?: "default" | "primary" | "warn" | "red" | "mint"; title?: string; testId?: string;
}) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={title} data-testid={testId}
            className={cn("os-btn", variant !== "default" && `os-btn-${variant}`)}>
      {children}
    </button>
  );
}

/* a human gate: approve, or send back with REQUEST_CHANGES; both need a written justification */

export function GateDecision({ runId, gate, role, approveTo, sendBack, mode, approver, previewing }: {
  runId: string; gate: string; role: string; approveTo: string; sendBack: string[]; mode: Mode; approver?: string | null; previewing: boolean;
}) {
  const { pending, outcome, run } = useDecision();
  const [note, setNote] = useState("");
  const [back, setBack] = useState(sendBack[0] ?? "");
  const ready = note.trim().length >= 3 && !pending && !previewing;
  return (
    <div data-testid="gate-decision">
      <label className="os-label" htmlFor={`note-${runId}`}>Business justification (recorded with your decision)</label>
      <textarea id={`note-${runId}`} value={note} onChange={(e) => setNote(e.target.value)} rows={3} disabled={previewing}
                placeholder={`Why this ${gate.toLowerCase()} is right, or what must change`} className="os-input resize-y" />
      {mode === "request" && (
        <p className="os-muted mt-2 text-[11.5px]"><Send className="mr-1 inline h-3 w-3" />You do not decide this gate. Your decision is sent to the {roleName(approver ?? role)} role for approval.</p>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Btn variant={mode === "do" ? "primary" : "warn"} disabled={!ready} testId="gate-approve"
             onClick={() => run(() => approveStage(runId, approveTo, note.trim(), "APPROVE"), `${gate} approved. The workflow continues.`)}>
          {pending ? <Loader2 className="h-3.5 w-3.5 os-spin" /> : <Check className="h-3.5 w-3.5" />}
          {mode === "do" ? "Approve" : "Request approval"}
        </Btn>
        {sendBack.length > 0 && (
          <>
            <Btn disabled={!ready || !back} testId="gate-send-back"
                 onClick={() => run(() => approveStage(runId, back, note.trim(), "REQUEST_CHANGES"), `Sent back to ${stateLabel(back).toLowerCase()}.`)}>
              <Undo2 className="h-3.5 w-3.5" /> {mode === "do" ? "Send back" : "Request changes"}
            </Btn>
            {sendBack.length > 1 && (
              <select value={back} onChange={(e) => setBack(e.target.value)} aria-label="Send back to" className="os-input w-auto py-1.5 text-[12px]" disabled={previewing}>
                {sendBack.map((s) => <option key={s} value={s}>to {stateLabel(s).toLowerCase()}</option>)}
              </select>
            )}
          </>
        )}
      </div>
      {previewing && <PreviewNote />}
      <OutcomeLine outcome={outcome} />
    </div>
  );
}

/* QA signoff of the run's test pack; a rejection needs a reason */

export function QaSignoffPanel({ runId, mode, previewing }: { runId: string; mode: Mode; previewing: boolean }) {
  const { pending, outcome, run } = useDecision();
  const [note, setNote] = useState("");
  const sign = (decision: "APPROVED" | "REJECTED") => run(async () => {
    const r = await signOffQa(runId, decision, note.trim());
    return r.ok ? { ok: true as const } : r;
  }, decision === "APPROVED" ? "QA signed off." : "QA rejected; the reason is recorded.");
  return (
    <div data-testid="qa-signoff">
      <input value={note} onChange={(e) => setNote(e.target.value)} disabled={previewing} aria-label="Signoff note"
             placeholder="Note (required to reject)" className="os-input" />
      <div className="mt-2 flex flex-wrap gap-2">
        <Btn variant={mode === "do" ? "mint" : "warn"} disabled={pending || previewing} onClick={() => sign("APPROVED")} testId="qa-approve">
          <BadgeCheck className="h-3.5 w-3.5" /> {mode === "do" ? "Sign off" : "Request signoff"}
        </Btn>
        <Btn variant="red" disabled={pending || previewing || note.trim().length < 3} onClick={() => sign("REJECTED")} testId="qa-reject">
          <X className="h-3.5 w-3.5" /> Reject
        </Btn>
      </div>
      {previewing && <PreviewNote />}
      <OutcomeLine outcome={outcome} />
    </div>
  );
}

/* publish the latest generation as a pull request, to the branch plan stored at generation */

export function PublishPr({ runId, mode, previewing, repo, base, cut }: {
  runId: string; mode: Mode; previewing: boolean; repo?: string; base?: string; cut?: string;
}) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [draft, setDraft] = useState(true);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const publish = () => start(async () => {
    setOutcome(null);
    const r = await publishDbt(runId, { draft });
    if (!r.ok) {
      setOutcome(r.error.startsWith("Sent to ") ? { tone: "routed", text: r.error } : { tone: "fail", text: r.error });
      return;
    }
    const pr = r.data.pull_request;
    setOutcome(pr?.url ? { tone: "done", text: `Pull request ${pr.number ? `#${pr.number} ` : ""}is open: ${pr.url}` }
      : r.data.status === "NO_CHANGES" ? { tone: "done", text: "The branch is up to date: nothing changed against the base branch." }
      : { tone: "fail", text: r.data.detail || r.data.status });
    router.refresh();
  });
  return (
    <div data-testid="publish-pr">
      <div className="os-muted text-[11.5px]">
        {repo ? <>Pushes <code className="text-[11px]">{cut}</code> to <b>{repo.replace(/^https?:\/\/(www\.)?github\.com\//, "")}</b>, cut from <code className="text-[11px]">{base}</code>.</> : "Pushes to the repository chosen at generation."}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <Btn variant={mode === "do" ? "primary" : "warn"} disabled={pending || previewing} onClick={publish} testId="publish-pr-button">
          {pending ? <Loader2 className="h-3.5 w-3.5 os-spin" /> : <GitPullRequest className="h-3.5 w-3.5" />}
          {mode === "do" ? "Publish pull request" : "Request publication"}
        </Btn>
        <label className="flex items-center gap-1.5 text-[12px]">
          <input type="checkbox" checked={draft} onChange={(e) => setDraft(e.target.checked)} disabled={previewing} className="accent-[var(--os-forest)]" /> Draft PR
        </label>
      </div>
      {previewing && <PreviewNote />}
      <OutcomeLine outcome={outcome} />
    </div>
  );
}

/* validate the current generation again (after a fix at the source or a better skill): VALIDATE_DBT scores it
   against the gate, and the attempt joins the run's history */

export function ValidateAgain({ runId, mode, previewing, variant = "primary" }: {
  runId: string; mode: Mode; previewing: boolean; variant?: "primary" | "default";
}) {
  const { pending, outcome, run } = useDecision();
  return (
    <div data-testid="validate-again">
      <Btn variant={mode === "do" ? variant : "warn"} disabled={pending || previewing} testId="validate-again-button"
           onClick={() => run(() => validateDbt(runId), "Validated again: the new score and the gate's verdict are below.")}
           title="Runs the independent checks on the current generated code and scores them against the gate">
        {pending ? <Loader2 className="h-3.5 w-3.5 os-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
        {pending ? "Validating" : mode === "do" ? "Validate again" : "Request validation"}
      </Btn>
      {previewing && <PreviewNote />}
      <OutcomeLine outcome={outcome} />
    </div>
  );
}

/* skill versions: make one the candidate, or promote it to production (a rollback is the same label move) */

export function SkillLabel({ name, skillId, version, label, mode, previewing, approver }: {
  name: string; skillId: string; version: string; label: "production" | "candidate"; mode: Mode; previewing: boolean; approver?: string | null;
}) {
  const { pending, outcome, run } = useDecision();
  const text = label === "production" ? (mode === "do" ? "Promote" : "Request promotion") : (mode === "do" ? "Make candidate" : "Request candidate");
  return (
    <span className="inline-flex flex-col items-end">
      <Btn variant={label === "production" ? (mode === "do" ? "mint" : "warn") : "default"} disabled={pending || previewing}
           title={previewing ? "Off while previewing another role" : mode === "request" ? `Sent to the ${roleName(approver)} role for approval` : `Move the ${label} label to v${version}`}
           onClick={() => run(async () => {
             const r = await moveLabel(name, label, skillId, `${label === "production" ? "Promoted" : "Made candidate"} from Agentic Data OS`);
             return r.ok ? { ok: true as const } : r;
           }, `v${version} is now ${label}.`)}
           testId={`skill-${label}-${skillId.slice(0, 8)}`}>
        {pending ? <Loader2 className="h-3 w-3 os-spin" /> : label === "production" ? <ShieldCheck className="h-3 w-3" /> : null}{text}
      </Btn>
      {outcome && <span className={cn("mt-1 max-w-[240px] text-right text-[10.5px]", outcome.tone === "fail" ? "text-[var(--os-red-ink)]" : outcome.tone === "routed" ? "text-[var(--os-blue-ink)]" : "text-[var(--os-green-ink)]")}>{outcome.text}</span>}
    </span>
  );
}

/* knowledge: verify a record (sets its next review), and decide agent proposals in the steward inbox */

export function VerifyKnowledge({ id, mode, previewing }: { id: string; mode: Mode; previewing: boolean }) {
  const { pending, outcome, run } = useDecision();
  return (
    <div data-testid="verify-knowledge">
      <Btn variant={mode === "do" ? "mint" : "warn"} disabled={pending || previewing}
           onClick={() => run(async () => { const r = await verifyKnowledge(id); return r.ok ? { ok: true as const } : r; }, "Verified; next review in 180 days.")}>
        <BadgeCheck className="h-3.5 w-3.5" /> {mode === "do" ? "Verify" : "Request verification"}
      </Btn>
      {previewing && <PreviewNote />}
      <OutcomeLine outcome={outcome} />
    </div>
  );
}

export function InboxDecision({ id, mode, previewing }: { id: string; mode: Mode; previewing: boolean }) {
  const { pending, outcome, run } = useDecision();
  const decide = (d: "approve" | "reject") => run(async () => {
    const r = await decideInbox([id], d);
    if (!r.ok) return r;
    // the API can refuse one record (your own proposal, for one) while the call itself succeeds
    const refused = r.data.refused?.find((x) => x.knowledge_id === id);
    return refused ? { ok: false as const, error: refused.reason } : { ok: true as const };
  }, d === "approve" ? "Approved into Enterprise Memory." : "Rejected.");
  if (outcome?.tone === "done") return <span className="text-[11px] text-[var(--os-green-ink)]">{outcome.text}</span>;
  return (
    <span className="inline-flex flex-col items-end gap-1">
      <span className="inline-flex gap-1.5">
        <Btn variant={mode === "do" ? "mint" : "warn"} disabled={pending || previewing} onClick={() => decide("approve")} testId={`inbox-approve-${id.slice(0, 8)}`}>
          <Check className="h-3 w-3" /> {mode === "do" ? "Approve" : "Request"}
        </Btn>
        <Btn disabled={pending || previewing} onClick={() => decide("reject")}><X className="h-3 w-3" /> Reject</Btn>
      </span>
      {outcome && <span className={cn("max-w-[240px] text-right text-[10.5px]", outcome.tone === "fail" ? "text-[var(--os-red-ink)]" : "text-[var(--os-blue-ink)]")}>{outcome.text}</span>}
    </span>
  );
}
