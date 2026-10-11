import { api } from "@/lib/api";
import type { ModelContext, ModelIssue, ModelSpec } from "@/lib/hub-types";
import { PageHeader } from "@/components/page-header";
import { NotDeployed } from "@/components/hub/states";
import { ModelingWorkspace } from "./workspace";

type Design = { design: { design_id: string; version: number; status: string; spec: ModelSpec; issues: ModelIssue[] | null; branch: string | null; pr_url: string | null; repo_id: string | null } };

export default async function ModelingHub({ searchParams }: { searchParams: { design?: string; tables?: string } }) {
  const [ctx, design] = await Promise.all([
    api<ModelContext>("/api/hub/models/context").catch(() => null),
    searchParams.design ? api<Design>(`/api/hub/models/${encodeURIComponent(searchParams.design)}`).catch(() => null) : Promise.resolve(null),
  ]);
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Asset store" title="Modeling Store"
                  description="Map onto an existing domain, fork one, or create a new domain model. Design entities and relationships on the canvas, target Snowflake or Iceberg types, and publish the domain pack, DDL and dbt models to a feature branch." />
      {ctx && !ctx.deployed && <NotDeployed what="Model design storage (GOVERNANCE.MODEL_DESIGNS)" />}
      {!ctx ? <p className="text-sm text-destructive">The modeling context could not be loaded.</p> : (
        <ModelingWorkspace ctx={ctx} initialTables={(searchParams.tables ?? "").split(",").map((t) => t.trim()).filter(Boolean)}
                           initialDesign={design?.design ?? null} />
      )}
    </div>
  );
}
