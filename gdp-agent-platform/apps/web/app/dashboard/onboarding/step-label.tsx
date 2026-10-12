import { cn } from "@/lib/utils";

/** A numbered step heading with a rule running to the edge, so each choice on the onboarding page reads as its own step. */
export function StepLabel({ n, title, hint, className }: { n: number; title: string; hint?: string; className?: string }) {
  return (
    <div className={cn("flex items-center gap-3", className)}>
      <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-primary/10 text-[11px] font-semibold text-primary">{n}</span>
      <span className="shrink-0 text-xs font-semibold uppercase tracking-[0.12em] text-foreground/80">{title}</span>
      {hint && <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">{hint}</span>}
      <span className="h-px flex-1 bg-border" aria-hidden />
    </div>
  );
}
