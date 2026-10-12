"use client";

import { useState } from "react";
import { CloudCog, Share2 } from "lucide-react";
import type { CatalogDatabase, LandingDefaults, ShareList } from "@/lib/hub-types";
import { cn } from "@/lib/utils";
import { ShareWizard } from "./share-wizard";
import { IngestionWizard } from "./ingestion-wizard";
import { StepLabel } from "../../onboarding/step-label";

export type DomainOption = { domain_id: string; domain_name: string };

export function OnboardWizard({ initialBranch, shares, domains, databases, landing, user }: {
  initialBranch: "share" | "external"; shares: ShareList; domains: DomainOption[]; databases: CatalogDatabase[];
  landing: LandingDefaults | null; user: string | null;
}) {
  const [branch, setBranch] = useState(initialBranch);
  // lighter than the "what are you onboarding" cards above: compact rows with a radio
  const tile = "group flex items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition";
  const icon = (on: boolean) => cn("grid h-8 w-8 shrink-0 place-items-center rounded-lg",
    on ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground group-hover:text-foreground");
  const body = (title: string, text: string) => (
    <span className="min-w-0 flex-1">
      <span className="block text-sm font-medium">{title}</span>
      <span className="block text-xs text-muted-foreground">{text}</span>
    </span>
  );
  // the two kinds onboarded here, as a wizard below the tiles
  const choice = (key: "share" | "external", title: string, text: string, Icon: typeof Share2) => {
    const on = branch === key;
    return (
      <button type="button" onClick={() => setBranch(key)} aria-pressed={on} data-testid={`onboard-source-${key}`}
              className={cn(tile, on ? "border-primary/50 bg-primary/[0.04]" : "border-border/80 bg-card/60 hover:border-primary/25")}>
        <span className={icon(on)}><Icon className="h-4 w-4" /></span>
        {body(title, text)}
        <span className={cn("grid h-4 w-4 shrink-0 place-items-center rounded-full border", on ? "border-primary" : "border-muted-foreground/40")} aria-hidden>
          {on && <span className="h-2 w-2 rounded-full bg-primary" />}
        </span>
      </button>
    );
  };
  return (
    <div className="space-y-6">
      <StepLabel n={2} title="Source type" hint="A Snowflake share, or an external system landed by SnapLogic" />
      <div className="grid gap-2 sm:grid-cols-2" aria-label="Source kind">
        {choice("share", "Snowflake share", "Unloaded to a Parquet stage and copied into landing.", Share2)}
        {choice("external", "External source", "A SnapLogic pipeline landing Oracle, Salesforce, REST or files.", CloudCog)}
      </div>
      <StepLabel n={3} title={branch === "share" ? "Onboard the share" : "Configure the SnapLogic ingestion"} />
      {branch === "share" ? <ShareWizard shares={shares} domains={domains} databases={databases} landing={landing} />
        : <IngestionWizard domains={domains} user={user} />}
    </div>
  );
}
