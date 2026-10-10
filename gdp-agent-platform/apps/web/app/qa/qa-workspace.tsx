"use client";

import { useState } from "react";
import { BarChart3, Inbox, LifeBuoy, ListChecks, Stethoscope } from "lucide-react";
import { cn } from "@/lib/utils";
import type { JiraStatus } from "../jira/actions";
import type { Access, Go, Nav, QaTab } from "./qa-shared";
import { CasesTab } from "./cases-tab";
import { caseQuery, type CaseFilterState } from "./cases/case-filters";
import { InboxTab } from "./inbox-tab";
import { SuitesTab } from "./suites-tab";
import { TriageTab } from "./triage-tab";
import { ResultsTab } from "./results-tab";

export type { QaTab } from "./qa-shared";

const TABS: { id: QaTab; label: string; icon: typeof Inbox; jira?: boolean }[] = [
  { id: "cases", label: "Cases", icon: LifeBuoy },
  { id: "inbox", label: "Inbox", icon: Inbox, jira: true },
  { id: "suites", label: "Suites", icon: ListChecks },
  { id: "triage", label: "Triage", icon: Stethoscope, jira: true },
  { id: "results", label: "Results", icon: BarChart3 },
];

/** Write the view into the URL without re-rendering the server page; the case filters only while Cases is shown. */
function syncUrl(nav: Nav, filters: CaseFilterState) {
  const p = new URLSearchParams(window.location.search);
  const caseKeys = caseQuery(filters);
  for (const [k, v] of Object.entries({ tab: nav.tab, table: nav.table, key: nav.key, suite: nav.suite })) {
    if (v) p.set(k, v); else p.delete(k);
  }
  for (const [k, v] of Object.entries(caseKeys)) {
    if (v && nav.tab === "cases") p.set(k, v); else p.delete(k);
  }
  window.history.replaceState(window.history.state, "", `${window.location.pathname}?${p}`);
}

/** The QA workspace: cases, Jira inbox, table suites, ticket triage and results, with the view kept in the URL. */
export function QaWorkspace({ initial, access, jira, domains, caseDomains, caseFilters, me }: {
  initial: Nav; access: Access; jira: JiraStatus | null; domains: { domain_id: string; name: string }[];
  /** the domains whose cases the caller can see */
  caseDomains: { domain_id: string; name: string }[]; caseFilters: CaseFilterState; me: string;
}) {
  const [nav, setNav] = useState<Nav>(initial);
  const [filters, setFiltersState] = useState<CaseFilterState>(caseFilters);
  const go: Go = (patch) => {
    setNav((n) => {
      const next = { ...n, ...patch };
      syncUrl(next, filters);
      return next;
    });
  };
  const setFilters = (f: CaseFilterState) => {
    setFiltersState(f);
    syncUrl(nav, f);
  };
  const tabs = TABS.filter((t) => !t.jira || access.canJiraRead);
  return (
    <div className="space-y-4">
      <nav role="tablist" aria-label="QA views" className="flex gap-1 border-b">
        {tabs.map(({ id, label, icon: Icon }) => (
          <button key={id} type="button" role="tab" aria-selected={nav.tab === id} onClick={() => go({ tab: id })}
                  className={cn("-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium",
                    nav.tab === id ? "border-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>
            <Icon className="h-4 w-4" />{label}
          </button>
        ))}
      </nav>
      {nav.tab === "cases" && <CasesTab filters={filters} setFilters={setFilters} access={access} domains={caseDomains} />}
      {nav.tab === "inbox" && <InboxTab jira={jira} access={access} go={go} />}
      {nav.tab === "suites" && <SuitesTab nav={nav} go={go} access={access} domains={domains} />}
      {nav.tab === "triage" && <TriageTab key={nav.key} nav={nav} go={go} access={access} jira={jira} me={me} />}
      {nav.tab === "results" && <ResultsTab nav={nav} go={go} access={access} me={me} />}
    </div>
  );
}
