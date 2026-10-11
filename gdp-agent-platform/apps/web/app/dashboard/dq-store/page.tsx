import { CalendarClock, ClipboardCheck, Gauge, Radar } from "lucide-react";
import { api, whoami } from "@/lib/api";
import { PendingChanges } from "@/components/governance/pending-changes";
import { canAct } from "@/lib/types";
import { scopedDomain } from "@/lib/domain-scope";
import type { DqCatalog, DqSummary } from "@/lib/store-types";
import { PageHeader } from "@/components/page-header";
import { Kpi } from "@/components/hub/kpi";
import { NotDeployed } from "@/components/hub/states";
import { DqStoreView } from "./dq-store-view";

type Params = { domain?: string; table?: string; status?: string; add?: string };

export default async function DqStorePage({ searchParams }: { searchParams: Params }) {
  const domain = scopedDomain(searchParams.domain);
  const qs = new URLSearchParams();
  if (domain) qs.set("domain", domain);
  if (searchParams.status) qs.set("status", searchParams.status);
  const [catalog, summary, me] = await Promise.all([
    api<DqCatalog>(`/api/dq/catalog?${qs}`).catch(() => null),
    api<DqSummary>(`/api/dq/summary${domain ? `?domain=${encodeURIComponent(domain)}` : ""}`).catch(() => null),
    whoami(),
  ]);
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Data Quality Store" title="Attribute checks catalog"
                  description="Declarative Soda and Great Expectations checks per table and column, managed outside any run. Seed them from a profile, promote a run's approved checks, backtest now, and schedule each table as a Snowflake task." />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label="Active checks in the store" value={summary?.checks ?? 0}
             hint={`on ${summary?.tables ?? 0} table${summary?.tables === 1 ? "" : "s"} · ${summary?.run_checks ?? 0} more approved in runs`} icon={ClipboardCheck} tone="blue" />
        <Kpi label="Pass rate" value={summary?.pass_rate == null ? "-" : `${summary.pass_rate}%`}
             hint={summary?.failed ? `${summary.failed} failing now` : "of checks with a last result"} icon={Gauge} tone={summary?.failed ? "rose" : "emerald"} highlight={!!summary?.failed} />
        <Kpi label="Profiled coverage" value={summary?.coverage == null ? "-" : `${summary.coverage}%`} hint="of profiled tables have checks" icon={Radar} tone="violet" />
        <Kpi label="Scheduled tables" value={summary?.scheduled_tables ?? 0} hint="each one a Snowflake task" icon={CalendarClock} tone="sky" />
      </div>
      <PendingChanges path="/api/dq/" what="data quality change" />
      {catalog && !catalog.deployed && <NotDeployed what="The Data Quality Store (GOVERNANCE.DQ_CHECKS_CATALOG, V037)" />}
      {!catalog ? <p className="text-sm text-destructive">The Data Quality Store could not be loaded{domain ? ` for domain ${domain}` : ""}.</p> : (
        <DqStoreView tables={catalog.tables} initialTable={searchParams.table ?? ""} status={searchParams.status ?? ""} openAdd={searchParams.add === "1"}
                     canEdit={canAct(me, "SODA.EDIT")} canRun={canAct(me, "PROFILE.RUN")} />
      )}
    </div>
  );
}
