"use client";

import { useEffect, useState } from "react";
import { CloudCog, Database, FileSpreadsheet, Snowflake, type LucideIcon } from "lucide-react";
import type { CatalogDatabase, LandingDefaults, ShareList } from "@/lib/hub-types";
import { cn } from "@/lib/utils";
import { ShareWizard } from "./share-wizard";
import { IngestionWizard } from "./ingestion-wizard";
import { Reveal } from "../../onboarding/rail";

export type DomainOption = { domain_id: string; domain_name: string };
type Branch = "share" | "external";
export type SourceKind = "database" | "file";

type Tab<K extends string> = { key: K; label: string; hint: string; icon: LucideIcon };

const BRANCHES: Tab<Branch>[] = [
  { key: "share", label: "Snowflake", hint: "Database or share", icon: Snowflake },
  { key: "external", label: "Other", hint: "PostgreSQL, Oracle, SQL Server, S3, SharePoint", icon: CloudCog },
];
// Other sources ask different questions for a database and for files (the onboarding configuration workbook)
const KINDS: Tab<SourceKind>[] = [
  { key: "database", label: "Relational database", hint: "Tables read over a connection", icon: Database },
  { key: "file", label: "File based", hint: "Files from a path or bucket", icon: FileSpreadsheet },
];

/** A row of tabs: the open one fills with the brand colour, the rest stay quiet until hovered. */
function Tabs<K extends string>({ label, tabs, value, onChange, testId, compact = false }: {
  label: string; tabs: Tab<K>[]; value: K | null; onChange: (key: K) => void; testId: string; compact?: boolean;
}) {
  return (
    <div role="tablist" aria-label={label} data-testid={testId}
         className="grid gap-1.5 rounded-xl border bg-muted/50 p-1.5 sm:grid-cols-2">
      {tabs.map((t) => {
        const on = value === t.key;
        return (
          <button key={t.key} type="button" role="tab" aria-selected={on} onClick={() => onChange(t.key)}
                  data-testid={`${testId}-${t.key}`} data-chosen={on || undefined}
                  className={cn("group flex min-w-0 items-center gap-3 rounded-lg text-left transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    compact ? "px-3 py-2" : "px-4 py-3",
                    on ? "bg-[var(--os-forest,hsl(var(--primary)))] text-white shadow-md"
                      : "text-foreground/80 hover:bg-card hover:text-foreground hover:shadow-sm")}>
            <span className={cn("grid shrink-0 place-items-center rounded-md transition-colors", compact ? "h-8 w-8" : "h-9 w-9",
              on ? "bg-white/20 text-white" : "bg-card text-foreground/70 group-hover:text-foreground")}>
              <t.icon className="h-[18px] w-[18px]" />
            </span>
            <span className="flex min-w-0 flex-col">
              <span className="text-sm font-semibold leading-tight">{t.label}</span>
              <span className={cn("truncate text-[11.5px] leading-snug", on ? "text-white/75" : "text-muted-foreground")}>{t.hint}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

export function OnboardWizard({ initialBranch, initialKind = null, shares, domains, databases, landing, user, initialName = "" }: {
  initialBranch: Branch | null; initialKind?: SourceKind | null; shares: ShareList; domains: DomainOption[];
  databases: CatalogDatabase[]; landing: LandingDefaults | null; user: string | null;
  /** the source name to start from (an onboarding request hands it over) */
  initialName?: string;
}) {
  const [branch, setBranch] = useState<Branch | null>(initialBranch);
  const [kind, setKind] = useState<SourceKind | null>(initialKind);
  // a link to a step while already on this page (the "+ New" menu) changes only the query, so follow it
  useEffect(() => { if (initialBranch) setBranch(initialBranch); }, [initialBranch]);
  useEffect(() => { if (initialKind) setKind(initialKind); }, [initialKind]);
  // keep the address bar in step without a server round trip, so a refresh or a shared link opens the same step
  const remember = (key: string, value: string | null) => {
    const url = new URL(window.location.href);
    if (value) url.searchParams.set(key, value); else url.searchParams.delete(key);
    window.history.replaceState(window.history.state, "", url);
  };
  const choose = (b: Branch) => {
    setBranch(b);
    remember("branch", b);
    if (b !== "external") { setKind(null); remember("kind", null); }
  };
  const chooseKind = (k: SourceKind) => { setKind(k); remember("kind", k); };
  return (
    <div className="space-y-5">
      <Tabs label="Source type" tabs={BRANCHES} value={branch} onChange={choose} testId="onboard-source" />
      {branch === "share" && (
        <Reveal key="share">
          <ShareWizard shares={shares} domains={domains} databases={databases} landing={landing} initialName={initialName} />
        </Reveal>
      )}
      {branch === "external" && (
        <Reveal key="external" className="space-y-5">
          <Tabs label="Kind of source" tabs={KINDS} value={kind} onChange={chooseKind} testId="onboard-kind-of-source" compact />
          {kind && <IngestionWizard key={kind} kind={kind} domains={domains} user={user} initialName={initialName} />}
        </Reveal>
      )}
    </div>
  );
}
