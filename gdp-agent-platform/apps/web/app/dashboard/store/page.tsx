import { redirect } from "next/navigation";
import { api, whoami } from "@/lib/api";
import { can } from "@/lib/types";
import type { Overview } from "@/lib/store-types";
import { scopedDomain } from "@/lib/domain-scope";
import { PageHeader } from "@/components/page-header";
import { STORES } from "@/components/shell/nav";
import { StoreCards } from "@/components/hub/store-cards";
import { StoreNav } from "@/components/hub/store-nav";

type Params = Record<string, string | undefined>;

/** The Store hub: every asset store in one place. `?tab=<store>` (sources, profiling, modeling, sttm, qa, quality) opens
 * that store's own page with the other parameters kept, so links into the hub land where they mean. */
export default async function StoreHub({ searchParams }: { searchParams: Params }) {
  const { tab, ...rest } = searchParams;
  const target = tab ? STORES.find((s) => s.id === tab.toLowerCase()) : null;
  if (target) {
    const qs = new URLSearchParams(Object.entries(rest).filter(([, v]) => !!v) as [string, string][]).toString();
    redirect(`${target.href}${qs ? `?${qs}` : ""}`);
  }
  const domain = scopedDomain(searchParams.domain);
  const [overview, me] = await Promise.all([
    api<Overview>(`/api/hub/overview${domain ? `?domain=${encodeURIComponent(domain)}` : ""}`).catch(() => null), whoami()]);
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Asset stores" title="Store"
                  description={`Every governed asset in one place: sources, profiles, domain models, mappings, QA packs and data quality checks${overview?.domain ? ` for ${overview.domain.domain_name}` : ""}. Open a store to work in it; each store keeps its own versions, reviews and Git history.`} />
      <StoreNav current={null} domain={searchParams.domain} />
      {!overview && <p className="text-sm text-destructive">Store summaries could not be loaded; the stores themselves are still available above.</p>}
      <StoreCards o={overview} domain={searchParams.domain} onboard={can(me, "SOURCE.CONNECT")} />
    </div>
  );
}
