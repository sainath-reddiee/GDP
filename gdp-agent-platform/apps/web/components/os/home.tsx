import Link from "next/link";
import type { ComponentType } from "react";
import { AlertTriangle, ArrowRight, ArrowUpRight, BookOpen, Boxes, ChevronDown, Clock, Crown, Database, Lock, Plus, ShieldCheck, Siren, Stamp, Wand2 } from "lucide-react";
import { stateLabel } from "@/lib/run-labels";
import { canSee, has, screenOfPath } from "@/lib/os/access";
import type { Domain } from "@/lib/os/data";
import type { OsAccess } from "@/lib/os/session";
import {
  PRIORITY_TONE, STAGE_NAME, actHref, childRunHref, childRunLabel, enhancesSource, intentOf, intentLabel, overdue, phaseOf, requestSteps, waitActionText, waitCta, waitRole, type OnboardingRequest, type RequestDetail, type RequestType,
} from "@/lib/os/requests";
import { Btn, Tag, ago, pretty } from "./ui";
import { Track, type TrackStep } from "./stage-board";
import { RUNNING_STATES, requestStepHref, stageHref, waitIsMine } from "@/lib/os/work";
import { RequestFiles } from "@/components/os/request-files";
import { DeleteRequestButton, DeleteRunButton, LiveWorkflowRefresh, StartEnhancementRun, StartSourceRunButton } from "./request-ui";
import type { ActionItem } from "@/lib/os/actions";
import { personaForPrivilege } from "@/lib/os/personas";
import { cn } from "@/lib/utils";

// Home and the Intake board: the build workflows (onboarding requests) with the step each one is at and who it waits on,
// the onboarded domains, and the panel that starts a new one. Server-safe, like ./ui.

type Icon = ComponentType<{ className?: string }>;

export const TYPE_ICON: Record<RequestType, Icon> = { DOMAIN: Boxes, SOURCE: Database, PRODUCT: Crown };
const TYPE_TINT: Record<RequestType, string> = {
  DOMAIN: "bg-[var(--os-purple-bg)] text-[var(--os-purple-ink)]",
  SOURCE: "bg-[var(--os-blue-bg)] text-[var(--os-blue-ink)]",
  PRODUCT: "bg-[var(--os-amber-bg)] text-[var(--os-amber-ink)]",
};

/** Who the request waits on: the role its wait names ("Model approver: approve the field mapping"), else the stage's owner. */
const waitingOn = (r: OnboardingRequest, owner: string) => (r.waiting_label?.includes(":") ? r.waiting_label.split(":")[0] : owner);
const cleanTitle = (title: string) => title.replace(/\s*\(E2E test\)\s*$/i, "").trim();

/** The request's path as a track, waiting on the role its wait names. Each step links directly to that stage. */
export function RequestTrack({ r, access, onIntake, className }: { r: OnboardingRequest; access?: OsAccess; onIntake?: boolean; className?: string }) {
  const steps = requestSteps(r);
  const now = steps.find((s) => s.state === "current" || s.state === "attention");
  return (
    <Track
      className={className}
      steps={steps.map((s) => ({
        key: s.stage,
        label: s.label,
        owner: s.owner,
        state: s.state as TrackStep["state"],
        href: requestStepHref(r, s.stage, access, onIntake),
      }))}
      waiting={r.status === "OPEN" && now ? waitingOn(r, now.owner) || "a person" : null}
    />
  );
}

/** One build workflow: what it is, its steps with where it waits, and the viewer's next action when it is theirs. */
export function WorkflowRow({ r, access, href }: { r: OnboardingRequest; access: OsAccess; href?: string }) {
  const I = TYPE_ICON[r.request_type] ?? Database;
  const open = r.status === "OPEN";
  // the viewer's next step, unless an agent is still running it (as requestNeedsMe)
  const mine = open ? r.waits.find((w) => waitIsMine(w, r.items, access)) : undefined;
  const item = mine ? r.items.find((i) => i.item_id === mine.item_id) : undefined;
  const next = mine ? actHref(mine, item, r.domain_id, r.request_type === "PRODUCT", r.request_id, enhancesSource(r)) : null;
  const detailsHref = href ?? `/os/requests/${r.request_id}`;
  const sources = r.items.filter((i) => !i.removed_at);
  const domain = r.domain_name ?? (r.proposed_domain ? `${r.proposed_domain} (new)` : null);
  const title = cleanTitle(r.title);
  const sourceSummary = sources.length === 1 ? sources[0].source_name : sources.length > 1 ? `${sources.length} sources` : null;
  const childRuns = sources.flatMap((i) => i.table_runs ?? []);
  const doneChildren = childRuns.filter((c) => c.stage === "DONE").length;
  const agentRunning = open && r.items.some((i) => !i.removed_at && !!i.run_state && RUNNING_STATES.has(i.run_state));
  return (
    <article data-testid="workflow-row" className={cn("os-card os-rise p-3.5 transition hover:shadow-[var(--os-shadow-pop)]",
                                                       mine && "border-l-[3px] border-l-[var(--os-forest)]")}>
      <LiveWorkflowRefresh active={agentRunning} />
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-[min(100%,260px)] flex-1 items-start gap-3">
          <span className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-lg", TYPE_TINT[r.request_type])}><I className="h-4 w-4" /></span>
          <div className="min-w-0">
            <Link href={detailsHref} className="block truncate text-[14px] font-extrabold tracking-[-0.01em] hover:underline" title={title}>{title}</Link>
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px]">
              <Tag tone={intentOf(r) === "ENHANCE" ? "purple" : "blue"}>{intentLabel(r)}</Tag>
              {domain && <span className="os-muted font-semibold">{domain}</span>}
              {sourceSummary && r.request_type !== "PRODUCT" && <span className="os-faint">· {sourceSummary}</span>}
              {childRuns.length > 0 && (
                <span className="os-faint font-semibold" data-testid="master-child-summary">
                  · {doneChildren} of {childRuns.length} {childRuns.length === 1 ? "pipeline" : "pipelines"} done
                </span>
              )}
              {overdue(r) && <Tag tone="red">Overdue</Tag>}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {mine ? (
            <Btn href={next ?? detailsHref} variant="primary" title={mine.label}>{next ? waitCta(mine) : "Open workflow"} <ArrowRight className="h-3.5 w-3.5" /></Btn>
          ) : open ? (
            // whose step it is, by persona (the step's own wait names it)
            <Tag tone={r.attention ? "red" : "neutral"} title={phaseOf(r).label}>
              {r.attention ? "Needs attention" : `Waiting on ${(r.waits[0] && waitRole(r.waits[0])) || phaseOf(r).label}`}
            </Tag>
          ) : (
            <Tag tone={r.status === "DONE" ? "green" : "neutral"}>{r.status === "DONE" ? "Published" : pretty(r.status)}</Tag>
          )}
          <Link href={detailsHref} className={cn("os-btn", mine && "max-sm:!hidden")} aria-label={`Open ${title}`} title="View request summary, sources, and history">
            Details
          </Link>
          <DeleteRequestButton requestId={r.request_id} iconOnly previewing={!!access.preview} />
        </div>
      </div>
      <RequestTrack r={r} access={access} className="mt-3" />
    </article>
  );
}

const START: { type: "domain" | "source" | "product"; label: string; short: string; what: string; icon: Icon; tint: string; soon?: boolean;
  new: string; enhance: string }[] = [
  { type: "domain", label: "Data domain", short: "Domain", what: "A business area's model, contract and memory", icon: Boxes, tint: TYPE_TINT.DOMAIN,
    new: "A business domain not on the platform yet", enhance: "Extend an existing domain's model: entities, attributes, rules" },
  { type: "source", label: "Data source", short: "Source", what: "A system that feeds a domain", icon: Database, tint: TYPE_TINT.SOURCE,
    new: "Onboard a system that is not on the platform yet", enhance: "More tables or scope from a source already registered" },
  { type: "product", label: "Data product", short: "Product", what: "Governed Gold products from mastered Silver domains", icon: Crown, tint: TYPE_TINT.PRODUCT,
    new: "A new governed data product", enhance: "Change a data product already published" },
];

const FILE_REQUEST = "Filing a workflow needs RUN.CREATE, SOURCE.CONNECT, DOMAIN.EDIT or REQUEST.CHANGES: ask a governance admin.";

/** New or Enhance for a domain, a source or a data product: one compact row each. Everyone sees it; the buttons follow
 *  the viewer's role. */
export function StartPanel({ access, inflight }: {
  access: OsAccess;
  /** open requests by intent; each count filters the Intake board to that intent (`active` is the one on show) */
  inflight?: { new: number; enhance: number; active: "new" | "enhance" | null };
}) {
  const may = canSee(access, "intakeNew");
  return (
    <section className="os-card os-rise px-4 pb-3 pt-3.5" data-testid="start-panel" aria-labelledby="start-title">
      <h2 id="start-title" className="text-[14.5px] font-extrabold tracking-[-0.01em]">Start a workflow</h2>
      <div className="os-divide mt-1.5">
        {START.map((t) => {
          const I = t.icon;
          const button = (intent: "new" | "enhance") => {
            const text = intent === "new" ? "New" : "Enhance";
            const B = intent === "new" ? Plus : Wand2;
            const why = t.soon ? "Data products are coming soon" : !may ? FILE_REQUEST : intent === "new" ? t.new : t.enhance;
            const cls = "os-btn justify-center px-2.5 py-1.5 text-[11.5px]";
            return t.soon || !may ? (
              <span aria-disabled="true" title={why} data-testid={`start-${t.type}-${intent}`}
                    className={cn(cls, "cursor-not-allowed opacity-50")}><B className="h-3 w-3" />{text}</span>
            ) : (
              <Link href={`/os/intake/new?${new URLSearchParams({ type: t.type, intent })}`} title={why} data-testid={`start-${t.type}-${intent}`}
                    className={cn(cls, intent === "new" && "os-btn-primary")}><B className="h-3 w-3" />{text}</Link>
            );
          };
          return (
            <div key={t.type} className="flex items-center gap-2 py-2" title={`${t.label}: ${t.what}`} data-testid={`start-${t.type}`}>
              <span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-lg", t.tint)}><I className="h-3.5 w-3.5" /></span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[12.5px] font-extrabold">{t.short}</span>
                {t.soon && <span className="block text-[10px] font-bold text-[var(--os-amber-ink)]">Coming soon</span>}
              </span>
              <span className="flex shrink-0 gap-1.5">{button("new")}{button("enhance")}</span>
            </div>
          );
        })}
      </div>
      {inflight && (
        <div className="mt-1 flex items-center gap-1.5 border-t border-[var(--os-line-soft)] pt-2 text-[11px]" data-testid="inflight">
          <span className="os-faint font-bold">In flight</span>
          {(["new", "enhance"] as const).map((k) => (
            <Link key={k} href={inflight.active === k ? "/os/intake?view=active" : `/os/intake?view=active&intent=${k}`} scroll={false}
                  aria-current={inflight.active === k ? "true" : undefined} data-testid={`inflight-${k}`}
                  className={cn("rounded-full px-2 py-0.5 font-bold transition", inflight.active === k
                    ? "bg-[var(--os-forest)] text-white" : k === "new" ? "bg-[var(--os-blue-bg)] text-[var(--os-blue-ink)]" : "bg-[var(--os-purple-bg)] text-[var(--os-purple-ink)]")}>
              {k === "new" ? "New" : "Enhance"} {k === "new" ? inflight.new : inflight.enhance}
            </Link>
          ))}
        </div>
      )}
      {!may && (
        <p className="os-faint mt-1 flex items-center gap-1.5 border-t border-[var(--os-line-soft)] pt-2 text-[10.5px]" title={FILE_REQUEST}>
          <Lock className="h-3 w-3 shrink-0" />View only: filing needs a builder role
        </p>
      )}
    </section>
  );
}

/** The selected request on the Intake board: its steps, who acts now (with the viewer's next action), its sources and
 *  where each is worked, and the latest history. The full request page keeps every control. */
export function RequestFocus({ r, access }: { r: RequestDetail; access: OsAccess }) {
  const I = TYPE_ICON[r.request_type] ?? Database;
  const product = r.request_type === "PRODUCT";
  const sources = r.items.filter((i) => !i.removed_at);
  const enhance = enhancesSource(r);
  const at = stageHref(r);
  const hasMyAction = r.status === "OPEN" && r.waits.some((w) => waitIsMine(w, r.items, access));
  const currentStep = requestSteps(r).find((s) => s.state === "current" || s.state === "attention");
  const agentRunning = r.status === "OPEN" && sources.some((i) => !!i.run_state && RUNNING_STATES.has(i.run_state));
  const domainWaits = r.status === "OPEN" ? r.waits.filter((w) => !w.item_id) : [];
  return (
    <section className="os-card os-rise p-5" data-testid="request-focus">
      <LiveWorkflowRefresh active={agentRunning} />
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className={cn("grid h-10 w-10 shrink-0 place-items-center rounded-xl", TYPE_TINT[r.request_type])}><I className="h-5 w-5" /></span>
          <div className="min-w-0">
            <h2 className="text-[18px] font-extrabold tracking-[-0.02em]">{r.title}</h2>
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11.5px]">
              <Tag tone={intentOf(r) === "ENHANCE" ? "purple" : "blue"}>{intentLabel(r)}</Tag>
              <span className="os-muted font-semibold">{r.domain_name ?? (r.proposed_domain ? `${r.proposed_domain} (new)` : "no domain")}</span>
              {overdue(r) && <Tag tone="red">Overdue</Tag>}
              <span className="os-faint">· filed by {r.requested_by} {ago(r.created_at)}</span>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {at && !hasMyAction && r.stage !== "ENABLE" && (
            <Btn href={at} variant="primary">
              Open in {currentStep?.label ?? "stage"} <ArrowRight className="h-3.5 w-3.5" />
            </Btn>
          )}
          <Btn href={`/os/requests/${r.request_id}`} title="Add or remove sources, link runs, comment, or close">Manage request</Btn>
          <DeleteRequestButton requestId={r.request_id} label="Delete" redirectTo="/os/intake?view=active" previewing={!!access.preview} />
        </div>
      </div>
      {r.business_outcome && !r.business_outcome.startsWith("Store build of ") && (
        <p className="os-muted mt-3 max-w-3xl text-[12.5px] leading-relaxed">{r.business_outcome}</p>
      )}
      <RequestTrack r={r} access={access} onIntake className="mt-5" />
      {r.status === "OPEN" && r.attention && (
        <p className="mt-3 flex items-start gap-1.5 text-[12px] text-[var(--os-red-ink)]"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{r.attention}</p>
      )}

      {domainWaits.length > 0 && (
        <div className="mt-5 rounded-xl border border-[var(--os-line)] bg-[var(--os-subtle)]/40 px-4 py-3">
          <div className="os-eyebrow mb-1.5">Next step</div>
          <div className="os-divide">
            {domainWaits.map((w) => {
              const href = actHref(w, undefined, r.domain_id, product, r.request_id, enhance);
              const you = waitIsMine(w, r.items, access);
              const role = waitRole(w) ?? personaForPrivilege(w.privilege);
              return (
                <div key={`${w.privilege}-${w.label}`} className="flex flex-wrap items-center justify-between gap-3 py-2 first:pt-0 last:pb-0">
                  <div className="min-w-0 text-[12.5px]">
                    <b className="block leading-snug">{waitActionText(w)}</b>
                    <span className="os-faint text-[11px]">Owner: {role}</span>
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
        </div>
      )}

      <div className="mt-5">
        <h3 className="os-eyebrow mb-2">{product ? "Gold run" : `Sources (${sources.length})`}</h3>
        {sources.length ? (
          <div className="os-divide rounded-xl border border-[var(--os-line)] px-4">
            {sources.map((i) => {
              const w = r.status === "OPEN" ? (r.waits.find((x) => x.item_id === i.item_id) ?? i.waits[0]) : undefined;
              const act = w ? actHref(w, i, r.domain_id, product, r.request_id, enhance) : null;
              const you = w ? waitIsMine(w, r.items, access) : false;
              const role = w ? (waitRole(w) ?? personaForPrivilege(w.privilege)) : null;
              const runHref = (i.active_run_id ?? i.run_id)
                ? `/os/${i.stage === "DESIGN" ? "design" : i.stage === "REVIEW" ? "review" : i.stage === "DONE" ? "release" : "build"}?view=selected&run=${i.active_run_id ?? i.run_id}`
                : null;
              const kids = i.table_runs ?? [];
              const doneKids = kids.filter((c) => c.stage === "DONE").length;
              const allRunIds = Array.from(new Set([i.run_id, i.active_run_id, ...kids.map((c) => c.run_id)].filter(Boolean) as string[]));
              return (
                <div key={i.item_id} className="py-3 text-[12.5px]">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <b className="text-[13.5px] font-extrabold">{i.source_name}</b>
                        <Tag tone={i.status === "ATTENTION" ? "red" : phaseOf(i).tone}>{phaseOf(i).label}</Tag>
                        {kids.length > 0 && (
                          <Tag tone={doneKids === kids.length ? "green" : "blue"}>
                            {doneKids} of {kids.length} {kids.length === 1 ? "pipeline" : "pipelines"} done
                          </Tag>
                        )}
                        {role && <span className="os-faint text-[11px]">· {role}</span>}
                      </div>
                      <p className="os-muted mt-1 text-[12px]">
                        {kids.length > 0
                          ? i.status_detail ?? `${doneKids} of ${kids.length} child pipelines done`
                          : i.run_name
                            ? `${i.run_name}: ${stateLabel(i.run_state).toLowerCase()}`
                            : i.status_detail ?? (w ? waitActionText(w) : "Ready")}
                      </p>
                      {i.source_note && (
                        <p className="os-faint mt-1 text-[11.5px]" data-testid="item-scope">
                          <b className="text-[var(--os-ink)]">{enhance ? "To add" : "Needed"}:</b> {i.source_note}
                        </p>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {you && enhance && w?.privilege === "RUN.CREATE" && !i.run_id && i.source_system_id ? (
                        <StartEnhancementRun
                          requestId={r.request_id}
                          itemId={i.item_id}
                          source={i.source_name}
                          sourceSystemId={i.source_system_id}
                          domainId={r.domain_id}
                          domainName={r.domain_name}
                          note={i.source_note}
                          previewing={!!access.preview}
                        />
                      ) : you && !enhance && !product && !i.run_id && i.source_system_id && i.status === "NO_RUN" ? (
                        <StartSourceRunButton
                          requestId={r.request_id}
                          itemId={i.item_id}
                          sourceName={i.source_name}
                          sourceSystemId={i.source_system_id}
                          domainId={r.domain_id}
                          domainName={r.domain_name}
                          note={i.source_note}
                          label="Start onboarding run"
                          previewing={!!access.preview}
                        />
                      ) : you && w && act ? (
                        <Btn href={act} variant="primary" title={w.label}>{waitCta(w)} <ArrowRight className="h-3.5 w-3.5" /></Btn>
                      ) : w && role ? (
                        <Tag>Waiting on {role}</Tag>
                      ) : runHref ? (
                        <Btn href={runHref}>Open run <ArrowRight className="h-3.5 w-3.5" /></Btn>
                      ) : null}
                      {allRunIds.length > 0 && (
                        <DeleteRunButton runIds={allRunIds} iconOnly previewing={!!access.preview} />
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
                                <DeleteRunButton runIds={[c.run_id]} iconOnly previewing={!!access.preview} />
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : <p className="os-faint text-[12px]">No source asked for yet.</p>}
      </div>

      {(r.request_type === "DOMAIN" || (r.files ?? []).length > 0) && (
        <div className="mt-5">
          <h3 className="os-eyebrow mb-2">Files ({(r.files ?? []).length})</h3>
          <RequestFiles requestId={r.request_id} files={r.files ?? []} mayAdd={!!r.may?.add_files && r.request_type === "DOMAIN"}
                        previewing={!!access.preview} domainId={r.domain_id} />
        </div>
      )}
    </section>
  );
}

/** Compact collapsible request history card placed below Start a workflow on Initiate Selected. */
export function RequestHistoryCard({ r }: { r: RequestDetail }) {
  if (!r.events?.length) return null;
  const eventsDesc = [...r.events].reverse();
  const latest = eventsDesc[0];
  const cleanNote = (note?: string | null) => note ? waitActionText({ label: note }) : null;
  return (
    <details className="os-card group overflow-hidden" data-testid="request-history-card">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-4 py-3 text-[12px] transition hover:bg-[var(--os-subtle)]">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Clock className="os-muted h-3.5 w-3.5 shrink-0" />
            <b className="font-extrabold">History</b>
            <Tag tone="neutral">{eventsDesc.length}</Tag>
          </div>
          {latest && (
            <div className="os-muted mt-1 truncate text-[11px]">
              Latest: <b className="text-[var(--os-ink)]">{cleanNote(latest.note) ?? pretty(latest.kind)}</b> ({ago(latest.created_at)})
            </div>
          )}
        </div>
        <span className="os-faint inline-flex shrink-0 items-center gap-1 text-[11px] font-bold">
          <span className="group-open:hidden">Show</span>
          <span className="hidden group-open:inline">Hide</span>
          <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
        </span>
      </summary>
      <ol className="os-scroll-y flex max-h-[260px] flex-col gap-2 border-t border-[var(--os-line)] px-4 py-3 text-[11.5px]">
        {eventsDesc.map((e) => (
          <li key={e.event_id} className="flex items-baseline gap-2">
            <span className="os-faint w-14 shrink-0 tabular-nums text-[10.5px]">{ago(e.created_at)}</span>
            <span className="min-w-0 flex-1">
              <b>{pretty(e.kind)}</b>
              {e.note ? <span className="os-muted">: {cleanNote(e.note)}</span> : null}
              <span className="os-faint"> · {e.automatic ? "platform" : e.actor}</span>
            </span>
          </li>
        ))}
      </ol>
    </details>
  );
}

/** The onboarded domains, as a row of cards below the workflows on Home: what each holds and what is moving in it. Domains with
 *  work for the viewer come first, then those with open workflows, then the largest. */
export function OnboardedDomains({ domains, requests, access, max = 5 }: {
  domains: Domain[]; requests: OnboardingRequest[]; access: OsAccess; max?: number;
}) {
  const open = (d: Domain) => requests.filter((r) => r.status === "OPEN" && r.domain_id === d.domain_id);
  const mine = (d: Domain) => open(d).filter((r) => r.waits.some((w) => has(access, w.privilege))).length;
  const shown = [...domains]
    .sort((a, b) => mine(b) - mine(a) || open(b).length - open(a).length || b.target_tables - a.target_tables || a.domain_name.localeCompare(b.domain_name))
    .slice(0, max);
  return (
    <section aria-labelledby="domains-title" data-testid="onboarded-domains">
      <div className="mb-2.5 flex items-baseline justify-between gap-3">
        <h2 id="domains-title" className="text-[15px] font-extrabold tracking-[-0.01em]">
          Onboarded domains <span className="os-faint ml-1 text-[12px] font-bold tabular-nums">{domains.length}</span>
        </h2>
        <Link href="/os/knowledge?view=domains" className="os-faint inline-flex items-center gap-1 text-[11.5px] font-bold hover:text-[var(--os-accent-ink)]">
          All domains <ArrowRight className="h-3 w-3" />
        </Link>
      </div>
      {shown.length ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {shown.map((d) => {
            const n = open(d).length;
            const you = mine(d);
            return (
              <Link key={d.domain_id} href={`/os/knowledge/domains/${d.domain_id}`} data-testid="domain-card"
                    className={cn("os-card os-rise group flex flex-col gap-2 p-3 transition hover:-translate-y-0.5 hover:shadow-[var(--os-shadow-pop)]",
                                  you > 0 && "border-l-[3px] border-l-[var(--os-accent)]")}>
                <div className="flex items-start gap-2.5">
                  <span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-lg", TYPE_TINT.DOMAIN)}><Boxes className="h-3.5 w-3.5" /></span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-extrabold tracking-[-0.01em]">{d.domain_name}</span>
                    <span className="os-faint line-clamp-2 text-[11px] leading-snug" title={d.description ?? undefined}>{d.description || (d.mdm_role ? `${pretty(d.mdm_role)} domain` : "Business domain")}</span>
                  </span>
                  <ArrowUpRight className="os-faint h-3.5 w-3.5 shrink-0 opacity-0 transition group-hover:opacity-100" />
                </div>
                {(you > 0 || (d.gold_tables ?? 0) > 0 || d.mdm_role) && (
                  <div className="flex flex-wrap gap-1.5">
                    {you > 0 && <Tag tone="green">{you} need{you === 1 ? "s" : ""} you</Tag>}
                    {(d.gold_tables ?? 0) > 0 && <Tag tone="amber">{d.gold_tables} Gold</Tag>}
                    {d.mdm_role && <Tag>{pretty(d.mdm_role)}</Tag>}
                  </div>
                )}
                <div className="os-faint mt-auto flex flex-wrap items-baseline gap-x-1.5 border-t border-[var(--os-line-soft)] pt-2 text-[11px]">
                  <span><b className="text-[var(--os-ink)] tabular-nums">{d.target_tables}</b> tables</span><span>·</span>
                  <span><b className="text-[var(--os-ink)] tabular-nums">{d.knowledge_items}</b> memory</span><span>·</span>
                  <span><b className="text-[var(--os-ink)] tabular-nums">{n}</b> open</span>
                </div>
              </Link>
            );
          })}
        </div>
      ) : (
        <div className="os-card p-5 text-[12.5px]"><span className="os-muted">No domain is onboarded yet. Start one with <b>Data domain, New</b>.</span></div>
      )}
      {domains.length > shown.length && (
        <Link href="/os/knowledge?view=domains" className="os-faint mt-2 inline-block text-[11.5px] font-bold hover:text-[var(--os-accent-ink)]">
          +{domains.length - shown.length} more domain{domains.length - shown.length === 1 ? "" : "s"}
        </Link>
      )}
    </section>
  );
}


const GROUP: Record<ActionItem["group"], { icon: Icon; tint: string; where: string }> = {
  "Gates": { icon: ShieldCheck, tint: "bg-[var(--os-amber-bg)] text-[var(--os-amber-ink)]", where: "Run gate" },
  "Approvals": { icon: Stamp, tint: "bg-[var(--os-amber-bg)] text-[var(--os-amber-ink)]", where: "Approval" },
  "Models and quality": { icon: Boxes, tint: "bg-[var(--os-purple-bg)] text-[var(--os-purple-ink)]", where: "Review" },
  "Knowledge and skills": { icon: BookOpen, tint: "bg-[var(--os-blue-bg)] text-[var(--os-blue-ink)]", where: "Knowledge" },
  "Operations": { icon: Siren, tint: "bg-[var(--os-red-bg)] text-[var(--os-red-ink)]", where: "Operations" },
};

/** One action from across the platform in Home's queue (lib/os/actions.ts): a run at a person's step, an approval, a
 *  model or check in review, knowledge or a skill to review, an incident. It names the persona that acts on it; the
 *  action button is the viewer's only when their current persona may act, else the row opens it to look at. */
export function PlatformActionRow({ a }: { a: ActionItem }) {
  const g = GROUP[a.group];
  const I = a.tone === "red" && a.group === "Gates" ? AlertTriangle : g.icon;
  return (
    <article data-testid="platform-action" className={cn("os-card os-rise p-3.5 transition hover:shadow-[var(--os-shadow-pop)]",
                                                          a.mine && "border-l-[3px]", a.mine && (a.tone === "red" ? "border-l-[var(--os-red-ink)]" : "border-l-[var(--os-forest)]"))}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-[min(100%,260px)] flex-1 items-start gap-3">
          <span className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-lg", a.tone === "red" ? "bg-[var(--os-red-bg)] text-[var(--os-red-ink)]" : g.tint)}><I className="h-4 w-4" /></span>
          <div className="min-w-0">
            <Link href={a.href} className="block truncate text-[14px] font-extrabold tracking-[-0.01em] hover:underline" title={a.title}>{a.title}</Link>
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px]">
              <Tag tone={a.tone === "red" ? "red" : a.tone === "purple" ? "purple" : a.tone === "blue" ? "blue" : "amber"}>{g.where}</Tag>
              <span className="os-muted">{a.detail}</span>
              {a.since && <span className="os-faint">· {ago(a.since)}</span>}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {a.mine ? (
            <>
              <Tag tone="green">Your turn</Tag>
              <Btn href={a.href} variant="primary">{a.cta} <ArrowRight className="h-3.5 w-3.5" /></Btn>
            </>
          ) : (
            <>
              <Tag tone="neutral">Waiting on {a.persona}</Tag>
              <Btn href={a.href}>Open <ArrowRight className="h-3.5 w-3.5" /></Btn>
            </>
          )}
        </div>
      </div>
    </article>
  );
}
