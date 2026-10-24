import { redirect } from "next/navigation";
import { api, whoami } from "@/lib/api";
import type { CatalogDatabase, LandingDefaults, ShareList } from "@/lib/hub-types";
import { PageHeader } from "@/components/page-header";
import { OnboardWizard } from "../sources/new/wizard";
import { RequestHandoff, sourceNameFor } from "./request-handoff";
import { OnboardingShell, RailStep } from "./rail";

type DomainRow = { domain_id: string; domain_name: string; active_flag?: boolean };

/** Onboarding a source: the Data OS Administrator's alone (Enable). The steps run down a rail like a pipeline: each is
 *  one choice, and once made the other options step away until Change brings them back. Onboarding registers the
 *  source, lands it and starts its run in the chosen domain; the Data Designer takes it from there. A domain is
 *  onboarded in Enable too (/os/enable/new-domain); links from before keep working. */
export default async function Onboarding({ searchParams }: { searchParams: { type?: string; branch?: string; kind?: string; how?: string; import?: string; request?: string; item?: string; name?: string } }) {
  if (searchParams.type === "domain" || searchParams.import === "1") {
    const keep = new URLSearchParams(Object.entries({ how: searchParams.how, import: searchParams.import }).filter((e): e is [string, string] => !!e[1]));
    redirect(`/os/enable/new-domain${keep.size ? `?${keep}` : ""}`);
  }
  if (searchParams.type !== "source") {
    // no kind (or the application kind, not available): sources are what this page onboards
    const keep = new URLSearchParams(Object.entries(searchParams).filter((e): e is [string, string] => !!e[1] && e[0] !== "type"));
    redirect(`/dashboard/onboarding?type=source${keep.size ? `&${keep}` : ""}`);
  }
  const branch = searchParams.branch === "share" || searchParams.branch === "external" ? searchParams.branch : null;
  const sourceKind = searchParams.kind === "database" || searchParams.kind === "file" ? searchParams.kind : null;
  const [{ domains }, shares, databases, landing, me] = await Promise.all([
    api<{ domains: DomainRow[] }>("/api/domains").catch(() => ({ domains: [] as DomainRow[] })),
    api<ShareList>("/api/hub/sources/shares").catch(() => ({ shares: [], mounted: [] } as ShareList)),
    api<{ databases: CatalogDatabase[] }>("/api/sources/databases").then((r) => r.databases).catch(() => [] as CatalogDatabase[]),
    api<LandingDefaults>("/api/landing/targets").catch(() => null),
    whoami(),
  ]);
  const active = domains.filter((d) => d.active_flag !== false);

  return (
    <OnboardingShell>
      <div className="space-y-8">
        <PageHeader eyebrow="Enable · Data OS Administrator" title="Onboard a source" />
        {searchParams.request && <RequestHandoff requestId={searchParams.request} itemId={searchParams.item} />}

        <div>
          <RailStep n={1} title="Onboarding a source" done>
            <p className="max-w-3xl text-sm text-muted-foreground">
              Register the source, land its tables and start its run in the domain. The Data Designer profiles and models it from there.
            </p>
          </RailStep>
          <OnboardWizard key="source" initialBranch={branch} initialKind={sourceKind} shares={shares} databases={databases} landing={landing} user={me?.user ?? null}
                         initialName={searchParams.name ? sourceNameFor(searchParams.name) : ""}
                         domains={active.map((d) => ({ domain_id: d.domain_id, domain_name: d.domain_name }))} />
        </div>
      </div>
    </OnboardingShell>
  );
}
