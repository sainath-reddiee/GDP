import { api, whoami } from "@/lib/api";
import { can, canAct } from "@/lib/types";
import { PageHeader } from "@/components/page-header";
import { displayDomain } from "@/lib/catalog-display";
import type { JiraStatus } from "../jira/actions";
import type { MyDomains } from "../qa/cases/actions";
import type { TicketFilters } from "./actions";
import { TicketsWorkspace, type TicketsTab } from "./tickets-workspace";

export const dynamic = "force-dynamic";

type Params = Record<string, string | undefined>;
type DomainRow = { domain_id: string; domain_name: string; active_flag: boolean };

const one = <T extends string>(value: string | undefined, allowed: readonly T[]): T | undefined =>
  allowed.find((a) => a === value);

/** The list's filters from the URL (shareable links); anything unknown is left out and the API checks the rest. */
function readFilters(sp: Params): TicketFilters {
  return {
    source: one(sp.source, ["all", "jira", "case"] as const), status: one(sp.status, ["open", "done", "any"] as const),
    assignee: one(sp.assignee, ["any", "me", "unassigned"] as const),
    ...Object.fromEntries((["priority", "type", "kind", "project", "sprint", "updated_from", "updated_to", "domain_id", "q"] as const)
      .filter((k) => sp[k]).map((k) => [k, sp[k]])),
    ...Object.fromEntries((["sla_breached", "linked", "copilot"] as const).filter((k) => sp[k] === "true").map((k) => [k, true])),
  };
}

/** Tickets: one place for every ticket, Jira issues (as the signed-in person) and platform cases, handed to the Copilot. */
export default async function TicketsPage({ searchParams }: { searchParams?: Params }) {
  const [me, jira, domains, mine] = await Promise.all([
    whoami(),
    api<JiraStatus>("/api/jira/status").catch(() => null),
    api<{ domains: DomainRow[] }>("/api/domains").catch(() => ({ domains: [] as DomainRow[] })),
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
  const tab: TicketsTab = sp.tab === "ticket" || (sp.key && sp.tab !== "jira" && sp.tab !== "list") ? "ticket"
    : sp.tab === "jira" ? "jira" : "list";
  const active = domains.domains.filter((d) => d.active_flag && displayDomain(d.domain_name))
    .map((d) => ({ domain_id: d.domain_id, name: displayDomain(d.domain_name) ?? d.domain_name }));
  const visible = mine ? mine.domains.map((d) => ({ domain_id: d.domain_id, name: displayDomain(d.name) ?? d.name })) : active;
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Overview" title="Tickets"
                  description="Every ticket in one place: your Jira issues and the platform's cases, with filters, saved views and sprints. Hand any Jira ticket to the Copilot to do the work; everything in Jira is done as you." />
      <TicketsWorkspace initial={{ tab, key: sp.key ?? "" }} filters={readFilters(sp)} openNew={sp.new === "1"} access={access}
                        jira={jira} me={jira?.connected?.display_name ?? me?.user ?? "you"} domains={visible} />
    </div>
  );
}
