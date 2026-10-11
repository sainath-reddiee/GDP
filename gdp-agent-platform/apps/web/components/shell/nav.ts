import {
  Activity, BadgeCheck, BarChart3, BookOpen, Boxes, ClipboardCheck, Database, FileClock, FlaskConical, FolderGit2, GitCompareArrows, GitPullRequest,
  LayoutDashboard, ListChecks, Network, Rocket, Settings, ShieldCheck, Siren, Sparkles, SquareTerminal,
} from "lucide-react";
import type { NavCounts } from "@/app/bff/nav/route";

export type NavLink = { href: string; label: string; icon: typeof Database; privilege?: string | string[]; exact?: boolean;
  badge?: (c: NavCounts) => { value: number; tone: string; title: string } | null };

/** Incidents shipped with PR O2. */
const SHOW_INCIDENTS = true;

// Overview, then the six asset stores in one place, then the work around them; a group with no visible links is hidden
export const NAV_GROUPS: { label: string; links: NavLink[] }[] = [
  {
    label: "Overview",
    links: [{ href: "/dashboard", label: "Command Center", icon: LayoutDashboard, exact: true }],
  },
  {
    label: "Asset stores",
    links: [
      {
        href: "/dashboard/sources", label: "Source Store", icon: Database,
        badge: (c) => c.profiling ? { value: c.profiling, tone: "bg-amber-400 text-amber-950", title: `${c.profiling} profiling now` }
          : c.staged ? { value: c.staged, tone: "bg-white/10 text-white/70", title: `${c.staged} tables staged` } : null,
      },
      { href: "/dashboard/profiling", label: "Profiling Store", icon: BarChart3 },
      { href: "/dashboard/modeling", label: "Modeling Store", icon: Network },
      {
        href: "/dashboard/sttm", label: "STTM Store", icon: GitCompareArrows,
        badge: (c) => c.sttm_pending ? { value: c.sttm_pending, tone: "bg-amber-400 text-amber-950", title: `${c.sttm_pending} mappings waiting for review` } : null,
      },
      {
        href: "/dashboard/qa-store", label: "QA Store", icon: SquareTerminal,
        badge: (c) => c.qa_pending ? { value: c.qa_pending, tone: "bg-violet-500 text-white", title: `${c.qa_pending} packs awaiting approval` } : null,
      },
      {
        href: "/dashboard/dq-store", label: "Data Quality Store", icon: ClipboardCheck,
        badge: (c) => c.dq_failing ? { value: c.dq_failing, tone: "bg-rose-500 text-white", title: `${c.dq_failing} checks failing` } : null,
      },
    ],
  },
  {
    label: "Build",
    links: [
      {
        href: "/runs", label: "Runs", icon: ListChecks,
        badge: (c) => c.review ? { value: c.review, tone: "bg-rose-500 text-white", title: `${c.review} waiting on review` }
          : c.running ? { value: c.running, tone: "bg-white/10 text-white/70", title: `${c.running} running` } : null,
      },
      { href: "/dashboard/onboarding", label: "Onboarding", icon: Rocket },
      { href: "/domains", label: "Domains", icon: Boxes },
      { href: "/code", label: "Code", icon: FolderGit2, privilege: "CODE.VIEW" },
    ],
  },
  {
    label: "Monitor",
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
    label: "Knowledge",
    links: [
      { href: "/knowledge", label: "Knowledge", icon: BookOpen },
      { href: "/skills", label: "Skills", icon: Sparkles },
    ],
  },
  {
    label: "Govern",
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

/** Every link the caller may open: a list of privileges means any of them, no list means governance is off. */
export function allowedBy(privileges?: string[] | null) {
  return (p?: string | string[]) => !p || !privileges || privileges.includes("*")
    || (Array.isArray(p) ? p.some((x) => privileges.includes(x)) : privileges.includes(p));
}
