import { GitBranchPlus, PlugZap, SquareTerminal, type LucideIcon } from "lucide-react";

export type QuickAction = { label: string; hint: string; href: string; icon: LucideIcon; privilege?: string | string[] };

/** The three things people start most often, in the top bar and the command palette. */
export const QUICK_ACTIONS: QuickAction[] = [
  { label: "Onboard source", hint: "Register a share, database or external pipeline", href: "/dashboard/onboarding?type=source", icon: PlugZap },
  { label: "Add QA query", hint: "A deterministic domain assertion in the QA Store", href: "/dashboard/qa-store?new=1", icon: SquareTerminal,
    privilege: ["PACKAGE.EDIT", "QA.EDIT"] },
  { label: "New model branch", hint: "Design a domain model on its own feature branch", href: "/dashboard/modeling", icon: GitBranchPlus },
];
