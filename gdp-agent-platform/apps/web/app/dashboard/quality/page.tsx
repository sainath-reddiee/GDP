import Link from "next/link";
import { Activity, BadgeCheck, FlaskConical, Siren } from "lucide-react";
import { api } from "@/lib/api";
import type { Execution, GateRun, Incident, QualitySummary, Suites } from "@/lib/hub-types";
import { PageHeader } from "@/components/page-header";
import { Kpi } from "@/components/hub/kpi";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Executions, GatesTable, IncidentsTable, SuitesView } from "./views";

const TABS = [
  { key: "executions", label: "Test executions" },
  { key: "gates", label: "Readiness gates" },
  { key: "suites", label: "Suites and expectations" },
  { key: "incidents", label: "Jira incidents" },
] as const;
type Tab = (typeof TABS)[number]["key"];

export default async function QualityCenter({ searchParams }: { searchParams: { tab?: string; kind?: string; q?: string } }) {
  const tab: Tab = TABS.find((t) => t.key === searchParams.tab)?.key ?? "executions";
  const kind = searchParams.kind === "soda" || searchParams.kind === "qa" ? searchParams.kind : "all";
  const summary = await api<QualitySummary>("/api/hub/quality/summary?days=30").catch(() => null);
  let body: React.ReactNode = null;
  if (tab === "executions") {
    const qs = new URLSearchParams({ kind, ...(searchParams.q ? { q: searchParams.q } : {}) });
    const data = await api<{ executions: Execution[] }>(`/api/hub/quality/executions?${qs}`).catch(() => ({ executions: [] as Execution[] }));
    body = <Executions rows={data.executions} kind={kind} />;
  } else if (tab === "gates") {
    const data = await api<{ runs: GateRun[] }>("/api/hub/quality/gates").catch(() => ({ runs: [] as GateRun[] }));
    body = <GatesTable runs={data.runs} />;
  } else if (tab === "suites") {
    const data = await api<Suites>("/api/hub/quality/suites").catch(() => ({ qa_suites: [], check_packs: [] } as Suites));
    body = <SuitesView suites={data} />;
  } else {
    const data = await api<{ incidents: Incident[] }>("/api/hub/quality/incidents").catch(() => ({ incidents: [] as Incident[] }));
    body = <IncidentsTable rows={data.incidents} />;
  }
  const s = summary;
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Quality" title="QA and Data Quality Center"
                  description="Soda scans, Great Expectations suites, QA test runs, readiness gates and Jira incidents in one place. Fix and sign off from the run or the QA workspace."
                  actions={<Link href="/qa" className={buttonVariants({ variant: "outline" })}><FlaskConical className="h-4 w-4" /> Open the QA workspace</Link>} />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label="Data quality health" value={s?.data_quality.avg_health == null ? "-" : `${Math.round(Number(s.data_quality.avg_health))}`}
             hint={`${s?.data_quality.scans ?? 0} scans, last 30 days`} icon={Activity} tone="emerald" />
        <Kpi label="Failed checks" value={s?.data_quality.failed_checks ?? 0} hint={`${s?.data_quality.warned_checks ?? 0} warnings`} icon={BadgeCheck}
             tone={s?.data_quality.failed_checks ? "rose" : "slate"} highlight={!!s?.data_quality.failed_checks} />
        <Kpi label="QA pass rate" value={s?.qa.pass_rate == null ? "-" : `${s.qa.pass_rate}%`} hint={`${s?.qa.tests ?? 0} tests in ${s?.qa.runs ?? 0} runs`} icon={FlaskConical} tone="blue" />
        <Kpi label="Open incidents" value={s?.incidents.open ?? 0} hint="linked Jira issues not done" icon={Siren} tone={s?.incidents.open ? "amber" : "slate"} href="/dashboard/quality?tab=incidents" />
      </div>
      <nav className="flex gap-1 rounded-xl bg-muted/60 p-1" aria-label="Quality views">
        {TABS.map((t) => (
          <Link key={t.key} href={`/dashboard/quality?tab=${t.key}`} aria-current={tab === t.key ? "page" : undefined}
                className={cn("rounded-lg px-3 py-1.5 text-sm", tab === t.key ? "bg-card font-medium shadow-card" : "text-muted-foreground hover:text-foreground")}>{t.label}</Link>
        ))}
      </nav>
      {body}
    </div>
  );
}
