"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { allowedBy, NAV_GROUPS } from "./nav";

/**
 * The tab bar of a workspace (components/shell/nav: a link with `tabs`), drawn by the shell above a workspace's own
 * pages: Pipelines (Silver, Gold, Lineage, Code), Knowledge, Quality, Operations and Governance. Plain links to each
 * page's own route, so history, bookmarks and the back button follow the tab. Only on the pages themselves, not on a
 * run or a domain under them; the Stores draw their own (components/hub/store-nav). Tabs the caller may not open are
 * left out, and a workspace with a single visible page shows no bar.
 */
export function WorkspaceTabs({ privileges }: { privileges?: string[] | null }) {
  const path = usePathname();
  const allowed = allowedBy(privileges);
  const ws = NAV_GROUPS.flatMap((g) => g.links).find((l) => l.tabs && !l.ownTabs && l.children?.some((c) => c.href === path));
  if (!ws) return null;
  const tabs = (ws.children ?? []).filter((c) => allowed(c.privilege));
  if (tabs.length < 2) return null;
  const Icon = ws.icon;
  return (
    <nav aria-label={`${ws.label} pages`} data-testid="workspace-tabs"
         className="mb-6 flex items-center gap-1 overflow-x-auto rounded-xl border bg-card/60 p-1 text-sm shadow-sm">
      <span className="inline-flex shrink-0 items-center gap-1.5 px-2.5 py-1.5 text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        <Icon className="h-4 w-4" />{ws.label}
      </span>
      <span className="mx-1 h-5 w-px shrink-0 bg-border" aria-hidden />
      {tabs.map((t) => {
        const on = t.href === path;
        const TIcon = t.icon;
        return (
          <Link key={t.href} href={t.href} aria-current={on ? "page" : undefined}
                className={cn("inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 transition",
                  on ? "bg-primary/10 font-medium text-primary ring-1 ring-inset ring-primary/20" : "text-muted-foreground hover:bg-muted hover:text-foreground")}>
            <TIcon className="h-4 w-4" />{t.label}
          </Link>
        );
      })}
    </nav>
  );
}
