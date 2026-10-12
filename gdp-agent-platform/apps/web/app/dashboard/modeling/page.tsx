import { api, whoami } from "@/lib/api";
import { StoreNav } from "@/components/hub/store-nav";
import { canAct } from "@/lib/types";
import type { DmListRow, TmListRow } from "@/lib/model-types";
import { scopedDomain } from "@/lib/domain-scope";
import type { ModelContext, ModelIssue, ModelSpec } from "@/lib/hub-types";
import type { DbtGeneration, RunModel } from "@/lib/store-types";
import { PageHeader } from "@/components/page-header";
import { NotDeployed } from "@/components/hub/states";
import { StoreTabs } from "@/components/hub/store-tabs";
import { ModelingWorkspace } from "./workspace";
import { DbtGenerations, RunModels } from "./run-assets";
import { ModelsList } from "./models-list";
import { TableModelsList } from "./table-models-list";

type Design = { design: { design_id: string; version: number; status: string; spec: ModelSpec; issues: ModelIssue[] | null; branch: string | null; pr_url: string | null; repo_id: string | null } };
type View = "models" | "tables" | "design" | "runs" | "dbt";
type Search = { design?: string; tables?: string; view?: string; domain?: string; q?: string; source?: string; layer?: string; run?: string };

export default async function ModelingHub({ searchParams }: { searchParams: Search }) {
  const view: View = searchParams.view === "runs" || searchParams.view === "dbt" || searchParams.view === "design" || searchParams.view === "tables" ? searchParams.view
    : searchParams.design || searchParams.tables ? "design" : "models";
  const domain = scopedDomain(searchParams.domain);
  const qs = domain ? `?domain=${encodeURIComponent(domain)}` : "";
  const tq = new URLSearchParams(Object.entries({ search: searchParams.q ?? "", source_name: searchParams.source ?? "", layer: searchParams.layer ?? "",
    origin_run_id: searchParams.run ?? "" }).filter(([, v]) => v)).toString();
  const [ctx, design, runModels, dbt, models, tableModels, me] = await Promise.all([
    view === "design" ? api<ModelContext>("/api/hub/models/context").catch(() => null) : Promise.resolve(null),
    view === "design" && searchParams.design ? api<Design>(`/api/hub/models/${encodeURIComponent(searchParams.design)}`).catch(() => null) : Promise.resolve(null),
    view === "runs" ? api<{ models: RunModel[]; deployed: boolean }>(`/api/hub/models/runs${qs}`).catch(() => null) : Promise.resolve(null),
    view === "dbt" ? api<{ generations: DbtGeneration[]; deployed: boolean }>(`/api/hub/dbt${qs}`).catch(() => null) : Promise.resolve(null),
    view === "models" ? api<{ models: DmListRow[]; deployed: boolean }>("/api/hub/domain-models").catch(() => null) : Promise.resolve(null),
    view === "tables" ? api<{ models: TmListRow[]; deployed: boolean }>(`/api/hub/table-models${tq ? `?${tq}` : ""}`).catch(() => null) : Promise.resolve(null),
    whoami(),
  ]);
  const rows = (models?.models ?? []).filter((r) => !domain || r.domain_name === domain);
  const tableRows = (tableModels?.models ?? []).filter((r) => !domain || r.domain_name === domain);
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Asset store" title="Modeling Store"
                  description="One governed model per domain: design, version, review and approve it here before anything downstream moves. Every table a run models (or you model from a source) stays here as a versioned table model you can reopen, edit and push to Git." />
      <StoreNav current="modeling" domain={searchParams.domain} />
      <StoreTabs base="/dashboard/modeling" current={view} keep={{ domain: searchParams.domain }}
                 tabs={[{ id: "models", label: "Domain models", count: models ? rows.length : null },
                        { id: "tables", label: "Table models", count: tableModels ? tableRows.length : null }, { id: "design", label: "Legacy designs" },
                        { id: "runs", label: "Run models", count: runModels?.models.length },
                        { id: "dbt", label: "dbt projects", count: dbt?.generations.length }]} />
      {view === "models" && (!models ? <p className="text-sm text-destructive">Domain models could not be loaded.</p>
        : !models.deployed ? <NotDeployed what="The Model Workbench (GOVERNANCE.DOMAIN_MODEL, V039)" />
        : <ModelsList rows={rows} canEdit={canAct(me, "MODEL.EDIT")} />)}
      {view === "tables" && (!tableModels ? <p className="text-sm text-destructive">Table models could not be loaded.</p>
        : !tableModels.deployed ? <NotDeployed what="The Table Model Store (GOVERNANCE.TABLE_MODEL, V044)" />
        : <TableModelsList rows={tableRows} canEdit={canAct(me, "MODEL.EDIT")}
                           filters={{ search: searchParams.q ?? "", source: searchParams.source ?? "", layer: searchParams.layer ?? "", run: searchParams.run ?? "", domain: domain ?? "" }} />)}
      {view === "design" && <>
        <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
          Legacy designs are kept for reference. A domain&apos;s governed model is changed in Domain models (draft, review, approval).
        </p>
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
