// Plain words for the resolution engine's states (services/resolve/rules.py), shared by the Resolutions and Known
// fixes pages, the incident page and My Work.

export const FAILURE_STATUS: Record<string, { label: string; tone: string; hint: string }> = {
  PENDING: { label: "Queued", tone: "bg-slate-100 text-slate-700", hint: "Waiting to be picked up (or automatic resolution is off)" },
  IN_PROGRESS: { label: "Working", tone: "bg-agent/10 text-agent", hint: "The agent is checking the guardrails and searching for a fix" },
  AWAITING_APPROVAL: { label: "Needs approval", tone: "bg-human/10 text-human", hint: "A known fix was found and waits for a person" },
  RESOLVED: { label: "Fixed", tone: "bg-success/10 text-success", hint: "A known fix ran and succeeded" },
  TICKETED: { label: "Ticketed", tone: "bg-amber-100 text-amber-800", hint: "No known fix: the cause was analysed and a ticket opened" },
  REJECTED: { label: "Rejected", tone: "bg-rose-100 text-rose-700", hint: "The approver decided the fix should not run" },
  OUT_OF_SCOPE: { label: "Out of scope", tone: "bg-muted text-muted-foreground", hint: "The pipeline is not in scope for automatic resolution" },
};

export const FLOW_MODES: { value: "AUTO" | "APPROVAL_ALWAYS" | "TICKET_ONLY" | "DISABLED"; label: string; hint: string }[] = [
  { value: "AUTO", label: "Automatic", hint: "Run a known fix; ask for approval only when the fix requires it" },
  { value: "APPROVAL_ALWAYS", label: "Always ask", hint: "Every known fix waits for a person's approval" },
  { value: "TICKET_ONLY", label: "Ticket only", hint: "Never run a fix: analyse the cause and open a ticket" },
  { value: "DISABLED", label: "Off", hint: "Leave this pipeline alone" },
];

export const flowLabel = (mode: string | null | undefined) => FLOW_MODES.find((m) => m.value === mode)?.label ?? "Not in scope";

export const FIX_STATUS: Record<string, { label: string; tone: string }> = {
  DRAFT: { label: "Draft", tone: "bg-slate-100 text-slate-700" },
  PUBLISHED: { label: "Published", tone: "bg-success/10 text-success" },
  RETIRED: { label: "Retired", tone: "bg-muted text-muted-foreground" },
};

/** A permanent fix proposal (V066): what it is waiting for, in plain words. */
export const PERMANENT_FIX_STATUS: Record<string, { label: string; tone: string; hint: string }> = {
  UNAVAILABLE: { label: "Needs mapping", tone: "bg-amber-100 text-amber-800", hint: "The platform could not find the files to change" },
  DRAFTED: { label: "Waiting for review", tone: "bg-human/10 text-human", hint: "A second person accepts or rejects it" },
  ACCEPTED: { label: "Accepted", tone: "bg-sky-100 text-sky-800", hint: "Ready to open its pull request" },
  REJECTED: { label: "Rejected", tone: "bg-muted text-muted-foreground", hint: "A reviewer did not take it" },
  PUBLISHED: { label: "Pull request open", tone: "bg-violet-100 text-violet-800", hint: "Waiting for the repository's reviewers" },
  MERGED: { label: "Merged", tone: "bg-success/10 text-success", hint: "The change is in the repository" },
  CLOSED: { label: "Closed", tone: "bg-muted text-muted-foreground", hint: "The pull request closed without merging" },
  SUPERSEDED: { label: "Replaced", tone: "bg-muted text-muted-foreground", hint: "A newer draft replaced it" },
};

export const score =(v: number | null | undefined) => (v === null || v === undefined ? "-" : `${Math.round(v * 100)}%`);
