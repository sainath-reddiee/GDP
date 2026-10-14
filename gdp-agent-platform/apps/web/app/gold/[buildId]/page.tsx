import { api } from "@/lib/api";
import { PipelineTabs } from "@/components/pipeline-tabs";
import type { GoldBuild } from "../actions";
import { BuildView } from "./build-view";

export default async function GoldBuildPage({ params }: { params: { buildId: string } }) {
  const build = await api<GoldBuild>(`/api/gold/builds/${encodeURIComponent(params.buildId)}`);
  return (
    <div className="space-y-5">
      <PipelineTabs active="gold" />
      <BuildView build={build} />
    </div>
  );
}
