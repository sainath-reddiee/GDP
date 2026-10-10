"use client";

import Link from "next/link";
import { useEffect, useState, useTransition, type ReactNode } from "react";
import {
  ArrowRightLeft, Check, ExternalLink, FileCode2, GitMerge, Link2, Loader2, MessageSquarePlus, Pencil, Sparkles, Unlink, UserPlus, X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select, Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { Alert, Notice, useLive, useSeq } from "../../qa-shared";
import { When } from "../../../ops/ops-shared";
import { incidentHref, SeverityPill } from "../../../incidents/incident-shared";
import {
  assignCase, caseDetail, commentCase, linkCase, listCases, mergeCase, setCaseStatus, unlinkCase, updateCase,
  type CaseArtifact, type CaseDetail, type CaseLink, type CaseResult,
} from "../actions";
import { RESOLVE_ONLY, SIDE, STATUSES, STEPS } from "../case-filters";
import { caseHref, CaseKindPill, CaseStatusPill, caseStatusLabel, pill, SourceChip, up } from "../case-ui";

const OVERRIDE_MIN = 15;
const JIRA_KEY = /^[A-Z][A-Z0-9_]+-\d+$/;
const CASE_NUMBER = /^CASE-\d+$/i;
type Mode = "assign" | "status" | "merge" | "title" | null;
type Busy = "assign" | "status" | "merge" | "title" | "comment" | "link" | "unlink" | null;

const LINK_KINDS: { id: string; label: string; placeholder: string; valid: (s: string) => boolean; hint: string }[] = [
  { id: "JIRA", label: "Jira issue", placeholder: "QA-123", valid: (s) => JIRA_KEY.test(s.toUpperCase()), hint: "An issue key looks like PROJECT-123." },
  { id: "INCIDENT", label: "Incident", placeholder: "Incident id", valid: (s) => s.length >= 4, hint: "Paste the incident id from its page." },
  { id: "RUN", label: "Run", placeholder: "Run id", valid: (s) => s.length >= 4, hint: "Paste the run id from its page." },
  { id: "PR", label: "Pull request", placeholder: "https://github.com/org/repo/pull/12", valid: (s) => /^https:\/\/\S+$/i.test(s), hint: "A pull request link starts with https://." },
];

const words = (s: string) => s.toLowerCase().replace(/_/g, " ");
const Muted = ({ children }: { children: ReactNode }) => <p className="text-xs text-muted-foreground">{children}</p>;
/** Only app paths and http(s) links are followed. */
const safeUrl = (u: string | null | undefined) => (u && (/^\/(?!\/)/.test(u) || /^https?:\/\//i.test(u)) ? u : null);

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

/** Where a link goes: its own URL when the API gave one, else the page for that kind of record in this app. */
function linkHref(l: CaseLink): string | null {
  const own = safeUrl(l.url);
  if (own) return own;
  switch (up(l.kind)) {
    case "INCIDENT": return incidentHref(l.ref);
    case "RUN": return `/runs/${encodeURIComponent(l.ref)}`;
    case "CASE": return caseHref(l.ref);
    case "JIRA": return `/qa?tab=triage&key=${encodeURIComponent(l.ref)}`;
    default: return null;
  }
}

function StatusSteps({ status }: { status: string }) {
  const s = up(status);
  const at = STEPS.indexOf(s as (typeof STEPS)[number]);
  const side = (SIDE as string[]).includes(s);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <ol className="flex flex-wrap items-center gap-1" aria-label="Case progress">
        {STEPS.map((step, i) => {
          const done = at >= 0 && i < at;
          const current = i === at;
          return (
            <li key={step} className="flex items-center gap-1">
              <span aria-current={current ? "step" : undefined}
                    className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset",
                      current ? "bg-primary text-primary-foreground ring-primary"
                        : done ? "bg-primary/10 text-primary ring-primary/20" : "bg-muted/40 text-muted-foreground ring-border")}>
                {done && <Check className="-ml-0.5 mr-0.5 inline h-3 w-3" />}{caseStatusLabel(step)}
              </span>
              {i < STEPS.length - 1 && <span className="h-px w-3 bg-border" aria-hidden />}
            </li>
          );
        })}
      </ol>
      {side && <CaseStatusPill status={s} className="text-[11px]" />}
    </div>
  );
}

/** One case: header with status steps and actions, then description, AI, artifacts and timeline, with links and details
 *  on the side. Every action reloads the case, which replaces what is shown. */
export function CaseView({ initial, canWork, canResolve, opened }: {
  initial: CaseDetail; canWork: boolean; canResolve: boolean; opened: "new" | "existing" | null;
}) {
  const [data, setData] = useState(initial);
  const [mode, setMode] = useState<Mode>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [pending, start] = useTransition();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState(opened ? `${opened === "new" ? "Opened" : "Already tracked as"} ${initial.case.number}.` : "");
  const [assignee, setAssignee] = useState(initial.case.assignee ?? "");
  const [title, setTitle] = useState(initial.case.title);
  const [nextStatus, setNextStatus] = useState("");
  const [statusNote, setStatusNote] = useState("");
  const [override, setOverride] = useState("");
  const [mergeInto, setMergeInto] = useState("");
  const [mergeTarget, setMergeTarget] = useState<{ case_id: string; number: string } | null>(null);
  const [comment, setComment] = useState("");
  const [linkKind, setLinkKind] = useState("JIRA");
  const [linkRef, setLinkRef] = useState("");
  const [unlinkAsk, setUnlinkAsk] = useState("");
  const refreshSeq = useSeq();
  const live = useLive();
  const c = data.case;
  const status = up(c.status);
  const caseId = c.case_id;

  // the ?opened= note is said once; a reload of the page should not repeat it
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    if (!p.has("opened")) return;
    p.delete("opened");
    const qs = p.toString();
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${qs ? `?${qs}` : ""}`);
  }, []);

  const reload = async () => {
    const ticket = refreshSeq.next();
    const r = await caseDetail(caseId);
    if (!live.current || !refreshSeq.current(ticket)) return;
    if (r.ok) setData(r.data);
    else setError(r.status === 404 ? "This case is no longer visible to you. It may have moved to a domain outside yours." : `Could not reload the case: ${r.error}`);
  };

  const run = (what: NonNullable<Busy>, fn: () => Promise<CaseResult<unknown>>, done: string, after?: () => void) =>
    start(async () => {
      setBusy(what); setError(""); setNotice("");
      try {
        const r = await fn();
        if (!live.current) return;
        if (!r.ok) { setError(r.error); return; }
        setNotice(done);
        after?.();
        await reload();
      } catch (e) {
        if (live.current) setError(e instanceof Error ? e.message : "The action failed.");
      } finally {
        if (live.current) setBusy(null);
      }
    });

  const spin = (b: Busy, icon: ReactNode) => (busy === b ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : icon);
  const toggle = (m: Mode) => { setMode(mode === m ? null : m); setError(""); setMergeTarget(null); };

  const statusChoices = STATUSES.filter((s) => s !== status && (canResolve || !RESOLVE_ONLY.includes(s)));
  const needsOverride = nextStatus === "RESOLVED" && status !== "VERIFIED";
  const overrideOk = !needsOverride || override.trim().length >= OVERRIDE_MIN;

  const findMergeTarget = () => start(async () => {
    setError(""); setMergeTarget(null);
    const want = mergeInto.trim();
    if (!want) return;
    try {
      if (!CASE_NUMBER.test(want)) {
        if (want === caseId) { setError("A case cannot be merged into itself."); return; }
        setMergeTarget({ case_id: want, number: want });
        return;
      }
      const r = await listCases({ q: want.toUpperCase(), status: [], limit: 10 });
      if (!live.current) return;
      if (!r.ok) { setError(r.error); return; }
      const hit = r.data.cases.find((x) => x.number.toUpperCase() === want.toUpperCase());
      if (!hit) { setError(`${want.toUpperCase()} was not found in your domains.`); return; }
      if (hit.case_id === caseId) { setError("A case cannot be merged into itself."); return; }
      setMergeTarget({ case_id: hit.case_id, number: hit.number });
    } catch (e) {
      if (live.current) setError(e instanceof Error ? e.message : "Could not find that case.");
    }
  });

  const kindDef = LINK_KINDS.find((k) => k.id === linkKind) ?? LINK_KINDS[0];
  const ref = linkKind === "JIRA" ? linkRef.trim().toUpperCase() : linkRef.trim();
  const refOk = !!ref && kindDef.valid(ref);
  const ai = c.ai;
  const aiSummary = (typeof ai?.summary === "string" && ai.summary) || c.ai_summary;

  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <p className="eyebrow">
          <Link href="/qa?tab=cases" className="hover:underline">QA cases</Link>
          <span className="mx-1.5 text-muted-foreground">/</span>
          <span className="font-mono normal-case tracking-normal text-muted-foreground">{c.number}</span>
        </p>
        <div className="flex flex-wrap items-start gap-3">
          <SeverityPill severity={c.severity} className="mt-1.5 text-xs" />
          {mode === "title" ? (
            <form className="flex min-w-0 flex-1 flex-wrap gap-2" onSubmit={(e) => {
              e.preventDefault();
              if (title.trim().length >= 3) run("title", () => updateCase(caseId, { title: title.trim() }), "Title saved.", () => setMode(null));
            }}>
              <Input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} className="min-w-[16rem] flex-1" aria-label="Case title" autoFocus />
              <Button size="sm" type="submit" disabled={pending || title.trim().length < 3}>{spin("title", <Check className="h-3.5 w-3.5" />)}Save</Button>
              <Button size="sm" type="button" variant="ghost" onClick={() => { setMode(null); setTitle(c.title); }}>Cancel</Button>
            </form>
          ) : (
            <h1 className="flex min-w-0 flex-1 items-start gap-2 break-words text-xl">
              <span className="min-w-0">{c.title}</span>
              {canWork && <button type="button" aria-label="Edit title" onClick={() => { setTitle(c.title); toggle("title"); }}
                                  className="mt-1 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"><Pencil className="h-3.5 w-3.5" /></button>}
            </h1>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <CaseStatusPill status={c.status} />
          <CaseKindPill kind={c.kind} />
          <SourceChip source={c.source} />
          <span>· {c.domain_name ?? "General"}</span>
          {c.target_fqn && <span>· <span className="font-mono">{c.target_fqn}</span></span>}
          {c.run_id && <span>· run <Link href={`/runs/${encodeURIComponent(c.run_id)}`} className="font-mono text-primary hover:underline">{c.run_id.slice(0, 8)}</Link></span>}
        </div>
        <dl className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
          <div><dt className="inline text-muted-foreground">Assignee </dt><dd className="inline">{c.assignee ?? "nobody"}</dd></div>
          <div><dt className="inline text-muted-foreground">SLA due </dt>
            <dd className={cn("inline", c.sla_breached && "font-medium text-destructive")}><When iso={c.sla_due_at} empty="none" />{c.sla_breached && " (breached)"}</dd></div>
          <div><dt className="inline text-muted-foreground">Opened </dt><dd className="inline"><When iso={c.opened_at} rel empty="-" />{c.opened_by && ` by ${c.opened_by}`}</dd></div>
          <div><dt className="inline text-muted-foreground">Updated </dt><dd className="inline"><When iso={c.updated_at} rel empty="-" /></dd></div>
          {c.duplicate_of && <div><dt className="inline text-muted-foreground">Duplicate of </dt>
            <dd className="inline"><Link href={caseHref(c.duplicate_of)} className="text-primary hover:underline">the original case</Link></dd></div>}
        </dl>
        <StatusSteps status={c.status} />
      </div>

      {(canWork || canResolve) && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            {canWork && <Button size="sm" variant={mode === "assign" ? "secondary" : "outline"} disabled={pending} onClick={() => toggle("assign")}>
              <UserPlus className="h-3.5 w-3.5" />Assign</Button>}
            {statusChoices.length > 0 && <Button size="sm" variant={mode === "status" ? "secondary" : "outline"} disabled={pending} onClick={() => toggle("status")}>
              <ArrowRightLeft className="h-3.5 w-3.5" />Change status</Button>}
            {canResolve && status !== "DUPLICATE" && <Button size="sm" variant={mode === "merge" ? "secondary" : "outline"} disabled={pending} onClick={() => toggle("merge")}>
              <GitMerge className="h-3.5 w-3.5" />Merge into another case</Button>}
          </div>
          {mode === "assign" && (
            <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => {
              e.preventDefault();
              if (assignee.trim()) run("assign", () => assignCase(caseId, assignee.trim()), `Assigned to ${assignee.trim()}.`, () => setMode(null));
            }}>
              <Input value={assignee} onChange={(e) => setAssignee(e.target.value)} placeholder="User name or email" className="w-64 text-xs" aria-label="Assignee" autoFocus />
              <Button size="sm" type="submit" disabled={pending || !assignee.trim()}>{spin("assign", null)}Assign</Button>
              <Button size="sm" type="button" variant="ghost" onClick={() => setMode(null)}>Cancel</Button>
            </form>
          )}
          {mode === "status" && (
            <form className="space-y-2 rounded-lg border p-3" onSubmit={(e) => {
              e.preventDefault();
              if (!nextStatus || !overrideOk) return;
              run("status", () => setCaseStatus(caseId, {
                status: nextStatus, ...(statusNote.trim() ? { note: statusNote.trim() } : {}),
                ...(needsOverride ? { override_reason: override.trim() } : {}),
              }), `Moved to ${caseStatusLabel(nextStatus)}.`, () => { setMode(null); setNextStatus(""); setStatusNote(""); setOverride(""); });
            }}>
              <div className="flex flex-wrap items-center gap-2">
                <Select value={nextStatus} onChange={(e) => setNextStatus(e.target.value)} className="h-8 w-auto text-xs" aria-label="New status" autoFocus>
                  <option value="">Choose a status</option>
                  {statusChoices.map((s) => <option key={s} value={s}>{caseStatusLabel(s)}</option>)}
                </Select>
                <Input value={statusNote} onChange={(e) => setStatusNote(e.target.value)} placeholder="Note for the timeline (optional)" className="h-8 min-w-[14rem] flex-1 text-xs" aria-label="Status note" />
              </div>
              {needsOverride && (
                <div className="space-y-1">
                  <p className="text-xs text-amber-800 dark:text-amber-200">This case is not verified. Resolving it anyway needs a reason of at least {OVERRIDE_MIN} characters; it is kept on the timeline.</p>
                  <Textarea value={override} onChange={(e) => setOverride(e.target.value)} rows={2} className="text-xs" aria-label="Override reason"
                            aria-invalid={!overrideOk} placeholder="Why this can be resolved without verification" />
                  {!overrideOk && override.length > 0 && <p role="alert" className="text-xs text-destructive">{OVERRIDE_MIN - override.trim().length} more characters needed.</p>}
                </div>
              )}
              <div className="flex gap-2">
                <Button size="sm" type="submit" disabled={pending || !nextStatus || !overrideOk}>{spin("status", <Check className="h-3.5 w-3.5" />)}Change status</Button>
                <Button size="sm" type="button" variant="ghost" onClick={() => setMode(null)}>Cancel</Button>
              </div>
            </form>
          )}
          {mode === "merge" && (
            <div className="space-y-2 rounded-lg border p-3">
              {!mergeTarget ? (
                <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); findMergeTarget(); }}>
                  <Input value={mergeInto} onChange={(e) => setMergeInto(e.target.value)} placeholder="CASE-123 or a case id" className="w-64 font-mono text-xs" aria-label="Merge into" autoFocus />
                  <Button size="sm" type="submit" disabled={pending || !mergeInto.trim()}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}Continue</Button>
                  <Button size="sm" type="button" variant="ghost" onClick={() => setMode(null)}>Cancel</Button>
                </form>
              ) : (
                <div className="space-y-2">
                  <p className="text-xs font-medium">Merge {c.number} into <span className="font-mono">{mergeTarget.number}</span>? This case is marked a duplicate of it.</p>
                  <div className="flex gap-2">
                    <Button size="sm" disabled={pending} onClick={() => run("merge", () => mergeCase(caseId, mergeTarget.case_id),
                      `Merged into ${mergeTarget.number}.`, () => { setMode(null); setMergeInto(""); setMergeTarget(null); })}>
                      {spin("merge", <GitMerge className="h-3.5 w-3.5" />)}Yes, merge</Button>
                    <Button size="sm" variant="ghost" disabled={pending} onClick={() => setMergeTarget(null)}>Back</Button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
      {notice && <Notice onDismiss={() => setNotice("")}>{notice}</Notice>}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-4">
          <Section title="Description">
            {c.description
              ? <div className="max-h-96 overflow-y-auto whitespace-pre-wrap break-words text-[13px] leading-relaxed">{c.description}</div>
              : <Muted>No description.</Muted>}
            {c.resolution && (
              <div className="mt-3 border-t pt-3 text-xs"><p className="font-medium">Resolution</p><p className="whitespace-pre-wrap break-words">{c.resolution}</p></div>
            )}
          </Section>

          <Section title="AI analysis" aside={<Sparkles className="h-4 w-4 text-muted-foreground" />}>
            <AiSlot summary={aiSummary || null} model={typeof ai?.model === "string" ? ai.model : null}
                    at={typeof ai?.generated_at === "string" ? ai.generated_at : null} />
          </Section>

          <Section title="Proposals and tests" aside={<span className="text-xs text-muted-foreground">{data.artifacts.length}</span>}>
            {data.artifacts.length ? (
              <ul className="space-y-2">{data.artifacts.map((a) => <Artifact key={a.artifact_id} a={a} />)}</ul>
            ) : <Muted>AI triage and fix proposals arrive in the next release.</Muted>}
          </Section>

          <Section title="Timeline" aside={<span className="text-xs text-muted-foreground">{data.events.length}</span>}>
            {data.events.length ? (
              <ol className="space-y-2.5">
                {data.events.map((e) => (
                  <li key={e.event_id} className="flex gap-3 text-xs">
                    <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-primary/60" />
                    <div className="min-w-0 flex-1">
                      <p><span className="font-medium">{words(e.kind)}</span>
                        {e.actor && <span className="text-muted-foreground"> by {e.actor}</span>}
                        <span className="text-muted-foreground"> · <When iso={e.created_at} rel empty="-" /></span></p>
                      {detailText(e.detail) && <p className="whitespace-pre-wrap break-words text-muted-foreground">{detailText(e.detail)}</p>}
                    </div>
                  </li>
                ))}
              </ol>
            ) : <Muted>No events yet.</Muted>}
            {canWork && (
              <form className="mt-3 space-y-2 border-t pt-3" onSubmit={(e) => {
                e.preventDefault();
                if (comment.trim()) run("comment", () => commentCase(caseId, comment.trim()), "Comment added.", () => setComment(""));
              }}>
                <Textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={3} maxLength={8000} className="text-xs" aria-label="Comment"
                          placeholder="Add a comment. Counts and findings only; never paste sample rows." />
                <div className="flex justify-end">
                  <Button size="sm" variant="outline" type="submit" disabled={pending || !comment.trim()}>{spin("comment", <MessageSquarePlus className="h-3.5 w-3.5" />)}Comment</Button>
                </div>
              </form>
            )}
          </Section>
        </div>

        <div className="min-w-0 space-y-4">
          <Section title="Links" aside={<span className="text-xs text-muted-foreground">{data.links.length}</span>}>
            {data.links.length ? (
              <ul className="space-y-1.5">
                {data.links.map((l) => {
                  const href = linkHref(l);
                  const external = !!href && /^https?:\/\//i.test(href);
                  return (
                    <li key={l.link_id} className="flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs">
                      <span className="shrink-0 rounded bg-muted px-1 text-[10px] text-muted-foreground">{words(l.kind)}</span>
                      <span className="min-w-0 flex-1 truncate" title={l.ref}>
                        {href
                          ? external
                            ? <a href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">{l.label || l.ref}<ExternalLink className="h-3 w-3" /></a>
                            : <Link href={href} className="text-primary hover:underline">{l.label || l.ref}</Link>
                          : <span>{l.label || l.ref}</span>}
                      </span>
                      {l.state && <span className={cn(pill, "bg-muted text-muted-foreground ring-border")}>{words(l.state)}</span>}
                      {canWork && (unlinkAsk === l.link_id ? (
                        <span className="inline-flex items-center gap-1">
                          <Button size="sm" variant="destructive" className="h-6 px-2 text-[11px]" disabled={pending}
                                  onClick={() => run("unlink", () => unlinkCase(caseId, l.link_id), "Link removed.", () => setUnlinkAsk(""))}>
                            {spin("unlink", null)}Unlink</Button>
                          <button type="button" aria-label="Cancel" onClick={() => setUnlinkAsk("")}><X className="h-3.5 w-3.5" /></button>
                        </span>
                      ) : (
                        <button type="button" onClick={() => setUnlinkAsk(l.link_id)} disabled={pending} className="text-muted-foreground hover:text-destructive" aria-label={`Unlink ${l.ref}`}>
                          <Unlink className="h-3.5 w-3.5" /></button>
                      ))}
                    </li>
                  );
                })}
              </ul>
            ) : <Muted>Nothing linked yet.</Muted>}
            {canWork && (
              <form className="mt-3 space-y-1.5 border-t pt-3" onSubmit={(e) => {
                e.preventDefault();
                if (refOk) run("link", () => linkCase(caseId, linkKind, ref), `Linked ${ref}.`, () => setLinkRef(""));
              }}>
                <div className="flex flex-wrap items-center gap-2">
                  <Select value={linkKind} onChange={(e) => setLinkKind(e.target.value)} className="h-8 w-auto text-xs" aria-label="Link kind">
                    {LINK_KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
                  </Select>
                  <Input value={linkRef} onChange={(e) => setLinkRef(e.target.value)} placeholder={kindDef.placeholder} aria-label="Link reference"
                         className="h-8 min-w-[10rem] flex-1 font-mono text-xs" />
                  <Button size="sm" variant="outline" type="submit" disabled={pending || !refOk}>{spin("link", <Link2 className="h-3.5 w-3.5" />)}Link</Button>
                </div>
                {linkRef.trim() && !refOk && <p className="text-[11px] text-muted-foreground">{kindDef.hint}</p>}
              </form>
            )}
          </Section>

          <Section title="Details">
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs">
              <dt className="text-muted-foreground">Domain</dt><dd>{c.domain_name ?? "General"}</dd>
              <dt className="text-muted-foreground">Table</dt><dd className="break-all font-mono">{c.target_fqn ?? <span className="font-sans text-muted-foreground">not resolved yet</span>}</dd>
              <dt className="text-muted-foreground">Models</dt><dd className="break-all font-mono">{c.models?.length ? c.models.join(", ") : <span className="font-sans text-muted-foreground">none</span>}</dd>
              {c.repo_id && <><dt className="text-muted-foreground">Repository</dt><dd className="break-all font-mono">{c.repo_id}</dd></>}
              <dt className="text-muted-foreground">Source</dt><dd><SourceChip source={c.source} /></dd>
              {c.fingerprint && <><dt className="text-muted-foreground">Fingerprint</dt><dd className="truncate font-mono" title={c.fingerprint}>{c.fingerprint.slice(0, 16)}</dd></>}
            </dl>
          </Section>
        </div>
      </div>
    </div>
  );
}

/** The AI slot: the stored summary until the triage engine adds hypotheses, evidence and blast radius. */
function AiSlot({ summary, model, at }: { summary: string | null; model: string | null; at: string | null }) {
  if (!summary) return <Muted>No AI analysis yet. Automatic triage of new cases arrives in the next release.</Muted>;
  return (
    <div className="space-y-2 text-xs">
      <p className="whitespace-pre-wrap break-words">{summary}</p>
      <p className="border-t pt-2 text-[11px] text-muted-foreground">
        {model ? <>By <span className="font-mono">{model}</span></> : "AI generated"}{at && <> · <When iso={at} /></>}. Check it before acting.
      </p>
    </div>
  );
}

const ARTIFACT_TONE: Record<string, string> = {
  PROPOSED: "bg-amber-50 text-amber-700 ring-amber-100",
  ACCEPTED: "bg-sky-50 text-sky-700 ring-sky-100",
  APPLIED: "bg-emerald-50 text-emerald-700 ring-emerald-100",
  REJECTED: "bg-slate-100 text-slate-600 ring-slate-200",
};

function Artifact({ a }: { a: CaseArtifact }) {
  return (
    <li className="rounded-lg border">
      <p className="flex flex-wrap items-center gap-2 border-b px-3 py-2 text-xs">
        <FileCode2 className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">{words(a.type)}</span>
        <span className="min-w-0 flex-1 truncate font-medium">{a.title || words(a.type)}</span>
        <span className={cn(pill, ARTIFACT_TONE[up(a.status)] ?? ARTIFACT_TONE.REJECTED)}>{words(a.status)}</span>
      </p>
      <div className="space-y-2 px-3 py-2 text-xs">
        {a.content && <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-2 font-mono text-[11px]">{a.content}</pre>}
        {a.diff && (
          <pre className="max-h-80 overflow-auto rounded-lg bg-slate-950 p-3 font-mono text-[11px] leading-relaxed text-slate-200" aria-label="Diff">
            {a.diff.split("\n").map((line, i) => (
              <span key={i} className={cn("block", line.startsWith("+") && !line.startsWith("+++") ? "text-emerald-300"
                : line.startsWith("-") && !line.startsWith("---") ? "text-rose-300" : line.startsWith("@@") ? "text-sky-300" : undefined)}>{line || " "}</span>
            ))}
          </pre>
        )}
        <p className="text-[11px] text-muted-foreground">
          <When iso={a.created_at} rel empty="-" />
          {a.decided_by && <> · {words(a.status)} by {a.decided_by}{a.decided_at && <> <When iso={a.decided_at} rel /></>}</>}
        </p>
      </div>
    </li>
  );
}
