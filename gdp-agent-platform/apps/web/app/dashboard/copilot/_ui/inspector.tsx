"use client";

import { useState } from "react";
import { AlertTriangle, Check, FileText, Loader2, Minus, PanelRightClose, ShieldCheck, Ticket, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { KIND_LABEL, type Attachment, type Change, type PlanStep } from "@/lib/copilot-types";
import { StatusPill } from "./thread";

type Tab = "plan" | "changes" | "context";

/** The right panel: the plan as a stepper, the drafted changes with Approve and Publish, and the conversation's context. */
export function Inspector({ plan, changes, attachments, jiraKey, onReview, onPublish, publishing, onRemoveFile, onHide, busy }: {
  plan: PlanStep[]; changes: Change[]; attachments: Attachment[]; jiraKey?: string | null; busy: boolean;
  onReview: (id?: string) => void; onPublish: () => void; publishing: boolean; onRemoveFile: (id: string) => void; onHide: () => void;
}) {
  const [tab, setTab] = useState<Tab>("plan");
  const open = changes.filter((c) => c.status === "PROPOSED");
  const done = plan.filter((s) => s.status === "DONE" || s.status === "SKIPPED").length;
  return (
    <aside aria-label="Plan and changes" className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-1 border-b px-2 pt-2">
        {([["plan", "Plan"], ["changes", `Changes${open.length ? ` · ${open.length}` : ""}`], ["context", "Context"]] as [Tab, string][]).map(([k, label]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                  className={cn("-mb-px border-b-2 px-3 py-2 text-xs font-medium transition",
                    tab === k ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}>{label}</button>
        ))}
        <button type="button" onClick={onHide} aria-label="Hide the panel" className="ml-auto mb-1 grid h-8 w-8 place-items-center rounded-lg text-muted-foreground hover:bg-muted">
          <PanelRightClose className="h-4 w-4" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4 pb-24">
        {tab === "plan" && (
          <div className="space-y-4">
            <div>
              <div className="mb-1.5 flex items-baseline justify-between text-xs"><span className="font-medium">Progress</span><span className="text-muted-foreground">{done} of {plan.length}</span></div>
              <div className="h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary transition-all" style={{ width: `${(done / Math.max(plan.length, 1)) * 100}%` }} /></div>
            </div>
            <ol className="relative">
              {plan.map((s, i) => (
                <li key={s.key} className="relative flex gap-3 pb-4 last:pb-0">
                  {i < plan.length - 1 && <span className={cn("absolute left-[11px] top-6 h-[calc(100%-18px)] w-px", s.status === "DONE" ? "bg-primary/40" : "bg-border")} aria-hidden />}
                  <StepDot status={s.status} gate={s.key === "approval"} busy={busy} />
                  <div className="min-w-0 pt-0.5">
                    <p className={cn("text-sm", s.status === "PENDING" && "text-muted-foreground", s.status === "SKIPPED" && "text-muted-foreground line-through")}>{s.title}</p>
                    {s.note && <p className="text-[11px] text-muted-foreground">{s.note}</p>}
                    {s.key === "approval" && s.status === "RUNNING" && open.length > 0 && (
                      <button type="button" onClick={() => onReview()} className="mt-1 text-xs font-medium text-primary hover:underline">Review {open.length} change{open.length === 1 ? "" : "s"}</button>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          </div>
        )}

        {tab === "changes" && (
          <div className="space-y-2">
            {!changes.filter((c) => c.status !== "DISCARDED").length && <p className="py-8 text-center text-xs text-muted-foreground">Nothing drafted yet. Drafts appear here as the copilot works.</p>}
            {changes.filter((c) => c.status !== "DISCARDED").map((c) => (
              <button key={c.change_id} type="button" onClick={() => onReview(c.change_id)}
                      className="block w-full rounded-xl border border-border bg-card p-3 text-left transition hover:border-primary/30 hover:shadow-xs">
                <span className="flex items-center gap-2">
                  <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{KIND_LABEL[c.kind] ?? c.kind}</span>
                  <span className="ml-auto"><StatusPill status={c.status} /></span>
                </span>
                <span className="mt-1 block text-sm">{c.title}</span>
                {c.result?.detail && <span className="mt-1 block text-[11px] text-muted-foreground">{c.result.detail}</span>}
              </button>
            ))}
          </div>
        )}

        {tab === "context" && (
          <div className="space-y-5">
            <section>
              <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Jira</h3>
              {jiraKey ? <span className="inline-flex items-center gap-1.5 rounded-lg bg-sky-500/10 px-2 py-1 font-mono text-xs text-sky-700 dark:text-sky-300"><Ticket className="h-3.5 w-3.5" />{jiraKey}</span>
                : <p className="text-xs text-muted-foreground">No issue yet. Add one from the message box.</p>}
            </section>
            <section>
              <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Files</h3>
              {!attachments.length && <p className="text-xs text-muted-foreground">No files. Attach requirements, schema files or mapping sheets from the message box.</p>}
              <ul className="space-y-1.5">
                {attachments.map((a) => (
                  <li key={a.attachment_id} className="flex items-start gap-2 rounded-lg border border-border px-2.5 py-2 text-xs">
                    {a.note && !a.chars ? <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" /> : <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">{a.file_name}</span>
                      <span className="text-muted-foreground">{a.chars ? `${a.chars.toLocaleString()} characters read` : a.note}</span>
                    </span>
                    <button type="button" aria-label={`Remove ${a.file_name}`} onClick={() => onRemoveFile(a.attachment_id)} className="text-muted-foreground hover:text-foreground"><X className="h-3.5 w-3.5" /></button>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        )}
      </div>

      <div className="border-t bg-card/80 p-3 backdrop-blur">
        <button type="button" disabled={!open.length || busy || publishing} onClick={onPublish}
                className="flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-primary text-sm font-medium text-primary-foreground shadow-sm transition hover:opacity-95 disabled:bg-muted disabled:text-muted-foreground">
          {publishing ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
          {open.length ? `Review and publish ${open.length}` : "Nothing to publish"}
        </button>
        <p className="mt-1.5 text-center text-[11px] text-muted-foreground">Publishing follows your approval rules</p>
      </div>
    </aside>
  );
}

function StepDot({ status, gate, busy }: { status: PlanStep["status"]; gate: boolean; busy: boolean }) {
  const base = "relative z-10 grid h-6 w-6 shrink-0 place-items-center rounded-full border text-[10px]";
  if (status === "DONE") return <span className={cn(base, "border-primary bg-primary text-primary-foreground")}><Check className="h-3.5 w-3.5" /></span>;
  if (status === "RUNNING") return (
    <span className={cn(base, gate ? "border-amber-500 bg-amber-500/10 text-amber-600" : "border-primary bg-primary/10 text-primary")}>
      {gate ? <ShieldCheck className="h-3.5 w-3.5" /> : busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <span className="h-2 w-2 rounded-full bg-primary" />}
    </span>
  );
  if (status === "BLOCKED") return <span className={cn(base, "border-destructive bg-destructive/10 text-destructive")}><AlertTriangle className="h-3 w-3" /></span>;
  if (status === "SKIPPED") return <span className={cn(base, "border-border bg-muted text-muted-foreground")}><Minus className="h-3 w-3" /></span>;
  return <span className={cn(base, "border-border bg-background")} />;
}
