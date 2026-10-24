import Link from "next/link";
import { Boxes } from "lucide-react";
import { api } from "@/lib/api";
import { displayDomain } from "@/lib/catalog-display";
import { guard } from "@/lib/os/session";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/hub/kpi";
import { DomainOnboarding } from "@/app/dashboard/onboarding/domain-onboarding";
import { FormatsMatrix } from "@/app/dashboard/onboarding/domain-formats";
import { OnboardingShell, RailStep } from "@/app/dashboard/onboarding/rail";

export const dynamic = "force-dynamic";
export const metadata = { title: "Onboard a domain" };

type DomainRow = { domain_id: string; domain_name: string; active_flag?: boolean; target_tables?: number; knowledge_items?: number };

/** Onboarding a domain (Design): its model, contract and memory, from files, a draft or a design session. Data stewards
 *  and product owners (DOMAIN.EDIT) do this; sources are onboarded by the Data OS Administrator in Enable. */
export default async function NewDomain({ searchParams }: { searchParams: { how?: string; import?: string } }) {
  await guard("newDomain");
  const how = searchParams.import === "1" ? "import"
    : searchParams.how === "import" || searchParams.how === "draft" || searchParams.how === "design" ? searchParams.how : null;
  const { domains } = await api<{ domains: DomainRow[] }>("/api/domains").catch(() => ({ domains: [] as DomainRow[] }));
  const active = domains.filter((d) => d.active_flag !== false);

  return (
    <OnboardingShell>
      <div className="space-y-8">
        <PageHeader eyebrow="Design · Data steward" title="Onboard a domain" />
        <div>
          <RailStep n={1} title="Onboarding a domain" done>
            <p className="max-w-3xl text-sm text-muted-foreground">
              A domain brings its model, contract and memory. Its sources are onboarded by the Data OS Administrator: file a request on Intake for any the domain needs.
            </p>
          </RailStep>
          <DomainOnboarding key="domain" initial={how} openImport={searchParams.import === "1"}
                            domains={active.filter((d) => displayDomain(d.domain_name) && d.domain_name !== "GDP")
                              .map((d) => ({ domain_id: d.domain_id, domain_name: d.domain_name, label: displayDomain(d.domain_name) ?? d.domain_name }))} />
        </div>
        <FormatsMatrix />
        <Panel title="Domains on the platform" icon={Boxes} action={<Link href="/domains" className="text-xs font-medium text-primary hover:underline">Manage domains</Link>}>
          {!active.length ? <p className="text-sm text-muted-foreground">No domains registered yet.</p> : (
            <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
              {active.map((d) => (
                <li key={d.domain_id}>
                  <Link href={`/domains/${d.domain_id}`} className="block rounded-lg border px-3 py-2 transition hover:border-primary/40 hover:bg-accent/40">
                    <p className="font-mono text-sm font-medium">{d.domain_name}</p>
                    <p className="text-xs text-muted-foreground">{d.target_tables ?? 0} tables · {d.knowledge_items ?? 0} knowledge items</p>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </OnboardingShell>
  );
}
