import Link from "next/link";
import type { ComponentType } from "react";
import { AlertTriangle, ArrowRight, ArrowUpRight, Boxes, Check, Crown, Database, Hand, Lock, Plus, Wand2 } from "lucide-react";
import { stateLabel } from "@/lib/run-labels";
import { canSee, has } from "@/lib/os/access";
import type { OsAccess } from "@/lib/os/session";
import {
  PRIORITY_TONE, actHref, intentOf, intentLabel, overdue, phaseOf, requestSteps, type OnboardingRequest, type RequestType,
} from "@/lib/os/requests";
import { Btn, Tag, ago, pretty } from "./ui";
import { cn } from "@/lib/utils";

// Home: the build workflows (onboarding requests) with the step each one is at and who it waits on, and the panel that
// starts a new one. Server-safe, like ./ui.

type Icon = ComponentType<{ className?: string }>;

export const TYPE_ICON: Record<RequestType, Icon> = { DOMAIN: Boxes, SOURCE: Database, PRODUCT: Crown };
const TYPE_TINT: Record<RequestType, string> = {
  DOMAIN: "bg-[var(--os-purple-bg)] text-[var(--os-purple-ink)]",
  SOURCE: "bg-[var(--os-blue-bg)] text-[var(--os-blue-ink)]",
  PRODUCT: "bg-[var(--os-amber-bg)] text-[var(--os-amber-ink)]",
};

/** A segmented control of links: Home's views and filters live in the URL. */
export function Segmented({ label, options, testId }: {
  label: string; testId?: string;
  options: { key: string; label: string; href: string; active: boolean; count?: number; dot?: boolean }[];
}) {
  return (
    <nav aria-label={label} data-testid={testId} className="inline-flex max-w-full overflow-x-auto rounded-lg border border-[var(--os-line)] bg-[var(--os-subtle)] p-0.5">
      {options.map((o) => (
        <Link key={o.key} href={o.href} scroll={false} aria-current={o.active ? "page" : undefined}
              className={cn("relative inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-1.5 text-[12px] font-bold transition",
                            o.active ? "bg-[var(--os-surface)] text-[var(--os-ink)] shadow-[var(--os-shadow)]" : "os-muted hover:text-[var(--os-ink)]")}>
          {o.dot && <span className="h-1.5 w-1.5 rounded-full bg-[#d85b52]" aria-hidden />}
          {o.label}
          {o.count !== undefined && (
            <span className={cn("rounded-full px-1.5 text-[10.5px] tabular-nums", o.active ? "bg-[var(--os-green-bg)] text-[var(--os-green-ink)]" : "bg-[var(--os-neutral-bg)]")}>{o.count}</span>
          )}
        </Link>
      ))}
    </nav>
  );
}

/** Who the request waits on: the role its wait names ("Model approver: approve the field mapping"), else the stage's owner. */
const waitingOn = (r: OnboardingRequest, owner: string) => (r.waiting_label?.includes(":") ? r.waiting_label.split(":")[0] : owner);

/** The request's path as a track: done, the step it waits at (and on whom, for how long), what comes next. */
function StepTrack({ r }: { r: OnboardingRequest }) {
  const steps = requestSteps(r);
  const since = r.stage_changed_at ? ago(r.stage_changed_at).replace(/ ago$/, "") : "";
  return (
    <ol className="mt-3.5 grid gap-1.5" style={{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }} aria-label="Workflow steps">
      {steps.map((s) => {
        const now = s.state === "current" || s.state === "attention";
        return (
          <li key={s.stage} className="min-w-0" aria-current={now ? "step" : undefined} title={`${s.label}${s.owner ? ` · ${s.owner}` : ""}`}>
            <span className={cn("block h-1.5 rounded-full",
              s.state === "done" ? "bg-[var(--os-leaf)]" : s.state === "current" ? "bg-[var(--os-forest)]"
                : s.state === "attention" ? "bg-[#d85b52]" : "bg-[var(--os-line)]")} />
            <span className={cn("mt-1.5 flex items-center gap-1 truncate text-[11px]", now ? "font-extrabold" : s.state === "done" ? "os-muted font-semibold" : "os-faint font-semibold")}>
              {s.state === "done" && <Check className="h-3 w-3 shrink-0 text-[var(--os-leaf)]" />}
              {s.state === "attention" && <AlertTriangle className="h-3 w-3 shrink-0 text-[var(--os-red-ink)]" />}
              {s.state === "current" && <Hand className="h-3 w-3 shrink-0 text-[var(--os-accent-ink)]" />}
              <span className="truncate">{s.label}</span>
            </span>
            {now && r.status === "OPEN" && (
              <span className="os-faint mt-0.5 block truncate text-[10.5px]" title={r.waiting_label ?? undefined}>
                Waiting on {waitingOn(r, s.owner) || "a person"}{since && since !== "just now" ? ` · ${since}` : ""}
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

/** One build workflow: what it is, its steps with where it waits, and the viewer's next action when it is theirs. */
export function WorkflowRow({ r, access }: { r: OnboardingRequest; access: OsAccess }) {
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
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <span className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-lg", TYPE_TINT[r.request_type])}><I className="h-4 w-4" /></span>
          <div className="min-w-0">
            <Link href={`/os/requests/${r.request_id}`} className="block truncate text-[14px] font-extrabold tracking-[-0.01em] hover:underline" title={r.title}>{r.title}</Link>
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
        </div>
      </div>
      <StepTrack r={r} />
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
  const may = canSee(access, "intake");
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
              <Link href={`/os/intake?${new URLSearchParams({ type: t.type, intent })}`} title={why} data-testid={`start-${t.type}-${intent}`}
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
