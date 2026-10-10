import Link from "next/link";
import { Settings2 } from "lucide-react";
import { api, ApiError, whoami } from "@/lib/api";
import { can, canAct } from "@/lib/types";
import { PageHeader } from "@/components/page-header";
import { cn } from "@/lib/utils";
import type { OpsTeam } from "../incidents/actions";
import type { AirflowEnv, DagRow, OpsSummary, Reliability } from "./actions";
import { OpsBoard, type OpsNav } from "./ops-board";
import { ReliabilityView } from "./reliability";

const PERIODS = [7, 30];

type Params = { env?: string; q?: string; state?: string; owner?: string; team?: string; view?: string; days?: string };

/** Only API errors become messages; anything else (a sign-out redirect included) keeps propagating. */
async function load<T>(fn: () => Promise<T>): Promise<{ data: T; error: null; status: 0 } | { data: null; error: string; status: number }> {
  try {
    return { data: await fn(), error: null, status: 0 };
  } catch (e) {
    if (e instanceof ApiError) return { data: null, error: e.message, status: e.status };
    throw e;
  }
}

function Tabs({ view, env }: { view: "dags" | "reliability"; env?: string }) {
  const tab = (id: "dags" | "reliability", label: string, href: string) => (
    <Link href={href} aria-current={view === id ? "page" : undefined}
          className={cn("-mb-px border-b-2 px-3 py-2 text-sm font-medium", view === id ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}>
      {label}</Link>
  );
  return (
    <nav className="flex gap-1 border-b" aria-label="Pipelines views">
      {tab("dags", "DAGs", env ? `/ops?${new URLSearchParams({ env })}` : "/ops")}
      {tab("reliability", "Reliability", "/ops?view=reliability")}
    </nav>
  );
}

export default async function OpsPage({ searchParams }: { searchParams?: Params }) {
  const me = await whoami();
  const access = { canOperate: can(me, "OPS.OPERATE"), canManage: canAct(me, "INTEGRATION.MANAGE") };
  const header = (
    <PageHeader eyebrow="Operate" title="Pipelines"
                description="Airflow DAG health from Amazon MWAA: what failed, what is running, how long runs take and how often they succeed. Open a DAG for its runs, task grid and logs."
                actions={access.canManage ? (
                  <Link href="/admin?section=integrations&view=airflow" className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-3 py-1.5 text-sm font-medium shadow-xs hover:bg-muted">
                    <Settings2 className="h-4 w-4" />Airflow environments
                  </Link>
                ) : undefined} />
  );
  if (!can(me, "OPS.VIEW")) {
    return (
      <div className="space-y-5">
        {header}
        <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">Pipelines need the OPS.VIEW privilege. Ask a governance admin for a role that includes it.</p>
      </div>
    );
  }
  const sp = searchParams ?? {};
  if (sp.view === "reliability") {
    const days = PERIODS.find((d) => String(d) === sp.days) ?? 7;
    const team = sp.team ?? "";
    const [rel, teams] = await Promise.all([
      load(() => api<Reliability>(`/api/ops/reliability?${new URLSearchParams({ days: String(days), ...(team ? { team_id: team } : {}) })}`)),
      load(() => api<{ teams: OpsTeam[] }>("/api/ops/teams")),
    ]);
    return (
      <div className="space-y-5">
        {header}
        <Tabs view="reliability" />
        <ReliabilityView initialDays={days} initialTeam={team} initial={rel.data} initialError={rel.error}
                         teams={(teams.data?.teams ?? []).map((t) => ({ id: t.team_id, name: t.name }))} />
      </div>
    );
  }
  const [envs, summary] = await Promise.all([
    load(() => api<{ envs: AirflowEnv[] }>("/api/ops/envs")),
    load(() => api<OpsSummary>("/api/ops/summary")),
  ]);
  if (envs.error !== null) {
    return (
      <div className="space-y-5">
        {header}
        <p role="alert" className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
          {envs.status === 404 ? "Pipelines need the latest deploy (migration V030)." : `Airflow environments did not load: ${envs.error}`}
        </p>
      </div>
    );
  }
  const list = envs.data.envs;
  const env = list.find((e) => e.env_id === sp.env)?.env_id ?? list.find((e) => e.enabled)?.env_id ?? list[0]?.env_id ?? "";
  const nav: OpsNav = { env, q: sp.q ?? "", state: sp.state ?? "", owner: sp.owner ?? "", team: sp.team ?? "" };
  const dags = env
    ? await load(() => api<{ dags: DagRow[] }>(`/api/ops/dags?${new URLSearchParams(Object.entries({
      env_id: env, q: nav.q, state: nav.state, owner: nav.owner, team_id: nav.team,
    }).filter(([, v]) => v))}`))
    : null;
  return (
    <div className="space-y-5">
      {header}
      <Tabs view="dags" env={env} />
      <OpsBoard envs={list} summary={summary.data} summaryError={summary.error} initial={nav}
                initialDags={dags?.data?.dags ?? null} initialError={dags?.error ?? null} access={access} />
    </div>
  );
}
