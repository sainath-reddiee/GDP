"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Loader2, MessageSquare, PanelLeftClose, Plus, Search, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { SessionRow } from "@/lib/copilot-types";

function bucket(updated?: string): string {
  if (!updated) return "Earlier";
  const then = new Date(updated);
  const days = (Date.now() - then.getTime()) / 86_400_000;
  if (Number.isNaN(days)) return "Earlier";
  if (days < 1) return "Today";
  if (days < 7) return "This week";
  return "Earlier";
}

/** Conversations: newest first, grouped by age, with a quick filter. */
export function ConversationList({ sessions, current, onNew, onHide, onDelete }: {
  sessions: SessionRow[]; current: string | null; onNew: () => void; onHide: () => void;
  onDelete: (sessionId: string) => Promise<boolean>;
}) {
  const [q, setQ] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const remove = async (id: string) => {
    setDeleting(id);
    const ok = await onDelete(id);
    setDeleting(null);
    if (ok) setConfirming(null);
  };
  const groups = useMemo(() => {
    const found = sessions.filter((s) => !q || `${s.title} ${s.jira_key ?? ""}`.toLowerCase().includes(q.toLowerCase()));
    const out: [string, SessionRow[]][] = [];
    for (const s of found) {
      const b = bucket(s.updated_at);
      const g = out.find(([name]) => name === b);
      if (g) g[1].push(s); else out.push([b, [s]]);
    }
    return out;
  }, [sessions, q]);
  return (
    <nav aria-label="Conversations" className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 p-3">
        <button type="button" onClick={onNew}
                className="flex h-9 flex-1 items-center gap-2 rounded-xl border border-border bg-background px-3 text-sm font-medium shadow-xs transition hover:border-primary/40 hover:text-primary">
          <Plus className="h-4 w-4" /> New conversation
        </button>
        <button type="button" onClick={onHide} aria-label="Hide conversations"
                className="grid h-9 w-9 place-items-center rounded-xl text-muted-foreground hover:bg-muted hover:text-foreground">
          <PanelLeftClose className="h-4 w-4" />
        </button>
      </div>
      <div className="px-3 pb-2">
        <label className="flex h-8 items-center gap-2 rounded-lg bg-muted/60 px-2.5 text-xs text-muted-foreground focus-within:ring-2 focus-within:ring-primary/25">
          <Search className="h-3.5 w-3.5" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a conversation" aria-label="Find a conversation"
                 className="w-full bg-transparent text-foreground outline-none placeholder:text-muted-foreground" />
        </label>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {!sessions.length && <p className="px-2 py-6 text-center text-xs text-muted-foreground">Your conversations appear here.</p>}
        {groups.map(([name, rows]) => (
          <div key={name} className="mb-3">
            <p className="px-2 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/80">{name}</p>
            {rows.map((s) => confirming === s.session_id ? (
              <div key={s.session_id} role="group" aria-label={`Delete ${s.title}`}
                   className="mb-0.5 rounded-lg border border-destructive/30 bg-destructive/5 px-2.5 py-2 text-xs">
                <p className="truncate font-medium text-foreground">{s.title}</p>
                <p className="mt-0.5 text-muted-foreground">Delete this conversation? Its unpublished drafts are discarded; anything already published stays.</p>
                <div className="mt-2 flex gap-1.5">
                  <button type="button" disabled={deleting === s.session_id} onClick={() => void remove(s.session_id)}
                          className="inline-flex h-7 items-center gap-1 rounded-md bg-destructive px-2.5 font-medium text-destructive-foreground disabled:opacity-60">
                    {deleting === s.session_id ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />} Delete
                  </button>
                  <button type="button" disabled={deleting === s.session_id} onClick={() => setConfirming(null)}
                          className="h-7 rounded-md px-2.5 hover:bg-muted">Cancel</button>
                </div>
              </div>
            ) : (
              <div key={s.session_id} className="group relative">
              <Link href={`/os/work/copilot/${s.session_id}`}
                    aria-current={s.session_id === current ? "page" : undefined}
                    className={cn("flex items-start gap-2 rounded-lg py-2 pl-2 pr-8 text-sm transition",
                      s.session_id === current ? "bg-primary/10 text-foreground" : "text-foreground/80 hover:bg-muted")}>
                <MessageSquare className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", s.session_id === current ? "text-primary" : "text-muted-foreground")} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{s.title}</span>
                  {(s.jira_key || s.proposed) ? (
                    <span className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                      {s.jira_key && <span className="font-mono">{s.jira_key}</span>}
                      {!!s.proposed && <span className="rounded-full bg-amber-500/15 px-1.5 text-amber-700 dark:text-amber-300">{s.proposed} to review</span>}
                    </span>
                  ) : null}
                </span>
              </Link>
              <button type="button" aria-label={`Delete conversation ${s.title}`} title="Delete conversation"
                      onClick={() => setConfirming(s.session_id)}
                      className="absolute right-1.5 top-1.5 grid h-6 w-6 place-items-center rounded-md text-muted-foreground opacity-0 transition hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
              </div>
            ))}
          </div>
        ))}
      </div>
    </nav>
  );
}
