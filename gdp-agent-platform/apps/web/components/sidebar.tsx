"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  BookOpen, Boxes, ChevronsLeft, ChevronsRight, Database, FileClock, LayoutDashboard, ListChecks, LogOut, Settings,
  Sparkles, Workflow,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { logout } from "@/app/login/actions";
import type { NavCounts } from "@/app/bff/nav/route";
import { RoleSelector } from "@/components/role-selector";

type NavLink = { href: string; label: string; icon: typeof Database; badge?: (c: NavCounts) => { value: number; tone: string; title: string } | null };

const groups: { label: string; links: NavLink[] }[] = [
  {
    label: "Work",
    links: [
      { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
      {
        href: "/sources", label: "Sources", icon: Database,
        badge: (c) => c.profiling ? { value: c.profiling, tone: "bg-amber-400 text-amber-950", title: `${c.profiling} profiling now` }
          : c.staged ? { value: c.staged, tone: "bg-white/10 text-white/70", title: `${c.staged} tables staged` } : null,
      },
      {
        href: "/runs", label: "Runs", icon: ListChecks,
        badge: (c) => c.review ? { value: c.review, tone: "bg-rose-500 text-white", title: `${c.review} waiting on review` }
          : c.running ? { value: c.running, tone: "bg-white/10 text-white/70", title: `${c.running} running` } : null,
      },
    ],
  },
  {
    label: "Knowledge",
    links: [
      { href: "/knowledge", label: "Knowledge", icon: BookOpen },
      { href: "/domains", label: "Domains", icon: Boxes },
      { href: "/skills", label: "Skills", icon: Sparkles },
    ],
  },
  {
    label: "Platform",
    links: [
      { href: "/audit", label: "Audit", icon: FileClock },
      { href: "/admin", label: "Admin", icon: Settings },
    ],
  },
];

const COLLAPSE_KEY = "aip.sidebar.collapsed";

function initials(user: string | null) {
  const name = (user ?? "?").replace(/[^A-Za-z ]/g, " ").trim();
  return (name.split(/\s+/).map((p) => p[0]).join("") || "?").slice(0, 2).toUpperCase();
}

export function Sidebar({ user, role, canLogout }: { user: string | null; role: string | null; canLogout: boolean }) {
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
         className={cn("sticky top-0 flex h-screen shrink-0 flex-col overflow-y-auto overflow-x-hidden border-r border-white/5 bg-sidebar text-sidebar-foreground transition-[width] duration-200",
           collapsed ? "w-[72px] px-2" : "w-[248px] px-3")}>
      <div className={cn("flex items-center gap-3 py-5", collapsed ? "justify-center" : "px-2")}>
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-sky-400 via-blue-500 to-violet-500 text-white shadow-lg shadow-blue-500/30">
          <Workflow className="h-[18px] w-[18px]" />
        </span>
        {!collapsed && (
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-white">Agentic pipeline</div>
            <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-white/40">Snowflake workspace</div>
          </div>
        )}
      </div>

      {groups.map((group) => (
        <div key={group.label} className="mb-4">
          {!collapsed
            ? <p className="mb-1.5 px-2.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-white/30">{group.label}</p>
            : <div className="mx-3 mb-2 h-px bg-white/10" />}
          {group.links.map(({ href, label, icon: Icon, badge }) => {
            const active = path === href || path.startsWith(`${href}/`);
            const b = counts && badge ? badge(counts) : null;
            return (
              <Link key={href} href={href} prefetch title={collapsed ? label : undefined}
                    aria-current={active ? "page" : undefined}
                    className={cn("group relative mb-0.5 flex items-center gap-3 rounded-xl py-2 text-sm transition",
                      collapsed ? "justify-center px-0" : "px-2.5",
                      active ? "bg-white/10 font-medium text-white" : "text-sidebar-foreground/80 hover:bg-white/5 hover:text-white")}>
                {active && <span className="absolute -left-3 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-sky-400" />}
                <Icon className={cn("h-[18px] w-[18px] shrink-0", active ? "text-sky-300" : "text-white/50 group-hover:text-white/80")} />
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
        {!collapsed ? (
          <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-3">
            <div className="flex items-center gap-2.5">
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-emerald-400 to-sky-500 text-[11px] font-bold text-white">
                {initials(user)}
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-semibold text-white">{user ?? "Not signed in"}</div>
                <div className="flex items-center gap-1 text-[10px] text-white/45">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> Connected to Snowflake
                </div>
              </div>
              {canLogout && (
                <form action={logout}>
                  <button title="Sign out" aria-label="Sign out" className="rounded-lg p-1.5 text-white/50 hover:bg-white/10 hover:text-white">
                    <LogOut className="h-3.5 w-3.5" />
                  </button>
                </form>
              )}
            </div>
            <RoleSelector currentRole={role} />
          </div>
        ) : (
          <div className="flex justify-center" title={`${user ?? ""} · ${role ?? ""}`}>
            <span className="grid h-8 w-8 place-items-center rounded-full bg-gradient-to-br from-emerald-400 to-sky-500 text-[11px] font-bold text-white">
              {initials(user)}
            </span>
          </div>
        )}
        <button type="button" onClick={toggle} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                className={cn("mt-2 flex w-full items-center gap-2 rounded-xl py-2 text-xs text-white/40 transition hover:bg-white/5 hover:text-white/80",
                  collapsed ? "justify-center" : "px-2.5")}>
          {collapsed ? <ChevronsRight className="h-4 w-4" /> : <><ChevronsLeft className="h-4 w-4" /> Collapse</>}
        </button>
      </div>
    </nav>
  );
}
