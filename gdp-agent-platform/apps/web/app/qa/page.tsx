import { api, whoami } from "@/lib/api";
import { can, canAct } from "@/lib/types";
import { PageHeader } from "@/components/page-header";
import { displayDomain } from "@/lib/catalog-display";
import type { JiraStatus } from "../jira/actions";
import { QaWorkspace, type QaTab } from "./qa-workspace";

type Params = { tab?: string; table?: string; key?: string; suite?: string };
const TABS: QaTab[] = ["inbox", "suites", "triage", "results"];

export default async function QaPage({ searchParams }: { searchParams?: Params }) {
  const [me, domains, jira] = await Promise.all([
    whoami(),
    api<{ domains: { domain_id: string; domain_name: string; active_flag: boolean }[] }>("/api/domains")
      .catch(() => ({ domains: [] as { domain_id: string; domain_name: string; active_flag: boolean }[] })),
    api<JiraStatus>("/api/jira/status").catch(() => null),
  ]);
  const access = {
    canEdit: can(me, "QA.EDIT"),
    canAI: can(me, "AI.USE"),
    canJiraRead: can(me, "JIRA.READ"),
    canJiraWrite: canAct(me, "JIRA.WRITE"),
  };
  const sp = searchParams ?? {};
  const fallback: QaTab = access.canJiraRead ? "inbox" : "suites";
  const tab = TABS.find((t) => t === sp.tab) ?? (sp.key ? "triage" : sp.table ? "suites" : fallback);
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Work" title="QA"
                  description="Test a domain's target tables with or without a run: Jira issues in, suites of read-only SQL tests, AI triage of a reported bug, and results that file or update tickets." />
      <QaWorkspace initial={{ tab, table: sp.table ?? "", key: sp.key ?? "", suite: sp.suite ?? "" }} access={access} jira={jira}
                   me={jira?.connected?.display_name ?? me?.user ?? "you"}
                   domains={domains.domains.filter((d) => d.active_flag && displayDomain(d.domain_name))
                     .map((d) => ({ domain_id: d.domain_id, name: displayDomain(d.domain_name) ?? d.domain_name }))} />
    </div>
  );
}
