"use client";

import { useState } from "react";
import { CloudCog, Share2 } from "lucide-react";
import type { CatalogDatabase, LandingDefaults, ShareList } from "@/lib/hub-types";
import { cn } from "@/lib/utils";
import { ShareWizard } from "./share-wizard";
import { IngestionWizard } from "./ingestion-wizard";

export type DomainOption = { domain_id: string; domain_name: string };

export function OnboardWizard({ initialBranch, shares, domains, databases, landing, user }: {
  initialBranch: "share" | "external"; shares: ShareList; domains: DomainOption[]; databases: CatalogDatabase[];
  landing: LandingDefaults | null; user: string | null;
}) {
  const [branch, setBranch] = useState(initialBranch);
  const choice = (key: "share" | "external", title: string, text: string, Icon: typeof Share2) => (
    <button type="button" onClick={() => setBranch(key)} aria-pressed={branch === key}
            className={cn("surface flex flex-1 items-start gap-3 p-4 text-left transition",
              branch === key ? "border-primary/40 ring-2 ring-primary/15" : "hover:border-primary/25")}>
      <span className={cn("grid h-10 w-10 shrink-0 place-items-center rounded-xl", branch === key ? "bg-primary text-primary-foreground" : "bg-accent text-accent-foreground")}>
        <Icon className="h-5 w-5" />
      </span>
      <span>
        <span className="block text-sm font-semibold">{title}</span>
        <span className="block text-xs text-muted-foreground">{text}</span>
      </span>
    </button>
  );
  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 md:flex-row">
        {choice("share", "Native Snowflake share", "Provider account, share, database, schema and tables. Unloaded to a Parquet stage and copied into landing.", Share2)}
        {choice("external", "External system", "Oracle, SQL Server, Postgres, Salesforce, REST, files. Configured for the SnapLogic ingestion pipelines.", CloudCog)}
      </div>
      {branch === "share" ? <ShareWizard shares={shares} domains={domains} databases={databases} landing={landing} />
        : <IngestionWizard domains={domains} user={user} />}
    </div>
  );
}
