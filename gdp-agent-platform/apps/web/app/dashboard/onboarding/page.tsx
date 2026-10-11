import Link from "next/link";
import { Boxes } from "lucide-react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/hub/kpi";
import { OnboardingTiles } from "./tiles";

type DomainRow = { domain_id: string; domain_name: string; target_tables?: number; knowledge_items?: number };

export default async function OnboardingHub() {
  const { domains } = await api<{ domains: DomainRow[] }>("/api/domains").catch(() => ({ domains: [] as DomainRow[] }));
  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Onboard" title="Onboarding Hub"
                  description="Bring a business domain onto the platform from its domain pack and data contract. Application onboarding for upstream services is on the roadmap." />
      <OnboardingTiles />
      <Panel title="Domains on the platform" icon={Boxes} action={<Link href="/domains" className="text-xs font-medium text-primary hover:underline">Manage domains</Link>}>
        {!domains.length ? <p className="text-sm text-muted-foreground">No domains registered yet.</p> : (
          <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            {domains.map((d) => (
              <li key={d.domain_id}>
                <Link href={`/domains/${d.domain_id}`} className="block rounded-xl border border-border/80 px-3 py-2 transition hover:border-primary/30 hover:bg-accent/40">
                  <p className="font-mono text-sm font-medium">{d.domain_name}</p>
                  <p className="text-xs text-muted-foreground">{d.target_tables ?? 0} targets · {d.knowledge_items ?? 0} knowledge items</p>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
