"use client";

import Link from "next/link";
import { useState, useTransition, type ReactNode } from "react";
import { Loader2, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { Alert, jiraProblem, useLive, useSeq } from "../../qa-shared";
import { codeHref } from "../../../incidents/[id]/incident-ai";
import { jiraComment, type CaseCitation, type CaseFailed } from "../actions";
import { ConnectJiraLink } from "../case-ui";

// Small pieces shared by the case page's AI panel and proposals.

export const words = (s: string) => s.toLowerCase().replace(/_/g, " ");
export const msgOf = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);
export const Muted = ({ children }: { children: ReactNode }) => <p className="text-xs text-muted-foreground">{children}</p>;
export const Info = ({ children }: { children: ReactNode }) => <p role="status" className="rounded-lg bg-sky-50 px-3 py-2 text-xs text-sky-800">{children}</p>;
export const pct = (n: number | null | undefined) => (typeof n === "number" && Number.isFinite(n) ? `${Math.round(Math.min(1, Math.max(0, n)) * 100)}%` : null);
export const str = (v: unknown) => (typeof v === "string" ? v : typeof v === "number" ? String(v) : "");
export const list = <T,>(v: T[] | null | undefined): T[] => (Array.isArray(v) ? v : []);

/** Only app paths and http(s) links are followed; anything else from the model shows as text. */
export const safeUrl = (u: string | null | undefined) => (u && (/^\/(?!\/)/.test(u) || /^https?:\/\//i.test(u)) ? u : null);

/** A failed AI call: the per-user limit reads as a plain message, a held request as information. */
export function aiProblem(r: CaseFailed): { info: boolean; text: string } {
  if (r.status === 429) return { info: false, text: "AI limit reached, try again later." };
  if (r.status === 202 || /approval|request/i.test(r.error)) return { info: true, text: r.error };
  return { info: false, text: r.error };
}

/** Where a cited thing lives in the app: code by repository, path and line; a table by its id; a run, case or incident by id. */
export function refHref(kind: string | null | undefined, ref: string | null | undefined, extra?: Partial<CaseCitation>, tables?: Record<string, string>): string | null {
  const own = safeUrl(extra?.url);
  if (own) return own;
  const code = codeHref({ repo_id: extra?.repo_id, path: extra?.path, line: extra?.line });
  if (code) return code;
  if (!ref) return null;
  const k = (kind ?? "").toUpperCase();
  const enc = encodeURIComponent;
  if (k === "RUN") return `/runs/${enc(ref)}`;
  if (k === "CASE") return `/qa/cases/${enc(ref)}`;
  if (k === "INCIDENT") return `/incidents/${enc(ref)}`;
  if (k === "TABLE" || k === "TARGET_TABLE") {
    // a table id is only known when the ref is one, or when it names a table this case already knows by name
    const id = tables?.[ref.toUpperCase()] ?? (/^[\w-]{8,}$/.test(ref) && !ref.includes(".") ? ref : null);
    return id ? `/qa?${new URLSearchParams({ tab: "suites", table: id })}` : null;
  }
  return null;
}

export function RefLink({ kind, refText, label, extra, tables, className }: {
  kind?: string | null; refText?: string | null; label?: string; extra?: Partial<CaseCitation>; tables?: Record<string, string>; className?: string;
}) {
  const href = refHref(kind, refText, extra, tables);
  const text = label || refText || "";
  if (!text) return null;
  if (!href) return <span className={cn("min-w-0 truncate font-mono", className)} title={refText ?? undefined}>{text}</span>;
  return href.startsWith("/")
    ? <Link href={href} className={cn("min-w-0 truncate font-mono text-primary hover:underline", className)} title={refText ?? undefined}>{text}</Link>
    : <a href={href} target="_blank" rel="noreferrer" className={cn("min-w-0 truncate font-mono text-primary hover:underline", className)} title={refText ?? undefined}>{text}</a>;
}

export function KindTag({ kind }: { kind?: string | null }) {
  return <span className="shrink-0 rounded bg-muted px-1 text-[10px] text-muted-foreground">{words(kind || "ref")}</span>;
}

export function Chips({ label, items, mono }: { label: string; items: string[]; mono?: boolean }) {
  if (!items.length) return null;
  return (
    <div className="flex flex-wrap items-baseline gap-1">
      <span className="mr-1 text-muted-foreground">{label}</span>
      {items.map((m) => <span key={m} className={cn("rounded bg-muted px-1.5 py-0.5 text-[11px]", mono && "font-mono")}>{m}</span>)}
    </div>
  );
}

export function SqlBlock({ sql, label = "SQL" }: { sql: string; label?: string }) {
  return (
    <pre aria-label={label} className="max-h-72 overflow-auto rounded-lg bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-200">{sql}</pre>
  );
}

/** A unified diff as text: added lines green, removed red, hunk headers blue; long lines scroll sideways. */
export function UnifiedDiff({ diff }: { diff: string }) {
  return (
    <pre className="max-h-96 overflow-auto rounded-lg bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-200" aria-label="Diff">
      {diff.split("\n").map((line, i) => (
        <span key={i} className={cn("block min-w-max whitespace-pre", line.startsWith("+") && !line.startsWith("+++") ? "bg-emerald-950/60 text-emerald-300"
          : line.startsWith("-") && !line.startsWith("---") ? "bg-rose-950/60 text-rose-300" : line.startsWith("@@") ? "text-sky-300"
            : line.startsWith("+++") || line.startsWith("---") ? "text-slate-400" : undefined)}>{line || " "}</span>
      ))}
    </pre>
  );
}

/** Post a comment to the case's linked Jira issues, as the person acting. Two steps: write (or check) the text, then confirm. */
export function JiraPost({ caseId, label, initialText = "", hint, onPosted, className }: {
  caseId: string; label: string; initialText?: string; hint: string; onPosted: (text: string) => void; className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(initialText);
  const [error, setError] = useState("");
  const [connect, setConnect] = useState(false);
  const [pending, start] = useTransition();
  const live = useLive();
  const seq = useSeq();
  const post = () => start(async () => {
    const ticket = seq.next();
    setError(""); setConnect(false);
    try {
      const r = await jiraComment(caseId, text.trim() || undefined);
      if (!live.current || !seq.current(ticket)) return;
      if (!r.ok) {
        const p = jiraProblem({ ...r, retryAfter: r.retryAfter ?? null });
        setConnect(p.connect); setError(p.text);
        return;
      }
      const posted = r.data.filter((x) => x.posted).map((x) => x.key);
      const missed = r.data.filter((x) => !x.posted).map((x) => x.key);
      setOpen(false);
      onPosted(posted.length
        ? `Posted to ${posted.join(", ")}.${missed.length ? ` Not posted to ${missed.join(", ")}.` : ""}`
        : `Nothing was posted${missed.length ? ` to ${missed.join(", ")}` : ""}.`);
    } catch (e) {
      if (live.current && seq.current(ticket)) setError(msgOf(e, "Could not post to Jira."));
    }
  });
  if (!open) {
    return (
      <Button size="sm" variant="outline" className={className} onClick={() => { setText(initialText); setOpen(true); setError(""); }}>
        <Send className="h-3.5 w-3.5" />{label}</Button>
    );
  }
  return (
    <div className={cn("w-full space-y-2 rounded-lg border p-3 text-xs", className)}>
      <p className="font-medium">{label}</p>
      <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} maxLength={8000} className="text-xs" aria-label="Jira comment"
                placeholder="Optional. Leave empty to post the case status and summary." />
      <p className="text-muted-foreground">{hint} It posts as you to every Jira issue linked to this case. Counts and findings only; never paste sample rows.</p>
      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
      {connect && <ConnectJiraLink />}
      <div className="flex gap-2">
        <Button size="sm" disabled={pending} onClick={post}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}Post to Jira</Button>
        <Button size="sm" variant="ghost" disabled={pending} onClick={() => setOpen(false)}>Cancel</Button>
      </div>
    </div>
  );
}
