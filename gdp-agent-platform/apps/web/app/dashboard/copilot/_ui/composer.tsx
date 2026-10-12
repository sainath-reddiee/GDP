"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp, FileText, Link2, Loader2, Paperclip, Search, Ticket, TriangleAlert, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Attachment } from "@/lib/copilot-types";
import { connectJira, jiraIssues, jiraStatus, type JiraIssue, type JiraStatus } from "@/app/jira/actions";

export const ACCEPT = ".txt,.md,.csv,.tsv,.json,.jsonl,.yaml,.yml,.sql,.ddl,.xml,.avsc,.docx,.xlsx,.xlsm,.log";

export type Uploading = { key: string; name: string };

function size(bytes: number) {
  return bytes > 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** The message box: text, attached files and Jira issues, sent with Enter. */
export function Composer({ value, onChange, onSend, busy, disabled, files, uploading, onAttach, onRemoveFile,
  jiraKeys, onAddJira, onRemoveJira, placeholder, banner, onCancelBanner, hero, autoFocus }: {
  value: string; onChange: (v: string) => void; onSend: () => void; busy: boolean; disabled?: string;
  files: Attachment[]; uploading: Uploading[]; onAttach: (files: File[]) => void; onRemoveFile: (id: string) => void;
  jiraKeys: string[]; onAddJira: (key: string) => void; onRemoveJira: (key: string) => void;
  placeholder?: string; banner?: string | null; onCancelBanner?: () => void; hero?: boolean; autoFocus?: boolean;
}) {
  const box = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [jiraOpen, setJiraOpen] = useState(false);
  const canSend = !busy && !disabled && !uploading.length && (value.trim().length > 0 || files.length > 0 || jiraKeys.length > 0);

  useEffect(() => {  // grow with the text, up to a limit
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, hero ? 260 : 220)}px`;
  }, [value, hero]);
  useEffect(() => { if (autoFocus) box.current?.focus(); }, [autoFocus]);

  return (
    <div className={cn("rounded-2xl border border-border bg-card shadow-card transition focus-within:border-primary/40 focus-within:shadow-hover",
                       disabled && "opacity-60")}>
      {banner && (
        <div className="flex items-center gap-2 border-b border-border/70 px-4 py-2 text-xs text-muted-foreground">
          <span className="min-w-0 flex-1 truncate">{banner}</span>
          {onCancelBanner && <button type="button" onClick={onCancelBanner} className="font-medium text-primary hover:underline">Cancel</button>}
        </div>
      )}
      {(files.length > 0 || uploading.length > 0 || jiraKeys.length > 0) && (
        <div className="flex flex-wrap gap-1.5 px-3 pt-3">
          {jiraKeys.map((k) => (
            <span key={k} className="inline-flex items-center gap-1.5 rounded-lg border border-sky-500/30 bg-sky-500/10 px-2 py-1 text-xs text-sky-700 dark:text-sky-300">
              <Ticket className="h-3.5 w-3.5" /> <span className="font-mono">{k}</span>
              <button type="button" aria-label={`Remove ${k}`} onClick={() => onRemoveJira(k)} className="opacity-60 hover:opacity-100"><X className="h-3 w-3" /></button>
            </span>
          ))}
          {files.map((f) => (
            <span key={f.attachment_id} title={f.note ?? undefined}
                  className={cn("inline-flex max-w-[260px] items-center gap-1.5 rounded-lg border px-2 py-1 text-xs",
                    f.note && !f.chars ? "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-200" : "border-border bg-muted/50")}>
              {f.note && !f.chars ? <TriangleAlert className="h-3.5 w-3.5 shrink-0" /> : <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
              <span className="truncate">{f.file_name}</span>
              <span className="shrink-0 text-muted-foreground">{size(f.size_bytes)}</span>
              <button type="button" aria-label={`Remove ${f.file_name}`} onClick={() => onRemoveFile(f.attachment_id)} className="opacity-60 hover:opacity-100"><X className="h-3 w-3" /></button>
            </span>
          ))}
          {uploading.map((u) => (
            <span key={u.key} className="inline-flex items-center gap-1.5 rounded-lg border border-dashed px-2 py-1 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> {u.name}
            </span>
          ))}
        </div>
      )}
      <label htmlFor={hero ? "copilot-hero" : "copilot-message"} className="sr-only">Message the copilot</label>
      <textarea id={hero ? "copilot-hero" : "copilot-message"} ref={box} value={value} rows={hero ? 3 : 1} disabled={!!disabled}
                onChange={(e) => onChange(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); if (canSend) onSend(); } }}
                onPaste={(e) => { const pasted = Array.from(e.clipboardData.files); if (pasted.length) { e.preventDefault(); onAttach(pasted); } }}
                placeholder={placeholder ?? "Ask anything, or describe the change you need"}
                className={cn("block w-full resize-none bg-transparent px-4 text-sm leading-6 outline-none placeholder:text-muted-foreground/80",
                  hero ? "min-h-[84px] pt-4" : "min-h-[44px] pt-3")} />
      <div className="flex items-center gap-1 px-2.5 pb-2.5 pt-1">
        <input ref={picker} type="file" multiple accept={ACCEPT} className="hidden"
               onChange={(e) => { const list = Array.from(e.target.files ?? []); e.target.value = ""; if (list.length) onAttach(list); }} />
        <ToolButton label="Attach files" onClick={() => picker.current?.click()} disabled={!!disabled}><Paperclip className="h-4 w-4" /></ToolButton>
        <div className="relative">
          <ToolButton label="Add a Jira issue" onClick={() => setJiraOpen(!jiraOpen)} disabled={!!disabled} active={jiraOpen}>
            <Ticket className="h-4 w-4" />
          </ToolButton>
          {jiraOpen && <JiraPicker onPick={(k) => { onAddJira(k); setJiraOpen(false); }} onClose={() => setJiraOpen(false)} taken={jiraKeys} />}
        </div>
        <span className="ml-2 hidden text-[11px] text-muted-foreground sm:inline">Enter to send · Shift+Enter for a new line</span>
        <button type="button" onClick={onSend} disabled={!canSend} aria-label="Send"
                className="ml-auto grid h-8 w-8 place-items-center rounded-full bg-primary text-primary-foreground shadow-sm transition hover:opacity-90 disabled:bg-muted disabled:text-muted-foreground">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowUp className="h-4 w-4" />}
        </button>
      </div>
    </div>
  );
}

function ToolButton({ label, onClick, disabled, active, children }: {
  label: string; onClick: () => void; disabled?: boolean; active?: boolean; children: React.ReactNode;
}) {
  return (
    <button type="button" aria-label={label} title={label} onClick={onClick} disabled={disabled}
            className={cn("grid h-8 w-8 place-items-center rounded-lg text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:opacity-50",
              active && "bg-muted text-foreground")}>
      {children}
    </button>
  );
}

/** Pick a Jira issue: the person's open issues, or a search. Offers to connect when the person is not connected. */
function JiraPicker({ onPick, onClose, taken }: { onPick: (key: string) => void; onClose: () => void; taken: string[] }) {
  const [status, setStatus] = useState<JiraStatus | null | "error">(null);
  const [issues, setIssues] = useState<JiraIssue[] | null>(null);
  const [q, setQ] = useState("");
  const [error, setError] = useState("");
  const [connecting, setConnecting] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void jiraStatus().then((r) => setStatus(r.ok ? r.data : "error"));
    const away = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) onClose(); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [onClose]);

  const connected = status && status !== "error" && status.connected;
  useEffect(() => {
    if (!connected) return;
    const t = setTimeout(() => {
      const query = q.trim();
      void (query ? jiraIssues("search", { q: query }) : jiraIssues("mine")).then((r) => {
        if (r.ok) { setIssues(r.data.issues); setError(""); } else setError(r.error);
      });
    }, q ? 300 : 0);
    return () => clearTimeout(t);
  }, [connected, q]);

  const key = q.trim().toUpperCase();
  const typedKey = /^[A-Z][A-Z0-9_]*-\d+$/.test(key) ? key : null;

  return (
    <div ref={root} role="dialog" aria-label="Add a Jira issue"
         className="popover absolute bottom-full left-0 z-30 mb-2 w-[360px] max-w-[85vw] animate-fade-up overflow-hidden p-0">
      <div className="border-b px-3 py-2 text-xs font-semibold">Jira issue</div>
      {status === null && <p className="flex items-center gap-2 p-4 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Checking your Jira connection…</p>}
      {status === "error" && <p className="p-4 text-xs text-muted-foreground">Jira could not be reached.</p>}
      {status && status !== "error" && (!status.installed || !status.ready) && (
        <p className="p-4 text-xs text-muted-foreground">Jira is not set up on this platform yet. A platform admin connects it in Admin, Integrations.</p>
      )}
      {status && status !== "error" && status.installed && status.ready && !status.connected && (
        <div className="space-y-3 p-4">
          <p className="text-xs text-muted-foreground">Connect your Atlassian account once, and the copilot can read your issues and, after you approve, comment on them.</p>
          <button type="button" disabled={connecting}
                  onClick={async () => {
                    setConnecting(true);
                    const r = await connectJira(window.location.pathname);
                    if (r.ok) window.location.href = r.data.url; else { setError(r.error); setConnecting(false); }
                  }}
                  className="inline-flex h-8 items-center gap-2 rounded-lg bg-primary px-3 text-xs font-medium text-primary-foreground">
            {connecting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Link2 className="h-3.5 w-3.5" />} Connect Jira
          </button>
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
      )}
      {connected && (
        <>
          <label className="flex items-center gap-2 border-b px-3 py-2">
            <Search className="h-3.5 w-3.5 text-muted-foreground" />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search, or type a key such as GDP-123"
                   aria-label="Search Jira" className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground" />
          </label>
          <ul className="max-h-72 overflow-y-auto p-1">
            {typedKey && !taken.includes(typedKey) && (
              <li><button type="button" onClick={() => onPick(typedKey)} className="w-full rounded-lg px-2 py-2 text-left text-sm hover:bg-muted">
                Add <span className="font-mono">{typedKey}</span></button></li>
            )}
            {issues === null && !error && <li className="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading issues…</li>}
            {issues?.length === 0 && <li className="px-2 py-3 text-xs text-muted-foreground">{q ? "No matching issues." : "No open issues assigned to you."}</li>}
            {issues?.filter((i) => !taken.includes(i.key)).map((i) => (
              <li key={i.key}>
                <button type="button" onClick={() => onPick(i.key)} className="flex w-full items-start gap-2 rounded-lg px-2 py-2 text-left hover:bg-muted">
                  <span className="mt-0.5 shrink-0 font-mono text-xs text-primary">{i.key}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm">{i.summary}</span>
                    <span className="text-[11px] text-muted-foreground">{[i.type, i.status, i.priority].filter(Boolean).join(" · ")}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {error && <p className="border-t px-3 py-2 text-xs text-destructive">{error}</p>}
        </>
      )}
    </div>
  );
}
