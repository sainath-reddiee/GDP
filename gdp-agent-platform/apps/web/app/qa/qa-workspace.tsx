"use client";

import { useState } from "react";
import { BarChart3, ListChecks } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Access, Go, Nav, QaTab } from "./qa-shared";
import { SuitesTab } from "./suites-tab";
import { ResultsTab } from "./results-tab";

export type { QaTab } from "./qa-shared";

// cases, the Jira inbox and ticket triage are on the Tickets page (/tickets)
const TABS: { id: QaTab; label: string; icon: typeof ListChecks }[] = [
  { id: "suites", label: "Suites", icon: ListChecks },
  { id: "results", label: "Results", icon: BarChart3 },
];

/** Write the view into the URL without re-rendering the server page. */
function syncUrl(nav: Nav) {
  const p = new URLSearchParams(window.location.search);
  for (const [k, v] of Object.entries({ tab: nav.tab, table: nav.table, key: nav.key, suite: nav.suite })) {
    if (v) p.set(k, v); else p.delete(k);
  }
  window.history.replaceState(window.history.state, "", `${window.location.pathname}?${p}`);
}

/** QA testing: table suites of read-only SQL tests and their results, with the view kept in the URL. */
export function QaWorkspace({ initial, access, domains, me }: {
  initial: Nav; access: Access; domains: { domain_id: string; name: string }[]; me: string;
}) {
  const [nav, setNav] = useState<Nav>(initial);
  const go: Go = (patch) => {
    setNav((n) => {
      const next = { ...n, ...patch };
      syncUrl(next);
      return next;
    });
  };
  return (
    <div className="space-y-4">
      <nav role="tablist" aria-label="QA views" className="flex gap-1 border-b">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button key={id} type="button" role="tab" aria-selected={nav.tab === id} onClick={() => go({ tab: id })}
                  className={cn("-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium",
                    nav.tab === id ? "border-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>
            <Icon className="h-4 w-4" />{label}
          </button>
        ))}
      </nav>
      {nav.tab === "suites" && <SuitesTab nav={nav} go={go} access={access} domains={domains} />}
      {nav.tab === "results" && <ResultsTab nav={nav} go={go} access={access} me={me} />}
    </div>
  );
}
