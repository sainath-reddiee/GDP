import Link from "next/link";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import { PipelineTabs } from "@/components/pipeline-tabs";
import type { GoldBuildRow } from "./actions";
import { NewGoldBuild } from "./new-build";

const STATUS: Record<string, string> = {
  DRAFT: "Choosing inputs", PROPOSED: "Gold model proposed", IN_REVIEW: "In review (Model Workbench)", APPROVED: "Approved",
  GENERATED: "Gold code generated", CANCELLED: "Cancelled",
};

/** Pipeline 2: gold builds. Each takes a domain's silver (consumer hubs, or post-MDM tables) to a reviewed GDP gold
 *  model (facts, dimensions, bridges) and its dbt models. */
export default async function GoldBuilds() {
  const [{ builds, deployed }, mdm] = await Promise.all([
    api<{ builds: GoldBuildRow[]; deployed: boolean }>("/api/gold/builds").catch(() => ({ builds: [] as GoldBuildRow[], deployed: false })),
    api<{ domains: { domain_id: string; domain_name: string; active_flag?: boolean }[] }>("/api/domains").catch(() => ({ domains: [] })),
  ]);
  return (
    <div className="space-y-5">
      <PageHeader eyebrow="Model and build" title="Pipelines"
                  description="Pipeline 2 runs on mastered data, decoupled from the runs: pick a domain's silver tables and get a GDP gold model (facts, dimensions, bridges for many-to-many), reviewed in the Model Workbench, then its dbt models."
                  actions={<NewGoldBuild domains={mdm.domains.filter((d) => d.active_flag !== false && d.domain_name !== "GDP")} />} />
      <PipelineTabs active="gold" />
      {!deployed && <p className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">Gold builds need migration V053 (Admin, Deploy).</p>}
      {deployed && !builds.length && (
        <p className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">No gold builds yet. Start one for a domain that consumes Company or Property (set on the domain&apos;s Lineage tab).</p>
      )}
      {!!builds.length && (
        <div className="overflow-x-auto rounded-xl border bg-card">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
              <tr><th className="px-3 py-2">Build</th><th className="px-3 py-2">Domain</th><th className="px-3 py-2">Status</th>
                <th className="px-3 py-2 text-right">Inputs</th><th className="px-3 py-2 text-right">Gold tables</th><th className="px-3 py-2">Updated</th></tr>
            </thead>
            <tbody>
              {builds.map((b) => (
                <tr key={b.build_id} className="border-t hover:bg-muted/30">
                  <td className="px-3 py-2"><Link href={`/gold/${b.build_id}`} className="font-medium text-primary hover:underline">{b.name}</Link>
                    <span className="block text-[11px] text-muted-foreground">by {b.created_by}</span></td>
                  <td className="px-3 py-2"><Link href={`/domains/${b.domain_id}?tab=lineage`} className="hover:underline">{b.domain_name}</Link></td>
                  <td className="px-3 py-2">{STATUS[b.status] ?? b.status}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{b.inputs}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{b.tables}</td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">{b.updated_at ?? b.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
