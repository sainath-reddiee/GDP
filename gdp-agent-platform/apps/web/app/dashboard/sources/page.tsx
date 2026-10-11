import Link from "next/link";
import { CloudCog, Database, Plus, Share2, Workflow } from "lucide-react";
import { api } from "@/lib/api";
import type { Pipeline, ShareList } from "@/lib/hub-types";
import { PageHeader } from "@/components/page-header";
import { Kpi, Panel } from "@/components/hub/kpi";
import { NotDeployed } from "@/components/hub/states";
import { buttonVariants } from "@/components/ui/button";
import { SourcesSection } from "@/app/sources/sources-section";
import { PipelineTable } from "./pipeline-table";

export default async function SourceHub({ searchParams }: { searchParams: { db?: string; schema?: string; mode?: string } }) {
  const [pipes, shares] = await Promise.all([
    api<{ pipelines: Pipeline[]; deployed: boolean }>("/api/hub/pipelines").catch(() => ({ pipelines: [] as Pipeline[], deployed: false })),
    api<ShareList>("/api/hub/sources/shares").catch(() => ({ shares: [], mounted: [] } as ShareList)),
  ]);
  const active = pipes.pipelines.filter((p) => p.status === "ACTIVE").length;
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Asset store" title="Source Store"
                  description="Onboard Snowflake shares straight into landing, register external systems for the ingestion pipelines, and explore, profile and model any schema."
                  actions={<>
                    <Link href="/dashboard/sources/new?branch=external" className={buttonVariants({ variant: "outline" })}><CloudCog className="h-4 w-4" /> Register external source</Link>
                    <Link href="/dashboard/sources/new?branch=share" className={buttonVariants()}><Share2 className="h-4 w-4" /> Onboard a share</Link>
                  </>} />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label="Mounted shares" value={shares.mounted.length} hint="databases created from a share" icon={Share2} tone="blue" />
        <Kpi label="Inbound shares" value={shares.shares.length} hint="visible to your role" icon={Database} tone="slate" />
        <Kpi label="External pipelines" value={pipes.pipelines.filter((p) => p.status !== "RETIRED").length} hint="registered for ingestion" icon={Workflow} tone="emerald" />
        <Kpi label="Active pipelines" value={active} hint="loading on schedule" icon={CloudCog} tone="amber" />
      </div>
      <Panel title="External ingestion pipelines" icon={Workflow}
             action={<Link href="/dashboard/sources/new?branch=external" className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"><Plus className="h-3.5 w-3.5" /> Register</Link>}>
        {!pipes.deployed ? <NotDeployed what="The ingestion pipeline catalog" /> : <PipelineTable pipelines={pipes.pipelines} />}
      </Panel>
      <section className="border-t border-border/70 pt-6">
        <SourcesSection searchParams={searchParams} />
      </section>
    </div>
  );
}
