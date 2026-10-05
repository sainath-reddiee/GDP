"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BookOpen, Boxes, FileClock, LayoutDashboard, ListChecks, PlusCircle, Settings, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { logout } from "@/app/login/actions";

const groups = [
  {
    label: "Work",
    links: [
      { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
      { href: "/onboarding", label: "New onboarding", icon: PlusCircle },
      { href: "/runs", label: "Runs", icon: ListChecks },
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

export function Sidebar({ user, role, canLogout }: { user: string | null; role: string | null; canLogout: boolean }) {
  const path = usePathname();
  return (
    <nav className="flex flex-col bg-sidebar px-3 py-4 text-sidebar-foreground">
      <div className="mb-6 flex items-center gap-2.5 px-2">
        <span className="flex h-7 w-7 items-center justify-center rounded-md bg-white/15 text-[11px] font-semibold text-white">
          AP
        </span>
        <div>
          <div className="text-sm font-semibold leading-none text-white">Agentic pipeline</div>
          <div className="mt-1 text-[10px] uppercase tracking-wide text-white/45">Snowflake workspace</div>
        </div>
      </div>
      {groups.map((group) => (
        <div key={group.label} className="mb-4">
          <p className="mb-1 px-2 text-[10px] font-semibold uppercase tracking-wider text-white/35">{group.label}</p>
          {group.links.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              prefetch
              className={cn(
                "mb-0.5 flex items-center gap-2.5 rounded-md px-2 py-2 text-sm transition-colors hover:bg-white/10",
                path.startsWith(href) && "bg-white/10 text-white",
              )}
            >
              <Icon className="h-4 w-4" /> {label}
            </Link>
          ))}
        </div>
      ))}
      <div className="mt-auto rounded-lg bg-white/5 px-3 py-3 text-xs">
        <div className="truncate font-medium text-white">{user ?? "Not signed in"}</div>
        {role && <div className="mt-0.5 truncate text-white/50">{role}</div>}
        {canLogout && (
          <form action={logout}>
            <button className="mt-2 text-white/70 underline-offset-2 hover:text-white hover:underline">Sign out</button>
          </form>
        )}
      </div>
    </nav>
  );
}
