import Link from "next/link";
import { notFound } from "next/navigation";
import { ApiError, api, whoami } from "@/lib/api";
import { canAct } from "@/lib/types";
import type { SttmContext, SttmEntry, SttmVersion } from "@/lib/hub-types";
import type { JoinGraph } from "@/components/sttm/join-plan-editor";
import { PageHeader } from "@/components/page-header";
import { StatusPill } from "@/components/hub/states";
import { PendingChanges } from "@/components/governance/pending-changes";
import { SttmWorkbench } from "./workbench";
import { SttmGit } from "./sttm-git";
import type { PullRequestRow } from "../../source-control/source-control-view";

const EMPTY_CONTEXT: SttmContext = { run_id: null, tables: [], columns: {}, profiles: {}, candidates: [], decisions: [] };

export default async function SttmDetail({ params, searchParams }: { params: { catalogId: string }; searchParams: { version?: string } }) {
  const id = encodeURIComponent(params.catalogId);
  const qs = searchParams.version && /^\d+$/.test(searchParams.version) ? `?version=${searchParams.version}` : "";
  const [data, context, preview, me, git] = await Promise.all([
    api<{ mapping: SttmEntry; versions: SttmVersion[] }>(`/api/hub/sttm/${id}${qs}`)
      .catch((e) => { if (e instanceof ApiError && e.status === 404) notFound(); throw e; }),
    api<SttmContext>(`/api/hub/sttm/${id}/context${qs}`).catch(() => EMPTY_CONTEXT),
    api<{ sql: string; join_graph: JoinGraph }>(`/api/hub/sttm/${id}/preview${qs}`).catch(() => null),
    whoami(),
    api<{ prs: PullRequestRow[]; deployed: boolean; publisher_ready: boolean; repos: { repo_id: string; name: string; branch: string | null }[] }>(
      `/api/source-control/prs?kind=STTM&asset=${id}`).catch(() => null),
  ]);
  const m = data.mapping;
  const latest = data.versions[0]?.version ?? m.version;
  const design = (m.table_design ?? {}) as { join_graph?: JoinGraph };
  const found = preview?.join_graph ?? design.join_graph;
  // a graph saved before the join planner, or the preview's fallback, has no unreachable or ambiguity lists
  const graph: JoinGraph = { driving_table: m.source_entities[0] ?? null, joins: [], ...found, unreachable: found?.unreachable ?? [], ambiguities: found?.ambiguities ?? [] };
  return (
    <div className="space-y-6">
      <p className="eyebrow"><Link href="/dashboard/sttm" className="hover:underline">STTM Store</Link></p>
      <PageHeader title={<span className="font-mono">{m.target_entity}</span>}
                  description={<>From {m.source_system ?? "source"} ({m.source_entities.join(", ") || "no source tables"}){m.domain_name ? ` · domain ${m.domain_name}` : ""}{m.run_id ? <> · <Link className="text-primary hover:underline" href={`/runs/${m.run_id}/sttm`}>producing run</Link></> : null} · changes go to STTM_APPROVER for approval</>}
                  actions={<StatusPill status={m.status} />} />
      <PendingChanges path={`/api/hub/sttm/${params.catalogId}/`} what="mapping change" />
      <SttmWorkbench entry={m} versions={data.versions} latest={latest} context={context ?? EMPTY_CONTEXT} graph={graph}
                     previewSql={preview?.sql ?? ""} canEdit={canAct(me, "STTM.EDIT")} />
      {git?.deployed && (
        <SttmGit catalogId={params.catalogId} version={m.version} status={m.status} canEdit={canAct(me, "STTM.EDIT")}
                 prs={git.prs} repos={git.repos} publisherReady={git.publisher_ready} />
      )}
    </div>
  );
}
