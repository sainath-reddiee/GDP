"use client";

import Link from "next/link";
import { useEffect, useRef, useState, useTransition, type ReactNode } from "react";
import { Bug, Link2, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { connectJira, type JiraStatus } from "../jira/actions";
import { listSuites, listTables, type Failed, type QaTable, type Suite } from "./actions";

export type QaTab = "inbox" | "suites" | "triage" | "results";
export type Access = { canEdit: boolean; canAI: boolean; canJiraRead: boolean; canJiraWrite: boolean };
export type Nav = { tab: QaTab; table: string; key: string; suite: string };
export type Go = (patch: Partial<Nav>) => void;

/** Ignore answers to requests that a newer one replaced: take a ticket before the call, check it after. */
export function useSeq() {
  const seq = useRef(0);
  return { next: () => ++seq.current, current: (n: number) => n === seq.current };
}

/** Is the component still mounted? For answers that arrive after the user left the view. */
export function useLive() {
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  return live;
}

export type JiraProblem = { connect: boolean; text: string };

/** How a failed Jira call reads in the UI: 428 asks to (re)connect, 429 says when to try again. */
export function jiraProblem(f: Failed): JiraProblem {
  if (f.status === 428) {
    return { connect: true, text: /reconnect/i.test(f.error) ? f.error : "Connect your Jira account to work with your issues here." };
  }
  if (f.status === 429) {
    const seconds = f.retryAfter ?? Number(/(\d+)\s*s/.exec(f.error)?.[1] ?? NaN);
    return { connect: false, text: Number.isFinite(seconds) ? `Jira is rate limiting; try again in ${seconds} s.` : "Jira is rate limiting; try again in a few seconds." };
  }
  return { connect: false, text: f.error };
}

export function Alert({ children, onDismiss, className }: { children: ReactNode; onDismiss?: () => void; className?: string }) {
  return (
    <p role="alert" className={cn("flex items-start gap-2 rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive", className)}>
      <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">{children}</span>
      {onDismiss && <button type="button" aria-label="Dismiss" onClick={onDismiss}><X className="h-3.5 w-3.5" /></button>}
    </p>
  );
}

export function Notice({ children, onDismiss }: { children: ReactNode; onDismiss?: () => void }) {
  return (
    <p role="status" className="flex items-start gap-2 rounded-lg bg-success/10 px-3 py-2 text-xs text-success">
      <span className="min-w-0 flex-1">{children}</span>
      {onDismiss && <button type="button" aria-label="Dismiss" onClick={onDismiss}><X className="h-3.5 w-3.5" /></button>}
    </p>
  );
}

export function Empty({ title, text, action, icon }: { title: string; text?: ReactNode; action?: ReactNode; icon?: ReactNode }) {
  return (
    <section className="surface flex flex-col items-center px-6 py-12 text-center">
      <span className="grid h-12 w-12 place-items-center rounded-2xl bg-sky-50 text-sky-600 ring-1 ring-inset ring-sky-100">{icon ?? <Bug className="h-6 w-6" />}</span>
      <p className="mt-3 text-sm font-semibold">{title}</p>
      {text && <div className="mt-1 max-w-md text-xs text-muted-foreground">{text}</div>}
      {action && <div className="mt-4">{action}</div>}
    </section>
  );
}

export function Pending({ text }: { text: string }) {
  return <p className="flex items-center gap-2 py-6 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />{text}</p>;
}

/** A Connect Jira card that comes back to the given QA page after Atlassian approves the access. */
export function ConnectJira({ text, returnTo, title = "Connect your Jira account" }: { text: string; returnTo: string; title?: string }) {
  const [error, setError] = useState("");
  const [busy, start] = useTransition();
  const connect = () => start(async () => {
    setError("");
    const r = await connectJira(returnTo);
    if (r.ok) window.location.href = r.data.url; else setError(r.error);
  });
  return (
    <Empty title={title} text={<>{text}{error && <span role="alert" className="mt-2 block text-destructive">{error}</span>}</>}
           action={<Button disabled={busy} onClick={connect}>{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}Connect Jira</Button>} />
  );
}

/** Shows why Jira cannot be used yet (not installed, not set up, not connected); renders children when it can. */
export function JiraGate({ jira, returnTo, children }: { jira: JiraStatus | null; returnTo: string; children: ReactNode }) {
  if (!jira) return <Empty title="Jira could not be reached" text="The Jira status did not load. Reload the page, or check the API." />;
  if (!jira.installed) return <Empty title="Jira is not installed" text="It needs the latest deploy (migration V027). Ask a platform admin." />;
  if (!jira.ready) {
    return <Empty title="Jira is not set up yet" text="A platform admin connects the Jira site once in Admin, Integrations, Jira; then each engineer signs in with their own account."
                  action={<Link href="/admin?section=integrations&view=jira" className="text-sm text-primary hover:underline">Open Jira setup</Link>} />;
  }
  if (!jira.connected) {
    return <ConnectJira returnTo={returnTo}
                        text={`See your issues${jira.site_url ? ` on ${jira.site_url.replace(/^https:\/\//, "")}` : ""}, triage a reported bug into a table suite, and post results back, all as yourself. You approve the access in Atlassian.`} />;
  }
  return <>{children}</>;
}

/** Every QA target table (optionally of one domain), loaded once per domain. */
export function useTables(domainId = "", enabled = true) {
  const [tables, setTables] = useState<QaTable[] | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let live = true;
    setTables(null); setError("");
    if (!enabled) return;
    listTables(domainId || undefined).then((r) => {
      if (!live) return;
      if (r.ok) setTables(r.data.tables); else { setError(r.error); setTables([]); }
    }, (e: unknown) => { if (live) { setError(e instanceof Error ? e.message : "Could not load the tables."); setTables([]); } });
    return () => { live = false; };
  }, [domainId, reload, enabled]);
  return { tables, error, refresh: () => setReload((n) => n + 1) };
}

/** The suites of one table, reloaded when the table changes. */
export function useSuites(tableId: string) {
  const [suites, setSuites] = useState<Suite[] | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let live = true;
    setSuites(null); setError("");
    if (!tableId) { setSuites([]); return; }
    listSuites(tableId).then((r) => {
      if (!live) return;
      if (r.ok) setSuites(r.data.suites); else { setError(r.error); setSuites([]); }
    }, (e: unknown) => { if (live) { setError(e instanceof Error ? e.message : "Could not load the suites."); setSuites([]); } });
    return () => { live = false; };
  }, [tableId, reload]);
  return { suites, error, refresh: () => setReload((n) => n + 1) };
}

export function TableSelect({ tables, value, onChange, placeholder = "Choose a table", className, label = "Target table" }: {
  tables: QaTable[] | null; value: string; onChange: (id: string) => void; placeholder?: string; className?: string; label?: string;
}) {
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)} className={cn("text-xs", className)} aria-label={label} disabled={!tables}>
      <option value="">{tables ? placeholder : "Loading tables…"}</option>
      {(tables ?? []).map((t) => <option key={t.target_table_id} value={t.target_table_id}>{t.fqn}{t.active ? "" : " (retired)"}</option>)}
    </Select>
  );
}

export function SuiteSelect({ suites, value, onChange, allLabel, className }: {
  suites: Suite[] | null; value: string; onChange: (id: string) => void; allLabel?: string; className?: string;
}) {
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)} className={cn("w-auto text-xs", className)} aria-label="Suite" disabled={!suites}>
      {allLabel !== undefined ? <option value="">{allLabel}</option> : !value && <option value="">{suites ? "Default suite" : "Loading suites…"}</option>}
      {(suites ?? []).map((s) => <option key={s.suite_id} value={s.suite_id}>{s.name}{s.is_default ? " (default)" : ""}</option>)}
    </Select>
  );
}

export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
