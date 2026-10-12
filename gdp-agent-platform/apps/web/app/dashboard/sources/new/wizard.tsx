"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowUpRight, CloudCog, FileUp, Share2, Snowflake } from "lucide-react";
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
  const tile = "surface flex items-start gap-3 p-4 text-left transition";
  const icon = (on: boolean) => cn("grid h-9 w-9 shrink-0 place-items-center rounded-xl", on ? "bg-primary text-primary-foreground" : "bg-accent text-accent-foreground");
  const body = (title: string, text: string, external?: boolean) => (
    <span className="min-w-0">
      <span className="flex items-center gap-1 text-sm font-semibold">{title}{external && <ArrowUpRight className="h-3.5 w-3.5 text-muted-foreground" />}</span>
      <span className="block text-xs text-muted-foreground">{text}</span>
    </span>
  );
  // the two kinds onboarded here, as a wizard below the tiles
  const choice = (key: "share" | "external", title: string, text: string, Icon: typeof Share2) => (
    <button type="button" onClick={() => setBranch(key)} aria-pressed={branch === key} data-testid={`onboard-source-${key}`}
            className={cn(tile, branch === key ? "border-primary/40 ring-2 ring-primary/15" : "hover:border-primary/25")}>
      <span className={icon(branch === key)}><Icon className="h-4 w-4" /></span>
      {body(title, text)}
    </button>
  );
  // the kinds connected in the Source Store (its catalog browser or its Connect dialog)
  const link = (href: string, title: string, text: string, Icon: typeof Share2, id: string) => (
    <Link href={href} data-testid={`onboard-source-${id}`} className={cn(tile, "hover:border-primary/25")}>
      <span className={icon(false)}><Icon className="h-4 w-4" /></span>
      {body(title, text, true)}
    </Link>
  );
  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="Source kind">
        {choice("share", "Snowflake share", "An inbound share: unloaded to a Parquet stage and copied into landing.", Share2)}
        {link("/dashboard/sources?mode=snowflake", "Snowflake database", "Any database your role can read, profiled in place.", Snowflake, "database")}
        {link("/dashboard/sources?mode=external&connect=files", "Files and cloud storage", "CSV, Parquet or JSON uploads, S3, Azure Blob or GCS.", FileUp, "files")}
        {choice("external", "External source", "A SnapLogic pipeline that lands Oracle, Salesforce, REST or files.", CloudCog)}
      </div>
      {branch === "share" ? <ShareWizard shares={shares} domains={domains} databases={databases} landing={landing} />
        : <IngestionWizard domains={domains} user={user} />}
    </div>
  );
}
