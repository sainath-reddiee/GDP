import Link from "next/link";
import { StoreNav } from "@/components/hub/store-nav";
import { scopedDomain } from "@/lib/domain-scope";
import { BarChart3, Bookmark, Fingerprint, ShieldAlert } from "lucide-react";
import { api, whoami } from "@/lib/api";
import { canAct } from "@/lib/types";
import { cn } from "@/lib/utils";
import type { ColumnAnnotation, ProfileSchedule, ProfileSearch } from "@/lib/hub-types";
import { PageHeader } from "@/components/page-header";
import { Kpi } from "@/components/hub/kpi";
import { NotDeployed } from "@/components/hub/states";
import { ProfileBrowser } from "./profile-browser";
import { SchedulesView } from "./schedules-view";
import { AnnotationsView } from "./annotations-view";

type Params = { domain?: string; table?: string; run?: string; tag?: string; q?: string; view?: string; bookmarked?: string; sort?: string; page?: string; section?: string };
const SECTIONS = [["profiles", "Profiles"], ["schedules", "Schedules"], ["annotations", "Annotations"]] as const;
const PAGE = 60;

export default async function ProfilingHub({ searchParams }: { searchParams: Params }) {
  const page = Math.max(0, Number(searchParams.page ?? 0) || 0);
  const qs = new URLSearchParams({ limit: String(PAGE), offset: String(page * PAGE), sort: searchParams.sort ?? "recent" });
  const scoped = { ...searchParams, domain: scopedDomain(searchParams.domain) };  // the top bar's domain unless the URL names one
  for (const [k, key] of [["domain", "domain"], ["table", "table"], ["run", "run_id"], ["tag", "tag"], ["q", "q"]] as const) {
    const v = scoped[k];
    if (v) qs.set(key, v);
  }
  if (searchParams.bookmarked === "1") qs.set("bookmarked", "true");
  const section = SECTIONS.find(([id]) => id === searchParams.section)?.[0] ?? "profiles";
  const [data, me, schedules, notes] = await Promise.all([
    api<ProfileSearch>(`/api/hub/profiles?${qs}`).catch(() => null),
    whoami(),
    section === "schedules" ? api<{ schedules: ProfileSchedule[] }>("/api/hub/profiles/schedules").catch(() => null) : Promise.resolve(null),
    api<{ annotations: ColumnAnnotation[] }>("/api/hub/profiles/annotations").catch(() => null),
  ]);
  const waiting = notes?.annotations.filter((a) => a.status === "PROPOSED").length ?? 0;
  const rows = data?.profiles ?? [];
  const nulls = rows.filter((r) => r.avg_null_pct != null);
  const avgNull = nulls.length ? nulls.reduce((n, r) => n + Number(r.avg_null_pct), 0) / nulls.length : null;
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Asset store" title="Profiling Store"
                  description="Every profiled table across every run: distributions, null rates, cardinality, semantic types and drift between passes. Compare any two passes, profile on a schedule, and annotate columns; approved annotations override the profiler and are proposed to the domain model." />
      <StoreNav current="profiling" domain={searchParams.domain} />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label="Tables profiled" value={data?.total ?? 0} hint="matching the filters" icon={BarChart3} tone="blue" />
        <Kpi label="Average null rate" value={avgNull == null ? "-" : `${avgNull.toFixed(1)}%`} hint="across the tables shown" icon={Fingerprint} tone="slate" />
        <Kpi label="PII columns" value={rows.reduce((n, r) => n + Number(r.pii_columns ?? 0), 0)} hint="masked in every profile" icon={ShieldAlert} tone="amber" />
        <Kpi label="Bookmarked" value={rows.filter((r) => r.bookmarked).length} hint="by you, on this page" icon={Bookmark} tone="emerald" />
      </div>
      <nav className="flex flex-wrap gap-1 border-b border-border/70" aria-label="Profiling sections">
        {SECTIONS.map(([id, label]) => (
          <Link key={id} href={id === "profiles" ? "/dashboard/profiling" : `/dashboard/profiling?section=${id}`} aria-current={section === id ? "page" : undefined}
                className={cn("-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition", section === id ? "border-primary font-medium text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}>
            {label}{id === "annotations" && waiting > 0 && <span className="rounded-full bg-amber-100 px-1.5 text-[10px] font-semibold text-amber-800">{waiting}</span>}
          </Link>
        ))}
      </nav>
      {section === "schedules" && (schedules ? <SchedulesView schedules={schedules.schedules} canRun={canAct(me, "PROFILE.RUN")} />
        : <NotDeployed what="Profiling schedules (CORE.PROFILE_SCHEDULE, V041)" />)}
      {section === "annotations" && (notes ? <AnnotationsView annotations={notes.annotations} me={(me?.user ?? "").toUpperCase()} canDecide={canAct(me, "KNOWLEDGE.EDIT")} />
        : <NotDeployed what="Column annotations (CORE.PROFILE_ANNOTATION, V041)" />)}
      {section === "profiles" && data && !data.deployed && <NotDeployed what="The tenant-wide profile catalog (CORE.DATA_PROFILES)" />}
      {section !== "profiles" ? null : !data ? <p className="text-sm text-destructive">The profile catalog could not be loaded.</p> : (
        <ProfileBrowser rows={rows} total={data.total} facets={data.facets} page={page} pageSize={PAGE}
                        filters={{ domain: searchParams.domain ?? "", table: searchParams.table ?? "", run: searchParams.run ?? "",
                                   tag: searchParams.tag ?? "", q: searchParams.q ?? "", bookmarked: searchParams.bookmarked === "1",
                                   sort: searchParams.sort ?? "recent" }}
                        view={searchParams.view === "grid" ? "grid" : "cards"} />
      )}
    </div>
  );
}
