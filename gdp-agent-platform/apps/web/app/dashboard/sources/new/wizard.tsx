"use client";

import { useEffect, useState } from "react";
import { CloudCog, Database, FileSpreadsheet, Share2 } from "lucide-react";
import type { CatalogDatabase, LandingDefaults, ShareList } from "@/lib/hub-types";
import { ShareWizard } from "./share-wizard";
import { IngestionWizard } from "./ingestion-wizard";
import { ChoiceStep, RailStep, Reveal, type Choice } from "../../onboarding/rail";

export type DomainOption = { domain_id: string; domain_name: string };
type Branch = "share" | "external";
export type SourceKind = "database" | "file";

const BRANCHES: Choice<Branch>[] = [
  { key: "share", label: "Snowflake database or share", icon: Share2, slug: "snowflake-share" },
  { key: "external", label: "Other sources (PostgreSQL, Oracle, SQL Server, S3, SharePoint)", icon: CloudCog, slug: "other-sources" },
];
// Other sources ask different questions for a database and for files (the onboarding configuration workbook)
const KINDS: Choice<SourceKind>[] = [
  { key: "database", label: "Relational database", icon: Database, slug: "relational-database" },
  { key: "file", label: "File based", icon: FileSpreadsheet, slug: "file-based" },
];

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
  const choose = (b: Branch | null) => {
    setBranch(b);
    remember("branch", b);
    if (b !== "external") { setKind(null); remember("kind", null); }
  };
  const chooseKind = (k: SourceKind | null) => { setKind(k); remember("kind", k); };
  return (
    <>
      <ChoiceStep n={2} title="Source type" choices={BRANCHES} value={branch} onChange={choose} testId="onboard-source" />
      {branch === "share" && (
        <Reveal key="share">
          <RailStep n={3} title="Onboard the database or share" last>
            <ShareWizard shares={shares} domains={domains} databases={databases} landing={landing} initialName={initialName} />
          </RailStep>
        </Reveal>
      )}
      {branch === "external" && (
        <Reveal key="external">
          <ChoiceStep n={3} title="Kind of source" choices={KINDS} value={kind} onChange={chooseKind} testId="onboard-kind-of-source" />
        </Reveal>
      )}
      {branch === "external" && kind && (
        <Reveal key={kind}>
          <RailStep n={4} title={kind === "database" ? "Configure the database ingestion" : "Configure the file ingestion"} last>
            <IngestionWizard key={kind} kind={kind} domains={domains} user={user} initialName={initialName} />
          </RailStep>
        </Reveal>
      )}
    </>
  );
}
