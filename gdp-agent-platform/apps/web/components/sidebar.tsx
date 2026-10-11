"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { ChevronsLeft, ChevronsRight, Workflow } from "lucide-react";
import { cn } from "@/lib/utils";
import type { NavCounts } from "@/app/bff/nav/route";
import { ReportProblemButton } from "@/components/report-problem";
import { allowedBy, NAV_GROUPS } from "@/components/shell/nav";

const COLLAPSE_KEY = "aip.sidebar.collapsed";

/** Lifecycle navigation. The account, role and theme live in the top bar (components/shell/top-bar). */
export function Sidebar({ privileges }: { privileges?: string[] | null }) {
  const allowed = allowedBy(privileges);
  const path = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [counts, setCounts] = useState<NavCounts | null>(null);

  useEffect(() => {
    try { setCollapsed(localStorage.getItem(COLLAPSE_KEY) === "1"); } catch { /* storage unavailable */ }
  }, []);
  useEffect(() => {
    let live = true;
    const load = () => fetch("/bff/nav", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((c: NavCounts | null) => { if (live && c) setCounts(c); })
      .catch(() => undefined);
    void load();
    const timer = setInterval(load, 60_000);
    return () => { live = false; clearInterval(timer); };
  }, []);

  const toggle = () => setCollapsed((v) => {
    try { localStorage.setItem(COLLAPSE_KEY, v ? "0" : "1"); } catch { /* storage unavailable */ }
    return !v;
  });

  return (
    <nav aria-label="Main"
         className={cn("sticky top-0 flex h-screen shrink-0 flex-col overflow-y-auto overflow-x-hidden border-r border-white/[0.06] bg-sidebar text-sidebar-foreground transition-[width] duration-200",
           collapsed ? "w-[72px] px-2" : "w-[244px] px-3")}>
      <Link href="/dashboard" className={cn("flex items-center gap-3 py-5", collapsed ? "justify-center" : "px-2")}>
        <span className="relative grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-indigo-400 via-indigo-500 to-violet-500 text-white shadow-lg shadow-indigo-500/30">
          <Workflow className="h-[18px] w-[18px]" />
          <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full border-2 border-sidebar bg-emerald-400" />
        </span>
        {!collapsed && (
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold tracking-tight text-white">Agentic pipeline</div>
            <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-white/40">Data command center</div>
          </div>
        )}
      </Link>

      {NAV_GROUPS.map((group) => ({ ...group, links: group.links.filter((l) => allowed(l.privilege)) }))
        .filter((group) => group.links.length > 0)
        .map((group) => (
        <div key={group.label} className="mb-4">
          {!collapsed
            ? <p className="mb-1.5 px-2.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-white/30">{group.label}</p>
            : <div className="mx-3 mb-2 h-px bg-white/10" />}
          {group.links.map(({ href, label, icon: Icon, badge, exact }) => {
            const active = exact ? path === href : path === href || path.startsWith(`${href}/`);
            const b = counts && badge ? badge(counts) : null;
            return (
              <Link key={href} href={href} prefetch title={collapsed ? label : undefined}
                    aria-current={active ? "page" : undefined}
                    className={cn("group relative mb-0.5 flex items-center gap-3 rounded-xl py-[7px] text-[13px] transition",
                      collapsed ? "justify-center px-0" : "px-2.5",
                      active ? "bg-white/[0.08] font-medium text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]" : "text-sidebar-foreground/70 hover:bg-white/[0.05] hover:text-white")}>
                {active && <span className="absolute -left-3 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-gradient-to-b from-indigo-300 to-violet-400" />}
                <Icon className={cn("h-[17px] w-[17px] shrink-0", active ? "text-indigo-300" : "text-white/40 group-hover:text-white/80")} />
                {!collapsed && <span className="flex-1 truncate">{label}</span>}
                {b && (
                  <span title={b.title}
                        className={cn("grid min-w-5 place-items-center rounded-full px-1.5 text-[10px] font-semibold tabular-nums",
                          b.tone, collapsed && "absolute right-1 top-0.5 h-4 min-w-4 px-1")}>
                    {b.value}
                  </span>
                )}
              </Link>
            );
          })}
        </div>
      ))}

      <div className="mt-auto pb-3">
        {allowed("CASE.WORK") && <ReportProblemButton collapsed={collapsed} />}
        <button type="button" onClick={toggle} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                className={cn("mt-2 flex w-full items-center gap-2 rounded-xl py-2 text-xs text-white/40 transition hover:bg-white/5 hover:text-white/80",
                  collapsed ? "justify-center" : "px-2.5")}>
          {collapsed ? <ChevronsRight className="h-4 w-4" /> : <><ChevronsLeft className="h-4 w-4" /> Collapse</>}
        </button>
      </div>
    </nav>
  );
}
