import {
  Boxes, CloudCog, FileUp, PackagePlus, Share2, ShieldCheck, Snowflake, TableProperties, type LucideIcon,
} from "lucide-react";

export type QuickActionGroup = "Onboard" | "Model" | "Quality";

export type QuickAction = {
  group: QuickActionGroup;
  label: string;
  hint: string;
  href: string;
  icon: LucideIcon;
  /** the privilege the page that opens checks before it lets the caller act (directly or by change request) */
  privilege: string;
};

export const QUICK_ACTION_GROUPS: QuickActionGroup[] = ["Onboard", "Model", "Quality"];

/** Every create action on the platform, in the top bar's "+ New" menu and the command palette. Each opens exactly the
 * flow it names: the onboarding wizard, the Source Store connect dialog on the right connectors, or a store's modal. */
export const QUICK_ACTIONS: QuickAction[] = [
  { group: "Onboard", label: "Snowflake share", hint: "Mount an inbound share and profile it in place",
    href: "/dashboard/onboarding?type=source&branch=share", icon: Share2, privilege: "SOURCE.CONNECT" },
  { group: "Onboard", label: "Snowflake database", hint: "Browse a database your role can read and profile in place",
    href: "/dashboard/sources?mode=snowflake", icon: Snowflake, privilege: "SOURCE.CONNECT" },
  { group: "Onboard", label: "Files and cloud storage", hint: "Upload CSV, Parquet or JSON, or read S3, Azure Blob or GCS",
    href: "/dashboard/sources?mode=external&connect=files", icon: FileUp, privilege: "SOURCE.CONNECT" },
  { group: "Onboard", label: "External source", hint: "Onboard a source that SnapLogic lands into Snowflake",
    href: "/dashboard/onboarding?type=source&branch=external", icon: CloudCog, privilege: "SOURCE.CONNECT" },
  { group: "Model", label: "Import a domain pack", hint: "Register a domain from domain_pack.json and its contract",
    href: "/dashboard/onboarding?type=domain&import=1", icon: Boxes, privilege: "DOMAIN.EDIT" },
  { group: "Model", label: "New table model", hint: "Design one target table, versioned and reviewed",
    href: "/dashboard/modeling?view=tables&new=1", icon: TableProperties, privilege: "MODEL.EDIT" },
  { group: "Quality", label: "New QA pack", hint: "A set of deterministic domain assertions in the QA Store",
    href: "/dashboard/qa-store?new=1", icon: PackagePlus, privilege: "PACKAGE.EDIT" },
  { group: "Quality", label: "Add a data quality check", hint: "A Soda check on a table in the Data Quality Store",
    href: "/dashboard/dq-store?add=1", icon: ShieldCheck, privilege: "SODA.EDIT" },
];

/** The same rule the pages use (lib/types canAct): the privilege itself, or REQUEST.CHANGES to raise a change
 * request. No list means governance is off and everything is allowed. */
export function actableBy(privileges?: string[] | null) {
  return (p: string) => !privileges || privileges.includes("*") || privileges.includes(p) || privileges.includes("REQUEST.CHANGES");
}
