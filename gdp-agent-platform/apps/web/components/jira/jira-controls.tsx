"use client";

import { useRef, useState, useTransition } from "react";
import { ArrowRightLeft, Check, Loader2, Send, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select, Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { commentJira, jiraTransitions, transitionJira } from "@/app/jira/actions";

const CATEGORY_TONE: Record<string, string> = {
  done: "bg-emerald-50 text-emerald-700 ring-emerald-100", indeterminate: "bg-sky-50 text-sky-700 ring-sky-100", new: "bg-slate-100 text-slate-700 ring-slate-200",
};

/** A Jira status, coloured by its category (to do, in progress, done). */
export function StatusPill({ name, category }: { name: string | null; category: string | null }) {
  if (!name) return null;
  return <span className={cn("whitespace-nowrap rounded-full px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset", CATEGORY_TONE[category ?? "new"] ?? CATEGORY_TONE.new)}>{name}</span>;
}

export const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "");

/** Move an issue to another status, from the transitions Jira offers the signed-in user. */
export function TransitionControl({ issueKey, runId, onDone }: { issueKey: string; runId?: string; onDone: (to: string) => void }) {
  const [options, setOptions] = useState<{ id: string; name: string; to: string | null }[] | null>(null);
  const [choice, setChoice] = useState("");
  const [error, setError] = useState("");
  const [busy, start] = useTransition();
  const open = () => start(async () => {
    setError("");
    const r = await jiraTransitions(issueKey);
    if (r.ok) setOptions(r.data.transitions); else setError(r.error);
  });
  const picked = options?.find((o) => o.id === choice);
  if (!options) {
    return (
      <span className="inline-flex flex-wrap items-center gap-1.5">
        <Button size="sm" variant="ghost" disabled={busy} onClick={open}>{busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowRightLeft className="h-3.5 w-3.5" />}Change status</Button>
        {error && <span role="alert" className="text-xs text-destructive">{error}</span>}
      </span>
    );
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <Select value={choice} onChange={(e) => setChoice(e.target.value)} className="h-8 w-auto text-xs" aria-label="New status">
        <option value="">Move to…</option>
        {options.map((o) => <option key={o.id} value={o.id}>{o.name}{o.to && o.to !== o.name ? ` (to ${o.to})` : ""}</option>)}
      </Select>
      {picked && <Button size="sm" disabled={busy} onClick={() => start(async () => {
        setError("");
        const r = await transitionJira(issueKey, picked.id, runId);
        if (r.ok) { setOptions(null); setChoice(""); onDone(r.data.status); } else setError(r.error);
      })}>{busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Confirm: {picked.to ?? picked.name}</Button>}
      <button type="button" onClick={() => { setOptions(null); setChoice(""); }} className="rounded p-0.5 text-muted-foreground hover:bg-muted" aria-label="Cancel"><X className="h-3.5 w-3.5" /></button>
      {error && <span role="alert" className="text-xs text-destructive">{error}</span>}
    </span>
  );
}

/** Write a comment and post it to an issue as the signed-in user, after an explicit confirmation. */
export function CommentComposer({ issueKey, initial = "", me = "you", runId, onPosted, onCancel, rows = 8 }: {
  issueKey: string; initial?: string; me?: string; runId?: string; onPosted: (text: string) => void; onCancel?: () => void; rows?: number;
}) {
  const [text, setText] = useState(initial);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const [busy, start] = useTransition();
  const posting = useRef(false);
  const post = () => start(async () => {
    if (posting.current) return;
    posting.current = true;
    setError("");
    try {
      const r = await commentJira(issueKey, text, runId);
      setConfirming(false);
      if (!r.ok) { setError(r.error); return; }
      onPosted(`Comment posted to ${issueKey} as ${me}.`);
      setText("");
    } finally {
      posting.current = false;
    }
  });
  return (
    <div className="space-y-2 text-sm">
      <Textarea value={text} onChange={(e) => { setText(e.target.value); setConfirming(false); }} rows={rows}
                className="font-mono text-xs" aria-label={`Comment on ${issueKey}`} placeholder="Write the comment (Markdown)" />
      {!confirming && (
        <div className="flex flex-wrap items-center gap-2">
          {onCancel && <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>}
          <Button size="sm" className="ml-auto" disabled={!text.trim() || busy} onClick={() => setConfirming(true)}><Send className="h-3.5 w-3.5" />Post to {issueKey}</Button>
        </div>
      )}
      {confirming && (
        <div className="space-y-2 rounded-lg border border-primary/30 bg-primary/5 p-3">
          <p className="text-xs font-medium">Post this comment to {issueKey} as {me}? It is visible to everyone who can see the issue.</p>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-card p-2 text-[11px]">{text}</pre>
          <div className="flex gap-2">
            <Button size="sm" disabled={busy} onClick={post}>{busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}Yes, post it</Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirming(false)}>Cancel</Button>
          </div>
        </div>
      )}
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
