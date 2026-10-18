"use client";

import { useState, useTransition } from "react";
import { Clock, Loader2, Trash2, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { requestRemoval, requestRestore, type RemovalKind } from "@/app/removal-actions";

/** Ask to remove one item (a domain table, a source, a knowledge item). It always needs another person's approval: the
 *  reason goes to the approver, and until they decide the item stays as it is. Nothing is ever deleted. */
export function RequestRemoval({ kind, id, name, label = "Request removal", onDone, className }: {
  kind: RemovalKind; id: string; name: string; label?: string; onDone?: (pending: boolean) => void; className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [state, setState] = useState<{ tone: "pending" | "done" | "error"; text: string } | null>(null);
  const [busy, start] = useTransition();
  const submit = () => start(async () => {
    if (reason.trim().length < 5) { setState({ tone: "error", text: "Say why it should be removed (a few words)." }); return; }
    const r = await requestRemoval(kind, id, reason);
    if (!r.ok) { setState({ tone: "error", text: r.error }); return; }
    setOpen(false);
    setState(r.pending ? { tone: "pending", text: "Removal requested: waiting for approval" } : { tone: "done", text: "Removed" });
    onDone?.(r.pending);
  });

  if (state?.tone === "pending" || state?.tone === "done") {
    return (
      <span className={cn("inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium",
        state.tone === "pending" ? "bg-human/10 text-human" : "bg-muted text-muted-foreground", className)} role="status">
        {state.tone === "pending" && <Clock className="h-3 w-3" />}{state.text}
      </span>
    );
  }
  if (!open) {
    return (
      <Button variant="ghost" size="sm" className={cn("h-7 px-2 text-xs text-muted-foreground hover:text-destructive", className)}
              onClick={() => setOpen(true)} aria-label={`${label}: ${name}`}>
        <Trash2 className="h-3.5 w-3.5" /> {label}
      </Button>
    );
  }
  return (
    <div className={cn("w-full space-y-2 rounded-lg border bg-card p-3", className)}>
      <p className="text-xs">Remove <span className="font-medium">{name}</span>? Another person approves this first; nothing is deleted and it can be restored.</p>
      <Textarea value={reason} onChange={(e) => { setReason(e.target.value); setState(null); }} rows={2} maxLength={2000}
                placeholder="Why: for example, a test table that is not part of the company model" aria-label="Reason for removal" />
      {state?.tone === "error" && <p role="alert" className="text-xs text-destructive">{state.text}</p>}
      <div className="flex gap-2">
        <Button size="sm" variant="outline" className="border-destructive/40 text-destructive hover:bg-destructive/5" disabled={busy} onClick={submit}>
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />} Request removal
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setOpen(false); setState(null); }}>Cancel</Button>
      </div>
    </div>
  );
}

/** Ask to bring a removed item back; also approved by another person. */
export function RequestRestore({ kind, id, name }: { kind: RemovalKind; id: string; name: string }) {
  const [state, setState] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, start] = useTransition();
  if (state) return <span role="status" className="inline-flex items-center gap-1 rounded-md bg-human/10 px-2 py-0.5 text-xs font-medium text-human"><Clock className="h-3 w-3" />{state}</span>;
  return (
    <span className="inline-flex items-center gap-2">
      <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" disabled={busy} aria-label={`Request restore of ${name}`}
              onClick={() => start(async () => {
                const r = await requestRestore(kind, id);
                if (!r.ok) { setError(r.error); return; }
                setState(r.pending ? "Restore requested: waiting for approval" : "Restored");
              })}>
        {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Undo2 className="h-3.5 w-3.5" />} Restore
      </Button>
      {error && <span role="alert" className="text-xs text-destructive">{error}</span>}
    </span>
  );
}
