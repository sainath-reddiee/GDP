"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Bot, ChevronDown, KanbanSquare, List, MessageSquareWarning, Plus, Stethoscope, Ticket } from "lucide-react";
import { cn } from "@/lib/utils";
import { ReportDialog } from "@/components/report-problem";
import type { JiraStatus } from "../jira/actions";
import type { Access, Nav } from "../qa/qa-shared";
import { InboxTab } from "../qa/inbox-tab";
import { TriageTab } from "../qa/triage-tab";
import { NewTicket } from "./new-ticket";
import { TicketList } from "./ticket-list";
import type { TicketFilters } from "./actions";

export type TicketsTab = "list" | "jira" | "ticket";

const TABS: { id: TicketsTab; label: string; icon: typeof List; jira?: boolean }[] = [
  { id: "list", label: "All tickets", icon: List },
  { id: "jira", label: "Jira search and boards", icon: KanbanSquare, jira: true },
  { id: "ticket", label: "Ticket", icon: Stethoscope, jira: true },
];

/** Tickets: Jira issues and platform cases in one list; Jira's own search (JQL, saved filters, boards); one Jira
 *  ticket's triage; and new tickets of either kind. The view is kept in the URL. */
export function TicketsWorkspace({ initial, filters, openNew, access, jira, me, domains }: {
  initial: { tab: TicketsTab; key: string }; filters: TicketFilters; openNew: boolean; access: Access;
  jira: JiraStatus | null; me: string; domains: { domain_id: string; name: string }[];
}) {
  const [tab, setTab] = useState<TicketsTab>(initial.tab);
  const [key, setKey] = useState(initial.key);
  const [creating, setCreating] = useState<"jira" | "case" | null>(openNew ? "jira" : null);
  const [menu, setMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent) => { if (!menuRef.current?.contains(e.target as Node)) setMenu(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menu]);

  const show = (next: TicketsTab, nextKey = key) => {
    setTab(next); setKey(nextKey);
    if (next !== "list") {   // the list keeps its own filters in the URL
      const p = new URLSearchParams({ tab: next, ...(next === "ticket" && nextKey ? { key: nextKey } : {}) });
      window.history.replaceState(window.history.state, "", `/tickets?${p}`);
    }
  };
  // the reused QA views speak in their own tab names: inbox (Jira search) and triage (one ticket)
  const nav: Nav = { tab: tab === "ticket" ? "triage" : "inbox", key, table: "", suite: "" };
  const go = (patch: Partial<Nav>) => {
    if (patch.tab === "triage" || patch.key) show("ticket", patch.key ?? key);
    else if (patch.tab === "inbox") show("jira");
  };
  const connected = !!jira?.connected;
  const tabs = TABS.filter((t) => !t.jira || access.canJiraRead);
  const canCreateJira = access.canJiraWrite && connected;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 border-b">
        <nav role="tablist" aria-label="Ticket views" className="flex gap-1">
          {tabs.map(({ id, label, icon: Icon }) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => show(id)}
                    className={cn("-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium",
                      tab === id ? "border-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>
              <Icon className="h-4 w-4" />{label}
            </button>
          ))}
        </nav>
        <div className="mb-1.5 ml-auto flex gap-2">
          <Link href="/dashboard/copilot" className="inline-flex h-8 items-center gap-1.5 rounded-lg border px-3 text-xs font-medium hover:bg-muted">
            <Bot className="h-3.5 w-3.5 text-primary" /> Ask the Copilot
          </Link>
          <div className="relative" ref={menuRef}>
            <button type="button" onClick={() => setMenu(!menu)} aria-expanded={menu} aria-haspopup="menu"
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-medium text-primary-foreground hover:opacity-90">
              <Plus className="h-3.5 w-3.5" /> New ticket <ChevronDown className="h-3 w-3" />
            </button>
            {menu && (
              <div role="menu" className="popover absolute right-0 top-10 z-50 w-72 animate-pop-in p-1.5 shadow-pop">
                <button type="button" role="menuitem" disabled={!canCreateJira} onClick={() => { setMenu(false); setCreating("jira"); }}
                        title={!canCreateJira ? (connected ? "Needs the JIRA.WRITE privilege" : "Connect your Jira account first") : undefined}
                        className="flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left text-sm hover:bg-muted disabled:opacity-50">
                  <Ticket className="mt-0.5 h-4 w-4 text-sky-600" /><span><b className="block font-medium">Jira ticket</b><span className="text-xs text-muted-foreground">In your Jira project, with a sprint</span></span>
                </button>
                <button type="button" role="menuitem" disabled={!access.canCase} onClick={() => { setMenu(false); setCreating("case"); }}
                        title={!access.canCase ? "Needs the CASE.WORK privilege" : undefined}
                        className="flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left text-sm hover:bg-muted disabled:opacity-50">
                  <MessageSquareWarning className="mt-0.5 h-4 w-4 text-violet-600" /><span><b className="block font-medium">Platform case</b><span className="text-xs text-muted-foreground">A data problem tracked here, with an SLA</span></span>
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
      {tab === "list" && <TicketList initial={filters} domains={domains} canJiraWrite={access.canJiraWrite} openTicket={(k) => show("ticket", k)} />}
      {tab === "jira" && <InboxTab jira={jira} access={access} go={go} />}
      {tab === "ticket" && <TriageTab key={key} nav={nav} go={go} access={access} jira={jira} me={me} />}
      <NewTicket open={creating === "jira"} onClose={() => setCreating(null)} defaultProject={jira?.default_project ?? null}
                 onCreated={(k) => show("ticket", k)} />
      {creating === "case" && <ReportDialog title="New platform case" domains={domains} onClose={() => setCreating(null)} />}
    </div>
  );
}
