import Link from "next/link";
import { StoreCrumb } from "@/components/hub/store-nav";
import { notFound } from "next/navigation";
import { ApiError, api, whoami } from "@/lib/api";
import { canAct } from "@/lib/types";
import type { DmComment, DmDetail } from "@/lib/model-types";
import { PageHeader } from "@/components/page-header";
import { StatusPill } from "@/components/hub/states";
import { ModelWorkbench } from "./workbench";

export default async function DomainModelPage({ params, searchParams }: { params: { modelId: string }; searchParams: { version?: string; tab?: string; import?: string; evolve?: string } }) {
  const id = encodeURIComponent(params.modelId);
  const qs = searchParams.version && /^\d+$/.test(searchParams.version) ? `?version=${searchParams.version}` : "";
  const [data, me] = await Promise.all([
    api<DmDetail>(`/api/hub/domain-models/${id}${qs}`).catch((e) => { if (e instanceof ApiError && e.status === 404) notFound(); throw e; }),
    whoami(),
  ]);
  const current = data.current;
  const comments = current
    ? await api<{ comments: DmComment[] }>(`/api/hub/domain-models/${id}/versions/${current.version}/comments`).then((r) => r.comments, () => [])
    : [];
  const m = data.model;
  // the approved version, to tell new columns (removable) from approved ones (retired, so the review shows them)
  const approvedSpec = current && m.approved_version && current.version !== m.approved_version
    ? await api<DmDetail>(`/api/hub/domain-models/${id}?version=${m.approved_version}`).then((r) => r.current?.spec ?? null, () => null)
    : current?.spec ?? null;
  return (
    <div className="space-y-5">
      <StoreCrumb store="modeling" trail={[{ label: "Domain models", href: "/os/design/modeling" }, { label: m.domain_name }]} />
      <PageHeader title={<span className="font-mono">{m.domain_name}</span>}
                  description={<>Canonical model of the {m.domain_name} domain
                    {m.approved_version ? <> · approved v{m.approved_version}</> : <> · not approved yet</>}
                    {data.open ? <> · v{data.open.version} {data.open.status === "IN_REVIEW" ? "in review" : "draft"}</> : null}
                    {m.forked_from_model_id ? <> · forked from <Link className="text-primary hover:underline" href={`/os/design/modeling/${m.forked_from_model_id}`}>its parent</Link> v{m.forked_from_version}</> : null}
                    {" "}· approvals by MODEL_APPROVER, four eyes</>}
                  actions={current ? <StatusPill status={current.status} /> : undefined} />
      <ModelWorkbench detail={data} comments={comments} me={(me?.user ?? "").toUpperCase()} initialTab={searchParams.tab ?? null}
                      canEdit={canAct(me, "MODEL.EDIT")} canApprove={canAct(me, "MODEL.APPROVE")} approvedSpec={approvedSpec}
                      autoImport={searchParams.import === "file"} autoEvolve={searchParams.evolve === "1"} />
    </div>
  );
}
