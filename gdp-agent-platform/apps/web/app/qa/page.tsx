import { api, whoami } from "@/lib/api";
import { can, canAct } from "@/lib/types";
import { PageHeader } from "@/components/page-header";
import { displayDomain } from "@/lib/catalog-display";
import type { JiraStatus } from "../jira/actions";
import type { MyDomains } from "./cases/actions";
import { readCaseFilters, type CaseQuery } from "./cases/case-filters";
import { QaWorkspace, type QaTab } from "./qa-workspace";

type Params = { tab?: string; table?: string; key?: string; suite?: string } & CaseQuery;
const TABS: QaTab[] = ["cases", "inbox", "suites", "triage", "results"];
type DomainRow = { domain_id: string; domain_name: string; active_flag: boolean };

export default async function QaPage({ searchParams }: { searchParams?: Params }) {
  const [me, domains, jira, mine] = await Promise.all([
    whoami(),
    api<{ domains: DomainRow[] }>("/api/domains").catch(() => ({ domains: [] as DomainRow[] })),
    api<JiraStatus>("/api/jira/status").catch(() => null),
    // the domains the caller may see cases of; an API without domain governance falls back to every active domain
    api<MyDomains>("/api/governance/my-domains").catch(() => null),
  ]);
  const access = {
    canEdit: can(me, "QA.EDIT"),
    canAI: can(me, "AI.USE"),
    canJiraRead: can(me, "JIRA.READ"),
    canJiraWrite: canAct(me, "JIRA.WRITE"),
    canCase: can(me, "CASE.WORK"),
    canResolve: can(me, "CASE.RESOLVE"),
  };
  const sp = searchParams ?? {};
  const tab = TABS.find((t) => t === sp.tab) ?? (sp.key ? "triage" : sp.table ? "suites" : "cases");
  const active = domains.domains.filter((d) => d.active_flag && displayDomain(d.domain_name))
    .map((d) => ({ domain_id: d.domain_id, name: displayDomain(d.domain_name) ?? d.domain_name }));
  const caseDomains = mine ? mine.domains.map((d) => ({ domain_id: d.domain_id, name: displayDomain(d.name) ?? d.name })) : active;
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Quality" title="QA"
                  description="Track every reported problem as a case, from report to verified fix. Jira issues in, suites of read-only SQL tests, AI triage of a reported bug, and results that open cases or file tickets." />
      <QaWorkspace initial={{ tab, table: sp.table ?? "", key: sp.key ?? "", suite: sp.suite ?? "" }} access={access} jira={jira}
                   me={jira?.connected?.display_name ?? me?.user ?? "you"} domains={active} caseDomains={caseDomains}
                   caseFilters={readCaseFilters(sp)} />
    </div>
  );
}
