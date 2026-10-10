import Link from "next/link";
import { Settings2 } from "lucide-react";
import { api, ApiError, whoami } from "@/lib/api";
import { can, canAct } from "@/lib/types";
import { PageHeader } from "@/components/page-header";
import type { AirflowEnv } from "../ops/actions";
import type { Incident, IncidentSummary, OpsTeam } from "./actions";
import { IncidentInbox } from "./incident-inbox";
import { parseNav, toFilters } from "./inbox-nav";

type Params = { status?: string; severity?: string; team?: string; env?: string; mine?: string; q?: string; dag?: string };

/** Only API errors become messages; anything else (a sign-out redirect included) keeps propagating. */
async function load<T>(fn: () => Promise<T>): Promise<{ data: T; error: null; status: 0 } | { data: null; error: string; status: number }> {
  try {
    return { data: await fn(), error: null, status: 0 };
  } catch (e) {
    if (e instanceof ApiError) return { data: null, error: e.message, status: e.status };
    throw e;
  }
}

const PAGE_SIZE = 50;

export default async function IncidentsPage({ searchParams }: { searchParams?: Params }) {
  const me = await whoami();
  const canManage = canAct(me, "INTEGRATION.MANAGE");
  const header = (
    <PageHeader eyebrow="Operate" title="Incidents"
                description="Airflow failures, late and long running DAGs, grouped and routed to a team. Acknowledge, assign and resolve them here; Jira and Teams follow."
                actions={canManage ? (
                  <Link href="/admin?section=integrations&view=incidents" className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-3 py-1.5 text-sm font-medium shadow-xs hover:bg-muted">
                    <Settings2 className="h-4 w-4" />Teams and routing
                  </Link>
                ) : undefined} />
  );
  if (!can(me, "OPS.VIEW")) {
    return (
      <div className="space-y-5">
        {header}
        <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">Incidents need the OPS.VIEW privilege. Ask a governance admin for a role that includes it.</p>
      </div>
    );
  }
  const nav = parseNav(searchParams ?? {});
  const [incidents, summary, envs, teams] = await Promise.all([
    load(() => api<{ incidents: Incident[]; total: number }>(`/api/ops/incidents?${new URLSearchParams(
      Object.entries({ ...toFilters(nav), limit: PAGE_SIZE }).filter(([, v]) => v !== undefined && v !== "" && v !== false).map(([k, v]) => [k, String(v)]))}`)),
    load(() => api<IncidentSummary>("/api/ops/incidents/summary")),
    load(() => api<{ envs: AirflowEnv[] }>("/api/ops/envs")),
    load(() => api<{ teams: OpsTeam[] }>("/api/ops/teams")),
  ]);
  if (incidents.error !== null && incidents.status === 404) {
    return (
      <div className="space-y-5">
        {header}
        <p role="alert" className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">Incidents need the latest deploy (migration V031).</p>
      </div>
    );
  }
  return (
    <div className="space-y-5">
      {header}
      <IncidentInbox initial={nav} initialRows={incidents.data?.incidents ?? null} initialTotal={incidents.data?.total ?? 0}
                     initialError={incidents.error} summary={summary.data} summaryError={summary.error}
                     envs={envs.data?.envs ?? null} envsError={envs.error} teams={teams.data?.teams ?? []}
                     teamsError={teams.error && teams.status !== 404 ? teams.error : null}
                     canOperate={can(me, "OPS.OPERATE")} canManage={canManage} pageSize={PAGE_SIZE} />
    </div>
  );
}
