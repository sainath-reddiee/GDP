import {
  Activity, BadgeCheck, BarChart3, BookOpen, Boxes, ClipboardCheck, Database, FileClock, FlaskConical, FolderGit2, GitCompareArrows, GitPullRequest,
  LayoutDashboard, ListChecks, Network, Rocket, Settings, ShieldCheck, Siren, Sparkles, SquareTerminal, Store,
} from "lucide-react";
import type { NavCounts } from "@/app/bff/nav/route";

export type NavBadge = { value: number; tone: string; title: string };
export type NavLink = { href: string; label: string; icon: typeof Database; privilege?: string | string[]; exact?: boolean;
  badge?: (c: NavCounts) => NavBadge | null;
  /** sub-links shown under the item (expanded sidebar) or in a flyout (collapsed); the item is active on any of them */
  children?: NavLink[] };

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
export type NavGroup = { label: string; links: NavLink[]; secondary?: boolean; defaultOpen?: boolean };

// The lifecycle first: onboard what comes in, the six asset stores it fills, then the runs that build on them. Monitor,
// Knowledge and Govern follow as foldable sections. A group with no visible links is hidden.
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Overview",
    links: [{ href: "/dashboard", label: "Command Center", icon: LayoutDashboard, exact: true }],
  },
  {
    label: "Onboard",
    links: [
      { href: "/dashboard/onboarding", label: "Onboarding", icon: Rocket },
      { href: "/domains", label: "Domains", icon: Boxes },
    ],
  },
  {
    label: "Asset stores",
    links: [{ href: "/dashboard/store", label: "Store", icon: Store, children: STORES, badge: storeBadge }],
  },
  {
    label: "Build",
    links: [
      {
        href: "/runs", label: "Runs", icon: ListChecks,
        badge: (c) => c.review ? { value: c.review, tone: "bg-rose-500 text-white", title: `${c.review} waiting on review` }
          : c.running ? { value: c.running, tone: "bg-white/10 text-white/70", title: `${c.running} running` } : null,
      },
      { href: "/code", label: "Code", icon: FolderGit2, privilege: "CODE.VIEW" },
    ],
  },
  {
    label: "Monitor", secondary: true, defaultOpen: true,
    links: [
      { href: "/dashboard/quality", label: "Quality center", icon: BadgeCheck },
      {
        href: "/qa", label: "QA workspace", icon: FlaskConical, privilege: ["QA.EDIT", "JIRA.READ", "CASE.WORK"],
        badge: (c) => c.cases ? { value: c.cases, tone: "bg-sky-500 text-white", title: `${c.cases} open cases` } : null,
      },
      { href: "/ops", label: "Pipelines", icon: Activity, privilege: "OPS.VIEW" },
      ...(SHOW_INCIDENTS ? [{
        href: "/incidents", label: "Incidents", icon: Siren, privilege: "OPS.VIEW",
        badge: (c: NavCounts) => c.incidents ? { value: c.incidents, tone: "bg-rose-500 text-white", title: `${c.incidents} open or acknowledged` } : null,
      }] : []),
    ],
  },
  {
    label: "Knowledge", secondary: true,
    links: [
      { href: "/knowledge", label: "Knowledge", icon: BookOpen },
      { href: "/skills", label: "Skills", icon: Sparkles },
    ],
  },
  {
    label: "Govern", secondary: true,
    links: [
      {
        href: "/approvals", label: "Approvals", icon: ShieldCheck,
        badge: (c) => c.approvals ? { value: c.approvals, tone: "bg-violet-500 text-white", title: `${c.approvals} waiting for you` } : null,
      },
      { href: "/dashboard/source-control", label: "Source control", icon: GitPullRequest },
      { href: "/audit", label: "Audit", icon: FileClock, privilege: "AUDIT.VIEW" },
      { href: "/admin", label: "Admin", icon: Settings, privilege: "ADMIN.VIEW" },
    ],
  },
];

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
