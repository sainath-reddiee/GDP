"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, useTransition, type ReactNode } from "react";
import {
  BookOpen, ChevronDown, CircleHelp, Gauge, Hammer, Home, LayoutGrid, Menu, Plus, Rocket, Search, ShieldCheck, Sparkles, SquareStack, Wrench, X,
} from "lucide-react";
import { ThemeToggle } from "@/components/theme/theme-toggle";
import { cn } from "@/lib/utils";
import { PERSONA_COOKIE, PERSONAS, type PersonaId } from "@/lib/os/personas";

// The Agentic Data OS shell: CBRE header (brand, global search, workspace pill, "View as" persona, avatar) and the
// ten-entry icon rail of the UX package. Persona is a view preference only (a cookie the /os pages read).

const RAIL = [
  { href: "/os", label: "My Work", icon: Home, exact: true },
  { href: "/os/intake", label: "Intake", icon: Plus },
  { href: "/os/enable", label: "Enable", icon: Wrench },
  { href: "/os/design", label: "Design", icon: SquareStack },
  { href: "/os/build", label: "Build", icon: Hammer },
  { href: "/os/review", label: "Review", icon: ShieldCheck },
  { href: "/os/skills", label: "Skills", icon: Sparkles },
  { href: "/os/release", label: "Release", icon: Rocket },
  { href: "/os/knowledge", label: "Knowledge", icon: BookOpen },
  { href: "/os/run", label: "Run & DQ", icon: Gauge },
];

function initials(user: string | null) {
  if (!user) return "?";
  const parts = user.replace(/[._@-]+/g, " ").trim().split(/\s+/);
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : user.slice(0, 2)).toUpperCase();
}

export function OsShell({ user, persona, environment, children }: { user: string | null; persona: PersonaId; environment: string; children: ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const [pending, start] = useTransition();
  const [menu, setMenu] = useState(false);
  useEffect(() => setMenu(false), [path]);

  const choose = (id: string) => {
    document.cookie = `${PERSONA_COOKIE}=${id}; path=/; max-age=${60 * 60 * 24 * 365}; samesite=lax`;
    start(() => router.refresh());
  };
  const active = (href: string, exact?: boolean) => (exact ? path === href : path === href || path.startsWith(`${href}/`));

  const rail = (
    <nav aria-label="Agentic Data OS" className="flex h-full flex-col items-center gap-1.5 px-1.5 py-3">
      {RAIL.map(({ href, label, icon: I, exact }) => (
        <Link key={href} href={href} aria-current={active(href, exact) ? "page" : undefined}
              className="os-rail-item flex h-[58px] w-[70px] flex-col items-center justify-center gap-1.5 text-center text-[10.5px] font-semibold">
          <I className="h-[19px] w-[19px]" />{label}
        </Link>
      ))}
      <div className="mt-auto flex flex-col items-center gap-1.5">
        {/* below lg the header has no room for "View as": it lives in the slide-out rail */}
        <select value={persona} onChange={(e) => choose(e.target.value)} aria-label="View as persona (menu)"
                className="os-input w-[78px] px-1 py-1 text-[10px] font-bold lg:hidden">
          {PERSONAS.map((p) => <option key={p.id} value={p.id}>{p.short}</option>)}
        </select>
        <Link href="/work" title="Open the classic workspace" className="os-rail-item flex h-[52px] w-[70px] flex-col items-center justify-center gap-1 text-center text-[10px] font-semibold">
          <LayoutGrid className="h-4 w-4" />Classic
        </Link>
        <Link href="/os/help" aria-current={active("/os/help") ? "page" : undefined} className="os-rail-item flex h-[52px] w-[70px] flex-col items-center justify-center gap-1 text-center text-[10px] font-semibold">
          <CircleHelp className="h-4 w-4" />Help
        </Link>
      </div>
    </nav>
  );

  return (
    <div className="ados">
      <header className="sticky top-0 z-40 flex h-[58px] items-center justify-between gap-3 border-b border-[var(--os-line)] bg-[var(--os-surface)] px-3 sm:px-5">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="lg:hidden">
            <button type="button" className="os-btn p-2" aria-label={menu ? "Close navigation" : "Open navigation"} onClick={() => setMenu((v) => !v)}>
              {menu ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
            </button>
          </span>
          <Link href="/os" className="flex items-center gap-2.5">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-[#004735] text-white shadow-sm">
              <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden><path fill="currentColor" d="M12 2 22 12 12 22 2 12Z M12 7.5 7.5 12 12 16.5 16.5 12Z" fillRule="evenodd" /></svg>
            </span>
            <span className="text-[19px] font-black tracking-[-0.04em] text-[#004535] dark:text-[#5fd8aa]">CBRE</span>
            <span className="hidden border-l border-[var(--os-line)] pl-3 text-[15px] font-bold tracking-[-0.02em] sm:inline">Agentic Data OS</span>
          </Link>
        </div>
        <button type="button" onClick={() => window.dispatchEvent(new Event("aip:palette"))}
                className="hidden h-9 w-full max-w-[440px] items-center gap-2 rounded-lg border border-[var(--os-line)] bg-[var(--os-subtle)] px-3 text-left text-[12px] text-[var(--os-faint)] md:flex">
          <Search className="h-3.5 w-3.5" /> Search runs, domains, sources, knowledge...
          <kbd className="ml-auto rounded border border-[var(--os-line)] px-1.5 text-[10px]">Ctrl K</kbd>
        </button>
        <div className="flex items-center gap-3 text-[12px]">
          <span className="os-tag os-tag-green hidden border border-[var(--os-green-line)] md:inline-flex" title="The workspace this platform runs in">
            <span className="h-1.5 w-1.5 rounded-full bg-current" /> {environment} WORKSPACE
          </span>
          <ThemeToggle />
          <label className="hidden items-center gap-2 lg:flex">
            <span className="os-muted">View as</span>
            <span className="relative">
              <select value={persona} onChange={(e) => choose(e.target.value)} aria-label="View as persona"
                      className={cn("os-input appearance-none py-1.5 pl-3 pr-8 text-[12px] font-bold", pending && "opacity-60")}>
                {PERSONAS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </select>
              <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 opacity-60" />
            </span>
          </label>
          <span title={user ?? undefined} className="grid h-8 w-8 place-items-center rounded-full bg-[var(--os-green-bg)] text-[11px] font-extrabold text-[var(--os-green-ink)]">
            {initials(user)}
          </span>
        </div>
      </header>
      <div className="flex">
        <aside className="sticky top-[58px] hidden h-[calc(100vh-58px)] w-[84px] shrink-0 border-r border-[var(--os-line)] bg-[var(--os-surface)] lg:block">{rail}</aside>
        {menu && (
          <div className="fixed inset-0 top-[58px] z-30 lg:hidden" onClick={() => setMenu(false)}>
            <div className="absolute inset-0 bg-black/30" />
            <aside className="relative h-full w-[96px] overflow-y-auto border-r border-[var(--os-line)] bg-[var(--os-surface)]" onClick={(e) => e.stopPropagation()}>{rail}</aside>
          </div>
        )}
        <main className={cn("min-w-0 flex-1 px-4 pb-10 pt-5 sm:px-6 xl:px-8", pending && "opacity-70 transition-opacity")}>
          <div className="mx-auto w-full max-w-[1500px]">{children}</div>
        </main>
      </div>
    </div>
  );
}

