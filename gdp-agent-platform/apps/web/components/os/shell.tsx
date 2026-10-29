"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, useTransition, type ComponentType, type ReactNode } from "react";
import {
  BookOpen, ChevronDown, CircleHelp, Eye, Gauge, Hammer, Home, Menu, Plus, Rocket, Settings, ShieldCheck, Sparkles, SquareStack, Wrench, X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { canSee, screenOfPath, type ScreenKey, type SectionKey } from "@/lib/os/access";
import { PERSONA_COOKIE } from "@/lib/os/personas";
import { CopilotButton } from "@/components/copilot/copilot-provider";

const PERSONA_PRIVS: Record<string, string[]> = {
  super: ["*"],
  forward_lead: ["RUN.CREATE", "RUN.OPERATE", "PROFILE.RUN", "MAPPING.DECIDE", "STTM.EDIT", "SODA.EDIT", "QA.EDIT", "DBT.EDIT", "SKILL.EDIT", "SKILL.RELEASE", "AI.USE", "REQUEST.CHANGES", "OPS.VIEW", "JIRA.READ"],
  product_owner: ["DOMAIN.EDIT", "KNOWLEDGE.EDIT", "AI.USE", "REQUEST.CHANGES"],
  admin: ["SOURCE.CONNECT", "DOMAIN.ONBOARD", "RUN.CREATE", "RUN.OPERATE", "AI.USE", "REQUEST.CHANGES", "OPS.VIEW", "OPS.OPERATE"],
  designer: ["RUN.CREATE", "PROFILE.RUN", "MODEL.EDIT", "MODEL.APPROVE", "MAPPING.DECIDE", "MAPPING.APPROVE", "STTM.EDIT", "STTM.APPROVE", "SODA.EDIT", "SODA.APPROVE", "SKILL.EDIT", "AI.USE", "REQUEST.CHANGES"],
  developer: ["REVIEW.APPROVE", "REVIEW.DECIDE", "DBT.EDIT", "AI.USE", "REQUEST.CHANGES", "OPS.VIEW"],
  qa: ["QA.EDIT", "QA.SIGNOFF", "PACKAGE.EDIT", "PACKAGE.APPROVE", "AI.USE", "REQUEST.CHANGES", "OPS.VIEW"],
  reliability: ["OPS.VIEW", "OPS.OPERATE", "OPS.FIX_APPROVE", "OPS.FIX_PUBLISH", "CASE.WORK", "JIRA.READ", "AI.USE", "REQUEST.CHANGES"],
  curator: ["DOMAIN.EDIT", "KNOWLEDGE.EDIT", "SKILL.EDIT", "SKILL.RELEASE", "AI.USE", "REQUEST.CHANGES"],
  viewer: [],
};

// The Agentic Data OS shell, around every signed-in page: header (brand, Copilot, "Preview as role", "View as"
// persona, new menu, account), the UX package's ten-entry icon rail (plus Admin for
// admins) and, above the page, the tabs of the entry on show: every page of that entry, /os and classic alike. The
// root layout passes only what the person's governance privileges allow: the entries, their tabs, and the personas.

const ICONS: Record<SectionKey, ComponentType<{ className?: string }>> = {
  work: Home, intake: Plus, enable: Wrench, design: SquareStack, build: Hammer, review: ShieldCheck, skills: Sparkles,
  release: Rocket, knowledge: BookOpen, run: Gauge, admin: Settings,
};
const PREVIEW_COOKIE = "ados_preview_persona";

export type ShellScreen = { key: ScreenKey; href: string; label: string; exact?: boolean; section?: SectionKey };
export type ShellSection = { key: SectionKey; label: string; href: string };
export type ShellPersona = { id: string; label: string; short: string; home: string; responsibility: string };
/** the persona the person works as, and how much of their own access it leaves out (screens and privileges) */
export type ShellScope = { label: string; open: number; hidden: number; privileges: number; ownPrivileges: number; full: boolean };
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
  user: string | null;
  /** the rail entries the person may open (each opens its first open tab), and every open tab */
  sections: ShellSection[]; screens: ShellScreen[]; personas: ShellPersona[]; persona: string;
  preview: ShellPreview | null; scope?: ShellScope | null;
  /** at the end of the header (new menu, account) */
  headerEnd?: ReactNode;
  children: ReactNode;
};

export function OsShell({ user, sections, screens, personas, persona, preview, scope, headerEnd, children }: ShellProps) {
  const path = usePathname();
  const router = useRouter();
  const [pending, start] = useTransition();
  const [menu, setMenu] = useState(false);
  useEffect(() => setMenu(false), [path]);

  // the page on show: its own tab, or the one it sits under (a run's workspace is under Build's Workbench)
  const here = screenOfPath(path);
  const current = here?.under ?? here?.key ?? null;
  const section = here?.section ?? null;
  const tabs = screens.filter((s) => s.section && s.section === section);
  const help = screens.find((s) => s.key === "help");

  // stay on the current page (and its query params) when the chosen persona can open it; else go to the persona's start page
  const choose = (id: string) => {
    setCookie(PERSONA_COOKIE, id);
    const keep = !here || canSee({ governance: true, privileges: PERSONA_PRIVS[id] ?? [] }, here.key);
    if (keep) {
      start(() => { router.refresh(); });
      return;
    }
    const home = personas.find((p) => p.id === id)?.home ?? "/os";
    start(() => { router.push(home); router.refresh(); });
  };
  const stopPreview = () => { setCookie(PREVIEW_COOKIE, null); setCookie(PERSONA_COOKIE, null); start(() => router.push("/os/people")); };
  // what the chosen persona leaves out, on hover of the picker (not a banner: it is not something to act on)
  const scopeHint = !preview?.current && scope && !scope.full && scope.hidden > 0
    ? `Working as ${scope.label}: ${scope.hidden} screen${scope.hidden === 1 ? " is" : "s are"} hidden for this persona. Switch persona to use them.`
    : undefined;
  const personaPicker = (compact: boolean) => personas.length > 1 ? (
    <select value={persona} onChange={(e) => choose(e.target.value)} aria-label={compact ? "View as persona (menu)" : "View as persona"} title={scopeHint}
            className={cn("os-input appearance-none font-bold", compact ? "w-[78px] px-1 py-1 text-[10px] lg:hidden" : "py-1.5 pl-3 pr-8 text-[12px]", pending && "opacity-60")}>
      {personas.map((p) => <option key={p.id} value={p.id} title={p.responsibility}>{compact ? p.short : p.label}</option>)}
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
    <div className="ados" data-persona={preview?.current ? "preview" : persona}>
      <header className="sticky top-0 z-40 flex h-[58px] items-center justify-between gap-3 border-b border-[var(--os-line)] bg-[var(--os-surface)] px-3 sm:px-5">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="lg:hidden">
            <button type="button" className="os-btn p-2" aria-label={menu ? "Close navigation" : "Open navigation"} onClick={() => setMenu((v) => !v)}>
              {menu ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
            </button>
          </span>
          <Link href="/os" className="flex items-center gap-2.5" aria-label="Anblicks · Agentic Data OS">
            <svg viewBox="0 0 3100 766" role="img" aria-label="Anblicks" className="h-7 w-auto shrink-0">
              <path fill="#1c4177" className="dark:fill-[#5b8def]" d="M652.2,455.8c19.9-69-69.7-186.9-275.1-108.4-166.4,63.6-197.9,186.4-179,272.1,11.3,51.5,50.8,91.5,79.9,96.1,0,0-15.7-9.1-29.5-38.7-24.9-53.5-7.8-118.7,49.5-144.3,50-22.3,85.1-16,132-14.1,99.6,4,197.9,21.5,222.1-62.7h.1Z" />
              <path fill="#e51e26" d="M750.4,435.6c14.1-205.7-156.6-390-382.1-385.6C157.5,54.2-24.8,269.2,81,373.9c37.2,36.9,86.8,28.1,128,6.1,71.5-38.3,147.1-123.7,293.5-122.5,201.8,1.7,215.5,154,215,176.8-.5,23-1.3,151.1-200.6,157.9-90.6,3.1-161.7-28.2-172.1,34.1-13.1,78.4,172.1,118.7,301.4,16.7,101-79.7,103.5-193.7,104.4-207.4h-.2Z" />
              <g fill="#1c4177" fillRule="evenodd" className="dark:fill-white">
                <path d="M1084.8,371.7c-6.5-4.1-12.7-8.5-18.7-13.4-14-10.5-29-19.6-44.8-27.1-16-7-33.3-10.3-50.8-9.9-16.7,0-33.2,3.4-48.6,9.9-15.4,6.3-29.4,15.6-41.1,27.5-11.8,11.6-21.3,25.3-28,40.5s-10.2,31.5-10.1,48.1c0,17,2.9,34,8.6,50,5.7,15.5,14.3,29.7,25.3,41.9,11,12,24.3,21.6,39.2,28.3,15.6,6.6,32.3,10,49.3,9.9,23,1.2,46-2.8,67.2-11.8,19.5-10.9,37.1-24.9,52.3-41.2l.2-152.8v.1ZM1035.2,287.7c22.1,10.7,43.3,23.2,63.4,37.4,1.4,1,2.9,1.9,4.5,2.7,6,4.3,12.3,8,19,11,6.4,1.8,13.1,2.6,19.8,2.3-5.3,3.5-9,9-10.1,15.3-1.3,11-1.8,22.1-1.5,33.2v201.8c0,8.8,5.1,16.8,13.1,20.6h-58.3v-36.6l-2.6-1.1c-10.2,17-25.6,30.3-44,37.8-20.3,7.6-41.8,11.2-63.5,10.7-23.1,0-46-4.3-67.5-12.6-21.1-8.2-40.6-20.3-57.3-35.6-34.3-31.5-53.7-76-53.4-122.5-.2-23.6,4.4-47,13.4-68.8,8.7-21,21.2-40.1,36.9-56.5,15.8-16.2,34.7-28.9,55.6-37.4,21.6-8.9,44.9-13.5,68.3-13.4,22-1,43.9,2.9,64.1,11.5" />
                <path d="M1205.1,365.8c0-5.7-1.5-11.2-4.5-16-.3-.7-.8-1.9-1.1-2.6,11.9-3.6,23.3-8.7,33.8-15.3,10-6.6,19.5-13.9,28.6-21.7,11.5-10.2,24.2-18.9,37.9-25.8,16.4-6.2,33.9-9,51.5-8.2,18.5,0,36.8,3.8,53.7,11.2,17,7.2,32.5,17.8,45.4,31,13.1,13.1,23.6,28.6,30.9,45.7,7.4,16.9,11.2,35,11.3,53.4v149.5c-.6,8.8.7,17.6,3.8,25.8,2.5,5.1,6.3,9.5,11,12.7h-73.4c5.2-2.8,9.2-7.5,11-13.1,1.8-7.7,2.5-15.6,2.2-23.5v-145c.7-27.4-9.9-53.9-29.3-73.3-19.2-19.8-45.8-30.6-73.3-29.9-16.3-.5-32.3,3.6-46.3,12-13.3,8.7-25.4,19.1-36.1,31-3.2,3.5-6.7,6.8-10.5,9.7v209.3c0,4.3,1.4,8.6,4.1,11.9,3.2,3.6,6.9,6.7,11,9.4l1.1,1.1h-72.8c4.1-2.6,7.2-6.6,8.6-11.2,1.4-6.8,2-13.6,1.9-20.6v-207.5h-.8.3Z" />
                <path d="M1617.6,526.6c6.5,4.1,12.7,8.5,18.7,13.4,14.2,10.2,29.1,19.1,44.8,26.8,15.9,7.3,33.3,10.9,50.8,10.4,33.6-.3,65.8-13.8,89.6-37.6,11.8-11.6,21.3-25.4,28-40.7,6.7-15.2,10.2-31.7,10.1-48.3,0-17.1-2.9-34.1-8.6-50.2-5.7-15.5-14.3-29.8-25.4-42.2-11-12-24.3-21.6-39.2-28.3-15.5-6.6-32.3-10-49.2-10-23-1.2-46,2.8-67.2,11.9-19.5,10.9-37.2,24.9-52.3,41.4v153.2h0l-.1.2ZM1572.4,159.6c0-9.2-5-17.6-13.1-22h58.2v184.7l2.8,1.2c10.1-17.1,25.6-30.4,44-38,20.3-7.7,41.8-11.3,63.4-10.7,46.2,0,90.8,17.2,125,48.3,34.3,31.7,53.7,76.3,53.4,123,.2,23.7-4.4,47.1-13.4,69-8.7,21-21.2,40.2-37,56.7-15.8,16.2-34.7,29-55.6,37.6-21.6,9-44.9,13.5-68.3,13.4-17.3.4-34.6-2.6-50.8-8.8-17.8-7.7-34.9-17.1-50.9-28.1-12.7-8.1-22-13.8-28-17.2-11.2-8.6-24.1-14.9-37.8-18.7,3.7-4.4,5.8-10,6.1-15.7.7-7.7,1.1-17.6,1.1-29.5V159.6h.9Z" />
                <path d="M1975.3,605.4c5.1-3.1,8.6-8.4,9.4-14.3,1.8-10.6,2.5-21.3,2.2-32.1V167.4c0-6.2-.5-12.4-1.9-18.5-1.2-4.8-4.3-9-8.7-11.4h56.5v440.7c-1.5,10.4,3,20.8,11.7,26.8h-69.4v.3h0l.2.1Z" />
                <path d="M2169.4,180.9c5.8,5.5,8.9,13.3,8.6,21.2.3,16.2-12.5,29.6-28.7,29.9-8,.2-15.8-3-21.4-8.7-5.8-5.5-8.9-13.3-8.6-21.2-.1-16.4,13-29.8,29.4-30,7.8.2,15.1,3.3,20.8,8.7h.1l-.2.1ZM2114,605.4c5.1-3.1,8.6-8.4,9.4-14.4,1.8-10.6,2.6-21.5,2.3-32.2v-246.4c0-6.3-.6-12.5-1.9-18.6-1.1-4.8-4.3-9-8.7-11.4h56.5v296c-1.6,10.5,2.9,20.9,11.7,26.9h-69.4l.1.1Z" />
                <path d="M2494.9,356c-11.9-10.9-25.3-20-39.8-27.1-14-5.5-29-8-44-7.3-18.4-.2-36.5,3.5-53.3,11-15.5,7.2-29.3,17.4-40.5,30.2-10.9,12.4-19.5,26.8-25.1,42.4-5.6,15-8.5,30.9-8.5,47-.8,51.2,30.1,97.5,77.7,116.4,16,6.1,33,9.2,50.2,9.1,31.7-.3,61.8-13.5,83.5-36.6v77l-3.5-1.9c-2-1.2-3.8-2.7-5.8-3.8-1.3-.9-2.7-1.4-4.3-1.5-.4,0-2.7.4-6.6,1.2-11.9,2.7-24.7,4.9-37.8,7.3-12.5,2.2-25.2,3.4-37.9,3.4-20.5-.1-40.7-4.4-59.5-12.6-86.8-37.8-127.3-138.1-91.1-225.5,8.5-20.9,20.8-40,36.3-56.5,15.3-16.2,33.7-29.2,54.1-38.2,20.5-9.2,42.8-13.8,65.3-13.8,9.2,0,18.4.7,27.5,2.3,8.5,1.5,18.9,3.8,30.9,6.8,5.6,1.9,11.5,3.2,17.4,3.8,2.3-.2,4.4-1.2,6.1-2.7,2.3-1.9,4.6-3.8,7.3-6.5l2.1-1.9v77.9h-.6v.1h-.1Z" />
                <path d="M2824.7,283.6v1.2c-15.3,7.8-29.2,18.1-41.2,30.2l-96.3,91.8c19.6,1.3,38.5,7.5,55,18.2,15.4,10.1,28.2,23.7,37.4,39.7,9.7,16.7,16.7,34.8,21,53.6,4.4,19.7,6.6,39.9,6.4,60-1.2,10.8,4,21.3,13.4,26.8h-71.7c8.7-6,13.3-16.4,11.9-26.8,0-49.9-11.5-86.9-34.4-111-23-24.1-58.8-36.2-107.6-36.3h-14.2l128.2-130.7c2.7-1.8,4.4-4.7,4.7-7.9-.1-3.7-2.6-6.9-6.1-7.9v-.8l93.4-.2h0l.1.1ZM2530.1,605.4c5.2-3.1,8.7-8.4,9.5-14.3,1.8-10.6,2.6-21.3,2.3-32.1V167.4c0-6.2-.6-12.4-1.9-18.5-1.2-4.9-4.4-9-8.9-11.4h57.3v440.7c-1.6,10.5,3,20.9,11.8,26.8h-70.1v.3h0v.1Z" />
                <path d="M3020.8,344.5c-7.8-7-16.7-12.7-26.3-16.8-9.4-4.2-19.4-6.4-29.6-6.5-12.8-.7-25.4,2.6-36.1,9.5-9.5,7.8-14.6,19.8-13.4,32-.9,12.5,4.1,24.8,13.4,33.2,12.7,9.8,26.9,17.6,41.9,23.3,13,5.7,23.5,10.7,31.4,14.9,8.5,4.6,16.4,10.6,23.1,17.6,8,8.2,14.1,17.9,18.1,28.6,5,13.4,7.3,27.7,6.8,42,.8,26.7-9.4,52.7-28.1,71.8-18,18.7-43,29.1-69,28.6-17.7,0-35.4-2.1-52.6-6.5-4.7-.9-9.3-2-13.8-3.4-4.7-1.7-9.5-2.9-14.4-3.4-2.2.2-4.3,1-6.1,2.3-2.4,1.4-4.3,3.4-5.8,5.7h-.7v-79.7c10.4,12,23.1,21.8,37.3,28.9,13.3,7.1,28.2,10.7,43.3,10.7,16.4.6,32.6-3.4,46.9-11.4,12.8-8.5,19.8-23.2,18.4-38.5,1.3-16.1-4.3-31.9-15.5-43.5-13.7-11.7-29.4-21-46.2-27.5-11.6-5.3-21.3-9.9-29.3-14.1-8.1-4.4-15.5-10-22-16.5-7.1-7.3-12.6-16-16.2-25.6-4.3-11.9-6.3-24.4-6.1-37-1.2-23.8,8-46.9,25.3-63.2,17.2-15.9,40.1-24.4,63.5-23.7,10.1.4,20.1,1.7,30,3.8,6,1.5,12,2.4,18.1,2.7,5.2.3,10.2-1.7,13.8-5.5v67.4h.2l-.3-.2Z" />
              </g>
              <path fill="#e51e26" fillRule="evenodd" d="M2183.5,206.9c0,19.1-15.5,34.6-34.7,34.6s-34.6-15.5-34.6-34.7,15.5-34.6,34.6-34.6,34.7,15.5,34.7,34.7h0Z" />
            </svg>
            <span className="hidden border-l border-[var(--os-line)] pl-3 text-[15px] font-bold tracking-[-0.02em] sm:inline">Agentic Data OS</span>
          </Link>
        </div>
        <div className="flex shrink-0 items-center gap-2.5 text-[12px]">
          <CopilotButton className="os-btn inline-flex h-9 items-center gap-2 px-2.5 text-[12px] font-bold lg:px-3" />
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
          <span><b>Previewing as {preview.current}</b>: the screens and actions this persona has. The API answers with this persona's privileges too, so anything outside it is refused.</span>
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
