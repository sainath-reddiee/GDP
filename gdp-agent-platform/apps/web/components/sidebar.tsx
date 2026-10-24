"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, ChevronsLeft, ChevronsRight } from "lucide-react";
import { LogoMark } from "@/components/brand/logo-mark";
import { PRODUCT, PRODUCT_SHORT } from "@/lib/brand";
import { cn } from "@/lib/utils";
import type { NavCounts } from "@/app/bff/nav/route";
import { ReportProblemButton } from "@/components/report-problem";
import { allowedBy, groupBadge, NAV_GROUPS, type NavGroup, type NavLink } from "@/components/shell/nav";

const COLLAPSE_KEY = "aip.sidebar.collapsed";
const OPEN_KEY = "aip.sidebar.open";
const GROUP_KEY = "aip.sidebar.group";

function isActive(link: NavLink, path: string): boolean {
  if (link.exact) return path === link.href;
  return path === link.href || path.startsWith(`${link.href}/`) || !!link.children?.some((c) => isActive(c, path));
}

function Badge({ b, collapsed }: { b: { value: number; tone: string; title: string } | null; collapsed: boolean }) {
  if (!b) return null;
  return (
    <span title={b.title}
          className={cn("grid min-w-5 place-items-center rounded-full px-1.5 text-[10px] font-semibold tabular-nums",
            b.tone, collapsed && "absolute right-1 top-0.5 h-4 min-w-4 px-1")}>
      {b.value}
    </span>
  );
}

const ROW = "group relative mb-0.5 flex items-center gap-3 rounded-xl py-[7px] text-[13px] transition";
const ON = "bg-white/[0.08] font-medium text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]";
const OFF = "text-sidebar-foreground/70 hover:bg-white/[0.05] hover:text-white";

function Item({ link, active, counts, collapsed, nested }: {
  link: NavLink; active: boolean; counts: NavCounts | null; collapsed: boolean; nested?: boolean;
}) {
  const { href, label, icon: Icon, badge } = link;
  const b = counts && badge ? badge(counts) : null;
  const descId = b ? `nav-badge-${href.replace(/\W+/g, "-")}` : undefined;
  // named by its label alone; what the badge counts is its description (and its title on hover)
  return (
    <Link href={href} title={collapsed ? label : undefined} aria-label={label} aria-describedby={descId} aria-current={active ? "page" : undefined}
          className={cn(ROW, collapsed ? "justify-center px-0" : nested ? "py-[6px] pl-9 pr-2.5 text-[12.5px]" : "px-2.5", active ? ON : OFF)}>
      {b && <span id={descId} className="sr-only">{b.title}</span>}
      {active && !nested && <span className="absolute -left-3 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-primary" />}
      <Icon className={cn("shrink-0", nested ? "h-[15px] w-[15px]" : "h-[17px] w-[17px]", active ? "text-blue-300" : "text-white/40 group-hover:text-white/80")} />
      {!collapsed && <span className="flex-1 truncate">{label}</span>}
      <Badge b={b} collapsed={collapsed} />
    </Link>
  );
}

/** An item with sub-links: its label opens its own page, the chevron shows the sub-links (opened by itself while you are
 * on one of them); collapsed, hovering or focusing the icon opens a flyout with the sub-links. */
function ParentItem({ link, kids, path, counts, collapsed }: {
  link: NavLink; kids: NavLink[]; path: string; counts: NavCounts | null; collapsed: boolean;
}) {
  const active = path === link.href || kids.some((k) => isActive(k, path));
  const inside = kids.some((k) => isActive(k, path));
  const listId = `nav-sub-${link.href.replace(/\W+/g, "-")}`;
  const [open, setOpen] = useState(!link.folded || inside);
  const [fly, setFly] = useState<{ top: number; left: number } | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const flyRef = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => {
    try { const v = localStorage.getItem(`${OPEN_KEY}.${link.href}`); if (v !== null) setOpen(v === "1"); } catch { /* storage unavailable */ }
  }, [link.href]);
  useEffect(() => { if (inside) setOpen(true); }, [inside, path]);
  useEffect(() => setFly(null), [path]);
  useEffect(() => {
    if (!fly) return;
    const click = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!root.current?.contains(t) && !flyRef.current?.contains(t)) setFly(null);
    };
    document.addEventListener("mousedown", click);
    return () => document.removeEventListener("mousedown", click);
  }, [fly]);
  const flip = () => setOpen((v) => {
    try { localStorage.setItem(`${OPEN_KEY}.${link.href}`, v ? "0" : "1"); } catch { /* storage unavailable */ }
    return !v;
  });
  const show = () => {
    clearTimeout(closeTimer.current);
    const r = root.current?.getBoundingClientRect();
    if (r) setFly({ top: r.top, left: r.right + 8 });
  };
  const hide = () => { closeTimer.current = setTimeout(() => setFly(null), 150); };
  const keys = (e: KeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(flyRef.current?.querySelectorAll<HTMLAnchorElement>("a") ?? []);
    const at = items.indexOf(document.activeElement as HTMLAnchorElement);
    if (e.key === "Escape") { setFly(null); root.current?.querySelector<HTMLAnchorElement>("a")?.focus(); }
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!fly) show();
      const next = items[(at + (e.key === "ArrowDown" ? 1 : items.length - 1)) % Math.max(items.length, 1)] ?? items[0];
      setTimeout(() => (next ?? flyRef.current?.querySelector<HTMLAnchorElement>("a"))?.focus(), 0);
    }
  };
  const Icon = link.icon;
  const b = counts && link.badge ? link.badge(counts) : null;

  if (collapsed) {
    return (
      <div ref={root} className="relative" onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide} onKeyDown={keys}>
        <Link href={link.href} aria-current={path === link.href ? "page" : undefined} aria-haspopup="menu"
              aria-expanded={!!fly} title={link.label} aria-label={link.label} className={cn(ROW, "justify-center px-0", active ? ON : OFF)}>
          {active && <span className="absolute -left-3 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-primary" />}
          <Icon className={cn("h-[17px] w-[17px] shrink-0", active ? "text-blue-300" : "text-white/40 group-hover:text-white/80")} />
          <Badge b={b} collapsed />
        </Link>
        {/* in a portal: the sticky sidebar is its own stacking context, so a flyout inside it would sit under the page */}
        {fly && createPortal(
          <div ref={flyRef} data-flyout role="menu" aria-label={link.label} style={{ top: fly.top, left: fly.left }}
               onMouseEnter={() => clearTimeout(closeTimer.current)} onMouseLeave={hide}
               className="popover fixed z-[60] w-60 animate-pop-in p-1.5 shadow-pop">
            <Link href={link.href} role="menuitem" className="mb-1 block rounded-lg px-2.5 py-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground hover:bg-muted">
              {link.label}
            </Link>
            {kids.map((k) => {
              const on = isActive(k, path);
              const kb = counts && k.badge ? k.badge(counts) : null;
              const KIcon = k.icon;
              return (
                <Link key={k.href} href={k.href} role="menuitem" aria-current={on ? "page" : undefined}
                      className={cn("flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm", on ? "bg-primary/10 font-medium text-primary" : "hover:bg-muted")}>
                  <KIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <span className="flex-1 truncate">{k.label}</span>
                  {kb && <span title={kb.title} className={cn("grid min-w-5 place-items-center rounded-full px-1.5 text-[10px] font-semibold", kb.tone)}>{kb.value}</span>}
                </Link>
              );
            })}
          </div>,
          document.body,
        )}
      </div>
    );
  }
  return (
    <div>
      <div className={cn(ROW, "pl-0 pr-1", path === link.href || (inside && !open) ? ON : inside ? "font-medium text-white" : OFF)}>
        {active && <span className="absolute -left-3 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-primary" />}
        {/* named by its label alone: the folded count badge inside it is a hint (its title says what waits), not part of the name */}
        <Link href={link.href} aria-current={path === link.href ? "page" : undefined} aria-label={link.label}
              aria-describedby={b && !open ? `${listId}-waiting` : undefined} className="flex flex-1 items-center gap-3 self-stretch pl-2.5">
          {b && !open && <span id={`${listId}-waiting`} className="sr-only">{b.title}</span>}
          <Icon className={cn("h-[17px] w-[17px] shrink-0", active ? "text-blue-300" : "text-white/40 group-hover:text-white/80")} />
          <span className="flex-1 truncate">{link.label}</span>
          {!open && <Badge b={b} collapsed={false} />}
        </Link>
        <button type="button" onClick={flip} aria-expanded={open} aria-controls={listId}
                aria-label={open ? `Hide ${link.label} sections` : `Show ${link.label} sections`}
                className="grid h-6 w-6 place-items-center rounded-lg text-white/40 hover:bg-white/10 hover:text-white">
          <ChevronDown className={cn("h-3.5 w-3.5 transition", open ? "" : "-rotate-90")} />
        </button>
      </div>
      {open && (
        <div id={listId} className="relative mb-1">
          <span className="absolute bottom-1 left-[18px] top-0 w-px bg-white/10" aria-hidden />
          {kids.map((k) => <Item key={k.href} link={k} active={isActive(k, path)} counts={counts} collapsed={false} nested />)}
        </div>
      )}
    </div>
  );
}

/** The onboarding entry: tinted and outlined so the start of the lifecycle stands apart from the sections below it. */
function Feature({ link, active, collapsed }: { link: NavLink; active: boolean; collapsed: boolean }) {
  const { href, label, icon: Icon, hint } = link;
  return (
    <Link href={href} title={collapsed ? label : undefined} aria-label={collapsed ? label : undefined} aria-current={active ? "page" : undefined}
          className={cn("group relative flex items-center gap-3 rounded-xl border py-2 text-[13px] font-medium transition",
            collapsed ? "justify-center px-0" : "px-2.5",
            active ? "border-blue-400/50 bg-white/[0.08] text-white"
              : "border-white/10 bg-white/[0.04] text-white/90 hover:border-blue-400/40 hover:bg-white/[0.07]")}>
      {active && <span className="absolute -left-3 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-primary" />}
      <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-primary text-white">
        <Icon className="h-3.5 w-3.5" />
      </span>
      {!collapsed && (
        <span className="min-w-0 flex-1">
          <span className="block truncate">{label}</span>
          {hint && <span className="block truncate text-[10.5px] font-normal text-white/45">{hint}</span>}
        </span>
      )}
    </Link>
  );
}

function Links({ links, path, counts, collapsed, allowed }: {
  links: NavLink[]; path: string; counts: NavCounts | null; collapsed: boolean; allowed: (p?: string | string[]) => boolean;
}) {
  return (
    <>
      {links.map((link) => {
        if (!link.children?.length) return <Item key={link.href} link={link} active={isActive(link, path)} counts={counts} collapsed={collapsed} />;
        const kids = link.children.filter((c) => allowed(c.privilege));
        if (!kids.length) return null;
        // a workspace is one row (its pages are tabs on the page). It opens its own page; only when that page is one of
        // its tabs the caller may not open does it open the first tab they may (a hub page like the Store hub stays)
        const ownPage = link.children.some((c) => c.href === link.href);
        const href = !ownPage || kids.some((k) => k.href === link.href) ? link.href : kids[0].href;
        const ws = link.tabs ? { ...link, href, children: kids } : link;
        return link.tabs && !collapsed
          ? <Item key={link.href} link={ws} active={isActive(ws, path)} counts={counts} collapsed={false} />
          : <ParentItem key={link.href} link={ws} kids={kids} path={path} counts={counts} collapsed={collapsed} />;
      })}
    </>
  );
}

/** One section. Primary sections always list their links; a secondary one folds to its header (remembered per browser),
 * opens by itself while you are on one of its pages, and folded shows its links' summed badge. Collapsed, every
 * section shows its icons. */
function Group({ group, path, counts, collapsed, allowed, divider }: {
  group: NavGroup; path: string; counts: NavCounts | null; collapsed: boolean;
  allowed: (p?: string | string[]) => boolean; divider: boolean;
}) {
  const inside = group.links.some((l) => isActive(l, path));
  const [open, setOpen] = useState(!group.secondary || !!group.defaultOpen);
  useEffect(() => {
    if (!group.secondary) return;
    try { const v = localStorage.getItem(`${GROUP_KEY}.${group.label}`); if (v !== null) setOpen(v === "1"); } catch { /* storage unavailable */ }
  }, [group.label, group.secondary]);
  useEffect(() => { if (inside) setOpen(true); }, [inside, path]);
  const flip = () => setOpen((v) => {
    try { localStorage.setItem(`${GROUP_KEY}.${group.label}`, v ? "0" : "1"); } catch { /* storage unavailable */ }
    return !v;
  });
  const listId = `nav-group-${group.label.replace(/\W+/g, "-").toLowerCase()}`;
  const folded = !!group.secondary && !open && !collapsed;
  if (group.feature) {
    return (
      <div className="mb-4">
        {group.links.map((link) => <Feature key={link.href} link={link} active={isActive(link, path)} collapsed={collapsed} />)}
      </div>
    );
  }
  return (
    <div className="mb-4">
      {divider && !collapsed && <div className="mx-2.5 mb-4 h-px bg-white/[0.07]" aria-hidden />}
      {collapsed ? <div className="mx-3 mb-2 h-px bg-white/10" />
        : group.secondary ? (
          <button type="button" onClick={flip} aria-expanded={open} aria-controls={listId}
                  className="group/h mb-1.5 flex w-full items-center gap-1.5 rounded-md px-2.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-white/30 hover:text-white/60">
            <span className="flex-1 text-left">{group.label}</span>
            {folded && <Badge b={counts ? groupBadge(group.links, counts) : null} collapsed={false} />}
            <ChevronDown className={cn("h-3 w-3 opacity-0 transition group-hover/h:opacity-100 group-focus-visible/h:opacity-100", !open && "-rotate-90 opacity-100")} />
          </button>
        ) : <p className="mb-1.5 px-2.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-white/40">{group.label}</p>}
      {!folded && (
        <div id={listId}>
          <Links links={group.links} path={path} counts={counts} collapsed={collapsed} allowed={allowed} />
        </div>
      )}
    </div>
  );
}

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
      <Link href="/os/work/inbox" title={PRODUCT} className={cn("group/brand flex items-center gap-3 py-5", collapsed ? "justify-center" : "px-2")}>
        <LogoMark size="sm" className="transition-transform duration-300 group-hover/brand:scale-105" />
        {!collapsed && (
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold tracking-tight text-white">{PRODUCT_SHORT}</div>
            <div className="text-[10px] font-medium uppercase leading-snug tracking-[0.08em] text-white/50">Agentic Data Operating System</div>
          </div>
        )}
      </Link>

      {NAV_GROUPS.map((group) => ({ ...group, links: group.links.filter((l) => allowed(l.privilege)) }))
        .filter((group) => group.links.length > 0)
        .map((group, i, all) => (
          <Group key={group.label} group={group} path={path} counts={counts} collapsed={collapsed} allowed={allowed}
                 divider={!!group.secondary && !all[i - 1]?.secondary} />
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
