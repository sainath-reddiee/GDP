import { api } from "@/lib/api";
import { scopedDomain } from "@/lib/domain-scope";
import type { ModelContext, ModelIssue, ModelSpec } from "@/lib/hub-types";
import type { DbtGeneration, RunModel } from "@/lib/store-types";
import { PageHeader } from "@/components/page-header";
import { NotDeployed } from "@/components/hub/states";
import { StoreTabs } from "@/components/hub/store-tabs";
import { ModelingWorkspace } from "./workspace";
import { DbtGenerations, RunModels } from "./run-assets";

type Design = { design: { design_id: string; version: number; status: string; spec: ModelSpec; issues: ModelIssue[] | null; branch: string | null; pr_url: string | null; repo_id: string | null } };
type View = "design" | "runs" | "dbt";

export default async function ModelingHub({ searchParams }: { searchParams: { design?: string; tables?: string; view?: string; domain?: string } }) {
  const view: View = searchParams.view === "runs" || searchParams.view === "dbt" ? searchParams.view : "design";
  const domain = scopedDomain(searchParams.domain);
  const qs = domain ? `?domain=${encodeURIComponent(domain)}` : "";
  const [ctx, design, runModels, dbt] = await Promise.all([
    view === "design" ? api<ModelContext>("/api/hub/models/context").catch(() => null) : Promise.resolve(null),
    view === "design" && searchParams.design ? api<Design>(`/api/hub/models/${encodeURIComponent(searchParams.design)}`).catch(() => null) : Promise.resolve(null),
    view === "runs" ? api<{ models: RunModel[]; deployed: boolean }>(`/api/hub/models/runs${qs}`).catch(() => null) : Promise.resolve(null),
    view === "dbt" ? api<{ generations: DbtGeneration[]; deployed: boolean }>(`/api/hub/dbt${qs}`).catch(() => null) : Promise.resolve(null),
  ]);
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Asset store" title="Modeling Store"
                  description="Design domain models on the canvas, see every run's model design, and review the dbt project each run generated. dbt is read-only here: it is regenerated from the approved model and the STTM." />
      <StoreTabs base="/dashboard/modeling" current={view} keep={{ domain: searchParams.domain }}
                 tabs={[{ id: "design", label: "Design a model" }, { id: "runs", label: "Run models", count: runModels?.models.length },
                        { id: "dbt", label: "dbt projects", count: dbt?.generations.length }]} />
      {view === "design" && <>
        {ctx && !ctx.deployed && <NotDeployed what="Model design storage (GOVERNANCE.MODEL_DESIGNS)" />}
        {!ctx ? <p className="text-sm text-destructive">The modeling context could not be loaded.</p> : (
          <ModelingWorkspace ctx={ctx} initialTables={(searchParams.tables ?? "").split(",").map((t) => t.trim()).filter(Boolean)}
                             initialDesign={design?.design ?? null} />
        )}
      </>}
      {view === "runs" && (!runModels ? <p className="text-sm text-destructive">Run model designs could not be loaded{domain ? ` for domain ${domain}` : ""}.</p>
        : !runModels.deployed ? <NotDeployed what="Run model designs (MODELING.MODEL_DESIGN)" /> : <RunModels models={runModels.models} />)}
      {view === "dbt" && (!dbt ? <p className="text-sm text-destructive">dbt generations could not be loaded{domain ? ` for domain ${domain}` : ""}.</p>
        : !dbt.deployed ? <NotDeployed what="dbt generations (CODEGEN.DBT_GENERATION_REGISTRY)" /> : <DbtGenerations generations={dbt.generations} />)}
    </div>
  );
}
