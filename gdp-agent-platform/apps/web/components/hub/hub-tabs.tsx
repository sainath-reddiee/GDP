"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BadgeCheck, BarChart3, Database, GitCompareArrows, LayoutDashboard, Network, Rocket } from "lucide-react";
import { cn } from "@/lib/utils";

export const HUBS = [
  { href: "/dashboard", label: "Overview", icon: LayoutDashboard, exact: true },
  { href: "/dashboard/sources", label: "Sources", icon: Database },
  { href: "/dashboard/profiling", label: "Profiling", icon: BarChart3 },
  { href: "/dashboard/modeling", label: "Modeling", icon: Network },
  { href: "/dashboard/sttm", label: "STTM", icon: GitCompareArrows },
  { href: "/dashboard/quality", label: "Quality", icon: BadgeCheck },
  { href: "/dashboard/onboarding", label: "Onboarding", icon: Rocket },
] as const;

/** The lifecycle hubs as one navigation row above every /dashboard page. */
export function HubTabs() {
  const path = usePathname();
  return (
    <nav aria-label="Lifecycle hubs" className="-mx-1 mb-6 flex gap-1 overflow-x-auto border-b border-border/70 px-1">
      {HUBS.map(({ href, label, icon: Icon, ...rest }) => {
        const exact = "exact" in rest && rest.exact;
        const active = exact ? path === href : path === href || path.startsWith(`${href}/`);
        return (
          <Link key={href} href={href} aria-current={active ? "page" : undefined}
                className={cn("relative flex shrink-0 items-center gap-2 px-3 pb-2.5 pt-1 text-sm transition",
                  active ? "font-medium text-foreground" : "text-muted-foreground hover:text-foreground")}>
            <Icon className={cn("h-4 w-4", active ? "text-primary" : "text-muted-foreground/70")} />
            {label}
            {active && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-primary" />}
          </Link>
        );
      })}
    </nav>
  );
}
