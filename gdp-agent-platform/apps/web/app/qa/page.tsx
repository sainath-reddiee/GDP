import { redirect } from "next/navigation";
import { api, whoami } from "@/lib/api";
import { can, canAct } from "@/lib/types";
import { PageHeader } from "@/components/page-header";
import { displayDomain } from "@/lib/catalog-display";
import type { CaseQuery } from "./cases/case-filters";
import { QaWorkspace, type QaTab } from "./qa-workspace";

type Params = { tab?: string; table?: string; key?: string; suite?: string } & CaseQuery;
const TABS: QaTab[] = ["suites", "results"];
type DomainRow = { domain_id: string; domain_name: string; active_flag: boolean };

/** Old QA workspace links to cases, the Jira inbox or a ticket's triage, as the matching Tickets page view. */
function ticketsUrl(sp: Params): string | null {
  if (sp.tab === "inbox") return "/tickets?tab=jira";
  if (sp.tab === "triage" || (sp.key && !sp.tab)) return `/tickets${sp.key ? `?${new URLSearchParams({ tab: "ticket", key: sp.key })}` : "?tab=jira"}`;
  const caseKeys = ["status", "severity", "kind", "domain", "mine", "q", "sla"] as const;
  if (sp.tab !== "cases" && (sp.tab || !caseKeys.some((k) => sp[k]))) return null;
  const p = new URLSearchParams({ source: "case" });
  if (sp.status === "any") p.set("status", "any");
  else if (sp.status && /RESOLVED|CLOSED|DUPLICATE/i.test(sp.status) && !/NEW|TRIAGED|IN_PROGRESS|FIX_|VERIFIED/i.test(sp.status)) p.set("status", "done");
  if (sp.severity) p.set("priority", sp.severity.toUpperCase());
  if (sp.kind) p.set("kind", sp.kind.toUpperCase());
  if (sp.domain) p.set("domain_id", sp.domain);
  if (sp.mine === "1" || sp.mine === "true") p.set("assignee", "me");
  if (sp.q) p.set("q", sp.q);
  if (sp.sla === "breached") p.set("sla_breached", "true");
  return `/tickets?${p}`;
}

export default async function QaPage({ searchParams }: { searchParams?: Params }) {
  const sp = searchParams ?? {};
  const moved = ticketsUrl(sp);
  if (moved) redirect(moved);
  const [me, domains] = await Promise.all([
    whoami(),
    api<{ domains: DomainRow[] }>("/api/domains").catch(() => ({ domains: [] as DomainRow[] })),
  ]);
  const access = {
    canEdit: can(me, "QA.EDIT"),
    canAI: can(me, "AI.USE"),
    canJiraRead: can(me, "JIRA.READ"),
    canJiraWrite: canAct(me, "JIRA.WRITE"),
    canCase: can(me, "CASE.WORK"),
    canResolve: can(me, "CASE.RESOLVE"),
  };
  const tab = TABS.find((t) => t === sp.tab) ?? "suites";
  const active = domains.domains.filter((d) => d.active_flag && displayDomain(d.domain_name))
    .map((d) => ({ domain_id: d.domain_id, name: displayDomain(d.domain_name) ?? d.domain_name }));
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Quality" title="QA testing"
                  description="Suites of read-only SQL tests per table, and their results, which open cases or file tickets. Cases and Jira tickets are on the Tickets page." />
      <QaWorkspace initial={{ tab, table: sp.table ?? "", key: sp.key ?? "", suite: sp.suite ?? "" }} access={access}
                   me={me?.user ?? "you"} domains={active} />
    </div>
  );
}
