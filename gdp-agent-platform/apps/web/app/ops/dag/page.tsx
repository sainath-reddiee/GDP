import Link from "next/link";
import { api, ApiError, whoami } from "@/lib/api";
import { can } from "@/lib/types";
import { displayDomain } from "@/lib/catalog-display";
import type { CodeRepo } from "../../code/actions";
import type { AirflowEnv, DagDetail, DagRun } from "../actions";
import { DagView } from "./dag-view";

type Params = { env?: string; dag?: string; run?: string };

async function load<T>(fn: () => Promise<T>): Promise<{ data: T; error: null; status: 0 } | { data: null; error: string; status: number }> {
  try {
    return { data: await fn(), error: null, status: 0 };
  } catch (e) {
    if (e instanceof ApiError) return { data: null, error: e.message, status: e.status };
    throw e;
  }
}

function Crumb({ env }: { env?: string }) {
  return (
    <p className="eyebrow">
      <Link href={env ? `/ops?${new URLSearchParams({ env })}` : "/ops"} className="hover:underline">Pipelines</Link>
    </p>
  );
}

/** One DAG: settings, runs timeline, a run's task grid and its task logs. Ids travel as query params since DAG and
 *  run ids hold characters a path segment cannot. */
export default async function DagPage({ searchParams }: { searchParams?: Params }) {
  const sp = searchParams ?? {};
  const me = await whoami();
  if (!can(me, "OPS.VIEW")) {
    return <div className="space-y-3"><Crumb /><p className="text-sm text-muted-foreground">Pipelines need the OPS.VIEW privilege.</p></div>;
  }
  if (!sp.env || !sp.dag) {
    return <div className="space-y-3"><Crumb /><p className="text-sm text-muted-foreground">Pick a DAG from the Pipelines list.</p></div>;
  }
  const canOperate = can(me, "OPS.OPERATE");
  const qs = new URLSearchParams({ env_id: sp.env, dag_id: sp.dag });
  const [detail, envs, domains, repos] = await Promise.all([
    load(() => api<{ dag: DagDetail; runs: DagRun[] }>(`/api/ops/dag?${qs}`)),
    load(() => api<{ envs: AirflowEnv[] }>("/api/ops/envs")),
    canOperate
      ? load(() => api<{ domains: { domain_id: string; domain_name: string; active_flag: boolean }[] }>("/api/domains"))
      : Promise.resolve(null),
    canOperate && can(me, "CODE.VIEW") ? load(() => api<{ repos: CodeRepo[] }>("/api/code/repos")) : Promise.resolve(null),
  ]);
  if (detail.error !== null) {
    return (
      <div className="space-y-3">
        <Crumb env={sp.env} />
        <h1 className="break-all font-mono text-xl">{sp.dag}</h1>
        <p role="alert" className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
          {detail.status === 404 ? "This DAG is not known in that environment. It may have been removed, or not polled yet." : `The DAG did not load: ${detail.error}`}
        </p>
      </div>
    );
  }
  const env = envs.data?.envs.find((e) => e.env_id === sp.env) ?? null;
  return (
    <DagView key={`${sp.env}:${sp.dag}`} envId={sp.env} envName={env?.name ?? sp.env} envEnabled={env?.enabled ?? true}
             initial={detail.data} initialRun={sp.run ?? ""} canOperate={canOperate}
             domains={(domains?.data?.domains ?? []).filter((d) => d.active_flag)
               .map((d) => ({ id: d.domain_id, name: displayDomain(d.domain_name) ?? d.domain_name }))}
             repos={(repos?.data?.repos ?? []).map((r) => ({ id: r.repo_id, name: r.name }))}
             lookupErrors={[domains?.error ? `Domains did not load: ${domains.error}` : "", repos?.error ? `Repositories did not load: ${repos.error}` : ""].filter(Boolean)} />
  );
}
