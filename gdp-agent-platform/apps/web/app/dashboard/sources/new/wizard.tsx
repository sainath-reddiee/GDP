"use client";

import { useEffect, useState } from "react";
import { CloudCog, Share2 } from "lucide-react";
import type { CatalogDatabase, LandingDefaults, ShareList } from "@/lib/hub-types";
import { ShareWizard } from "./share-wizard";
import { IngestionWizard } from "./ingestion-wizard";
import { ChoiceStep, RailStep, Reveal, type Choice } from "../../onboarding/rail";

export type DomainOption = { domain_id: string; domain_name: string };
type Branch = "share" | "external";

const BRANCHES: Choice<Branch>[] = [
  { key: "share", label: "Snowflake share", icon: Share2, slug: "snowflake-share" },
  { key: "external", label: "Other sources (PostgreSQL, Oracle, SQL Server, Salesforce, APIs, files)", icon: CloudCog, slug: "other-sources" },
];

export function OnboardWizard({ initialBranch, shares, domains, databases, landing, user }: {
  initialBranch: Branch | null; shares: ShareList; domains: DomainOption[]; databases: CatalogDatabase[];
  landing: LandingDefaults | null; user: string | null;
}) {
  const [branch, setBranch] = useState<Branch | null>(initialBranch);
  // a link to a step while already on this page (the "+ New" menu) changes only the query, so follow it
  useEffect(() => { if (initialBranch) setBranch(initialBranch); }, [initialBranch]);
  // keep the address bar in step without a server round trip, so a refresh or a shared link opens the same step
  const choose = (b: Branch | null) => {
    setBranch(b);
    const url = new URL(window.location.href);
    if (b) url.searchParams.set("branch", b); else url.searchParams.delete("branch");
    window.history.replaceState(window.history.state, "", url);
  };
  return (
    <>
      <ChoiceStep n={2} title="Source type" choices={BRANCHES} value={branch} onChange={choose} testId="onboard-source" depth={1} />
      {branch && (
        <Reveal key={branch}>
          <RailStep n={3} title={branch === "share" ? "Onboard the share" : "Configure the SnapLogic ingestion"} last>
            {branch === "share" ? <ShareWizard shares={shares} domains={domains} databases={databases} landing={landing} />
              : <IngestionWizard domains={domains} user={user} />}
          </RailStep>
        </Reveal>
      )}
    </>
  );
}
