"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, useTransition, type ComponentType, type ReactNode } from "react";
import {
  Activity, BadgeCheck, BookOpen, ChevronDown, CircleHelp, ClipboardCheck, Eye, FileClock, Gauge, Hammer, Home, Inbox, LayoutDashboard, ListChecks,
  Menu, MessageSquareText, Plus, Rocket, Search, Settings, ShieldCheck, Sparkles, SquareStack, Store, TrendingUp, Wrench, X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { SECTIONS, screenOfPath, type ScreenKey, type Section } from "@/lib/os/access";
import { PERSONA_COOKIE } from "@/lib/os/personas";

// The Agentic Data OS shell, around every signed-in page: CBRE header (brand, domain scope, global search, workspace
// pill, "Preview as role", "View as" persona, new menu, account) and the sectioned icon rail. The root layout passes
// only what the person's governance privileges allow: the rail's screens and the personas of "View as".

const ICONS: Record<ScreenKey, ComponentType<{ className?: string }>> = {
  work: Home, inbox: Inbox, overview: LayoutDashboard, copilot: MessageSquareText,
  intake: Plus, enable: Wrench, design: SquareStack, build: Hammer, runs: Hammer, review: ShieldCheck, skills: Sparkles, release: Rocket,
  pipelines: ListChecks, stores: Store, knowledge: BookOpen,
  run: Gauge, quality: BadgeCheck, operations: Activity,
  governance: ClipboardCheck, audit: FileClock, admin: Settings, value: TrendingUp, help: CircleHelp,
};
const PREVIEW_COOKIE = "ados_preview_role";

export type ShellScreen = { key: ScreenKey; href: string; label: string; exact?: boolean; section?: Section };
export type ShellPersona = { id: string; label: string; short: string };
export type ShellPreview = { current: string | null; own: string; roles: { role: string; description: string; members: number }[] };

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
  user: string | null; environment: string; screens: ShellScreen[]; personas: ShellPersona[]; persona: string;
  preview: ShellPreview | null;
  /** beside the brand (the domain scope) and at the end of the header (new menu, account) */
  headerStart?: ReactNode; headerEnd?: ReactNode;
  children: ReactNode;
};

export function OsShell({ user, environment, screens, personas, persona, preview, headerStart, headerEnd, children }: ShellProps) {
  const path = usePathname();
  const router = useRouter();
  const [pending, start] = useTransition();
  const [menu, setMenu] = useState(false);
  useEffect(() => setMenu(false), [path]);

  const choose = (id: string) => { setCookie(PERSONA_COOKIE, id); start(() => router.refresh()); };
  // a preview starts from that role's first persona, so the persona cookie is cleared with it
  const previewAs = (role: string) => { setCookie(PREVIEW_COOKIE, role || null); setCookie(PERSONA_COOKIE, null); start(() => router.refresh()); };
  // the rail entry of the page on show: its own screen, or the one it sits under (a run's workspace is under Build)
  const here = screenOfPath(path);
  const current = here?.under ?? here?.key ?? null;
  const active = (key: ScreenKey) => key === current;
  const help = screens.find((s) => s.key === "help");
  const personaPicker = (compact: boolean) => personas.length > 1 ? (
    <select value={persona} onChange={(e) => choose(e.target.value)} aria-label={compact ? "View as persona (menu)" : "View as persona"}
            className={cn("os-input appearance-none font-bold", compact ? "w-[78px] px-1 py-1 text-[10px] lg:hidden" : "py-1.5 pl-3 pr-8 text-[12px]", pending && "opacity-60")}>
      {personas.map((p) => <option key={p.id} value={p.id}>{compact ? p.short : p.label}</option>)}
    </select>
  ) : compact ? null : <span className="os-tag os-tag-green" aria-label="Persona">{personas[0]?.label}</span>;

  const rail = (
    <nav aria-label="Agentic Data OS" className="flex min-h-full flex-col items-center gap-0.5 px-1.5 py-2">
      {SECTIONS.map((section) => {
        const items = screens.filter((s) => s.section === section);
        if (!items.length) return null;
        return (
          <div key={section} role="group" aria-label={section} className="flex w-full flex-col items-center gap-0.5 border-b border-[var(--os-line-soft)] pb-1.5 pt-1 last:border-b-0">
            <span className="os-faint w-full px-1 text-center text-[8.5px] font-extrabold uppercase tracking-[0.12em]">{section}</span>
            {items.map(({ key, href, label }) => {
              const I = ICONS[key];
              return (
                <Link key={key} href={href} aria-current={active(key) ? "page" : undefined}
                      className="os-rail-item flex h-[46px] w-[70px] flex-col items-center justify-center gap-1 text-center text-[10px] font-semibold leading-tight">
                  <I className="h-[17px] w-[17px]" />{label}
                </Link>
              );
            })}
          </div>
        );
      })}
      <div className="mt-auto flex flex-col items-center gap-1 pt-2">
        {/* below lg the header has no room for "View as": it lives in the slide-out rail */}
        {personaPicker(true)}
        {help && (
          <Link href={help.href} aria-current={active("help") ? "page" : undefined} className="os-rail-item flex h-[46px] w-[70px] flex-col items-center justify-center gap-1 text-center text-[10px] font-semibold">
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
          {preview && (
            <label className="hidden items-center gap-2 xl:flex" title="See the screens, personas and actions a role sees">
              <Eye className="h-3.5 w-3.5 opacity-60" />
              <span className="relative">
                <select value={preview.current ?? ""} onChange={(e) => previewAs(e.target.value)} aria-label="Preview as role"
                        className={cn("os-input appearance-none py-1.5 pl-3 pr-8 text-[12px] font-bold", preview.current && "border-[var(--os-amber-line)] bg-[var(--os-amber-bg)]")}>
                  <option value="">Myself ({preview.own})</option>
                  {preview.roles.map((r) => <option key={r.role} value={r.role}>{r.role.replace(/_/g, " ").toLowerCase()}{r.members ? ` · ${r.members}` : ""}</option>)}
                </select>
                <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 opacity-60" />
              </span>
            </label>
          )}
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
          <span><b>Previewing as {preview.current.replace(/_/g, " ").toLowerCase()}</b>: the screens, personas and actions this role is shown. Data still loads as you, and the classic pages still act as you.</span>
          <button type="button" className="font-bold underline" onClick={() => previewAs("")}>Stop preview</button>
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
          <div className="mx-auto w-full max-w-[1500px]">{children}</div>
        </main>
      </div>
    </div>
  );
}
