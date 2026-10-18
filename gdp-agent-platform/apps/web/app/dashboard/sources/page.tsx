import { CloudCog, Database, TableProperties, TriangleAlert, Workflow } from "lucide-react";
import { api, whoami } from "@/lib/api";
import { can } from "@/lib/types";
import type { SourcesOverview } from "@/lib/types";
import { RegisteredSources } from "@/components/governance/registered-sources";
import type { IngestionControl, Pipeline } from "@/lib/hub-types";
import type { Overview } from "@/lib/store-types";
import { scopedDomain } from "@/lib/domain-scope";
import { PageHeader } from "@/components/page-header";
import { Kpi, Panel } from "@/components/hub/kpi";
import { StoreNav } from "@/components/hub/store-nav";
import { SourcesSection } from "@/app/sources/sources-section";
import { PipelineTable } from "./pipeline-table";
import { IngestionTable } from "./ingestion-table";

const n = (v: number | null | undefined) => (v == null ? "-" : v.toLocaleString());

/** The Source Store: what is connected, what has landed and whether it is fresh. Profiles live in the Profiling Store. */
export default async function SourceHub({ searchParams }: { searchParams: { db?: string; schema?: string; domain?: string } }) {
  const [me, registeredSources] = await Promise.all([
    whoami(),
    api<SourcesOverview>("/api/sources/overview").catch(() => null),
  ]);
  const domain = scopedDomain(searchParams.domain);
  const [pipes, overview, control] = await Promise.all([
    api<{ pipelines: Pipeline[]; deployed: boolean }>("/api/hub/pipelines").catch(() => ({ pipelines: [] as Pipeline[], deployed: false })),
    api<Overview>(`/api/hub/overview${domain ? `?domain=${encodeURIComponent(domain)}` : ""}`).catch(() => null),
    api<{ controls: IngestionControl[]; deployed: boolean }>("/api/hub/ingestion-control").catch(() => ({ controls: [] as IngestionControl[], deployed: false })),
  ]);
  const src = overview?.sources;
  const fresh = overview?.freshness;
  const registered = pipes.pipelines.filter((p) => p.status !== "RETIRED");
  const active = registered.filter((p) => p.status === "ACTIVE").length;
  const issues = fresh ? fresh.failed_loads + fresh.stale : null;
  // External sources appear only once something has been onboarded externally (a SnapLogic configuration or a pipeline)
  const hasIngestion = control.deployed && control.controls.length > 0;
  const hasPipelines = pipes.deployed && pipes.pipelines.length > 0;
  const external = hasIngestion || hasPipelines;
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Asset store" title="Source Store"
                  description="Every source the platform reads: Snowflake shares and databases explored in place, and external systems landed into Snowflake through ingestion pipelines. See what is connected, what has landed and whether it is fresh." />
      <StoreNav current="sources" domain={searchParams.domain} />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" data-testid="source-kpis">
        <Kpi label="Sources connected" value={n(src?.total)} icon={Database} tone="blue"
             hint={src ? [`${n(src.shares)} shares`, `${n(src.databases)} databases`, src.other ? `${n(src.other)} files and other systems` : null, `${n(src.external)} pipelines`].filter(Boolean).join(" · ") : "source registry not available"} />
        <Kpi label="Ingestion pipelines" value={`${active} / ${registered.length}`} icon={Workflow} tone="emerald"
             hint={registered.length ? `active of registered${registered.length - active ? ` · ${registered.length - active} not loading yet` : ""}` : "none registered yet"} />
        <Kpi label="Landed tables" value={n(fresh?.tables)} icon={TableProperties} tone="violet"
             hint={fresh ? `${n(fresh.fresh)} loaded in the last 24 hours${fresh.oldest_hours != null ? ` · oldest ${Math.round(fresh.oldest_hours / 24)}d ago` : ""}` : "landing registry not available"} />
        <Kpi label="Ingestion issues" value={n(issues)} icon={TriangleAlert} tone={issues ? "rose" : "slate"} highlight={!!issues}
             hint={fresh ? `${n(fresh.failed_loads)} failed load${fresh.failed_loads === 1 ? "" : "s"} this week · ${n(fresh.stale)} table${fresh.stale === 1 ? "" : "s"} not loaded for 7 days` : "not available"} />
      </div>
      <section className="border-t border-border/70 pt-6">
        <SourcesSection searchParams={searchParams}
          pipelines={external ? (
            <Panel title="External sources" icon={CloudCog}>
              <div className="space-y-6" data-testid="external-sources">
                {hasIngestion && (
                  <div className="space-y-2">
                    <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Ingestion configurations · SnapLogic</p>
                    <IngestionTable controls={control.controls} />
                  </div>
                )}
                {hasPipelines && (
                  <div className="space-y-2">
                    <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Registered pipelines</p>
                    <PipelineTable pipelines={pipes.pipelines} />
                  </div>
                )}
              </div>
            </Panel>
          ) : null} />
      </section>
      <RegisteredSources sources={registeredSources?.sources ?? []} canEdit={can(me, "SOURCE.CONNECT") || can(me, "REQUEST.CHANGES")} />
    </div>
  );
}
