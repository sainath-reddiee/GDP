import { AlertTriangle, GitMerge, GitPullRequest, Layers } from "lucide-react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import { Kpi } from "@/components/hub/kpi";
import { NotDeployed } from "@/components/hub/states";
import { SourceControlView, type PullRequestRow } from "./source-control-view";

type Params = { kind?: string; state?: string; domain?: string; q?: string };
type Listing = {
  prs: PullRequestRow[]; counts: { total: number; open: number; merged: number; attention: number; no_changes: number };
  deployed: boolean; publisher_ready: boolean; kinds?: Record<string, string>;
};

export default async function SourceControlPage({ searchParams }: { searchParams: Params }) {
  const qs = new URLSearchParams(Object.entries(searchParams).filter(([, v]) => !!v) as [string, string][]).toString();
  const data = await api<Listing>(`/api/source-control/prs${qs ? `?${qs}` : ""}`).catch(() => null);
  const c = data?.counts;
  const base = (state?: string) => `/dashboard/source-control${state ? `?state=${state}` : ""}`;
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Governance" title="Source control"
                  description="Every pull request the app opened for an approved asset version: domain models, data quality suites, QA packs and STTM mappings. The repository's owners merge them in GitHub; their state is read back here and onto each asset." />
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi href={base("open")} label="Open" value={c?.open ?? 0} hint="waiting for review or merge in GitHub" icon={GitPullRequest} tone="blue" />
        <Kpi href={base("merged")} label="Merged" value={c?.merged ?? 0} hint="the repository holds the approved version" icon={GitMerge} tone="emerald" />
        <Kpi href={base("attention")} label="Needs attention" value={c?.attention ?? 0} hint="publishing failed or closed without merging"
             icon={AlertTriangle} tone="rose" highlight={!!c?.attention} />
        <Kpi href={base()} label="All pull requests" value={c?.total ?? 0} hint={`${c?.no_changes ?? 0} already in the repository`} icon={Layers} tone="slate" />
      </div>
      {!data ? <p className="text-sm text-destructive">Pull requests could not be loaded.</p>
        : !data.deployed ? <NotDeployed what="Source control (CORE.GIT_PR, V043)" />
        : <SourceControlView prs={data.prs} kinds={data.kinds ?? {}} publisherReady={data.publisher_ready}
                             filters={{ kind: searchParams.kind ?? "", state: searchParams.state ?? "", q: searchParams.q ?? "", domain: searchParams.domain ?? "" }} />}
    </div>
  );
}
