import Link from "next/link";
import { Boxes } from "lucide-react";
import { api, whoami } from "@/lib/api";
import type { CatalogDatabase, LandingDefaults, ShareList } from "@/lib/hub-types";
import { displayDomain } from "@/lib/catalog-display";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/hub/kpi";
import { OnboardWizard } from "../sources/new/wizard";
import { ApplicationCard } from "./tiles";
import { DomainOnboarding } from "./domain-onboarding";
import { FormatsMatrix } from "./domain-formats";
import { KindPicker, type Kind } from "./kind-picker";
import { PipelinePreview } from "./pipeline-preview";
import { OnboardingShell, RailStep, Reveal, RouteTrail } from "./rail";

type DomainRow = { domain_id: string; domain_name: string; active_flag?: boolean; target_tables?: number; knowledge_items?: number };

const KINDS: Kind[] = ["source", "domain", "application"];

/** One place to bring something onto the platform: a source, a domain, or (soon) an application. The steps run down a
 *  rail like a pipeline: each is one choice, and once made the other options step away until Change brings them back. */
export default async function Onboarding({ searchParams }: { searchParams: { type?: string; branch?: string; how?: string; import?: string } }) {
  const kind = KINDS.find((k) => k === searchParams.type) ?? null;
  const branch = searchParams.branch === "share" || searchParams.branch === "external" ? searchParams.branch : null;
  const how = searchParams.import === "1" ? "import"
    : searchParams.how === "import" || searchParams.how === "draft" || searchParams.how === "design" ? searchParams.how : null;
  const { domains } = kind && kind !== "application"
    ? await api<{ domains: DomainRow[] }>("/api/domains").catch(() => ({ domains: [] as DomainRow[] }))
    : { domains: [] as DomainRow[] };
  const active = domains.filter((d) => d.active_flag !== false);
  const [shares, databases, landing, me] = kind === "source"
    ? await Promise.all([
        api<ShareList>("/api/hub/sources/shares").catch(() => ({ shares: [], mounted: [] } as ShareList)),
        api<{ databases: CatalogDatabase[] }>("/api/sources/databases").then((r) => r.databases).catch(() => [] as CatalogDatabase[]),
        api<LandingDefaults>("/api/landing/targets").catch(() => null),
        whoami(),
      ])
    : [{ shares: [], mounted: [] } as ShareList, [] as CatalogDatabase[], null, null];

  return (
    <OnboardingShell>
      <div className="space-y-8">
        <PageHeader eyebrow="Build" title="Onboarding"><RouteTrail /></PageHeader>

        <div>
          <KindPicker value={kind} />

          {kind === "source" && (
            <OnboardWizard key="source" initialBranch={branch} shares={shares} databases={databases} landing={landing} user={me?.user ?? null}
                           domains={active.map((d) => ({ domain_id: d.domain_id, domain_name: d.domain_name }))} />
          )}

          {kind === "domain" && (
            <DomainOnboarding key="domain" initial={how} openImport={searchParams.import === "1"}
                              domains={active.filter((d) => displayDomain(d.domain_name) && d.domain_name !== "GDP")
                                .map((d) => ({ domain_id: d.domain_id, domain_name: d.domain_name, label: displayDomain(d.domain_name) ?? d.domain_name }))} />
          )}

          {kind === "application" && (
            <Reveal>
              <RailStep n={2} title="What comes next" last>
                <div className="max-w-3xl"><ApplicationCard /></div>
              </RailStep>
            </Reveal>
          )}
        </div>

        {!kind && <PipelinePreview />}

        {kind === "domain" && (
          <>
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
          </>
        )}
      </div>
    </OnboardingShell>
  );
}
