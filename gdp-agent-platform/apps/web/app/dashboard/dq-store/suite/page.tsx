import { StoreCrumb } from "@/components/hub/store-nav";
import { notFound } from "next/navigation";
import { ApiError, api, whoami } from "@/lib/api";
import { canAct } from "@/lib/types";
import type { DqSuiteDetail } from "@/lib/store-types";
import { PageHeader } from "@/components/page-header";
import { StatusPill } from "@/components/hub/states";
import { SuiteView } from "./suite-view";

export default async function DqSuitePage({ searchParams }: { searchParams: { table?: string; version?: string; tab?: string } }) {
  const table = (searchParams.table ?? "").toUpperCase();
  if (table.split(".").length !== 3) notFound();
  const qs = new URLSearchParams({ table, ...(searchParams.version && /^\d+$/.test(searchParams.version) ? { version: searchParams.version } : {}) });
  const [data, me] = await Promise.all([
    api<DqSuiteDetail>(`/api/dq/suites/one?${qs}`).catch((e) => { if (e instanceof ApiError && e.status === 404) notFound(); throw e; }),
    whoami(),
  ]);
  const s = data.suite;
  return (
    <div className="space-y-5">
      <StoreCrumb store="quality" trail={[{ label: "Suites", href: `/dashboard/dq-store?table=${encodeURIComponent(table)}` }, { label: table.split(".").pop() ?? table }]} />
      <PageHeader title={<span className="font-mono">{table.split(".").pop()}</span>}
                  description={<><span className="font-mono">{table}</span>
                    {s?.domain_name ? <> · {s.domain_name}</> : null}
                    {s?.approved_version ? <> · running approved v{s.approved_version}</> : <> · no approved version yet</>}
                    {data.open ? <> · v{data.open.version} {data.open.status === "IN_REVIEW" ? "in review" : "draft"}</> : null}
                    {" "}· approvals by DQ_APPROVER, four eyes</>}
                  actions={data.current ? <StatusPill status={data.current.status} /> : undefined} />
      <SuiteView table={table} detail={data} me={(me?.user ?? "").toUpperCase()} initialTab={searchParams.tab ?? null}
                 canEdit={canAct(me, "SODA.EDIT")} canApprove={canAct(me, "SODA.APPROVE")} />
    </div>
  );
}
