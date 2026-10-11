import { CheckCircle2, ClipboardList, Clock3, Gauge, SquareTerminal } from "lucide-react";
import { StoreNav } from "@/components/hub/store-nav";
import { api, whoami } from "@/lib/api";
import { canAct } from "@/lib/types";
import { scopedDomain } from "@/lib/domain-scope";
import type { PackRow, PackSummary, RunTest, RunTestSummary } from "@/lib/store-types";
import { StoreTabs } from "@/components/hub/store-tabs";
import { RunTestsView } from "./run-tests";
import { PageHeader } from "@/components/page-header";
import { Kpi } from "@/components/hub/kpi";
import { NotDeployed } from "@/components/hub/states";
import { QaStoreView } from "./qa-store-view";

type Params = { domain?: string; pack?: string; new?: string; view?: string };

export default async function QaStorePage({ searchParams }: { searchParams: Params }) {
  const domain = scopedDomain(searchParams.domain);
  const qs = domain ? `?domain=${encodeURIComponent(domain)}` : "";
  const view = searchParams.view === "tests" ? "tests" : "packs";
  const [list, summary, domains, me, runTests] = await Promise.all([
    api<{ packs: PackRow[]; deployed: boolean }>(`/api/qa-packs${qs}`).catch(() => null),
    api<PackSummary>(`/api/qa-packs/summary${qs}`).catch(() => null),
    api<{ domains: { domain_id: string; domain_name: string; active_flag?: boolean }[] }>("/api/domains").then((r) => r.domains, () => []),
    whoami(),
    view === "tests" ? api<{ tests: RunTest[]; summary: RunTestSummary; deployed: boolean }>(`/api/qa/run-tests${qs}`).catch(() => null) : Promise.resolve(null),
  ]);
  const activeDomains = domains.filter((d) => d.active_flag !== false).map(({ domain_id, domain_name }) => ({ domain_id, domain_name }));
  const packs = list?.packs ?? [];
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="QA Store" title="Domain test packages"
                  description="Deterministic SQL assertions owned by a domain: reconciliations, orphan keys, duplicate business keys and joins that must resolve. Every change is a versioned draft; the approved version runs in pipelines and feeds the AI's QA context." />
      <StoreNav current="qa" domain={searchParams.domain} />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label="Approved packs" value={summary?.approved_packs ?? 0} hint={`${summary?.packs ?? 0} packs in all`} icon={ClipboardList} tone="blue" />
        <Kpi label="Live rules" value={summary?.rules ?? 0} hint={`across ${summary?.domains ?? 0} domain${summary?.domains === 1 ? "" : "s"} · ${summary?.run_tests ?? 0} tests saved in runs`} icon={SquareTerminal} tone="violet" />
        <Kpi label="Waiting for approval" value={summary?.pending ?? 0} hint="submitted versions" icon={Clock3} tone="amber" highlight={!!summary?.pending} />
        <Kpi label="Latest pass rate" value={summary?.pass_rate == null ? "-" : `${summary.pass_rate}%`} hint="last execution of each approved pack" icon={summary?.pass_rate != null && summary.pass_rate >= 95 ? CheckCircle2 : Gauge} tone="emerald" />
      </div>
      <StoreTabs base="/dashboard/qa-store" current={view} keep={{ domain: searchParams.domain }}
                 tabs={[{ id: "packs", label: "Domain packs", count: summary?.packs ?? null }, { id: "tests", label: "Run tests", count: summary?.run_tests ?? null }]} />
      {view === "tests" && (!runTests ? <p className="text-sm text-destructive">Run tests could not be loaded{domain ? ` for domain ${domain}` : ""}.</p>
        : !runTests.deployed ? <NotDeployed what="Saved QA tests (CONTRACT.QA_TEST_CASE)" />
        : <RunTestsView tests={runTests.tests} summary={runTests.summary} packs={packs} domains={activeDomains} scope={domain}
                        canPromote={canAct(me, "PACKAGE.EDIT")} canCase={canAct(me, "CASE.WORK")} />)}
      {view === "packs" && list && !list.deployed && <NotDeployed what="The QA Store (GOVERNANCE.DOMAIN_QA_PACKS, V037)" />}
      {view === "packs" && (!list ? <p className="text-sm text-destructive">The QA Store could not be loaded{domain ? ` for domain ${domain}` : ""}.</p> : (
        <QaStoreView packs={packs} domains={activeDomains}
                     scope={domain} initialPack={searchParams.pack ?? null} openNew={searchParams.new === "1"}
                     canEdit={canAct(me, "PACKAGE.EDIT")} canApprove={canAct(me, "PACKAGE.APPROVE")} canRun={canAct(me, "QA.EDIT")} />
      ))}
    </div>
  );
}
