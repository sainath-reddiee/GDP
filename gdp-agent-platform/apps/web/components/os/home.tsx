import Link from "next/link";
import type { ComponentType } from "react";
import {
  AlertTriangle, ArrowRight, ArrowUpRight, Boxes, CheckCircle2, ChevronRight, Crown, Database, Hammer, Inbox, Lock, Plus, ShieldCheck, SquareStack, Wand2, Wrench,
} from "lucide-react";
import { stateLabel } from "@/lib/run-labels";
import { canSee, has, type ScreenKey } from "@/lib/os/access";
import type { Domain } from "@/lib/os/data";
import type { OsAccess } from "@/lib/os/session";
import {
  PRIORITY_TONE, actHref, intentOf, intentLabel, overdue, phaseOf, requestSteps, type OnboardingRequest, type RequestDetail, type RequestType,
} from "@/lib/os/requests";
import { Btn, Tag, ago, pretty } from "./ui";
import { Track, type TrackStep } from "./stage-board";
import { workflowHref, type Stage } from "@/lib/os/work";
import { cn } from "@/lib/utils";

// Home and the Intake board: the build workflows (onboarding requests) with the step each one is at and who it waits on,
// the stage strip, the panel that starts a new one, and the domains and data products. Server-safe, like ./ui.

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
  const next = mine ? actHref(mine, item, r.domain_id, r.request_type === "PRODUCT", r.request_id) : null;
  const sources = r.items.filter((i) => !i.removed_at);
  const domain = r.domain_name ?? (r.proposed_domain ? `${r.proposed_domain} (new)` : null);
  return (
    <article data-testid="workflow-row" className={cn("os-card os-rise p-4 transition hover:shadow-[var(--os-shadow-pop)]",
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
      {href ? <Link href={href} className="block rounded-lg" aria-label={`${r.title}: steps`}><RequestTrack r={r} className="mt-3.5" /></Link>
        : <RequestTrack r={r} className="mt-3.5" />}
      {open && r.attention && (
        <p className="mt-2.5 flex items-start gap-1.5 text-[11.5px] text-[var(--os-red-ink)]"><AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />{r.attention}</p>
      )}
      {open && mine && <p className="os-muted mt-2.5 text-[11.5px]"><b className="text-[var(--os-ink)]">Next for you:</b> {mine.label}</p>}
      {sources.length > 0 && r.request_type !== "PRODUCT" && (
        <details className="group mt-2.5">
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

const START: { type: "domain" | "source" | "product"; label: string; what: string; icon: Icon; tint: string; soon?: boolean;
  new: string; enhance: string; direct?: { privilege: string; href: string } }[] = [
  { type: "domain", label: "Data domain", what: "A business area's model, contract and memory", icon: Boxes, tint: TYPE_TINT.DOMAIN,
    new: "A business domain not on the platform yet", enhance: "Extend an existing domain's model: entities, attributes, rules",
    direct: { privilege: "DOMAIN.ONBOARD", href: "/os/enable/new-domain" } },
  { type: "source", label: "Data source", what: "A system that feeds a domain", icon: Database, tint: TYPE_TINT.SOURCE,
    new: "Onboard a system that is not on the platform yet", enhance: "More tables or scope from a source already registered",
    direct: { privilege: "SOURCE.CONNECT", href: "/os/enable/onboarding?type=source" } },
  { type: "product", label: "Data product", what: "Governed Gold products from mastered Silver domains", icon: Crown, tint: TYPE_TINT.PRODUCT, soon: true,
    new: "A new governed data product", enhance: "Change a data product already published" },
];

const FILE_REQUEST = "Filing a workflow needs RUN.CREATE, SOURCE.CONNECT, DOMAIN.EDIT or REQUEST.CHANGES: ask a governance admin.";

/** New or Enhance for a domain, a source or a data product. Everyone sees it; the buttons follow the viewer's role. */
export function StartPanel({ access }: { access: OsAccess }) {
  const may = canSee(access, "intakeNew");
  return (
    <section className="os-card os-rise p-4" data-testid="start-panel" aria-labelledby="start-title">
      <div className="os-eyebrow">Build</div>
      <h2 id="start-title" className="mt-0.5 text-[15px] font-extrabold tracking-[-0.01em]">Start a workflow</h2>
      <p className="os-muted mt-0.5 hidden text-[11.5px] sm:block">Something new, or more from what is already on the platform.</p>
      <div className="mt-3 grid gap-2.5 sm:grid-cols-3 xl:grid-cols-1">
        {START.map((t) => {
          const I = t.icon;
          const button = (intent: "new" | "enhance") => {
            const text = intent === "new" ? "New" : "Enhance";
            const B = intent === "new" ? Plus : Wand2;
            const why = t.soon ? "Data products are coming soon" : !may ? FILE_REQUEST : intent === "new" ? t.new : t.enhance;
            return t.soon || !may ? (
              <span aria-disabled="true" title={why} data-testid={`start-${t.type}-${intent}`}
                    className="os-btn cursor-not-allowed justify-center opacity-50"><B className="h-3.5 w-3.5" />{text}</span>
            ) : (
              <Link href={`/os/intake/new?${new URLSearchParams({ type: t.type, intent })}`} title={why} data-testid={`start-${t.type}-${intent}`}
                    className={cn("os-btn justify-center", intent === "new" && "os-btn-primary")}><B className="h-3.5 w-3.5" />{text}</Link>
            );
          };
          return (
            <div key={t.type} className="rounded-xl border border-[var(--os-line)] p-2.5 sm:p-3">
              <div className="flex items-start gap-2.5">
                <span className={cn("grid h-8 w-8 shrink-0 place-items-center rounded-lg", t.tint)}><I className="h-4 w-4" /></span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5 text-[13px] font-extrabold">{t.label}{t.soon && <Tag tone="amber">Coming soon</Tag>}</div>
                  <div className="os-faint hidden text-[11px] leading-snug sm:block">{t.what}</div>
                </div>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-2 sm:mt-2.5">{button("new")}{button("enhance")}</div>
              {t.direct && has(access, t.direct.privilege) && (
                <Link href={t.direct.href} className="os-faint mt-2 inline-flex items-center gap-1 text-[10.5px] font-bold hover:text-[var(--os-accent-ink)]">
                  Onboard directly as administrator <ArrowUpRight className="h-3 w-3" />
                </Link>
              )}
            </div>
          );
        })}
      </div>
      {!may && <p className="os-faint mt-3 flex items-start gap-1.5 text-[11px]"><Lock className="mt-0.5 h-3 w-3 shrink-0" />Your role can follow every workflow here. {FILE_REQUEST}</p>}
    </section>
  );
}

/** The selected request on the Intake board: its steps, who acts now (with the viewer's next action), its sources and
 *  where each is worked, and the latest history. The full request page keeps every control. */
export function RequestFocus({ r, access }: { r: RequestDetail; access: OsAccess }) {
  const I = TYPE_ICON[r.request_type] ?? Database;
  const product = r.request_type === "PRODUCT";
  const sources = r.items.filter((i) => !i.removed_at);
  const at = workflowHref(r);
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
          {at !== `/os/intake?view=selected&request=${r.request_id}` && <Btn href={at} variant="primary">Open where it is <ArrowRight className="h-3.5 w-3.5" /></Btn>}
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
                const href = actHref(w, item, r.domain_id, product, r.request_id);
                const you = has(access, w.privilege);
                return (
                  <div key={`${w.privilege}-${w.label}-${w.item_id ?? ""}`} className="flex items-center justify-between gap-3 py-2.5">
                    <span className="min-w-0 text-[12px]"><b className="block leading-snug">{w.label}</b>
                      <span className="os-faint text-[10.5px]">{item ? `${item.source_name} · ` : ""}needs {w.privilege}</span></span>
                    {you && href ? <Btn href={href} variant="primary">Do it <ArrowRight className="h-3.5 w-3.5" /></Btn> : you ? <Tag tone="green">You</Tag> : <Tag>Another role</Tag>}
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
                <div key={i.item_id} className="flex items-center justify-between gap-3 py-2.5 text-[12px]">
                  <span className="min-w-0"><b className="block truncate">{i.source_name}</b>
                    <span className="os-faint block truncate text-[10.5px]">{i.run_name ? `${i.run_name}: ${stateLabel(i.run_state).toLowerCase()}` : i.status_detail ?? "no run yet"}</span></span>
                  <span className="flex shrink-0 items-center gap-2">
                    <Tag tone={i.status === "ATTENTION" ? "red" : phaseOf(i).tone}>{phaseOf(i).label}</Tag>
                    {i.run_id && ["DESIGN", "BUILD"].includes(i.stage) && (
                      <Link href={`/os/${i.stage === "DESIGN" ? "design" : "build"}?view=selected&run=${i.run_id}`} className="os-btn px-2 py-1 text-[11px]">Open</Link>
                    )}
                  </span>
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

const FLOW: { stage: Stage; label: string; icon: Icon; screen: ScreenKey; href: (view: "action" | "active") => string }[] = [
  { stage: "intake", label: "Intake", icon: Inbox, screen: "intake", href: (v) => `/os/intake?view=${v}` },
  { stage: "enable", label: "Enable", icon: Wrench, screen: "enable", href: () => "/os/enable" },
  { stage: "design", label: "Design", icon: SquareStack, screen: "design", href: (v) => `/os/design?view=${v}` },
  { stage: "build", label: "Build", icon: Hammer, screen: "build", href: (v) => `/os/build?view=${v}` },
  { stage: "review", label: "Review", icon: ShieldCheck, screen: "review", href: () => "/os/review" },
  { stage: "done", label: "Done", icon: CheckCircle2, screen: "intake", href: () => "/os/intake?view=completed" },
];
const REQUEST_STAGE: Record<string, Stage> = { NEW: "intake", DOMAIN: "intake", ENABLE: "enable", DESIGN: "design", BUILD: "build", REVIEW: "review", DONE: "done" };

/** The studio at a glance: every workflow by the stage it is at, and how many wait on the viewer there. Each stage
 *  opens its board, on the viewer's queue when they have work there. */
export function StageStrip({ requests, access }: { requests: OnboardingRequest[]; access: OsAccess }) {
  const stats = FLOW.map((f) => {
    const here = requests.filter((r) => (r.status === "OPEN" ? REQUEST_STAGE[r.stage] : r.status === "DONE" ? "done" : null) === f.stage);
    const mine = here.filter((r) => r.status === "OPEN" && r.waits.some((w) => has(access, w.privilege))).length;
    return { ...f, count: here.length, mine };
  });
  return (
    <nav aria-label="Workflows by stage" data-testid="stage-strip"
         className="os-rise mb-5 grid grid-cols-3 overflow-hidden rounded-2xl bg-[#003f2d] text-white shadow-[0_10px_30px_rgba(0,63,45,0.22)] sm:grid-cols-6">
      {stats.map((s, i) => {
        const I = s.icon;
        const body = (
          <>
            <span className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.08em] text-[#80bbad]"><I className="h-3.5 w-3.5" />{s.label}</span>
            <span className="mt-1.5 block text-[28px] font-extrabold leading-none tabular-nums tracking-[-0.03em]">{s.count}</span>
            <span className="mt-2 block h-5">
              {s.mine > 0 && <span className="inline-flex items-center gap-1 rounded-full bg-[#17e88f] px-2 py-0.5 text-[10.5px] font-extrabold text-[#003f2d]">{s.mine} need{s.mine === 1 ? "s" : ""} you</span>}
            </span>
            {i < stats.length - 1 && <ChevronRight className="absolute right-1.5 top-1/2 hidden h-4 w-4 -translate-y-1/2 text-white/25 sm:block" aria-hidden />}
          </>
        );
        const cls = "relative block px-4 py-3.5 transition border-white/10 [&:not(:last-child)]:border-r";
        return canSee(access, s.screen)
          ? <Link key={s.stage} href={s.href(s.mine ? "action" : "active")} className={cn(cls, "hover:bg-white/10")} data-testid={`stage-${s.stage}`}>{body}</Link>
          : <span key={s.stage} className={cls} data-testid={`stage-${s.stage}`}>{body}</span>;
      })}
    </nav>
  );
}

/** The domains on the platform and what is moving in each. */
export function DomainsPanel({ domains, requests }: { domains: Domain[]; requests: OnboardingRequest[] }) {
  const open = (d: Domain) => requests.filter((r) => r.status === "OPEN" && r.domain_id === d.domain_id).length;
  const shown = [...domains].sort((a, b) => open(b) - open(a) || b.target_tables - a.target_tables).slice(0, 6);
  return (
    <section className="os-card os-rise p-4" data-testid="domains-panel">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-[14px] font-extrabold tracking-[-0.01em]">Active data domains</h2>
        <Link href="/os/knowledge?view=domains" className="os-faint text-[11px] font-bold hover:text-[var(--os-accent-ink)]">All {domains.length}</Link>
      </div>
      {shown.length ? (
        <div className="mt-2.5 flex flex-col gap-1.5">
          {shown.map((d) => (
            <Link key={d.domain_id} href={`/os/knowledge/domains/${d.domain_id}`} className="os-hover flex items-center gap-2.5 rounded-lg px-2 py-1.5">
              <span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-lg", TYPE_TINT.DOMAIN)}><Boxes className="h-3.5 w-3.5" /></span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[12.5px] font-bold">{d.domain_name}</span>
                <span className="os-faint block truncate text-[10.5px]">{d.target_tables} tables · {d.knowledge_items} memory</span>
              </span>
              {open(d) > 0 && <Tag tone="blue">{open(d)} open</Tag>}
            </Link>
          ))}
        </div>
      ) : <p className="os-faint mt-2 text-[12px]">No domain is onboarded yet.</p>}
    </section>
  );
}

/** Data products: the Gold sets built so far (a domain with Gold tables); product requests are coming soon. */
export function ProductsPanel({ domains }: { domains: Domain[] }) {
  const gold = domains.filter((d) => (d.gold_tables ?? 0) > 0);
  return (
    <section className="os-card os-rise p-4" data-testid="products-panel">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-[14px] font-extrabold tracking-[-0.01em]">Data products</h2>
        <Tag tone="amber">Coming soon</Tag>
      </div>
      {gold.length ? (
        <div className="mt-2.5 flex flex-col gap-1.5">
          {gold.map((d) => (
            <Link key={d.domain_id} href={`/os/knowledge/domains/${d.domain_id}`} className="os-hover flex items-center gap-2.5 rounded-lg px-2 py-1.5">
              <span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-lg", TYPE_TINT.PRODUCT)}><Crown className="h-3.5 w-3.5" /></span>
              <span className="min-w-0 flex-1"><span className="block truncate text-[12.5px] font-bold">{d.domain_name} Gold</span>
                <span className="os-faint block text-[10.5px]">{d.gold_tables} Gold table{d.gold_tables === 1 ? "" : "s"}</span></span>
            </Link>
          ))}
        </div>
      ) : <p className="os-faint mt-2 text-[12px] leading-relaxed">Governed Gold products built from mastered Silver domains will be listed and requested here.</p>}
    </section>
  );
}
