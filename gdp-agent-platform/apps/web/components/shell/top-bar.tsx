"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { Boxes, Check, ChevronDown, Loader2, LogOut, Plus, Search, Sparkles, Ticket } from "lucide-react";
import { cn } from "@/lib/utils";
import { logout } from "@/app/login/actions";
import { setDomainScope } from "@/app/scope-actions";
import { RoleSelector } from "@/components/role-selector";
import { ThemeSegmented, ThemeToggle } from "@/components/theme/theme-toggle";
import { actableBy, QUICK_ACTION_GROUPS, QUICK_ACTIONS } from "./quick-actions";
import { useDismiss } from "./use-dismiss";

export type DomainOption = { domain_id: string; domain_name: string };

export function initials(user: string | null) {
  const name = (user ?? "?").replace(/[^A-Za-z ]/g, " ").trim();
  return (name.split(/\s+/).map((p) => p[0]).join("") || "?").slice(0, 2).toUpperCase();
}

/** Opens the command palette from anywhere (components/shell/command-palette listens). */
export const openPalette = () => window.dispatchEvent(new Event("aip:palette"));

export function DomainSwitcher({ domains, scope }: { domains: DomainOption[]; scope: string }) {
  const { open, setOpen, ref } = useDismiss();
  const [pending, start] = useTransition();
  const [filter, setFilter] = useState("");
  const router = useRouter();
  const pick = (name: string) => {
    setOpen(false);
    start(async () => { await setDomainScope(name); router.refresh(); });
  };
  const shown = domains.filter((d) => d.domain_name.toLowerCase().includes(filter.toLowerCase()));
  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen(!open)} aria-haspopup="listbox" aria-expanded={open} data-testid="domain-switcher"
              className="flex h-9 items-center gap-2 rounded-xl border border-border/80 bg-card/70 pl-2.5 pr-2 text-sm shadow-xs transition hover:border-primary/40">
        <span className="grid h-5 w-5 place-items-center rounded-md bg-primary text-white">
          {pending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Boxes className="h-3 w-3" />}
        </span>
        <span className="max-w-[160px] truncate font-medium">{scope || "All domains"}</span>
        <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
      </button>
      {open && (
        <div className="popover absolute left-0 top-11 z-50 w-72 animate-pop-in p-1.5 shadow-pop" role="listbox" aria-label="Domain scope">
          <p className="px-2.5 pb-1 pt-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Domain scope</p>
          {domains.length > 6 && (
            <input autoFocus value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter domains"
                   className="mb-1 h-8 w-full rounded-lg border border-input bg-card px-2.5 text-xs outline-none focus:border-primary/60" />
          )}
          <div className="max-h-72 overflow-y-auto">
            {[{ domain_id: "", domain_name: "" }, ...shown].map((d) => {
              const active = (scope || "").toUpperCase() === d.domain_name.toUpperCase();
              return (
                <button key={d.domain_id || "all"} type="button" role="option" aria-selected={active} onClick={() => pick(d.domain_name)}
                        className={cn("flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition hover:bg-muted",
                          active && "bg-accent text-accent-foreground")}>
                  <span className="flex-1 truncate">{d.domain_name || "All domains"}</span>
                  {active && <Check className="h-3.5 w-3.5" />}
                </button>
              );
            })}
            {!domains.length && <p className="px-2.5 py-2 text-xs text-muted-foreground">No domains registered yet.</p>}
          </div>
          <p className="border-t border-border/70 px-2.5 pb-1 pt-2 text-[11px] text-muted-foreground">Hubs, stores and the dashboard filter to this domain.</p>
        </div>
      )}
    </div>
  );
}

export function NewMenu({ privileges }: { privileges?: string[] | null }) {
  const { open, setOpen, ref } = useDismiss();
  const allowed = actableBy(privileges);
  const groups = QUICK_ACTION_GROUPS.map((g) => ({ g, items: QUICK_ACTIONS.filter((a) => a.group === g && allowed(a.privilege)) }))
    .filter((x) => x.items.length);
  if (!groups.length) return null;
  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen(!open)} aria-haspopup="menu" aria-expanded={open} data-testid="new-menu"
              className="flex h-9 items-center gap-1.5 rounded-xl bg-primary px-3 text-sm font-medium text-primary-foreground shadow-[0_1px_2px_rgba(17,24,54,0.15),inset_0_1px_0_rgba(255,255,255,0.16)] transition hover:bg-primary/90">
        <Plus className="h-4 w-4" /> <span className="hidden sm:inline">New</span>
      </button>
      {open && (
        <div role="menu" className="popover absolute right-0 top-11 z-50 max-h-[calc(100vh-5rem)] w-[340px] animate-pop-in overflow-y-auto p-1.5 shadow-pop">
          {groups.map(({ g, items }, n) => (
            <div key={g} role="group" aria-label={g} className={cn(n > 0 && "mt-1 border-t border-border/70 pt-1")}>
              <p className="px-2.5 pb-0.5 pt-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">{g}</p>
              {items.map(({ label, hint, href, icon: Icon }) => (
                <Link key={href} href={href} role="menuitem" onClick={() => setOpen(false)}
                      className="flex items-start gap-3 rounded-lg px-2.5 py-2 transition hover:bg-muted">
                  <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary"><Icon className="h-3.5 w-3.5" /></span>
                  <span className="min-w-0">
                    <span className="block text-sm font-medium">{label}</span>
                    <span className="block text-xs text-muted-foreground">{hint}</span>
                  </span>
                </Link>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function AccountMenu({ user, role, canLogout, appRoles }: {
  user: string | null; role: string | null; canLogout: boolean; appRoles?: string[] | null;
}) {
  const { open, setOpen, ref } = useDismiss();
  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen(!open)} aria-haspopup="menu" aria-expanded={open} aria-label="Account" data-testid="account-menu"
              className="flex h-9 items-center gap-2 rounded-xl pl-1 pr-2 transition hover:bg-muted">
        <span className="grid h-7 w-7 place-items-center rounded-full bg-primary text-[11px] font-bold text-white ring-2 ring-background">
          {initials(user)}
        </span>
        <span className="hidden max-w-[140px] truncate text-left text-xs leading-tight lg:block">
          <span className="block truncate font-medium">{user ?? "Not signed in"}</span>
          <span className="block truncate text-muted-foreground">{role ?? "default role"}</span>
        </span>
        <ChevronDown className="hidden h-3.5 w-3.5 text-muted-foreground lg:block" />
      </button>
      {open && (
        <div role="menu" className="popover absolute right-0 top-11 z-50 w-80 animate-pop-in space-y-3 p-3 shadow-pop">
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-primary text-sm font-bold text-white">
              {initials(user)}
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold">{user ?? "Not signed in"}</div>
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <span className="h-1.5 w-1.5 rounded-full bg-success" /> Connected to Snowflake
              </div>
              {appRoles && (
                <div className="truncate text-xs text-primary" title={appRoles.join(", ") || "No app role yet"}>
                  {appRoles.length ? appRoles.map((r) => r.toLowerCase().replace(/_/g, " ")).join(", ") : "no app role"}
                </div>
              )}
            </div>
          </div>
          <RoleSelector currentRole={role} />
          <div>
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Appearance</p>
            <ThemeSegmented />
          </div>
          {canLogout && (
            <form action={logout} className="border-t border-border/70 pt-2">
              <button className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-muted-foreground transition hover:bg-muted hover:text-foreground">
                <LogOut className="h-4 w-4" /> Sign out
              </button>
            </form>
          )}
        </div>
      )}
    </div>
  );
}

/** Tickets, one click from any page: the caller's open Jira issues and cases (from the sidebar counters, cached 30 s). */
function TicketsShortcut({ privileges }: { privileges?: string[] | null }) {
  const allowed = !privileges || privileges.includes("*") || privileges.includes("JIRA.READ") || privileges.includes("CASE.WORK");
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => {
    if (!allowed) return;
    let live = true;
    const load = () => fetch("/bff/nav").then((r) => (r.ok ? r.json() : null))
      .then((c: { jira_open?: number; cases_mine?: number } | null) => { if (live && c) setOpen((c.jira_open ?? 0) + (c.cases_mine ?? 0)); }, () => undefined);
    void load();
    const t = setInterval(load, 60_000);
    return () => { live = false; clearInterval(t); };
  }, [allowed]);
  if (!allowed) return null;
  const label = open ? `Tickets: ${open} open ticket${open === 1 ? "" : "s"} assigned to you` : "Tickets";
  return (
    <Link href="/os/run/tickets" aria-label={label} title={label} data-testid="tickets-shortcut"
          className="relative grid h-9 w-9 place-items-center rounded-xl border border-border/80 bg-card/70 text-muted-foreground shadow-xs transition hover:border-primary/40 hover:text-foreground">
      <Ticket className="h-4 w-4" />
      {!!open && (
        <span className="absolute -right-1 -top-1 grid h-4 min-w-4 place-items-center rounded-full bg-sky-500 px-1 text-[10px] font-semibold tabular-nums text-white">
          {open > 99 ? "99+" : open}
        </span>
      )}
    </Link>
  );
}

/** Copilot from any page: the full chat workspace (sessions, attachments, Jira, drafted changes). Ctrl+J, or the round
 *  button, still opens the quick drawer for a question about the page on screen. Shown to people with AI.USE. */
function CopilotShortcut({ privileges }: { privileges?: string[] | null }) {
  const allowed = !privileges || privileges.includes("*") || privileges.includes("AI.USE");
  if (!allowed) return null;
  return (
    <Link href="/os/work/copilot" title="Copilot chat (Ctrl+J for a quick question about this page)" data-testid="copilot-shortcut"
          className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-border/80 bg-card/70 px-3 text-sm font-medium text-muted-foreground shadow-xs transition hover:border-agent/40 hover:text-agent">
      <Sparkles className="h-4 w-4" /><span className="hidden lg:inline">Copilot</span>
    </Link>
  );
}

/** Sticky frosted bar above every signed-in page: domain scope, search (Ctrl/Cmd+K), quick actions, theme, account. */
export function TopBar({ user, role, canLogout, appRoles, privileges, domains, scope }: {
  user: string | null; role: string | null; canLogout: boolean; appRoles?: string[] | null; privileges?: string[] | null;
  domains: DomainOption[]; scope: string;
}) {
  const [mac, setMac] = useState(false);
  useEffect(() => { setMac(/Mac|iPhone|iPad/.test(navigator.platform)); }, []);
  return (
    <header className="sticky top-0 z-40 border-b border-border/60 bg-background/95 backdrop-blur-xl backdrop-saturate-150">
      <div className="mx-auto flex h-14 w-full max-w-[1600px] items-center gap-2 px-6 lg:px-10">
        <DomainSwitcher domains={domains} scope={scope} />
        <button type="button" onClick={openPalette} data-testid="open-palette"
                className="group ml-1 flex h-9 min-w-0 flex-1 items-center gap-2 rounded-xl border border-border/80 bg-card/70 px-3 text-sm text-muted-foreground shadow-xs transition hover:border-primary/40 sm:max-w-md">
          <Search className="h-4 w-4 shrink-0" />
          <span className="truncate">Search runs, tables, mappings, packs...</span>
          <span className="ml-auto hidden items-center gap-0.5 sm:flex"><kbd className="kbd">{mac ? "⌘" : "Ctrl"}</kbd><kbd className="kbd">K</kbd></span>
        </button>
        <div className="ml-auto flex items-center gap-1.5">
          <CopilotShortcut privileges={privileges} />
          <TicketsShortcut privileges={privileges} />
          <NewMenu privileges={privileges} />
          <ThemeToggle />
          <AccountMenu user={user} role={role} canLogout={canLogout} appRoles={appRoles} />
        </div>
      </div>
    </header>
  );
}
