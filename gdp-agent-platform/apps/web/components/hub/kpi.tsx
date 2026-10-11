import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowUpRight, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

// soft tints per state: chip, the faint glow in the corner, and the accent line of a highlighted card
export const TONES = {
  blue: { chip: "bg-indigo-50 text-indigo-600 ring-indigo-100", glow: "bg-indigo-400", line: "from-indigo-400 to-violet-400" },
  rose: { chip: "bg-rose-50 text-rose-600 ring-rose-100", glow: "bg-rose-400", line: "from-rose-400 to-pink-400" },
  slate: { chip: "bg-slate-100 text-slate-600 ring-slate-200", glow: "bg-slate-400", line: "from-slate-300 to-slate-400" },
  emerald: { chip: "bg-emerald-50 text-emerald-600 ring-emerald-100", glow: "bg-emerald-400", line: "from-emerald-400 to-teal-400" },
  amber: { chip: "bg-amber-50 text-amber-600 ring-amber-100", glow: "bg-amber-400", line: "from-amber-400 to-orange-400" },
} as const;

/** A headline number tile; a link when `href` is given. */
export function Kpi({ href, label, value, hint, icon: Icon, tone, highlight }: {
  href?: string; label: string; value: ReactNode; hint: string; icon: LucideIcon; tone: keyof typeof TONES; highlight?: boolean;
}) {
  const t = TONES[tone];
  const body = (
    <>
      {highlight && <div className={cn("absolute inset-x-0 top-0 h-0.5 bg-gradient-to-r", t.line)} />}
      <div className={cn("absolute -right-10 -top-10 h-28 w-28 rounded-full opacity-[0.07] blur-xl", t.glow)} />
      <div className="flex items-center justify-between">
        <span className={cn("grid h-9 w-9 place-items-center rounded-xl ring-1 ring-inset", t.chip)}><Icon className="h-4 w-4" /></span>
        {href && <ArrowUpRight className="h-4 w-4 text-muted-foreground/60 opacity-0 transition group-hover:opacity-100" />}
      </div>
      <p className="mt-3 text-[28px] font-semibold leading-none tracking-tight tabular-nums">{value}</p>
      <p className="mt-1.5 text-sm font-medium">{label}</p>
      <p className="text-xs text-muted-foreground">{hint}</p>
    </>
  );
  const cls = cn("surface group relative overflow-hidden p-4 transition duration-200",
    href && "hover:border-primary/25 hover:shadow-hover",
    highlight && "border-rose-200 bg-gradient-to-br from-rose-50/70 via-card to-card");
  return href ? <Link href={href} className={cls}>{body}</Link> : <div className={cls}>{body}</div>;
}

/** A titled section card with an optional action on the right. */
export function Panel({ title, icon: Icon, action, children, className }: {
  title: string; icon: LucideIcon; action?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={cn("surface flex flex-col p-5", className)}>
      <div className="mb-4 flex items-center gap-2">
        <span className="grid h-7 w-7 place-items-center rounded-lg bg-accent text-accent-foreground ring-1 ring-inset ring-primary/10"><Icon className="h-3.5 w-3.5" /></span>
        <h3 className="text-sm font-semibold">{title}</h3>
        <div className="ml-auto">{action}</div>
      </div>
      {children}
    </section>
  );
}
