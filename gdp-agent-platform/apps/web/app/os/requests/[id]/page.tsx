import Link from "next/link";
import { AlertTriangle, ArrowRight, Ban, CalendarClock, Check, CheckCircle2, ChevronDown, CircleDashed, Clock, Hand, Inbox, Link2, MessageSquare, Settings2, Sparkles, X } from "lucide-react";
import { stateLabel } from "@/lib/run-labels";
import { loadDomains, loadRequest, loadRuns, loadSources } from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import { requestStepHref, waitIsMine } from "@/lib/os/work";
import { STAGE_NAME, actHref, childRunHref, childRunLabel, enhancesSource, intentLabel, overdue, phaseOf, requestSteps, waitActionText, waitCta, waitRole, type RequestEvent, type RequestItem, type RequestStage } from "@/lib/os/requests";
import { ActivityRow, Banner, Btn, Card, Empty, PageHeader, SourceError, Tag, ago, pretty, type Tone } from "@/components/os/ui";
import { AddSource, DeleteRequestButton, DeleteRunButton, ItemControls, LinkDomain, RequestControls } from "@/components/os/request-ui";
import { RequestFiles } from "@/components/os/request-files";
import { personaForPrivilege } from "@/lib/os/personas";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const metadata = { title: "Onboarding request" };

const ITEM_TONE: Record<string, Tone> = {
  MISSING: "amber", UNREACHABLE: "red", NOT_LANDED: "amber", NO_RUN: "blue", DESIGN: "blue", BUILD: "purple", REVIEW: "amber",
  DONE: "green", ATTENTION: "red", REMOVED: "neutral",
};
const ITEM_LABEL: Record<string, string> = {
  MISSING: "Not registered", UNREACHABLE: "Unreachable", NOT_LANDED: "Not landed", NO_RUN: "Ready, no run", DESIGN: "In design",
  BUILD: "In build", REVIEW: "In review", DONE: "Done", ATTENTION: "Needs attention", REMOVED: "Removed",
};

const STAGE_SUMMARY: Record<RequestStage, string> = {
  DOMAIN: "Domain model & contract",
  NEW: "Scope & outcome",
  ENABLE: "Connect & land",
  DESIGN: "Profile, model & map",
  BUILD: "dbt, DQ & QA",
  REVIEW: "Validation & review",
  DONE: "PR & QA sign-off",
  CLOSED: "Closed",
};

const EVENT: Record<string, (e: RequestEvent) => string> = {
  SUBMITTED: () => "Filed the request",
  ROUTED: (e) => `Routed to ${pretty(e.to_value)}`,
  STAGE: (e) => `Moved from ${pretty(e.from_value)} to ${pretty(e.to_value)}`,
  SOURCE_ADDED: (e) => `Added the source ${e.to_value}`,
  SOURCE_LINKED: () => "Linked a source registration",
  RUN_LINKED: () => "Linked a run",
  RUN_UNLINKED: () => "Unlinked a run",
  SOURCE_REMOVED: (e) => `Removed the source ${e.from_value}`,
  DOMAIN_LINKED: () => "Linked the domain",
  COMMENT: () => "Commented",
  CANCELLED: () => "Cancelled the request",
  REJECTED: () => "Rejected the request",
  REOPENED: () => "Reopened the request",
  DONE: () => "Completed and published",
  SOURCE_ONBOARDED: () => "Source onboarded",
  DESIGN_STORED: () => "Design approved and stored",
  FILE_ADDED: () => "Attached files",
  FILE_REMOVED: () => "Removed a file",
  FILES_HANDED_OVER: () => "Files added to the domain's documents",
};

export default async function RequestPage({ params, searchParams }: { params: { id: string }; searchParams?: { files?: string } }) {
  const access = await guard("requests");
  const [detail, sources, runs, domains] = await Promise.all([loadRequest(params.id, !access.preview), loadSources(), loadRuns("sort=updated&limit=200"), loadDomains()]);
  const r = detail.data;
  if (!r) {
    return (
      <>
        <PageHeader eyebrow="Onboarding request" title="Request not found" />
        <SourceError what="The request" error={detail.error} />
        <Empty icon={Inbox} title="This request does not exist, or it belongs to a domain you are not a member of" action={<Btn href="/os/intake">Back to Requests</Btn>} />
      </>
    );
  }
  const previewing = !!access.preview;
  const product = r.request_type === "PRODUCT";
  const steps = requestSteps(r);
  const live = r.items.filter((i) => !i.removed_at);
  const removed = r.items.filter((i) => i.removed_at);
  const registered = sources.data.sources.map((s) => ({ source_system_id: s.source_system_id, source_system_name: s.source_system_name }));
  const runsFor = (i: RequestItem) => runs.data.runs
    .filter((x) => !x.is_archived && x.current_state !== "CANCELLED" && (!r.domain_id || x.domain_name === r.domain_name)
      && (product ? x.pipeline === "GOLD" : x.source_system_name === i.source_name))
    .map((x) => ({ run_id: x.run_id, run_name: x.run_name, current_state: x.current_state }));
  const eventsDesc = [...r.events].reverse();
  const latestEvent = eventsDesc[0];
  const latestEventText = latestEvent ? (EVENT[latestEvent.kind] ?? ((x: RequestEvent) => pretty(x.kind)))(latestEvent) : null;
  const canManageSources = r.may.add_source || r.may.link_source || r.may.link_run || r.may.remove_source;

  return (
    <div>
      <PageHeader
        eyebrow={`Onboarding request · ${intentLabel(r)}`}
        title={r.title}
        subtitle={r.business_outcome && !r.business_outcome.startsWith("Store build of ") ? r.business_outcome : undefined}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Btn href={`/os/intake?view=selected&request=${r.request_id}`}>Back to Initiate</Btn>
            <DeleteRequestButton requestId={r.request_id} label="Delete" redirectTo="/os/intake?view=active" previewing={previewing} />
          </div>
        }
        meta={<>
          <Tag tone={r.status === "OPEN" ? phaseOf(r).tone : r.status === "DONE" ? "green" : "neutral"}>
            {r.status === "OPEN" ? phaseOf(r).label : r.status === "DONE" ? "Published" : pretty(r.status)}
          </Tag>
          <span className="os-muted">Domain: <b className="os-ink">{r.domain_name ?? (r.proposed_domain ? `${r.proposed_domain} (to create)` : "none")}</b></span>
          <span className="os-muted">· {r.pipeline === "GOLD" ? "Gold" : "Silver"} pipeline</span>
          {r.needed_by && (
            <span className={cn("inline-flex items-center gap-1", overdue(r) ? "text-[var(--os-red-ink)]" : "os-muted")}>
              · <CalendarClock className="h-3.5 w-3.5" />Needed by {r.needed_by.slice(0, 10)}{overdue(r) ? " (overdue)" : ""}
            </span>
          )}
          <span className="os-muted">· Filed by <b className="os-ink">{r.requested_by}</b> {ago(r.created_at)}</span>
          {r.updated_at && <span className="os-faint">· Updated {ago(r.updated_at)}</span>}
        </>}
      />
      <SourceError what="Part of the request" error={sources.error ?? runs.error} />
      {searchParams?.files && <Banner tone="amber" icon={AlertTriangle}><b>Not every file was attached.</b> {searchParams.files} Attach them again under Files.</Banner>}
      {(r.status === "CANCELLED" || r.status === "REJECTED") && (
        <Banner tone="amber" icon={r.status === "REJECTED" ? X : Ban}><b>{pretty(r.status)} by {r.closed_by}</b>{r.close_reason ? `: ${r.close_reason}` : "."}</Banner>
      )}
      {r.status === "OPEN" && r.attention && <Banner tone="red" icon={AlertTriangle}><b>Needs attention.</b> {r.attention}</Banner>}
      {r.duplicates.length > 0 && r.status === "OPEN" && (
        <Banner tone="blue" icon={Link2}>Another open request asks for the same thing: {r.duplicates.map((d, i) => (
          <span key={d.request_id}>{i ? ", " : ""}<Link className="underline" href={`/os/requests/${d.request_id}`}>{d.title}</Link> ({d.source_name})</span>))}.</Banner>
      )}

      {/* Stage progression: what is done, what is active now, and what comes next */}
      <ol className="os-card os-rise mb-4 grid gap-2 p-2.5 sm:grid-cols-3 xl:grid-cols-5" aria-label="Request path">
        {steps.map((s, i) => {
          const isCurrent = s.state === "current" || s.state === "attention";
          const owner = s.owner || "Release & QA";
          return (
            <li key={s.stage} aria-current={isCurrent ? "step" : undefined}>
              <Link
                href={requestStepHref(r, s.stage, access, true)}
                title={`${s.label} · ${STAGE_SUMMARY[s.stage]} (${owner})`}
                className={cn(
                  "flex h-full flex-col justify-between rounded-lg border px-3 py-2.5 transition hover:-translate-y-0.5 hover:shadow-sm",
                  s.state === "current"
                    ? "border-[var(--os-forest)] bg-[var(--os-green-bg)] shadow-sm"
                    : s.state === "attention"
                      ? "border-[var(--os-red-line)] bg-[var(--os-red-bg)]"
                      : s.state === "done"
                        ? "border-[var(--os-line)] bg-[var(--os-surface)] hover:border-[var(--os-forest)]"
                        : "border-[var(--os-line-soft)] bg-[var(--os-subtle)]/40 opacity-80 hover:border-[var(--os-forest)] hover:opacity-100",
                )}
              >
                <div className="flex items-center justify-between gap-1.5">
                  <div className="flex items-center gap-2">
                    <span className={cn("grid h-5 w-5 shrink-0 place-items-center rounded-full text-[10.5px] font-extrabold",
                      s.state === "done" ? "bg-[var(--os-green-bg)] text-[var(--os-green-ink)]" : s.state === "current" ? "bg-[var(--os-forest)] text-white"
                        : s.state === "attention" ? "bg-[var(--os-red-ink)] text-white" : "bg-[var(--os-neutral-bg)] os-faint")}>
                      {s.state === "done" ? <Check className="h-3 w-3" /> : s.state === "attention" ? <AlertTriangle className="h-3 w-3" /> : i + 1}
                    </span>
                    <span className="text-[13px] font-extrabold tracking-[-0.01em]">{s.label}</span>
                  </div>
                  {s.state === "current" && <Tag tone="blue">Active</Tag>}
                  {s.state === "attention" && <Tag tone="red">Flagged</Tag>}
                </div>
                <div className="os-muted mt-1 truncate text-[11px] font-medium">
                  {STAGE_SUMMARY[s.stage]}
                </div>
              </Link>
            </li>
          );
        })}
      </ol>

      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(340px,1fr)]">
        {/* Left column: Sources in scope, domain next step, files */}
        <div className="flex min-w-0 flex-col gap-4">
          {r.status === "OPEN" && r.waits.some((w) => !w.item_id) && (
            <section className="os-card os-rise border-l-[3px] border-l-[var(--os-forest)] p-4">
              <div className="os-eyebrow mb-2">Next step</div>
              <div className="os-divide">
                {r.waits.filter((w) => !w.item_id).map((w) => {
                  const href = actHref(w, undefined, r.domain_id, product, r.request_id, enhancesSource(r));
                  const you = waitIsMine(w, r.items, access);
                  const role = waitRole(w) ?? personaForPrivilege(w.privilege);
                  return (
                    <div key={`${w.privilege}-${w.label}`} className="flex flex-wrap items-center justify-between gap-3 py-2 first:pt-0 last:pb-0">
                      <div className="min-w-0">
                        <div className="text-[13px] font-bold leading-snug">{waitActionText(w)}</div>
                        <div className="os-faint text-[11px]">Owner: {role}</div>
                      </div>
                      {you && href ? (
                        <Btn href={href} variant="primary" title={w.label}>{waitCta(w)} <ArrowRight className="h-3.5 w-3.5" /></Btn>
                      ) : you ? (
                        <Tag tone="green">Your turn</Tag>
                      ) : (
                        <Tag>Waiting on {role}</Tag>
                      )}
                    </div>
                  );
                })}
              </div>
            </section>
          )}

          <Card title={`Sources in scope (${live.length})`} subtitle="Current status, tables, and next action for each source" pad={false}>
            {live.length ? (
              <div className="os-divide">
                {live.map((i) => {
                  const w = r.status === "OPEN" ? (r.waits.find((x) => x.item_id === i.item_id) ?? i.waits[0]) : undefined;
                  const act = w ? actHref(w, i, r.domain_id, product, r.request_id, enhancesSource(r)) : null;
                  const you = w ? waitIsMine(w, r.items, access) : false;
                  const role = w ? (waitRole(w) ?? personaForPrivilege(w.privilege)) : null;
                  const runHref = (i.active_run_id ?? i.run_id)
                    ? `/os/${i.stage === "DESIGN" ? "design" : i.stage === "REVIEW" ? "review" : i.stage === "DONE" ? "release" : "build"}?view=selected&run=${i.active_run_id ?? i.run_id}`
                    : null;
                  const kids = i.table_runs ?? [];
                  const doneKids = kids.filter((c) => c.stage === "DONE").length;
                  const allRunIds = Array.from(new Set([i.run_id, i.active_run_id, ...kids.map((c) => c.run_id)].filter(Boolean) as string[]));
                  return (
                    <div key={i.item_id} className="px-5 py-3.5">
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-[14px] font-extrabold">{i.source_name}</span>
                            <Tag tone={i.status === "ATTENTION" ? "red" : phaseOf(i).tone}>{phaseOf(i).label}</Tag>
                            {kids.length > 0 && (
                              <Tag tone={doneKids === kids.length ? "green" : "blue"}>
                                {doneKids} of {kids.length} {kids.length === 1 ? "pipeline" : "pipelines"} done
                              </Tag>
                            )}
                            {["MISSING", "UNREACHABLE", "NOT_LANDED", "ATTENTION"].includes(i.status) && (
                              <Tag tone={ITEM_TONE[i.status] ?? "neutral"}>{ITEM_LABEL[i.status] ?? pretty(i.status)}</Tag>
                            )}
                            {!i.source_system_id && !product && <Tag tone="amber">named only</Tag>}
                            {role && <span className="os-faint text-[11px]">· {role}</span>}
                          </div>
                          <div className="os-muted mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11.5px]">
                            <span>{i.status_detail ?? (w ? waitActionText(w) : "Ready")}</span>
                            {i.source_note && <span className="os-faint">· <b className="text-[var(--os-ink)]">Scope:</b> {i.source_note}</span>}
                          </div>
                        </div>
                        <div className="flex shrink-0 items-center gap-2">
                          {you && w && act ? (
                            <Btn href={act} variant="primary" title={w.label}>{waitCta(w)} <ArrowRight className="h-3.5 w-3.5" /></Btn>
                          ) : w && role ? (
                            <Tag>Waiting on {role}</Tag>
                          ) : null}
                          {runHref && (
                            <Btn href={runHref}>
                              {kids.length > 0 ? "Open workflow" : (i.run_name ?? "Open run")} <ArrowRight className="h-3.5 w-3.5" />
                            </Btn>
                          )}
                          {allRunIds.length > 0 && (
                            <DeleteRunButton runIds={allRunIds} iconOnly previewing={previewing} />
                          )}
                        </div>
                      </div>
                      {kids.length > 0 && (
                        <div className="mt-3 rounded-lg border border-[var(--os-line-soft)] bg-[var(--os-subtle)]/35 p-2.5" data-testid="child-pipelines">
                          <div className="mb-1.5 flex items-center justify-between px-1">
                            <span className="os-eyebrow">Child pipelines ({kids.length})</span>
                            <span className="os-faint text-[11px]">{doneKids} of {kids.length} completed</span>
                          </div>
                          <div className="os-divide">
                            {kids.map((c) => {
                              const label = childRunLabel(c);
                              const cHref = childRunHref(c);
                              const stageTitle = STAGE_NAME[c.stage] ?? c.stage;
                              const isDone = c.stage === "DONE";
                              const isAttn = c.status === "ATTENTION";
                              return (
                                <div key={c.run_id} className="flex flex-wrap items-center justify-between gap-2 px-1 py-2 text-[12px]" data-testid="child-pipeline-row">
                                  <div className="flex min-w-0 items-center gap-2">
                                    <span
                                      className={cn(
                                        "h-2 w-2 shrink-0 rounded-full",
                                        isDone ? "bg-[var(--os-green)]" : isAttn ? "bg-[var(--os-red-ink)]" : "bg-[var(--os-accent)]",
                                      )}
                                    />
                                    <Link href={cHref} className="truncate font-extrabold hover:underline" title={c.run_name ?? label}>
                                      {label}
                                    </Link>
                                    {c.run_name && c.run_name !== label && (
                                      <span className="os-faint truncate text-[11px]">· {c.run_name}</span>
                                    )}
                                  </div>
                                  <div className="flex shrink-0 items-center gap-1.5">
                                    <Tag tone={isDone ? "green" : isAttn ? "red" : "blue"}>
                                      {stageTitle} · {stateLabel(c.current_state)}
                                    </Tag>
                                    <Link href={cHref} className="os-btn px-2 py-1 text-[11px]">
                                      Open in {stageTitle} <ArrowRight className="h-3 w-3" />
                                    </Link>
                                    <DeleteRunButton runIds={[c.run_id]} iconOnly previewing={previewing} />
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}
                      {canManageSources && (
                        <details className="group mt-2">
                          <summary className="os-faint inline-flex cursor-pointer list-none items-center gap-1 text-[11px] font-semibold hover:text-[var(--os-ink)]">
                            <Settings2 className="h-3 w-3" /> Manage source / run link
                            <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-180" />
                          </summary>
                          <div className="mt-2 rounded-lg border border-[var(--os-line-soft)] bg-[var(--os-subtle)] p-2.5">
                            <ItemControls request={r} item={i} sources={registered} runs={runsFor(i)} previewing={previewing} />
                          </div>
                        </details>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="p-5"><Empty icon={CircleDashed} title={r.request_type === "DOMAIN" ? "No source asked for yet" : "No source"}>
                {r.request_type === "DOMAIN" ? "A domain request can be only the domain and its model; add the sources it should onboard when they are known." : undefined}
              </Empty></div>
            )}
            {r.may.add_source && (
              <details className="group border-t border-[var(--os-line)] px-5 py-3">
                <summary className="os-muted flex cursor-pointer list-none items-center justify-between text-[12px] font-bold hover:text-[var(--os-ink)]">
                  <span>+ Add another source to this request</span>
                  <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
                </summary>
                <div className="mt-3">
                  <AddSource requestId={r.request_id} sources={registered} previewing={previewing} />
                </div>
              </details>
            )}
            {removed.length > 0 && <div className="os-faint border-t border-[var(--os-line)] px-5 py-2.5 text-[11px]">Removed: {removed.map((i) => i.source_name).join(", ")}</div>}
          </Card>

          {r.may.link_domain && (
            <Card title="Link domain" subtitle={`Asked for a new domain${r.proposed_domain ? `, ${r.proposed_domain}` : ""}.`}>
              <LinkDomain requestId={r.request_id} domains={domains.data.map((d) => ({ domain_id: d.domain_id, domain_name: d.domain_name }))} previewing={previewing} />
            </Card>
          )}

          {(r.request_type === "DOMAIN" || (r.files ?? []).length > 0) && (
            <Card title="Files" subtitle={r.domain_id ? "Handed to the domain's documents" : "Kept on the request until the domain exists"}>
              <RequestFiles requestId={r.request_id} files={r.files ?? []} mayAdd={!!r.may.add_files && r.request_type === "DOMAIN"} previewing={previewing} domainId={r.domain_id} />
            </Card>
          )}
        </div>

        {/* Right column: History & Request actions */}
        <div className="flex min-w-0 flex-col gap-3">
          <details className="os-card group overflow-hidden" data-testid="request-history-dropdown">
            <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-2 px-5 py-3.5 transition hover:bg-[var(--os-subtle)]">
              <div className="flex min-w-0 flex-wrap items-center gap-2 text-[13px]">
                <Clock className="os-muted h-4 w-4 shrink-0" />
                <span className="font-extrabold">History</span>
                <Tag tone="neutral">{eventsDesc.length}</Tag>
                {latestEvent && (
                  <span className="os-muted truncate text-[12px]">
                    · <b className="text-[var(--os-ink)]">{latestEventText}</b> ({ago(latestEvent.created_at)})
                  </span>
                )}
              </div>
              <span className="os-faint inline-flex items-center gap-1 text-[11.5px] font-bold">
                <span className="group-open:hidden">Show</span>
                <span className="hidden group-open:inline">Hide</span>
                <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" />
              </span>
            </summary>
            <div className="os-divide border-t border-[var(--os-line)] px-5">
              {eventsDesc.map((e) => (
                <ActivityRow key={e.event_id} tone={e.kind === "REJECTED" || e.kind === "CANCELLED" ? "red" : e.kind === "DONE" ? "green" : e.automatic ? "blue" : "neutral"}
                             icon={e.automatic ? Sparkles : e.kind === "DONE" ? CheckCircle2 : e.kind === "COMMENT" ? Inbox : Check}
                             title={<>{(EVENT[e.kind] ?? ((x: RequestEvent) => pretty(x.kind)))(e)} <span className="os-faint font-semibold">· {e.automatic ? "platform" : e.actor}</span></>}
                             sub={e.note ? waitActionText({ label: e.note }) : undefined} when={ago(e.created_at)} />
              ))}
            </div>
          </details>

          <details className="os-card group overflow-hidden" open={r.status === "OPEN"}>
            <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-5 py-3.5 transition hover:bg-[var(--os-subtle)]">
              <div className="flex items-center gap-2 text-[13px]">
                <MessageSquare className="os-muted h-4 w-4 shrink-0" />
                <span className="font-extrabold">Comments & request actions</span>
                <span className="os-faint text-[11.5px]">· Comment, {r.status === "OPEN" ? "cancel, or reject" : "or reopen"}</span>
              </div>
              <ChevronDown className="os-faint h-4 w-4 transition-transform group-open:rotate-180" />
            </summary>
            <div className="border-t border-[var(--os-line)] px-5 py-4">
              <RequestControls request={r} previewing={previewing} />
            </div>
          </details>
        </div>
      </div>
    </div>
  );
}

