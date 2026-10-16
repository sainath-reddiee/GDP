import {
  Activity, Crown, BadgeCheck, BarChart3, BookOpen, Boxes, ClipboardCheck, Database, FileClock, FlaskConical, FolderGit2, GitCompareArrows, GitPullRequest,
  Inbox, LayoutDashboard, ListChecks, Network, Rocket, Settings, ShieldCheck, Siren, Sparkles, SquareTerminal, Store, Ticket, Wrench, BookCheck,
} from "lucide-react";
import type { NavCounts } from "@/app/bff/nav/route";

export type NavBadge = { value: number; tone: string; title: string };
export type NavLink = { href: string; label: string; icon: typeof Database; privilege?: string | string[]; exact?: boolean;
  badge?: (c: NavCounts) => NavBadge | null;
  /** a second line under the label, shown on a featured entry */
  hint?: string;
  /** sub-links shown under the item (expanded sidebar) or in a flyout (collapsed); the item is active on any of them */
  children?: NavLink[];
  /** sub-links start folded (they open on their own on one of their pages, and the chevron choice is remembered) */
  folded?: boolean;
  /** a workspace: its children are its pages, shown as a tab bar on those pages (components/shell/workspace-tabs) and as
   *  a flyout when the sidebar is collapsed, never as rows under it. The row opens the first page the caller may see. */
  tabs?: boolean;
  /** the pages draw their own tab bar (the stores: components/hub/store-nav), so the shell adds none */
  ownTabs?: boolean };

/** Incidents shipped with PR O2. */
const SHOW_INCIDENTS = true;

/** The six asset stores, one entry under Store in the sidebar and one tab of the Store hub (components/hub/store-nav). */
export const STORES: (NavLink & { id: string; short: string })[] = [
  {
    id: "sources", short: "Sources", href: "/dashboard/sources", label: "Source Store", icon: Database,
    badge: (c) => c.profiling ? { value: c.profiling, tone: "bg-amber-400 text-amber-950", title: `${c.profiling} profiling now` }
      : c.staged ? { value: c.staged, tone: "bg-white/10 text-white/70", title: `${c.staged} tables staged` } : null,
  },
  { id: "profiling", short: "Profiling", href: "/dashboard/profiling", label: "Profiling Store", icon: BarChart3 },
  { id: "modeling", short: "Modeling", href: "/dashboard/modeling", label: "Modeling Store", icon: Network },
  {
    id: "sttm", short: "Mapping (STTM)", href: "/dashboard/sttm", label: "STTM Store", icon: GitCompareArrows,
    badge: (c) => c.sttm_pending ? { value: c.sttm_pending, tone: "bg-amber-400 text-amber-950", title: `${c.sttm_pending} mappings waiting for review` } : null,
  },
  {
    id: "qa", short: "QA", href: "/dashboard/qa-store", label: "QA Store", icon: SquareTerminal,
    badge: (c) => c.qa_pending ? { value: c.qa_pending, tone: "bg-violet-500 text-white", title: `${c.qa_pending} packs awaiting approval` } : null,
  },
  {
    id: "quality", short: "Data Quality", href: "/dashboard/dq-store", label: "Data Quality Store", icon: ClipboardCheck,
    badge: (c) => c.dq_failing ? { value: c.dq_failing, tone: "bg-rose-500 text-white", title: `${c.dq_failing} checks failing` } : null,
  },
];

/** The store a path belongs to (a store page or anything under it), or null. */
export function storeFor(path: string) {
  return STORES.find((s) => path === s.href || path.startsWith(`${s.href}/`)) ?? null;
}

/** Store's own badge: the stores that need attention, summed, with each named in the title. */
const storeBadge = (c: NavCounts) => groupBadge(STORES, c);

/** A sidebar section. Primary sections (the onboard, store and build path) always show their links; the others can be
 * folded to their header, which then carries the summed badge of its links so nothing waiting is hidden. */
export type NavGroup = { label: string; links: NavLink[]; secondary?: boolean; defaultOpen?: boolean;
  /** a single call to action, drawn as a highlighted entry with no section heading */
  feature?: boolean };

// Nine workspaces in four sections, in the order people work: their own work and the estate; then build (onboard,
// the pipelines, the stores they fill, the knowledge behind them); then run (quality and operations); then govern.
// Related pages live inside a workspace as tabs, so the sidebar stays short. A section with no visible links is hidden.
const n = (v?: number) => v ?? 0;
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Work",
    links: [
      {
        href: "/work", label: "My Work", icon: Inbox, exact: true,
        badge: (c) => c.approvals ? { value: c.approvals, tone: "bg-human text-white", title: `${c.approvals} change requests waiting for you` } : null,
      },
      { href: "/dashboard", label: "Overview", icon: LayoutDashboard, exact: true },
    ],
  },
  {
    label: "Onboard", feature: true,
    links: [{ href: "/dashboard/onboarding", label: "Onboard", icon: Rocket, hint: "Sources, domains, applications" }],
  },
  {
    // the run engine first: the Silver and Gold pipelines and what they build, then the stores and the knowledge
    label: "Build",
    links: [
      {
        href: "/runs", label: "Pipelines", icon: ListChecks, tabs: true,
        badge: (c) => c.review ? { value: c.review, tone: "bg-human text-white", title: `${c.review} waiting on review` }
          : c.running ? { value: c.running, tone: "bg-white/10 text-white/70", title: `${c.running} running` } : null,
        children: [
          { href: "/runs", label: "Silver Pipeline", icon: ListChecks },
          { href: "/gold", label: "Gold Pipeline", icon: Crown },
          { href: "/lineage", label: "Lineage", icon: Network },
          { href: "/code", label: "Code", icon: FolderGit2, privilege: "CODE.VIEW" },
        ],
      },
      { href: "/dashboard/store", label: "Stores", icon: Store, children: STORES, badge: storeBadge, tabs: true, ownTabs: true },
      {
        href: "/domains", label: "Knowledge", icon: BookOpen, tabs: true,
        children: [
          { href: "/domains", label: "Domains", icon: Boxes },
          { href: "/knowledge", label: "Knowledge", icon: BookOpen },
          { href: "/skills", label: "Skills", icon: Sparkles },
        ],
      },
    ],
  },
  {
    label: "Run",
    links: [
      {
        href: "/dashboard/quality", label: "Quality", icon: BadgeCheck, tabs: true,
        badge: (c) => n(c.dq_failing) ? { value: n(c.dq_failing), tone: "bg-rose-500 text-white", title: `${n(c.dq_failing)} checks failing` } : null,
        children: [
          { href: "/dashboard/quality", label: "Quality center", icon: BadgeCheck },
          { href: "/qa", label: "QA testing", icon: FlaskConical, privilege: "QA.EDIT" },
        ],
      },
      {
        href: "/ops", label: "Operations", icon: Activity, tabs: true,
        badge: (c) => {
          const v = c.incidents + n(c.jira_open) + n(c.cases_mine);
          return v ? { value: v, tone: c.incidents ? "bg-rose-500 text-white" : "bg-sky-500 text-white",
                       title: `${c.incidents} open incidents; ${n(c.jira_open) + n(c.cases_mine)} tickets assigned to you` } : null;
        },
        children: [
          { href: "/ops", label: "Monitoring", icon: Activity, privilege: "OPS.VIEW" },
          ...(SHOW_INCIDENTS ? [{ href: "/incidents", label: "Incidents", icon: Siren, privilege: "OPS.VIEW" }] : []),
          { href: "/ops/resolutions", label: "Resolutions", icon: Wrench, privilege: "OPS.VIEW" },
          { href: "/ops/fixes", label: "Known fixes", icon: BookCheck, privilege: "OPS.VIEW" },
          { href: "/tickets", label: "Tickets", icon: Ticket, privilege: ["JIRA.READ", "CASE.WORK"] },
        ],
      },
    ],
  },
  {
    label: "Govern",
    links: [
      {
        href: "/approvals", label: "Governance", icon: ShieldCheck, tabs: true,
        badge: (c) => c.approvals ? { value: c.approvals, tone: "bg-human text-white", title: `${c.approvals} waiting for you` } : null,
        children: [
          { href: "/approvals", label: "Approvals", icon: ShieldCheck },
          { href: "/dashboard/source-control", label: "Source control", icon: GitPullRequest },
          { href: "/audit", label: "Audit", icon: FileClock, privilege: "AUDIT.VIEW" },
          { href: "/admin", label: "Admin", icon: Settings, privilege: "ADMIN.VIEW" },
        ],
      },
    ],
  },
];

/** The workspace a path belongs to (any of its pages, or a page under one), or null. */
export function workspaceFor(path: string): NavLink | null {
  for (const g of NAV_GROUPS) {
    for (const l of g.links) {
      if (!l.tabs) continue;
      if (l.children?.some((c) => path === c.href || path.startsWith(`${c.href}/`))) return l;
    }
  }
  return null;
}

/** A folded section's badge: its links' badges that need attention (not the grey "in progress" ones), summed. */
export function groupBadge(links: NavLink[], c: NavCounts): NavBadge | null {
  const parts = links.map((l) => ({ l, b: l.badge?.(c) })).filter((x) => x.b && !x.b.tone.includes("white/10"));
  if (!parts.length) return null;
  return { value: parts.reduce((n, x) => n + (x.b?.value ?? 0), 0),
           tone: parts.length === 1 ? parts[0].b!.tone : "bg-amber-400 text-amber-950",
           title: parts.map((x) => `${x.l.label}: ${x.b!.title}`).join("; ") };
}

/** Every link the caller may open: a list of privileges means any of them, no list means governance is off. */
export function allowedBy(privileges?: string[] | null) {
  return (p?: string | string[]) => !p || !privileges || privileges.includes("*")
    || (Array.isArray(p) ? p.some((x) => privileges.includes(x)) : privileges.includes(p));
}
