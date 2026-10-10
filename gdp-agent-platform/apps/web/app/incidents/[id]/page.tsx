import Link from "next/link";
import { api, ApiError, whoami } from "@/lib/api";
import { can, canAct } from "@/lib/types";
import type { JiraStatus } from "../../jira/actions";
import type { AirflowEnv } from "../../ops/actions";
import type { IncidentDetail } from "../actions";
import { IncidentView } from "./incident-view";

async function load<T>(fn: () => Promise<T>): Promise<{ data: T; error: null; status: 0 } | { data: null; error: string; status: number }> {
  try {
    return { data: await fn(), error: null, status: 0 };
  } catch (e) {
    if (e instanceof ApiError) return { data: null, error: e.message, status: e.status };
    throw e;
  }
}

function Crumb() {
  return <p className="eyebrow"><Link href="/incidents" className="hover:underline">Incidents</Link></p>;
}

/** One incident. `?ack=1` (the Teams card button) asks to acknowledge; it never acknowledges on load. */
export default async function IncidentPage({ params, searchParams }: { params: { id: string }; searchParams?: { ack?: string } }) {
  const me = await whoami();
  if (!can(me, "OPS.VIEW")) {
    return <div className="space-y-3"><Crumb /><p className="text-sm text-muted-foreground">Incidents need the OPS.VIEW privilege.</p></div>;
  }
  let id = params.id;
  try { id = decodeURIComponent(params.id); } catch { /* already decoded */ }
  const canJiraWrite = canAct(me, "JIRA.WRITE");
  const [detail, envs, jira] = await Promise.all([
    load(() => api<IncidentDetail>(`/api/ops/incidents/${encodeURIComponent(id)}`)),
    load(() => api<{ envs: AirflowEnv[] }>("/api/ops/envs")),
    canJiraWrite ? api<JiraStatus>("/api/jira/status").catch(() => null) : Promise.resolve(null),
  ]);
  if (detail.error !== null) {
    return (
      <div className="space-y-3">
        <Crumb />
        <p role="alert" className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
          {detail.status === 404 ? "This incident does not exist, or the incidents API is not deployed yet (migration V031)." : `The incident did not load: ${detail.error}`}
        </p>
      </div>
    );
  }
  const env = envs.data?.envs.find((e) => e.env_id === detail.data.incident.env_id) ?? null;
  return (
    <IncidentView key={id} initial={detail.data} envName={env?.name ?? detail.data.incident.env_id}
                  askAck={searchParams?.ack === "1"} canOperate={can(me, "OPS.OPERATE")} canAI={can(me, "AI.USE")} canCase={can(me, "CASE.WORK")}
                  jiraComment={canJiraWrite && !!jira?.ready && !!jira.connected}
                  jiraMe={jira?.connected?.display_name ?? me?.user ?? "you"} />
  );
}
