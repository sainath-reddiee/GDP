import Link from "next/link";
import type { ComponentType } from "react";
import { AlertTriangle, ArrowRight, ArrowUpRight, Boxes, Crown, Database, Lock, Plus, Wand2 } from "lucide-react";
import { stateLabel } from "@/lib/run-labels";
import { canSee, has } from "@/lib/os/access";
import type { Domain } from "@/lib/os/data";
import type { OsAccess } from "@/lib/os/session";
import {
  PRIORITY_TONE, actHref, enhancesSource, intentOf, intentLabel, overdue, phaseOf, requestSteps, type OnboardingRequest, type RequestDetail, type RequestType,
} from "@/lib/os/requests";
import { Btn, Tag, ago, pretty } from "./ui";
import { Track, type TrackStep } from "./stage-board";
import { stageHref } from "@/lib/os/work";
import { StartEnhancementRun } from "./request-ui";
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

/** The request's path as a track, waiting on the role its wait names, for how long. */
export function RequestTrack({ r, className }: { r: OnboardingRequest; className?: string }) {
  const steps = requestSteps(r);
  const since = r.stage_changed_at ? ago(r.stage_changed_at).replace(/ ago$/, "") : "";
  const now = steps.find((s) => s.state === "current" || s.state === "attention");
  return (
    <Track className={className} steps={steps.map((s) => ({ key: s.stage, label: s.label, owner: s.owner, state: s.state as TrackStep["state"] }))}
           waiting={r.status === "OPEN" && now ? waitingOn(r, now.owner) || "a person" : null}
           since={since && since !== "just now" ? since : null} />
  );
}

/** One build workflow: what it is, its steps with where it waits, and the viewer's next action when it is theirs. */
export function WorkflowRow({ r, access, href }: { r: OnboardingRequest; access: OsAccess; href?: string }) {
  const I = TYPE_ICON[r.request_type] ?? Database;
  const open = r.status === "OPEN";
  const mine = open ? r.waits.find((w) => has(access, w.privilege)) : undefined;
  const item = mine ? r.items.find((i) => i.item_id === mine.item_id) : undefined;
  const next = mine ? actHref(mine, item, r.domain_id, r.request_type === "PRODUCT", r.request_id, enhancesSource(r)) : null;
  const sources = r.items.filter((i) => !i.removed_at);
  const domain = r.domain_name ?? (r.proposed_domain ? `${r.proposed_domain} (new)` : null);
  return (
    <article data-testid="workflow-row" className={cn("os-card os-rise p-3.5 transition hover:shadow-[var(--os-shadow-pop)]",
                                                       mine && "border-l-[3px] border-l-[var(--os-forest)]")}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-[min(100%,260px)] flex-1 items-start gap-3">
          <span className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-lg", TYPE_TINT[r.request_type])}><I className="h-4 w-4" /></span>
          <div className="min-w-0">
            <Link href={href ?? `/os/requests/${r.request_id}`} className="block truncate text-[14px] font-extrabold tracking-[-0.01em] hover:underline" title={r.title}>{r.title}</Link>
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px]">
              <Tag tone={intentOf(r) === "ENHANCE" ? "purple" : "blue"}>{intentLabel(r)}</Tag>
              {domain && <span className="os-muted font-semibold">{domain}</span>}
              {sources.length > 0 && r.request_type !== "PRODUCT" && <span className="os-faint">· {sources.length} source{sources.length === 1 ? "" : "s"}</span>}
              {r.priority === "HIGH" && <Tag tone={PRIORITY_TONE.HIGH}>High priority</Tag>}
              {overdue(r) && <Tag tone="red">Overdue</Tag>}
              <span className="os-faint">· filed {ago(r.created_at)}</span>
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {mine ? (
            <>
              <Tag tone="green">Your turn</Tag>
              <Btn href={next ?? `/os/requests/${r.request_id}`} variant="primary" title={mine.label}>{next ? "Do it" : "Open"} <ArrowRight className="h-3.5 w-3.5" /></Btn>
            </>
          ) : open ? (
            <Tag tone={r.attention ? "red" : phaseOf(r).tone}>{r.attention ? "Needs attention" : phaseOf(r).label}</Tag>
          ) : (
            <Tag tone={r.status === "DONE" ? "green" : "neutral"}>{pretty(r.status)}</Tag>
          )}
          {href && <Link href={href} className={cn("os-btn", mine && "max-sm:!hidden")} aria-label={`Open ${r.title}`}>Open <ArrowRight className="h-3.5 w-3.5" /></Link>}
        </div>
      </div>
      {href ? <Link href={href} className="block rounded-lg" aria-label={`${r.title}: steps`}><RequestTrack r={r} className="mt-3" /></Link>
        : <RequestTrack r={r} className="mt-3" />}
      {open && r.attention && (
        <p className="mt-2 flex items-start gap-1.5 text-[11.5px] text-[var(--os-red-ink)]"><AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />{r.attention}</p>
      )}
      {open && mine && <p className="os-muted mt-2 text-[11.5px]"><b className="text-[var(--os-ink)]">Next for you:</b> {mine.label}</p>}
      {sources.length > 0 && r.request_type !== "PRODUCT" && (
        <details className="group mt-2">
          <summary className="os-faint cursor-pointer select-none text-[11px] font-bold hover:text-[var(--os-ink)]">
            {sources.length === 1 ? "The source" : `Its ${sources.length} sources`}
          </summary>
          <div className="os-divide mt-2 rounded-lg border border-[var(--os-line-soft)] px-3">
            {sources.map((i) => (
              <div key={i.item_id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-[11.5px]">
                <span className="min-w-0">
                  <b>{i.source_name}</b>
                  <span className="os-faint"> · {i.run_name ? `${i.run_name}: ${stateLabel(i.run_state).toLowerCase()}` : i.status_detail ?? "no run yet"}</span>
                </span>
                <Tag tone={i.status === "ATTENTION" ? "red" : phaseOf(i).tone}>{phaseOf(i).label}</Tag>
              </div>
            ))}
          </div>
        </details>
      )}
    </article>
  );
}

const START: { type: "domain" | "source" | "product"; label: string; short: string; what: string; icon: Icon; tint: string; soon?: boolean;
  new: string; enhance: string; direct?: { privilege: string; href: string } }[] = [
  { type: "domain", label: "Data domain", short: "Domain", what: "A business area's model, contract and memory", icon: Boxes, tint: TYPE_TINT.DOMAIN,
    new: "A business domain not on the platform yet", enhance: "Extend an existing domain's model: entities, attributes, rules",
    direct: { privilege: "DOMAIN.ONBOARD", href: "/os/enable/new-domain" } },
  { type: "source", label: "Data source", short: "Source", what: "A system that feeds a domain", icon: Database, tint: TYPE_TINT.SOURCE,
    new: "Onboard a system that is not on the platform yet", enhance: "More tables or scope from a source already registered",
    direct: { privilege: "SOURCE.CONNECT", href: "/os/enable/onboarding?type=source" } },
  { type: "product", label: "Data product", short: "Product", what: "Governed Gold products from mastered Silver domains", icon: Crown, tint: TYPE_TINT.PRODUCT, soon: true,
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
              {t.direct && has(access, t.direct.privilege) && (
                <Link href={t.direct.href} title="Onboard directly as administrator" aria-label={`Onboard a ${t.type} directly`}
                      className="os-faint grid h-7 w-7 place-items-center rounded-md hover:bg-[var(--os-hover)] hover:text-[var(--os-accent-ink)]">
                  <ArrowUpRight className="h-3.5 w-3.5" />
                </Link>
              )}
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
  return (
    <section className="os-card os-rise p-5" data-testid="request-focus">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className={cn("grid h-10 w-10 shrink-0 place-items-center rounded-xl", TYPE_TINT[r.request_type])}><I className="h-5 w-5" /></span>
          <div className="min-w-0">
            <h2 className="text-[18px] font-extrabold tracking-[-0.02em]">{r.title}</h2>
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11.5px]">
              <Tag tone={intentOf(r) === "ENHANCE" ? "purple" : "blue"}>{intentLabel(r)}</Tag>
              <span className="os-muted font-semibold">{r.domain_name ?? (r.proposed_domain ? `${r.proposed_domain} (new)` : "no domain")}</span>
              <Tag tone={PRIORITY_TONE[r.priority] ?? "neutral"}>{pretty(r.priority)} priority</Tag>
              {overdue(r) && <Tag tone="red">Overdue</Tag>}
              <span className="os-faint">· filed by {r.requested_by} {ago(r.created_at)}</span>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {at && <Btn href={at} variant="primary">Open where it is <ArrowRight className="h-3.5 w-3.5" /></Btn>}
          <Btn href={`/os/requests/${r.request_id}`}>Full request</Btn>
        </div>
      </div>
      {r.business_outcome && <p className="os-muted mt-3 max-w-3xl text-[12.5px] leading-relaxed">{r.business_outcome}</p>}
      <RequestTrack r={r} className="mt-5" />
      {r.status === "OPEN" && r.attention && (
        <p className="mt-3 flex items-start gap-1.5 text-[12px] text-[var(--os-red-ink)]"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{r.attention}</p>
      )}

      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        <div>
          <h3 className="os-eyebrow mb-2">Who acts now</h3>
          {r.status === "OPEN" && r.waits.length ? (
            <div className="os-divide rounded-xl border border-[var(--os-line)] px-3">
              {r.waits.map((w) => {
                const item = r.items.find((i) => i.item_id === w.item_id);
                const href = actHref(w, item, r.domain_id, product, r.request_id, enhancesSource(r));
                const you = has(access, w.privilege);
                return (
                  <div key={`${w.privilege}-${w.label}-${w.item_id ?? ""}`} className="flex items-center justify-between gap-3 py-2.5">
                    <span className="min-w-0 text-[12px]"><b className="block leading-snug">{w.label}</b>
                      <span className="os-faint text-[10.5px]">{item ? `${item.source_name} · ` : ""}needs {w.privilege}</span></span>
                    {you && enhance && w.privilege === "RUN.CREATE" && item && !item.run_id && item.source_system_id
                      ? <StartEnhancementRun requestId={r.request_id} itemId={item.item_id} source={item.source_name} previewing={!!access.preview} />
                      : you && href ? <Btn href={href} variant="primary">Do it <ArrowRight className="h-3.5 w-3.5" /></Btn> : you ? <Tag tone="green">You</Tag> : <Tag>Another role</Tag>}
                  </div>
                );
              })}
            </div>
          ) : <p className="os-faint text-[12px]">{r.status === "OPEN" ? "Nobody right now." : `Nobody: the request is ${pretty(r.status).toLowerCase()}.`}</p>}
        </div>
        <div>
          <h3 className="os-eyebrow mb-2">{product ? "Gold run" : `Sources (${sources.length})`}</h3>
          {sources.length ? (
            <div className="os-divide rounded-xl border border-[var(--os-line)] px-3">
              {sources.map((i) => (
                <div key={i.item_id} className="py-2.5 text-[12px]">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                  <span className="min-w-0 flex-1"><b className="block break-words">{i.source_name}</b>
                    <span className="os-faint block text-[10.5px]">{i.run_name ? `${i.run_name}: ${stateLabel(i.run_state).toLowerCase()}` : i.status_detail ?? "no run yet"}</span></span>
                  <span className="flex shrink-0 items-center gap-2">
                    <Tag tone={i.status === "ATTENTION" ? "red" : phaseOf(i).tone}>{phaseOf(i).label}</Tag>
                    {i.run_id && ["DESIGN", "BUILD"].includes(i.stage) && (
                      <Link href={`/os/${i.stage === "DESIGN" ? "design" : "build"}?view=selected&run=${i.run_id}`} className="os-btn px-2 py-1 text-[11px]">Open</Link>
                    )}
                  </span>
                  </div>
                  {i.source_note && <p className="os-muted mt-1 text-[11.5px]" data-testid="item-scope"><b>{enhance ? "To add" : "Needed"}:</b> {i.source_note}</p>}
                </div>
              ))}
            </div>
          ) : <p className="os-faint text-[12px]">No source asked for yet.</p>}
        </div>
      </div>

      {r.events.length > 0 && (
        <div className="mt-5">
          <h3 className="os-eyebrow mb-2">Latest</h3>
          <ol className="flex flex-col gap-1.5 text-[11.5px]">
            {[...r.events].reverse().slice(0, 5).map((e) => (
              <li key={e.event_id} className="flex items-baseline gap-2">
                <span className="os-faint w-16 shrink-0 tabular-nums">{ago(e.created_at)}</span>
                <span><b>{pretty(e.kind)}</b>{e.note ? <span className="os-muted">: {e.note}</span> : null}
                  <span className="os-faint"> · {e.automatic ? "platform" : e.actor}</span></span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
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

