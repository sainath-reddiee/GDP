import Link from "next/link";
import { notFound } from "next/navigation";
import { ApiError, api, whoami } from "@/lib/api";
import { canAct } from "@/lib/types";
import type { DbtGenerationDetail } from "@/lib/store-types";
import { PageHeader } from "@/components/page-header";
import { StatusPill } from "@/components/hub/states";
import { DbtViewer } from "./viewer";

export default async function DbtGenerationPage({ params, searchParams }: { params: { generationId: string }; searchParams: { file?: string } }) {
  const id = encodeURIComponent(params.generationId);
  const [data, me] = await Promise.all([
    api<DbtGenerationDetail>(`/api/hub/dbt/${id}`).catch((e) => { if (e instanceof ApiError && e.status === 404) notFound(); throw e; }),
    whoami(),
  ]);
  const g = data.generation;
  const latest = data.latest ? null : data.versions[0] ?? null;
  return (
    <div className="space-y-6">
      <p className="eyebrow"><Link href="/dashboard/modeling?view=dbt" className="hover:underline">Modeling Store · dbt projects</Link></p>
      <PageHeader title={<span className="font-mono">{g.target_model ?? "dbt project"}</span>}
                  description={<>Generation v{g.generation_version} of <Link className="text-primary hover:underline" href={`/runs/${g.run_id}/dbt`}>{data.run?.run_name ?? "the run"}</Link>
                    {g.sttm_version ? ` · Data Mapping v${g.sttm_version}` : ""} · read-only, regenerated from the model and Data Mapping</>}
                  actions={<span className="flex items-center gap-2"><StatusPill status={g.generation_status} />{data.published && <StatusPill status="PUBLISHED" />}</span>} />
      {latest && (
        <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100">
          A newer generation exists (v{latest.generation_version}). <Link className="font-medium underline" href={`/dashboard/modeling/dbt/${latest.generation_id}`}>Open it</Link>.
        </p>
      )}
      <DbtViewer detail={data} initialFile={searchParams.file ?? null} canReview={canAct(me, "AI.USE") && data.latest} />
    </div>
  );
}
