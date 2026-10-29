import Link from "next/link";
import type { ComponentType, ReactNode } from "react";
import { AlertTriangle, Check, Circle, Clock, Hand, Loader2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { StepStatus, StepView } from "@/lib/os/lifecycle";

// The building blocks of the Agentic Data OS screens, in the look of the UX package (app/os/ados.css).
// Server-safe: no hooks, so pages stay server components and read the platform directly.

type Icon = ComponentType<{ className?: string }>;
export type Tone = "green" | "blue" | "amber" | "red" | "purple" | "neutral";

export function PageHeader({ eyebrow, title, subtitle, actions, meta }: {
  eyebrow: string; title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; meta?: ReactNode;
}) {
  return (
    <header className="os-rise mb-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="os-eyebrow mb-1.5">{eyebrow}</div>
          <h1 className="text-[26px] font-extrabold leading-tight tracking-[-0.03em]">{title}</h1>
          {subtitle && <p className="os-muted mt-1.5 max-w-3xl text-[13px] leading-relaxed">{subtitle}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {meta && <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-[12px]">{meta}</div>}
    </header>
  );
}

export function Card({ title, subtitle, action, children, className, pad = true, id }: {
  title?: ReactNode; subtitle?: ReactNode; action?: ReactNode; children: ReactNode; className?: string; pad?: boolean; id?: string;
}) {
  return (
    <section id={id} className={cn("os-card os-rise min-w-0", className)}>
      {(title || action) && (
        <div className={cn("flex flex-wrap items-start justify-between gap-3", pad ? "px-5 pt-4" : "px-5 py-4")}>
          <div className="min-w-0">
            {title && <h2 className="text-[14.5px] font-extrabold tracking-[-0.01em]">{title}</h2>}
            {subtitle && <p className="os-muted mt-0.5 text-[12px]">{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      <div className={cn(pad && "p-5", pad && (title || action) && "pt-3.5")}>{children}</div>
    </section>
  );
}

export function Kpi({ label, value, hint, icon: I, tone = "green", href }: {
  label: string; value: ReactNode; hint?: ReactNode; icon: Icon; tone?: Tone; href?: string;
}) {
  const body = (
    <div className="os-card os-rise flex h-full items-center justify-between gap-3 p-4 transition hover:-translate-y-0.5 hover:shadow-[var(--os-shadow-pop)]">
      <div className="min-w-0">
        <div className="os-muted text-[11.5px] font-semibold">{label}</div>
        <div className="my-1 text-[28px] font-extrabold leading-none tracking-[-0.03em] tabular-nums">{value}</div>
        {hint && <div className="os-faint truncate text-[11px]">{hint}</div>}
      </div>
      <span className={cn("os-tag grid h-11 w-11 shrink-0 place-items-center rounded-xl p-0", `os-tag-${tone}`)}><I className="h-5 w-5" /></span>
    </div>
  );
  return href ? <Link href={href} className="block">{body}</Link> : body;
}

export function Tag({ tone = "neutral", children, className, title }: { tone?: Tone; children: ReactNode; className?: string; title?: string }) {
  return <span title={title} className={cn("os-tag", tone !== "neutral" && `os-tag-${tone}`, className)}>{children}</span>;
}

export function Btn({ href, children, variant = "default", className, external, title }: {
  href: string; children: ReactNode; variant?: "default" | "primary" | "mint" | "warn" | "red"; className?: string; external?: boolean; title?: string;
}) {
  const cls = cn("os-btn", variant !== "default" && `os-btn-${variant}`, className);
  return external
    ? <a href={href} target="_blank" rel="noreferrer" className={cls} title={title}>{children}</a>
    : <Link href={href} className={cls} title={title}>{children}</Link>;
}

export function Banner({ tone = "green", icon: I, children, action }: { tone?: "green" | "amber" | "red" | "blue"; icon?: Icon; children: ReactNode; action?: ReactNode }) {
  return (
    <div className={cn("os-banner os-rise mb-4 flex flex-wrap items-center gap-3", tone !== "green" && `os-banner-${tone}`)}>
      {I && <I className="h-4 w-4 shrink-0" />}
      <div className="min-w-0 flex-1">{children}</div>
      {action}
    </div>
  );
}

export function Bar({ value, tone = "green", className }: { value: number; tone?: "green" | "amber" | "red"; className?: string }) {
  return (
    <div className={cn("os-bar", tone !== "green" && `os-bar-${tone}`, className)} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value)}>
      <div style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

/** An empty or unavailable section, said plainly. */
export function Empty({ icon: I = Circle, title, children, action }: { icon?: Icon; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-[var(--os-line)] px-6 py-8 text-center">
      <I className="os-faint h-6 w-6" />
      <div className="text-[13px] font-bold">{title}</div>
      {children && <div className="os-muted max-w-md text-[12px] leading-relaxed">{children}</div>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

/** A source that could not be read: the reason, not a blank. */
export function SourceError({ what, error }: { what: string; error: string | null }) {
  if (!error) return null;
  return (
    <div className="os-banner os-banner-amber mb-3 flex items-start gap-2 text-[12px]">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span><b>{what} could not be read.</b> {error}</span>
    </div>
  );
}

const STEP_STYLE: Record<StepStatus, { bubble: string; label: string; icon?: Icon }> = {
  done: { bubble: "bg-[var(--os-green-bg)] text-[var(--os-green-ink)]", label: "Completed", icon: Check },
  active: { bubble: "bg-[var(--os-forest)] text-white", label: "In progress", icon: Loader2 },
  waiting: { bubble: "bg-[var(--os-amber-bg)] text-[var(--os-amber-ink)] ring-2 ring-[var(--os-amber-line)]", label: "Needs a person", icon: Hand },
  failed: { bubble: "bg-[var(--os-red-bg)] text-[var(--os-red-ink)] ring-2 ring-[var(--os-red-line)]", label: "Failed", icon: X },
  pending: { bubble: "bg-[var(--os-neutral-bg)] text-[var(--os-faint)]", label: "Not started" },
  cancelled: { bubble: "bg-[var(--os-neutral-bg)] text-[var(--os-faint)]", label: "Cancelled", icon: X },
};

export const stepStatusLabel = (s: StepStatus) => STEP_STYLE[s].label;

/** The eight-step lifecycle of one run. Each step links to where it is worked, when a link is given. */
export function Stepper({ steps, hrefOf, compact }: { steps: StepView[]; hrefOf?: (s: StepView) => string | null; compact?: boolean }) {
  return (
    <ol className={cn("flex items-center gap-1.5 overflow-x-auto", compact ? "" : "os-card os-rise px-4 py-4")} aria-label="Workflow lifecycle">
      {steps.map((s, i) => {
        const st = STEP_STYLE[s.status];
        const I = st.icon;
        const href = hrefOf?.(s) ?? null;
        const inner = (
          <>
            <span className={cn("grid shrink-0 place-items-center rounded-full font-extrabold", compact ? "h-6 w-6 text-[10px]" : "h-8 w-8 text-[12px]", st.bubble)}>
              {I ? <I className={cn(compact ? "h-3 w-3" : "h-3.5 w-3.5", s.status === "active" && "os-spin")} /> : i + 1}
            </span>
            {!compact && (
              <span className="min-w-0">
                <span className="block whitespace-nowrap text-[11.5px] font-extrabold">{s.label}</span>
                <span className="os-faint block whitespace-nowrap text-[10px]">{st.label}</span>
              </span>
            )}
          </>
        );
        return (
          <li key={s.key} className={cn("flex min-w-0 items-center gap-1.5", !compact && "flex-1")} aria-current={s.status === "active" || s.status === "waiting" ? "step" : undefined}
              title={`${s.label}: ${st.label} (${s.owner})`}>
            {href ? <Link href={href} className="flex min-w-0 items-center gap-2 rounded-lg p-1 os-hover">{inner}</Link> : <span className="flex min-w-0 items-center gap-2 p-1">{inner}</span>}
            {i < steps.length - 1 && <span className={cn("h-px shrink-0 bg-[var(--os-line)]", compact ? "w-2" : "hidden w-4 lg:block")} />}
          </li>
        );
      })}
    </ol>
  );
}

/** One line of the activity stream. */
export function ActivityRow({ tone = "green", title, sub, when, icon: I = Check }: { tone?: Tone; title: ReactNode; sub?: ReactNode; when?: string; icon?: Icon }) {
  return (
    <div className="flex gap-3 py-2.5">
      <span className={cn("os-tag mt-0.5 grid h-5 w-5 shrink-0 place-items-center p-0", `os-tag-${tone}`)}><I className="h-3 w-3" /></span>
      <div className="min-w-0 flex-1">
        <div className="text-[12.5px] font-bold leading-snug">{title}</div>
        {sub && <div className="os-faint mt-0.5 text-[11px] leading-snug">{sub}</div>}
      </div>
      {when && <div className="os-faint shrink-0 text-[10.5px] tabular-nums">{when}</div>}
    </div>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="border-l-2 border-[var(--os-line)] py-1 pl-3">
      <div className="os-muted text-[10.5px] font-bold uppercase tracking-wide">{label}</div>
      <div className="mt-0.5 break-words text-[12.5px]">{children}</div>
    </div>
  );
}

export function Pending() {
  return <span className="inline-flex items-center gap-1"><Clock className="h-3 w-3" /> pending</span>;
}

/** "3h ago" for a timestamp the API sends (Snowflake text or ISO). */
export function parseTs(ts: string | null | undefined): number {
  if (!ts) return NaN;
  // "2026-10-23 04:46:55.745 -0700" (Snowflake) or ISO "2026-10-16T14:03:19.342+00:00"
  const m = ts.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(\.\d+)?\s*(Z|[+-]\d{2}):?(\d{2})?$/);
  if (m) return new Date(`${m[1]}T${m[2]}${(m[3] ?? "").slice(0, 4)}${m[4] === "Z" ? "Z" : `${m[4]}:${m[5] ?? "00"}`}`).getTime();
  return new Date(ts).getTime();
}

export function ago(ts: string | null | undefined): string {
  const when = parseTs(ts);
  if (Number.isNaN(when)) return "";
  const m = Math.round((Date.now() - when) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

/** UPPER_SNAKE to "Upper snake". */
export const pretty = (raw: string | null | undefined) => {
  if (!raw) return "";
  const s = raw.replace(/[_-]+/g, " ").toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
};
