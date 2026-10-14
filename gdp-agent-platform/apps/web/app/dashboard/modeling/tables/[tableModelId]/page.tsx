import Link from "next/link";
import { notFound } from "next/navigation";
import { StoreCrumb } from "@/components/hub/store-nav";
import { ApiError, api, whoami } from "@/lib/api";
import { canAct } from "@/lib/types";
import type { DmListRow, TmDetail } from "@/lib/model-types";
import { PageHeader } from "@/components/page-header";
import { StatusPill } from "@/components/hub/states";
import { TableWorkbench } from "./table-workbench";

export default async function TableModelPage({ params, searchParams }: { params: { tableModelId: string }; searchParams: { version?: string; tab?: string } }) {
  const id = encodeURIComponent(params.tableModelId);
  const qs = searchParams.version && /^\d+$/.test(searchParams.version) ? `?version=${searchParams.version}` : "";
  const [data, domains, me] = await Promise.all([
    api<TmDetail>(`/api/hub/table-models/${id}${qs}`).catch((e) => { if (e instanceof ApiError && e.status === 404) notFound(); throw e; }),
    api<{ models: DmListRow[] }>("/api/hub/domain-models").then((r) => r.models, () => [] as DmListRow[]),
    whoami(),
  ]);
  const m = data.model;
  return (
    <div className="space-y-5">
      <StoreCrumb store="modeling" trail={[{ label: "Table models", href: "/dashboard/modeling?view=tables" }, { label: m.table_name }]} />
      <PageHeader title={<span className="font-mono">{m.table_name}</span>}
                  description={<>Table model of <span className="font-mono">{m.source_name}</span> · {m.model_layer.toLowerCase()} layer
                    {m.domain_name ? <> · domain {m.domain_name}</> : null}
                    {m.origin_run_id ? <> · designed in run <Link className="text-primary hover:underline" href={`/runs/${m.origin_run_id}/modeling?tab=mapping`}>{m.origin_run_name ?? m.origin_run_id.slice(0, 8)}</Link></> : <> · created {m.origin.toLowerCase()}</>}
                    {m.linked_model_id ? <> · proposed to the <Link className="text-primary hover:underline" href={`/dashboard/modeling/${m.linked_model_id}?tab=changes`}>domain model</Link></> : null}</>}
                  actions={data.current ? <StatusPill status={data.current.status} /> : undefined} />
      <TableWorkbench detail={data} initialTab={searchParams.tab ?? null} canEdit={canAct(me, "MODEL.EDIT")} canAI={canAct(me, "AI.USE")}
                      domains={domains.map((d) => ({ domain_id: d.domain_id, domain_name: d.domain_name }))} />
    </div>
  );
}
