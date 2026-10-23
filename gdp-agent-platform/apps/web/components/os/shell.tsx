"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, useTransition, type ComponentType, type ReactNode } from "react";
import {
  BookOpen, ChevronDown, CircleHelp, Eye, Gauge, Hammer, Home, Menu, Plus, Rocket, Search, Settings, ShieldCheck, Sparkles, SquareStack, Wrench, X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { screenOfPath, type ScreenKey, type SectionKey } from "@/lib/os/access";
import { PERSONA_COOKIE } from "@/lib/os/personas";

// The Agentic Data OS shell, around every signed-in page: CBRE header (brand, domain scope, global search, workspace
// pill, "Preview as role", "View as" persona, new menu, account), the UX package's ten-entry icon rail (plus Admin for
// admins) and, above the page, the tabs of the entry on show: every page of that entry, /os and classic alike. The
// root layout passes only what the person's governance privileges allow: the entries, their tabs, and the personas.

const ICONS: Record<SectionKey, ComponentType<{ className?: string }>> = {
  work: Home, intake: Plus, enable: Wrench, design: SquareStack, build: Hammer, review: ShieldCheck, skills: Sparkles,
  release: Rocket, knowledge: BookOpen, run: Gauge, admin: Settings,
};
const PREVIEW_COOKIE = "ados_preview_persona";

export type ShellScreen = { key: ScreenKey; href: string; label: string; exact?: boolean; section?: SectionKey };
export type ShellSection = { key: SectionKey; label: string; href: string };
export type ShellPersona = { id: string; label: string; short: string };
/** the persona a super admin is previewing (started from Admin, People) */
export type ShellPreview = { current: string };

function initials(user: string | null) {
  if (!user) return "?";
  const parts = user.replace(/[._@-]+/g, " ").trim().split(/\s+/);
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : user.slice(0, 2)).toUpperCase();
}

const setCookie = (name: string, value: string | null) => {
  document.cookie = value === null
    ? `${name}=; path=/; max-age=0; samesite=lax`
    : `${name}=${encodeURIComponent(value)}; path=/; max-age=${60 * 60 * 24 * 365}; samesite=lax`;
};

export type ShellProps = {
  user: string | null; environment: string;
  /** the rail entries the person may open (each opens its first open tab), and every open tab */
  sections: ShellSection[]; screens: ShellScreen[]; personas: ShellPersona[]; persona: string;
  preview: ShellPreview | null;
  /** beside the brand (the domain scope) and at the end of the header (new menu, account) */
  headerStart?: ReactNode; headerEnd?: ReactNode;
  children: ReactNode;
};

export function OsShell({ user, environment, sections, screens, personas, persona, preview, headerStart, headerEnd, children }: ShellProps) {
  const path = usePathname();
  const router = useRouter();
  const [pending, start] = useTransition();
  const [menu, setMenu] = useState(false);
  useEffect(() => setMenu(false), [path]);

  const choose = (id: string) => { setCookie(PERSONA_COOKIE, id); start(() => router.refresh()); };
  const stopPreview = () => { setCookie(PREVIEW_COOKIE, null); setCookie(PERSONA_COOKIE, null); start(() => router.push("/os/people")); };
  // the page on show: its own tab, or the one it sits under (a run's workspace is under Build's Workbench)
  const here = screenOfPath(path);
  const current = here?.under ?? here?.key ?? null;
  const section = here?.section ?? null;
  const tabs = screens.filter((s) => s.section && s.section === section);
  const help = screens.find((s) => s.key === "help");
  const personaPicker = (compact: boolean) => personas.length > 1 ? (
    <select value={persona} onChange={(e) => choose(e.target.value)} aria-label={compact ? "View as persona (menu)" : "View as persona"}
            className={cn("os-input appearance-none font-bold", compact ? "w-[78px] px-1 py-1 text-[10px] lg:hidden" : "py-1.5 pl-3 pr-8 text-[12px]", pending && "opacity-60")}>
      {personas.map((p) => <option key={p.id} value={p.id}>{compact ? p.short : p.label}</option>)}
    </select>
  ) : compact ? null : <span className="os-tag os-tag-green" aria-label="Persona">{personas[0]?.label}</span>;

  const rail = (
    <nav aria-label="Agentic Data OS" className="flex min-h-full flex-col items-center gap-1 px-1.5 py-3">
      {sections.map(({ key, label, href }) => {
        const I = ICONS[key];
        return (
          <Link key={key} href={href} aria-current={key === section ? "page" : undefined}
                className={cn("os-rail-item flex h-[54px] w-[70px] flex-col items-center justify-center gap-1.5 text-center text-[10.5px] font-semibold leading-tight",
                              key === "admin" && "mt-2 border-t border-[var(--os-line-soft)] pt-1")}>
            <I className="h-[18px] w-[18px]" />{label}
          </Link>
        );
      })}
      <div className="mt-auto flex flex-col items-center gap-1 pt-2">
        {/* below lg the header has no room for "View as": it lives in the slide-out rail */}
        {personaPicker(true)}
        {help && (
          <Link href={help.href} aria-current={current === "help" ? "page" : undefined} className="os-rail-item flex h-[48px] w-[70px] flex-col items-center justify-center gap-1 text-center text-[10px] font-semibold">
            <CircleHelp className="h-4 w-4" />Help
          </Link>
        )}
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
          {headerStart && <div className="ml-2 hidden xl:block">{headerStart}</div>}
        </div>
        <button type="button" onClick={() => window.dispatchEvent(new Event("aip:palette"))}
                className="hidden h-9 min-w-0 max-w-[440px] flex-1 items-center gap-2 truncate rounded-lg border border-[var(--os-line)] bg-[var(--os-subtle)] px-3 text-left text-[12px] text-[var(--os-faint)] md:flex">
          <Search className="h-3.5 w-3.5" /> Search runs, domains, sources, knowledge...
          <kbd className="ml-auto rounded border border-[var(--os-line)] px-1.5 text-[10px]">Ctrl K</kbd>
        </button>
        <div className="flex shrink-0 items-center gap-2.5 text-[12px]">
          <span className="os-tag os-tag-green hidden border border-[var(--os-green-line)] xl:inline-flex" title="The workspace this platform runs in">
            <span className="h-1.5 w-1.5 rounded-full bg-current" /> {environment} WORKSPACE
          </span>
          <label className="hidden items-center gap-2 lg:flex">
            <span className="os-muted hidden 2xl:inline">View as</span>
            <span className="relative">
              {personaPicker(false)}
              {personas.length > 1 && <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 opacity-60" />}
            </span>
          </label>
          {headerEnd ?? (
            <span title={user ?? undefined} className="grid h-8 w-8 place-items-center rounded-full bg-[var(--os-green-bg)] text-[11px] font-extrabold text-[var(--os-green-ink)]">
              {initials(user)}
            </span>
          )}
        </div>
      </header>
      {preview?.current && (
        <div role="status" className="sticky top-[58px] z-30 flex flex-wrap items-center justify-center gap-x-3 gap-y-1 border-b border-[var(--os-amber-line)] bg-[var(--os-amber-bg)] px-4 py-1.5 text-[12px] text-[var(--os-amber-ink)]">
          <Eye className="h-3.5 w-3.5" />
          <span><b>Previewing as {preview.current}</b>: the screens and actions this persona is shown. Data still loads as you; decisions are off.</span>
          <button type="button" className="font-bold underline" onClick={stopPreview}>Stop preview</button>
        </div>
      )}
      <div className="flex">
        <aside className={cn("sticky hidden w-[84px] shrink-0 overflow-y-auto border-r border-[var(--os-line)] bg-[var(--os-surface)] lg:block",
                             preview?.current ? "top-[88px] h-[calc(100vh-88px)]" : "top-[58px] h-[calc(100vh-58px)]")}>{rail}</aside>
        {menu && (
          <div className="fixed inset-0 top-[58px] z-30 lg:hidden" onClick={() => setMenu(false)}>
            <div className="absolute inset-0 bg-black/30" />
            <aside className="relative h-full w-[96px] overflow-y-auto border-r border-[var(--os-line)] bg-[var(--os-surface)]" onClick={(e) => e.stopPropagation()}>{rail}</aside>
          </div>
        )}
        <main className={cn("min-w-0 flex-1 px-4 pb-10 pt-5 sm:px-6 xl:px-8", pending && "opacity-70 transition-opacity")}>
          <div className="mx-auto w-full max-w-[1500px]">
            {tabs.length > 1 && (
              <nav aria-label="Section" data-testid="section-tabs" className="-mt-1 mb-4 flex gap-x-5 overflow-x-auto border-b border-[var(--os-line)]">
                {tabs.map((t) => (
                  <Link key={t.key} href={t.href} aria-current={t.key === current ? "page" : undefined}
                        className={cn("os-tab whitespace-nowrap", t.key === current && "!text-[var(--os-accent-ink)] !border-[var(--os-accent-ink)]")}>{t.label}</Link>
                ))}
              </nav>
            )}
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
