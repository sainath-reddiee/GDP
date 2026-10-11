import { BarChart3, Bookmark, Fingerprint, ShieldAlert } from "lucide-react";
import { api } from "@/lib/api";
import type { ProfileSearch } from "@/lib/hub-types";
import { PageHeader } from "@/components/page-header";
import { Kpi } from "@/components/hub/kpi";
import { NotDeployed } from "@/components/hub/states";
import { ProfileBrowser } from "./profile-browser";

type Params = { domain?: string; table?: string; run?: string; tag?: string; q?: string; view?: string; bookmarked?: string; sort?: string; page?: string };
const PAGE = 60;

export default async function ProfilingHub({ searchParams }: { searchParams: Params }) {
  const page = Math.max(0, Number(searchParams.page ?? 0) || 0);
  const qs = new URLSearchParams({ limit: String(PAGE), offset: String(page * PAGE), sort: searchParams.sort ?? "recent" });
  for (const [k, key] of [["domain", "domain"], ["table", "table"], ["run", "run_id"], ["tag", "tag"], ["q", "q"]] as const) {
    const v = searchParams[k];
    if (v) qs.set(key, v);
  }
  if (searchParams.bookmarked === "1") qs.set("bookmarked", "true");
  const data = await api<ProfileSearch>(`/api/hub/profiles?${qs}`).catch(() => null);
  const rows = data?.profiles ?? [];
  const nulls = rows.filter((r) => r.avg_null_pct != null);
  const avgNull = nulls.length ? nulls.reduce((n, r) => n + Number(r.avg_null_pct), 0) / nulls.length : null;
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Asset store" title="Profiling Store"
                  description="Every profiled table across every run: distributions, null rates, cardinality, semantic types and drift between passes. Bookmark, re-profile, or take a table straight to STTM or modeling." />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label="Tables profiled" value={data?.total ?? 0} hint="matching the filters" icon={BarChart3} tone="blue" />
        <Kpi label="Average null rate" value={avgNull == null ? "-" : `${avgNull.toFixed(1)}%`} hint="across the tables shown" icon={Fingerprint} tone="slate" />
        <Kpi label="PII columns" value={rows.reduce((n, r) => n + Number(r.pii_columns ?? 0), 0)} hint="masked in every profile" icon={ShieldAlert} tone="amber" />
        <Kpi label="Bookmarked" value={rows.filter((r) => r.bookmarked).length} hint="by you, on this page" icon={Bookmark} tone="emerald" />
      </div>
      {data && !data.deployed && <NotDeployed what="The tenant-wide profile catalog (GOVERNANCE.DATA_PROFILES)" />}
      {!data ? <p className="text-sm text-destructive">The profile catalog could not be loaded.</p> : (
        <ProfileBrowser rows={rows} total={data.total} facets={data.facets} page={page} pageSize={PAGE}
                        filters={{ domain: searchParams.domain ?? "", table: searchParams.table ?? "", run: searchParams.run ?? "",
                                   tag: searchParams.tag ?? "", q: searchParams.q ?? "", bookmarked: searchParams.bookmarked === "1",
                                   sort: searchParams.sort ?? "recent" }}
                        view={searchParams.view === "grid" ? "grid" : "cards"} />
      )}
    </div>
  );
}
