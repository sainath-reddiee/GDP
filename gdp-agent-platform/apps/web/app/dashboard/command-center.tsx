import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowRight, Check, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { RunSummary } from "@/lib/types";
import { PHASES, phaseIndex } from "@/lib/stages";
import { ago, runHref } from "@/lib/run-insights";
import { formatAge, statusPill } from "@/lib/run-status";

const n = (v: number | null | undefined) => (v == null ? "-" : v.toLocaleString());

/** Area sparkline for a short series (newest on the right). */
export function Sparkline({ values, className }: { values: number[]; className?: string }) {
  if (values.length < 2) return <div className={cn("h-10", className)} />;
  const w = 120;
  const h = 36;
  const max = Math.max(...values, 0.0001);
  const pts = values.map((v, i) => [(i / (values.length - 1)) * w, h - 2 - (v / max) * (h - 6)] as const);
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className={cn("h-10 w-full", className)} aria-hidden>
      <defs>
        <linearGradient id="spark-fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity="0.35" />
          <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${line} L${w},${h} L0,${h} Z`} fill="url(#spark-fill)" />
      <path d={line} fill="none" stroke="hsl(var(--primary))" strokeWidth="1.6" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Thin progress meter with a tone that follows the value. */
export function Meter({ value, tone }: { value: number | null | undefined; tone?: "auto" | "primary" }) {
  const v = Math.max(0, Math.min(100, value ?? 0));
  const color = tone === "primary" ? "bg-gradient-to-r from-indigo-500 to-violet-500"
    : v >= 90 ? "bg-emerald-500" : v >= 70 ? "bg-amber-500" : "bg-rose-500";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
      <div className={cn("h-full rounded-full transition-all", value == null ? "" : color)} style={{ width: `${value == null ? 0 : v}%` }} />
    </div>
  );
}

export function RibbonMetric({ label, value, unit, hint, href, children }: {
  label: string; value: ReactNode; unit?: string; hint: ReactNode; href: string; children?: ReactNode;
}) {
  return (
    <Link href={href} className="group relative flex flex-col gap-1 rounded-xl p-4 transition hover:bg-card/60">
      <span className="text-xs font-medium uppercase tracking-[0.1em] text-muted-foreground">{label}</span>
      <span className="flex items-baseline gap-1">
        <span className="text-[32px] font-semibold leading-none tracking-tight tabular-nums">{value}</span>
        {unit && <span className="text-sm font-medium text-muted-foreground">{unit}</span>}
      </span>
      <span className="text-xs text-muted-foreground">{hint}</span>
      <div className="mt-2">{children}</div>
      <ArrowRight className="absolute right-4 top-4 h-4 w-4 text-muted-foreground/0 transition group-hover:text-muted-foreground" />
    </Link>
  );
}

const STORE_TONE = {
  indigo: "from-indigo-500 to-blue-500 shadow-indigo-500/25",
  sky: "from-sky-500 to-cyan-500 shadow-sky-500/25",
  violet: "from-violet-500 to-fuchsia-500 shadow-violet-500/25",
  emerald: "from-emerald-500 to-teal-500 shadow-emerald-500/25",
  amber: "from-amber-500 to-orange-500 shadow-amber-500/25",
  rose: "from-rose-500 to-pink-500 shadow-rose-500/25",
} as const;

/** One asset store: what it holds, what needs action, and one task to start. The whole card opens the store (an
 *  overlay link, so the action button inside stays a separate, valid link). */
export function StoreCard({ title, subtitle, icon: Icon, tone, href, metrics, meter, alert, action, missing, testId }: {
  title: string; subtitle: string; icon: LucideIcon; tone: keyof typeof STORE_TONE; href: string;
  metrics: { label: string; value: ReactNode; tone?: string }[];
  meter?: { label: string; value: number | null | undefined; tone?: "auto" | "primary" };
  alert?: { text: string; tone: "amber" | "rose" | "violet" } | null;
  action: { label: string; href: string; icon: LucideIcon };
  missing?: boolean; testId?: string;
}) {
  const ActionIcon = action.icon;
  return (
    <section data-testid={testId} className="surface group relative flex flex-col overflow-hidden p-5 transition duration-200 hover:-translate-y-px hover:border-primary/30 hover:shadow-hover">
      <Link href={href} aria-label={`Open the ${title}`} className="absolute inset-0 z-0 rounded-2xl focus-visible:ring-2 focus-visible:ring-ring/50" />
      <div className={cn("pointer-events-none absolute -right-12 -top-12 h-32 w-32 rounded-full bg-gradient-to-br opacity-[0.08] blur-2xl transition-opacity group-hover:opacity-[0.16]", STORE_TONE[tone])} />
      <div className="pointer-events-none relative flex items-start gap-3">
        <span className={cn("grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br text-white shadow-lg", STORE_TONE[tone])}>
          <Icon className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-[15px] font-semibold">{title}</h3>
          <p className="text-xs text-muted-foreground">{subtitle}</p>
        </div>
        <ArrowRight className="h-4 w-4 text-muted-foreground/0 transition group-hover:translate-x-0.5 group-hover:text-muted-foreground" />
      </div>
      {missing ? (
        <p className="pointer-events-none relative mt-5 text-sm text-muted-foreground">Not deployed in this environment yet.</p>
      ) : (
        <div className="pointer-events-none relative mt-5 grid grid-cols-3 gap-3">
          {metrics.map((m) => (
            <div key={m.label} className="min-w-0">
              <p className={cn("text-2xl font-semibold leading-none tabular-nums", m.tone)}>{m.value}</p>
              <p className="mt-1 truncate text-xs text-muted-foreground" title={m.label}>{m.label}</p>
            </div>
          ))}
        </div>
      )}
      {meter && !missing && (
        <div className="pointer-events-none relative mt-4 space-y-1.5">
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>{meter.label}</span><span className="tabular-nums">{meter.value == null ? "-" : `${Math.round(meter.value)}%`}</span>
          </div>
          <Meter value={meter.value} tone={meter.tone} />
        </div>
      )}
      <div className="min-h-4 flex-1" />
      <div className="relative mt-4 flex items-center gap-3 border-t border-border/60 pt-3">
        {alert && !missing ? (
          <span className={cn("pointer-events-none flex min-w-0 items-center gap-1.5 text-xs font-medium",
            alert.tone === "rose" ? "text-rose-600" : alert.tone === "violet" ? "text-violet-600" : "text-amber-700")}>
            <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", alert.tone === "rose" ? "bg-rose-500" : alert.tone === "violet" ? "bg-violet-500" : "bg-amber-500")} />
            <span className="truncate">{alert.text}</span>
          </span>
        ) : (
          <span className="pointer-events-none text-xs text-muted-foreground">{missing ? "" : "Nothing needs action"}</span>
        )}
        <Link href={action.href} data-testid={testId ? `${testId}-action` : undefined}
              className="relative z-10 ml-auto inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-primary/10 px-2.5 py-1.5 text-xs font-medium text-primary transition hover:bg-primary/15">
          <ActionIcon className="h-3.5 w-3.5" /> {action.label}
        </Link>
      </div>
    </section>
  );
}

/** Six-step phase stepper for a run: done, current (pulsing while running) and upcoming. */
export function PhaseStepper({ run }: { run: RunSummary }) {
  const at = phaseIndex(run.current_stage);
  const done = run.lifecycle === "COMPLETED";
  const failed = run.lifecycle === "FAILED";
  return (
    <ol className="flex items-center" aria-label={`Phase ${at + 1} of ${PHASES.length}`}>
      {PHASES.map((p, i) => {
        const complete = done || i < at;
        const current = !done && i === at;
        return (
          <li key={p.label} className="flex items-center" title={p.label}>
            <span className={cn("grid h-5 w-5 place-items-center rounded-full text-[9px] font-semibold ring-1 ring-inset",
              complete ? "bg-emerald-500 text-white ring-emerald-500"
                : current ? (failed ? "bg-rose-500 text-white ring-rose-500" : "bg-primary text-primary-foreground ring-primary shadow-[0_0_0_3px_hsl(var(--primary)/0.18)]")
                : "bg-muted text-muted-foreground ring-border")}>
              {complete ? <Check className="h-3 w-3" /> : i + 1}
            </span>
            {i < PHASES.length - 1 && <span className={cn("h-0.5 w-4 sm:w-6", complete ? "bg-emerald-500" : "bg-border")} />}
          </li>
        );
      })}
    </ol>
  );
}

export function PipelineTable({ runs }: { runs: RunSummary[] }) {
  if (!runs.length) {
    return <p className="px-5 py-10 text-center text-sm text-muted-foreground">No runs in flight. Onboard a source or start a run to see it here.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border/70 text-left text-[11px] uppercase tracking-[0.06em] text-muted-foreground">
            <th className="px-5 py-2.5 font-semibold">Run</th>
            <th className="px-3 py-2.5 font-semibold">
              <span className="hidden gap-6 sm:flex">{PHASES.map((p) => <span key={p.label} className="flex w-5 justify-center whitespace-nowrap text-[9px]">{p.label}</span>)}</span>
              <span className="sm:hidden">Phase</span>
            </th>
            <th className="px-3 py-2.5 font-semibold">Elapsed</th>
            <th className="px-3 py-2.5 font-semibold">Status</th>
            <th className="px-5 py-2.5" />
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const pill = statusPill(r);
            const review = pill.label === "Needs review";
            return (
              <tr key={r.run_id} className="border-b border-border/50 transition last:border-0 hover:bg-muted/40">
                <td className="max-w-[260px] px-5 py-3">
                  <Link href={runHref(r)} className="block truncate font-medium hover:text-primary">{r.run_name}</Link>
                  <span className="block truncate text-xs text-muted-foreground">
                    {r.domain_name ?? "No domain"}{r.source_system_name ? ` · ${r.source_system_name}` : ""} · updated {ago(r.updated_at ?? r.created_at)}
                  </span>
                </td>
                <td className="px-3 py-3"><PhaseStepper run={r} /></td>
                <td className="px-3 py-3 tabular-nums text-muted-foreground">{formatAge(r.age_minutes, r.created_at)}</td>
                <td className="px-3 py-3">
                  <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium", pill.cls)}>
                    <span className={cn("h-1.5 w-1.5 rounded-full", pill.dot)} /> {pill.label}
                  </span>
                </td>
                <td className="px-5 py-3 text-right">
                  <Link href={runHref(r)} className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-lg px-2.5 py-1 text-xs font-medium transition",
                    review ? "bg-rose-500 text-white hover:bg-rose-600" : "text-primary hover:bg-primary/10")}>
                    {review ? "Review" : "Open"} <ArrowRight className="h-3 w-3" />
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export { n as fmt };
