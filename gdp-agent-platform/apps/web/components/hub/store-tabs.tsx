import Link from "next/link";
import { cn } from "@/lib/utils";

/** Sections of one asset store, kept in `?view=` so a section can be linked and survives a reload. */
export function StoreTabs({ base, current, tabs, keep }: {
  base: string; current: string; tabs: { id: string; label: string; count?: number | null }[]; keep?: Record<string, string | undefined>;
}) {
  const href = (id: string) => {
    const qs = new URLSearchParams();
    if (id !== tabs[0].id) qs.set("view", id);
    for (const [k, v] of Object.entries(keep ?? {})) if (v) qs.set(k, v);
    const s = qs.toString();
    return s ? `${base}?${s}` : base;
  };
  return (
    <nav className="flex flex-wrap gap-1 border-b border-border/70" aria-label="Store sections">
      {tabs.map((t) => (
        <Link key={t.id} href={href(t.id)} aria-current={current === t.id ? "page" : undefined} data-testid={`store-tab-${t.id}`}
              className={cn("relative flex items-center gap-1.5 px-3 pb-2 pt-1 text-sm transition",
                current === t.id ? "font-medium text-foreground" : "text-muted-foreground hover:text-foreground")}>
          {t.label}
          {t.count != null && <span className="rounded-full bg-muted px-1.5 text-[10px] font-semibold tabular-nums text-muted-foreground">{t.count}</span>}
          {current === t.id && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-primary" />}
        </Link>
      ))}
    </nav>
  );
}
