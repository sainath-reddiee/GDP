import Link from "next/link";
import { ArrowLeft, Boxes, Inbox } from "lucide-react";
import type { RequestDetail } from "@/lib/os/requests";
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

/** Opened for a domain request (Enable, "Onboard this domain"): which request it is for, the name to give the domain
 *  (a domain of that name is attached to the request on its own) and the way back. */
async function DomainHandoff({ requestId }: { requestId: string }) {
  const r = await api<RequestDetail>(`/api/onboarding-requests/${encodeURIComponent(requestId)}`).catch(() => null);
  if (!r) {
    return <div role="status" className="surface mb-4 flex items-center gap-2 p-4 text-sm text-muted-foreground"><Inbox className="h-4 w-4" /> The request this was opened for could not be read. Onboard the domain, then link it from the request.</div>;
  }
  return (
    <div role="status" data-testid="domain-handoff" className="surface mb-4 flex flex-wrap items-start justify-between gap-3 border-primary/30 bg-accent/40 p-4">
      <div className="min-w-0 text-sm">
        <p className="font-semibold"><Inbox className="mr-1.5 inline h-4 w-4" />Onboarding {r.proposed_domain ? `the ${r.proposed_domain} domain` : "a domain"} for the request &ldquo;{r.title}&rdquo;</p>
        <p className="mt-1 text-muted-foreground">
          Filed by {r.requested_by}.{r.proposed_domain ? <> Name the domain <span className="font-mono font-medium text-foreground">{r.proposed_domain.toUpperCase()}</span> and the request attaches it on its own.</> : " Link the domain from the request once it exists."}
          {r.business_outcome ? ` What it is for: ${r.business_outcome}` : ""}
        </p>
      </div>
      <Link href={`/os/requests/${r.request_id}`} className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-xs font-medium hover:bg-accent"><ArrowLeft className="h-3.5 w-3.5" /> Back to the request</Link>
    </div>
  );
}

/** Onboarding a domain: its model, contract and memory, from files, a draft or a design session. The Data OS
 *  Administrator's alone (DOMAIN.ONBOARD, Enable); data owners ask for a domain on Intake, and stewards keep editing
 *  domains once they exist (Design, Domains). */
export default async function NewDomain({ searchParams }: { searchParams: { how?: string; import?: string; request?: string } }) {
  await guard("newDomain");
  const how = searchParams.import === "1" ? "import"
    : searchParams.how === "import" || searchParams.how === "draft" || searchParams.how === "design" ? searchParams.how : null;
  const { domains } = await api<{ domains: DomainRow[] }>("/api/domains").catch(() => ({ domains: [] as DomainRow[] }));
  const active = domains.filter((d) => d.active_flag !== false);

  return (
    <OnboardingShell>
      <div className="space-y-8">
        <PageHeader eyebrow="Enable / Data OS Administrator" title="Onboard domain" />
        {searchParams.request && <DomainHandoff requestId={searchParams.request} />}
        <div>
          <RailStep n={1} title="Onboarding a domain" done>
            <p className="max-w-3xl text-sm text-muted-foreground">
              A domain brings its model, contract and memory. Once it exists, the Data Designer approves its model and its sources are onboarded from Enable.
            </p>
          </RailStep>
          <DomainOnboarding key="domain" initial={how} openImport={searchParams.import === "1"}
                            domains={active.filter((d) => displayDomain(d.domain_name) && d.domain_name !== "GDP")
                              .map((d) => ({ domain_id: d.domain_id, domain_name: d.domain_name, label: displayDomain(d.domain_name) ?? d.domain_name }))} />
        </div>
        <FormatsMatrix />
        <Panel title="Domains on the platform" icon={Boxes} action={<Link href="/os/knowledge?view=domains" className="text-xs font-medium text-primary hover:underline">Manage domains</Link>}>
          {!active.length ? <p className="text-sm text-muted-foreground">No domains registered yet.</p> : (
            <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
              {active.map((d) => (
                <li key={d.domain_id}>
                  <Link href={`/os/knowledge/domains/${d.domain_id}`} className="block rounded-lg border px-3 py-2 transition hover:border-primary/40 hover:bg-accent/40">
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
