"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition, type ReactNode } from "react";
import {
  BellOff, CheckCheck, CircleCheck, ExternalLink, Loader2, MessageSquarePlus, RotateCcw, RotateCw, Sparkles, Ticket, UserPlus, X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { CommentComposer } from "@/components/jira/jira-controls";
import { cn } from "@/lib/utils";
import { CopyButton } from "../../runs/[runId]/dbt/studio-ui";
import { Alert, Notice, useSeq } from "../../qa/qa-shared";
import { RunStrip, When } from "../../ops/ops-shared";
import {
  ackIncident, assignIncident, commentIncident, incidentDetail, muteIncident, reopenIncident, resolveIncident, ticketIncident,
  type IncidentDetail,
} from "../actions";
import { dagRunHref, incidentHref, IncidentStatusPill, KindPill, localInput, SeverityPill, up } from "../incident-shared";
import { AiPanel, ImpactPanel, RetryBadge, RetryDialog } from "./incident-ai";

type Mode = "assign" | "resolve" | "mute" | null;
type Busy = "ack" | "assign" | "resolve" | "mute" | "reopen" | "ticket" | "note" | null;
const MAX_MUTE_DAYS = 30;
const toneOf = (e: string): "info" | "error" => (/approval|request/i.test(e) ? "info" : "error");

function Section({ title, aside, children, className }: { title: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("surface overflow-hidden", className)}>
      <header className="flex items-center gap-2 border-b px-4 py-3">
        <h2 className="flex-1 text-sm font-semibold">{title}</h2>
        {aside}
      </header>
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

const Muted = ({ children }: { children: ReactNode }) => <p className="text-xs text-muted-foreground">{children}</p>;

/** Event detail from the API: plain text, or a small object shown as key and value pairs. */
function detailText(d: unknown): string {
  if (d === null || d === undefined || d === "") return "";
  if (typeof d === "string") return d;
  if (typeof d === "object" && !Array.isArray(d)) {
    return Object.entries(d as Record<string, unknown>).filter(([, v]) => v !== null && v !== undefined && v !== "")
      .map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ");
  }
  return JSON.stringify(d);
}

/** One incident: header with status actions, then error, diagnosis, impact, timeline, notifications, runs, children,
 *  Jira and the resolution. Every action returns the full detail, which replaces what is shown. */
export function IncidentView({ initial, envName, askAck, canOperate, canAI, jiraComment, jiraMe }: {
  initial: IncidentDetail; envName: string; askAck: boolean; canOperate: boolean; canAI: boolean; jiraComment: boolean; jiraMe: string;
}) {
  const router = useRouter();
  const [data, setData] = useState(initial);
  const [mode, setMode] = useState<Mode>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [pending, start] = useTransition();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState<{ tone: "ok" | "info"; text: string } | null>(null);
  const [ackAsk, setAckAsk] = useState(askAck);
  const [assignee, setAssignee] = useState(initial.incident.assignee ?? "");
  const [resolution, setResolution] = useState("");
  const [muteUntil, setMuteUntil] = useState(() => localInput(new Date(Date.now() + 24 * 3600 * 1000)));
  const [muteReason, setMuteReason] = useState("");
  const [note, setNote] = useState("");
  const [composing, setComposing] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const refreshSeq = useSeq();
  const inc = data.incident;
  const status = up(inc.status);
  const closed = status === "RESOLVED";

  const dropAckParam = () => {
    setAckAsk(false);
    const p = new URLSearchParams(window.location.search);
    if (!p.has("ack")) return;
    p.delete("ack");
    const qs = p.toString();
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${qs ? `?${qs}` : ""}`);
  };

  const run = (what: NonNullable<Busy>, fn: () => Promise<{ ok: true; data: IncidentDetail } | { ok: false; error: string }>, done: string, after?: () => void) =>
    start(async () => {
      setBusy(what); setError(""); setNotice(null);
      refreshSeq.next(); // a refresh still in flight must not overwrite the answer to this action
      try {
        const r = await fn();
        if (!r.ok) {
          if (toneOf(r.error) === "info") setNotice({ tone: "info", text: r.error }); else setError(r.error);
          return;
        }
        setData(r.data);
        setNotice({ tone: "ok", text: done });
        after?.();
      } catch (e) {
        setError(e instanceof Error ? e.message : "The action failed.");
      } finally {
        setBusy(null);
      }
    });

  const refresh = () => {
    const ticket = refreshSeq.next();
    incidentDetail(inc.incident_id).then((r) => {
      if (!refreshSeq.current(ticket)) return;
      if (r.ok) setData(r.data); else setError(r.error);
    }, (e: unknown) => { if (refreshSeq.current(ticket)) setError(e instanceof Error ? e.message : "Could not reload the incident."); });
  };

  const ack = () => run("ack", () => ackIncident(inc.incident_id), "Acknowledged.", dropAckParam);
  const muteDate = muteUntil ? new Date(muteUntil) : null;
  const muteBad = !muteDate || Number.isNaN(muteDate.getTime()) || muteDate.getTime() <= Date.now()
    || muteDate.getTime() > Date.now() + MAX_MUTE_DAYS * 86400 * 1000;
  const spin = (b: Busy, icon: ReactNode) => (busy === b ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : icon);
  const jiraFailed = !inc.jira_key && /fail|error|not.?raised/i.test(inc.jira_state ?? "");

  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <p className="eyebrow">
          <Link href="/incidents" className="hover:underline">Incidents</Link>
          <span className="mx-1.5 text-muted-foreground">/</span>
          <span className="normal-case tracking-normal text-muted-foreground">{envName}</span>
        </p>
        <div className="flex flex-wrap items-start gap-3">
          <SeverityPill severity={inc.severity} className="mt-1.5 text-xs" />
          <h1 className="min-w-0 flex-1 break-words text-xl">{inc.title || inc.dag_id}</h1>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <IncidentStatusPill status={inc.status} />
          <KindPill kind={inc.kind} />
          <span>DAG <Link href={dagRunHref(inc.env_id, inc.dag_id)} className="font-mono text-primary hover:underline">{inc.dag_id}</Link></span>
          {inc.task_id && <span>· task <span className="font-mono">{inc.task_id}</span></span>}
          {inc.run_id && <span>· run <Link href={dagRunHref(inc.env_id, inc.dag_id, inc.run_id)} className="font-mono text-primary hover:underline">{inc.run_id}</Link></span>}
          {inc.airflow_url && (
            <a href={inc.airflow_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-primary hover:underline">
              · Airflow<ExternalLink className="h-3 w-3" /></a>
          )}
        </div>
        <dl className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
          <div><dt className="inline text-muted-foreground">Team </dt><dd className="inline">{inc.team_name ?? <span className="text-amber-700">unrouted</span>}</dd></div>
          <div><dt className="inline text-muted-foreground">Assignee </dt><dd className="inline">{inc.assignee ?? "nobody"}</dd></div>
          <div><dt className="inline text-muted-foreground">Seen </dt><dd className="inline tabular-nums">{inc.occurrences}×</dd></div>
          <div><dt className="inline text-muted-foreground">First </dt><dd className="inline"><When iso={inc.first_seen} empty="-" /></dd></div>
          <div><dt className="inline text-muted-foreground">Last </dt><dd className="inline"><When iso={inc.last_seen} rel empty="-" /></dd></div>
          {status === "MUTED" && inc.muted_until && <div><dt className="inline text-muted-foreground">Muted until </dt><dd className="inline"><When iso={inc.muted_until} /></dd></div>}
          {inc.parent_incident_id && <div><dt className="inline text-muted-foreground">Grouped under </dt>
            <dd className="inline"><Link href={incidentHref(inc.parent_incident_id)} className="text-primary hover:underline">the upstream incident</Link></dd></div>}
        </dl>
      </div>

      {ackAsk && (
        <div role="alertdialog" aria-label="Acknowledge this incident" className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100">
          {status !== "OPEN" ? (
            <span className="flex-1">This incident is already {status === "ACK" ? "acknowledged" : up(inc.status).toLowerCase()}.</span>
          ) : !canOperate ? (
            <span className="flex-1">Acknowledging needs the OPS.OPERATE privilege.</span>
          ) : (
            <>
              <span className="flex-1 font-medium">Acknowledge this incident?</span>
              <Button size="sm" disabled={pending} onClick={ack}>{spin("ack", <CheckCheck className="h-3.5 w-3.5" />)}Acknowledge</Button>
            </>
          )}
          <Button size="sm" variant="ghost" disabled={pending} onClick={dropAckParam} aria-label="Dismiss"><X className="h-3.5 w-3.5" /></Button>
        </div>
      )}

      {canOperate && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            {status === "OPEN" && <Button size="sm" disabled={pending} onClick={ack}>{spin("ack", <CheckCheck className="h-3.5 w-3.5" />)}Acknowledge</Button>}
            <Button size="sm" variant={mode === "assign" ? "secondary" : "outline"} disabled={pending} onClick={() => setMode(mode === "assign" ? null : "assign")}>
              <UserPlus className="h-3.5 w-3.5" />Assign</Button>
            {!closed && <Button size="sm" variant={mode === "resolve" ? "secondary" : "outline"} disabled={pending} onClick={() => setMode(mode === "resolve" ? null : "resolve")}>
              <CircleCheck className="h-3.5 w-3.5" />Resolve</Button>}
            {!closed && status !== "MUTED" && <Button size="sm" variant={mode === "mute" ? "secondary" : "outline"} disabled={pending} onClick={() => setMode(mode === "mute" ? null : "mute")}>
              <BellOff className="h-3.5 w-3.5" />Mute</Button>}
            {(closed || status === "MUTED" || status === "MITIGATED") && (
              <Button size="sm" variant="outline" disabled={pending} onClick={() => run("reopen", () => reopenIncident(inc.incident_id), "Reopened.")}>
                {spin("reopen", <RotateCcw className="h-3.5 w-3.5" />)}Reopen</Button>
            )}
            {inc.run_id && (
              <Button size="sm" variant="outline" disabled={pending} onClick={() => setRetrying(true)}
                      title={inc.ai?.safe_to_retry === "no" ? "The diagnosis says this is not safe to retry" : undefined}>
                <RotateCw className="h-3.5 w-3.5" />Retry task</Button>
            )}
            {inc.ai?.safe_to_retry && <RetryBadge value={inc.ai.safe_to_retry} />}
            {(!inc.jira_key || jiraFailed) && (
              <Button size="sm" variant="outline" disabled={pending}
                      onClick={() => run("ticket", () => ticketIncident(inc.incident_id), "Jira ticket requested. The worker raises it with the Jira bot.")}>
                {spin("ticket", <Ticket className="h-3.5 w-3.5" />)}{jiraFailed ? "Retry Jira ticket" : "Raise Jira ticket"}</Button>
            )}
          </div>
          {mode === "assign" && (
            <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => {
              e.preventDefault();
              if (assignee.trim()) run("assign", () => assignIncident(inc.incident_id, assignee.trim()), `Assigned to ${assignee.trim()}.`, () => setMode(null));
            }}>
              <Input value={assignee} onChange={(e) => setAssignee(e.target.value)} placeholder="User name or email" className="w-64 text-xs" aria-label="Assignee" autoFocus />
              <Button size="sm" type="submit" disabled={pending || !assignee.trim()}>{spin("assign", null)}Assign</Button>
              <Button size="sm" type="button" variant="ghost" onClick={() => setMode(null)}>Cancel</Button>
            </form>
          )}
          {mode === "resolve" && (
            <form className="space-y-2" onSubmit={(e) => {
              e.preventDefault();
              if (resolution.trim()) run("resolve", () => resolveIncident(inc.incident_id, resolution.trim()), "Resolved.", () => { setMode(null); setResolution(""); });
            }}>
              <Textarea value={resolution} onChange={(e) => setResolution(e.target.value)} rows={3} className="text-xs" aria-label="Resolution note"
                        placeholder="What was wrong and what fixed it. Saved on the incident and used to diagnose similar ones later." autoFocus />
              <div className="flex gap-2">
                <Button size="sm" type="submit" disabled={pending || !resolution.trim()}>{spin("resolve", <CircleCheck className="h-3.5 w-3.5" />)}Resolve</Button>
                <Button size="sm" type="button" variant="ghost" onClick={() => setMode(null)}>Cancel</Button>
              </div>
            </form>
          )}
          {mode === "mute" && (
            <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => {
              e.preventDefault();
              if (!muteBad && muteDate && muteReason.trim()) {
                run("mute", () => muteIncident(inc.incident_id, muteDate.toISOString(), muteReason.trim()), "Muted.", () => { setMode(null); setMuteReason(""); });
              }
            }}>
              <label className="space-y-1 text-xs font-medium">Until
                <Input type="datetime-local" value={muteUntil} onChange={(e) => setMuteUntil(e.target.value)} className="text-xs" aria-invalid={muteBad}
                       min={localInput(new Date())} max={localInput(new Date(Date.now() + MAX_MUTE_DAYS * 86400 * 1000))} />
              </label>
              <label className="min-w-[16rem] flex-1 space-y-1 text-xs font-medium">Reason
                <Input value={muteReason} onChange={(e) => setMuteReason(e.target.value)} placeholder="Planned maintenance on the source" className="text-xs" />
              </label>
              <Button size="sm" type="submit" disabled={pending || muteBad || !muteReason.trim()}>{spin("mute", <BellOff className="h-3.5 w-3.5" />)}Mute</Button>
              <Button size="sm" type="button" variant="ghost" onClick={() => setMode(null)}>Cancel</Button>
              {muteBad && <span role="alert" className="basis-full text-xs text-destructive">Pick a time in the future, at most {MAX_MUTE_DAYS} days ahead.</span>}
            </form>
          )}
        </div>
      )}

      {retrying && canOperate && (
        <RetryDialog incidentId={inc.incident_id} safe={inc.ai?.safe_to_retry} onClose={() => setRetrying(false)}
                     onDone={(text) => { setNotice({ tone: "ok", text }); refresh(); }} />
      )}
      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
      {notice && (notice.tone === "ok"
        ? <Notice onDismiss={() => setNotice(null)}>{notice.text}</Notice>
        : <p role="status" className="flex items-start gap-2 rounded-lg bg-sky-50 px-3 py-2 text-xs text-sky-800">
            <span className="flex-1">{notice.text}</span>
            <button type="button" aria-label="Dismiss" onClick={() => setNotice(null)}><X className="h-3.5 w-3.5" /></button>
          </p>)}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-4">
          <Section title="Error" aside={inc.error_excerpt ? <CopyButton text={inc.error_excerpt} label="Copy" /> : undefined}>
            {inc.error_excerpt
              ? <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-200">{inc.error_excerpt}</pre>
              : <Muted>No error text was captured. Open the run for the task log.</Muted>}
          </Section>

          <Section title="AI diagnosis" aside={<Sparkles className="h-4 w-4 text-muted-foreground" />}>
            <AiPanel incidentId={inc.incident_id} ai={inc.ai} summary={inc.ai_summary} canAI={canAI}
                     onAi={(ai) => setData((d) => ({ ...d, incident: { ...d.incident, ai } }))} />
          </Section>

          <Section title="Impact">
            <ImpactPanel incidentId={inc.incident_id} envId={inc.env_id} dagId={inc.dag_id} />
          </Section>

          <Section title="Timeline" aside={<span className="text-xs text-muted-foreground">{data.events.length}</span>}>
            {data.events.length ? (
              <ol className="space-y-2.5">
                {data.events.map((e) => (
                  <li key={e.event_id} className="flex gap-3 text-xs">
                    <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-primary/60" />
                    <div className="min-w-0 flex-1">
                      <p><span className="font-medium">{e.kind.toLowerCase().replace(/_/g, " ")}</span>
                        {e.actor && <span className="text-muted-foreground"> by {e.actor}</span>}
                        <span className="text-muted-foreground"> · <When iso={e.created_at} rel empty="-" /></span></p>
                      {detailText(e.detail) && <p className="whitespace-pre-wrap break-words text-muted-foreground">{detailText(e.detail)}</p>}
                    </div>
                  </li>
                ))}
              </ol>
            ) : <Muted>No events yet.</Muted>}
            {canOperate && (
              <form className="mt-3 flex flex-wrap gap-2 border-t pt-3" onSubmit={(e) => {
                e.preventDefault();
                if (note.trim()) run("note", () => commentIncident(inc.incident_id, note.trim()), "Note added.", () => setNote(""));
              }}>
                <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add a note to the timeline" className="min-w-[14rem] flex-1 text-xs" aria-label="Timeline note" />
                <Button size="sm" variant="outline" type="submit" disabled={pending || !note.trim()}>{spin("note", <MessageSquarePlus className="h-3.5 w-3.5" />)}Add note</Button>
              </form>
            )}
          </Section>

          <Section title="Notifications" aside={<button type="button" onClick={refresh} className="text-xs text-primary hover:underline">Refresh</button>}>
            {data.notifications.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                    <tr className="border-b"><th className="py-1.5 pr-3 font-medium">Channel</th><th className="px-3 py-1.5 font-medium">Kind</th>
                      <th className="px-3 py-1.5 font-medium">Status</th><th className="px-3 py-1.5 text-right font-medium">Attempts</th>
                      <th className="px-3 py-1.5 font-medium">When</th><th className="py-1.5 pl-3 font-medium">Error</th></tr>
                  </thead>
                  <tbody className="divide-y">
                    {data.notifications.map((n) => (
                      <tr key={n.notification_id}>
                        <td className="py-1.5 pr-3">{n.channel.toLowerCase()}</td>
                        <td className="px-3 py-1.5">{n.kind.toLowerCase().replace(/_/g, " ")}</td>
                        <td className="px-3 py-1.5"><DeliveryPill status={n.status} /></td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{n.attempts}</td>
                        <td className="whitespace-nowrap px-3 py-1.5"><When iso={n.sent_at ?? n.created_at} rel empty="-" /></td>
                        <td className="max-w-[16rem] py-1.5 pl-3 text-destructive"><span className="line-clamp-2 break-words" title={n.error ?? undefined}>{n.error ?? ""}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <Muted>Nothing sent yet. Teams cards go to the team&apos;s channel once it has a webhook.</Muted>}
          </Section>
        </div>

        <div className="min-w-0 space-y-4">
          <Section title="Jira">
            {inc.jira_key ? (
              <div className="space-y-2 text-xs">
                <p className="flex flex-wrap items-center gap-2">
                  {inc.jira_url
                    ? <a href={inc.jira_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-sm text-primary hover:underline">{inc.jira_key}<ExternalLink className="h-3 w-3" /></a>
                    : <span className="font-mono text-sm">{inc.jira_key}</span>}
                  {inc.jira_state && <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px]">{inc.jira_state}</span>}
                </p>
                {jiraComment && (composing
                  ? <CommentComposer issueKey={inc.jira_key} me={jiraMe} rows={4} onCancel={() => setComposing(false)}
                                     onPosted={(t) => { setComposing(false); setNotice({ tone: "ok", text: t }); }} />
                  : <Button size="sm" variant="outline" onClick={() => setComposing(true)}><MessageSquarePlus className="h-3.5 w-3.5" />Comment on {inc.jira_key}</Button>)}
              </div>
            ) : (
              <Muted>{inc.jira_state ? `No ticket: ${inc.jira_state.toLowerCase().replace(/_/g, " ")}.` : "No ticket raised."}
                {canOperate ? " Use Raise Jira ticket above." : ""}</Muted>
            )}
          </Section>

          <Section title="Resolution">
            {inc.resolution || inc.resolved_at ? (
              <div className="space-y-1 text-xs">
                {inc.resolution && <p className="whitespace-pre-wrap break-words">{inc.resolution}</p>}
                <p className="text-muted-foreground">{inc.resolved_by ? `By ${inc.resolved_by}` : "Resolved"}{inc.resolved_at && <> · <When iso={inc.resolved_at} /></>}</p>
              </div>
            ) : <Muted>{closed ? "Resolved without a note." : "Not resolved yet."}</Muted>}
          </Section>

          <Section title="Grouped incidents" aside={<span className="text-xs text-muted-foreground">{data.children.length}</span>}>
            {data.children.length ? (
              <ul className="space-y-1.5 text-xs">
                {data.children.map((c) => (
                  <li key={c.incident_id} className="flex items-center gap-2">
                    <Link href={incidentHref(c.incident_id)} className="min-w-0 flex-1 truncate font-mono text-primary hover:underline">
                      {c.dag_id}{c.task_id ? ` / ${c.task_id}` : ""}</Link>
                    <IncidentStatusPill status={c.status} />
                  </li>
                ))}
              </ul>
            ) : <Muted>No downstream failures are grouped under this incident.</Muted>}
          </Section>
        </div>
      </div>

      <section className="surface overflow-hidden">
        <header className="flex items-center gap-2 border-b px-4 py-3">
          <h2 className="text-sm font-semibold">Recent runs</h2>
          <span className="text-xs text-muted-foreground">Click a run to open it</span>
        </header>
        {data.runs.length
          ? <RunStrip runs={data.runs} highlight={inc.run_id} onSelect={(runId) => router.push(dagRunHref(inc.env_id, inc.dag_id, runId))} />
          : <p className="px-4 py-6 text-center text-sm text-muted-foreground">No runs recorded for this DAG.</p>}
      </section>
    </div>
  );
}

function DeliveryPill({ status }: { status: string }) {
  const s = up(status);
  const tone = s === "SENT" ? "bg-emerald-50 text-emerald-700 ring-emerald-100"
    : s === "DEAD" || s === "FAILED" ? "bg-rose-50 text-rose-700 ring-rose-100"
      : "bg-amber-50 text-amber-700 ring-amber-100";
  return <span className={cn("whitespace-nowrap rounded-full px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset", tone)}>{status.toLowerCase().replace(/_/g, " ")}</span>;
}
