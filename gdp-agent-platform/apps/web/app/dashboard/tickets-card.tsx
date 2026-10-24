import Link from "next/link";
import { AlarmClock, Bot, Ticket } from "lucide-react";
import { Panel } from "@/components/hub/kpi";
import { StatusPill } from "@/components/jira/jira-controls";
import type { Ticket as TicketRow } from "@/app/tickets/actions";

export type TicketsCardData = {
  tickets: Pick<TicketRow, "id" | "sources" | "key" | "title" | "status" | "done" | "priority" | "case" | "sla_breached">[];
  jiraConnect?: boolean; error?: boolean;
};

/** My tickets on the Command Center: the open Jira issues and cases assigned to me, each one click from the Copilot. */
export function TicketsCard({ data }: { data: TicketsCardData }) {
  const shown = data.tickets.slice(0, 6);
  return (
    <Panel title="My tickets" icon={Ticket}
           action={<Link href="/os/run/tickets?assignee=me" className="text-xs font-medium text-primary hover:underline">
             {data.tickets.length > shown.length ? `All ${data.tickets.length}` : "Open Tickets"}</Link>}>
      <div data-testid="tickets-card" className="space-y-2">
        {data.jiraConnect && <p className="text-xs text-muted-foreground">Connect your Jira account to see your Jira tickets here too. <Link href="/os/run/tickets" className="font-medium text-primary hover:underline">Connect Jira</Link></p>}
        {data.error ? <p className="text-sm text-muted-foreground">Tickets could not be loaded right now.</p>
          : !shown.length ? <p className="text-sm text-muted-foreground">No open tickets assigned to you.</p> : (
          <ul className="divide-y divide-border/60">
            {shown.map((t) => {
              const jira = t.sources.includes("JIRA");
              const href = jira ? `/os/run/tickets?tab=ticket&key=${encodeURIComponent(t.key)}` : `/os/release/qa/cases/${encodeURIComponent(t.case?.case_id ?? "")}`;
              return (
                <li key={t.id} className="flex items-center gap-3 py-2">
                  <Link href={href} className="font-mono text-xs font-semibold text-primary hover:underline">{t.key}</Link>
                  <span className={jira ? "rounded bg-sky-500/10 px-1.5 text-[10px] text-sky-700 dark:text-sky-400" : "rounded bg-blue-500/10 px-1.5 text-[10px] text-blue-700 dark:text-blue-400"}>{jira ? "Jira" : "Case"}</span>
                  <span className="min-w-0 flex-1 truncate text-sm" title={t.title}>{t.title}</span>
                  {t.sla_breached && <AlarmClock className="h-3.5 w-3.5 text-rose-600" aria-label="SLA breached" />}
                  {t.priority && <span className="text-[11px] font-semibold text-muted-foreground">{t.priority}</span>}
                  {jira ? <StatusPill name={t.status} category={t.done ? "done" : null} /> : <span className="text-xs text-muted-foreground">{t.status}</span>}
                  {jira && (
                    <Link href={`/os/work/copilot?jira=${encodeURIComponent(t.key)}`} aria-label={`Work on ${t.key} in the Copilot`}
                          className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs font-medium text-primary hover:bg-primary/10">
                      <Bot className="h-3.5 w-3.5" /> Copilot
                    </Link>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Panel>
  );
}
