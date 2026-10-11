import Link from "next/link";
import { AppWindow, Boxes, Database, FileUp, Globe, PlugZap, Rocket, Snowflake } from "lucide-react";
import { api, whoami } from "@/lib/api";
import type { CatalogDatabase, LandingDefaults, ShareList } from "@/lib/hub-types";
import { displayDomain } from "@/lib/catalog-display";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/hub/kpi";
import { PackEditor } from "@/app/domains/pack-editor";
import { OnboardWizard } from "../sources/new/wizard";
import { ApplicationCard, DomainImportCard } from "./tiles";

type DomainRow = { domain_id: string; domain_name: string; active_flag?: boolean; target_tables?: number; knowledge_items?: number };
type Kind = "source" | "domain" | "application";

const KINDS: { kind: Kind; title: string; text: string; icon: typeof Database; soon?: boolean }[] = [
  { kind: "source", title: "Source", text: "A Snowflake share, a database, files or an external system to land and profile.", icon: Database },
  { kind: "domain", title: "Domain", text: "A business domain: its pack, contract, target models and knowledge.", icon: Boxes },
  { kind: "application", title: "Application", text: "An upstream application and its data products.", icon: AppWindow, soon: true },
];

const CONNECTORS = [
  { title: "Snowflake database", text: "Browse any database your role can read, profile in place.", icon: Snowflake, href: "/dashboard/sources?mode=snowflake" },
  { title: "Files and cloud storage", text: "CSV, Parquet or JSON uploads, S3, Azure Blob or GCS.", icon: FileUp, href: "/dashboard/sources?mode=external" },
  { title: "Oracle and other databases", text: "Guided Oracle connection, or register PostgreSQL, SQL Server, MySQL.", icon: Globe, href: "/dashboard/sources?mode=external" },
];

/** One place to bring something onto the platform: a source, a domain, or (soon) an application. */
export default async function Onboarding({ searchParams }: { searchParams: { type?: string; branch?: string } }) {
  const kind: Kind = searchParams.type === "domain" || searchParams.type === "application" ? searchParams.type : "source";
  const { domains } = await api<{ domains: DomainRow[] }>("/api/domains").catch(() => ({ domains: [] as DomainRow[] }));
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
    <div className="space-y-6">
      <PageHeader eyebrow="Onboarding" title="What are you onboarding?"
                  description="Choose what you are bringing onto the platform. Sources land and profile data; domains register the business model the data maps to." />

      <nav aria-label="Onboarding type" className="grid gap-3 md:grid-cols-3">
        {KINDS.map(({ kind: k, title, text, icon: Icon, soon }) => {
          const on = k === kind;
          return (
            <Link key={k} href={`/dashboard/onboarding?type=${k}`} aria-current={on ? "page" : undefined} data-testid={`onboard-${k}`}
                  className={cn("surface relative flex items-start gap-3 p-4 transition", on ? "border-primary/40 ring-2 ring-primary/15" : "hover:border-primary/25",
                    soon && !on && "border-dashed")}>
              <span className={cn("grid h-10 w-10 shrink-0 place-items-center rounded-xl",
                on ? "bg-primary text-primary-foreground" : soon ? "bg-muted text-muted-foreground" : "bg-accent text-accent-foreground")}>
                <Icon className="h-5 w-5" />
              </span>
              <span className="min-w-0">
                <span className="flex items-center gap-2 text-sm font-semibold">
                  {title}
                  {soon && <span className="rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-700">Coming soon</span>}
                </span>
                <span className="block text-xs text-muted-foreground">{text}</span>
              </span>
            </Link>
          );
        })}
      </nav>

      {kind === "source" && (
        <section className="space-y-5" aria-label="Source onboarding">
          <OnboardWizard initialBranch={searchParams.branch === "external" ? "external" : "share"} shares={shares}
                         databases={databases} landing={landing} user={me?.user ?? null}
                         domains={active.map((d) => ({ domain_id: d.domain_id, domain_name: d.domain_name }))} />
          <Panel title="Other ways to connect a source" icon={PlugZap}>
            <div className="grid gap-3 md:grid-cols-3">
              {CONNECTORS.map(({ title, text, icon: Icon, href }) => (
                <Link key={title} href={href} className="flex items-start gap-3 rounded-xl border border-border/80 p-3 transition hover:border-primary/30 hover:bg-accent/40">
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground"><Icon className="h-4 w-4" /></span>
                  <span><span className="block text-sm font-medium">{title}</span><span className="block text-xs text-muted-foreground">{text}</span></span>
                </Link>
              ))}
            </div>
          </Panel>
        </section>
      )}

      {kind === "domain" && (
        <section className="space-y-5" aria-label="Domain onboarding">
          <div className="grid gap-4 lg:grid-cols-2">
            <DomainImportCard />
            <article className="surface flex flex-col p-6">
              <span className="grid h-11 w-11 place-items-center rounded-2xl bg-violet-100 text-violet-700"><Rocket className="h-5 w-5" /></span>
              <h2 className="mt-4 text-lg font-semibold">No pack yet?</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Draft a pack from any contract document with AI below and review it before importing, or design the domain model on
                the canvas in the Modeling Store.
              </p>
              <Link href="/dashboard/modeling" className="mt-5 w-fit text-sm font-medium text-primary hover:underline">Open the Modeling Store</Link>
            </article>
          </div>
          <PackEditor domains={active.filter((d) => displayDomain(d.domain_name) && d.domain_name !== "GDP")
            .map((d) => ({ domain_id: d.domain_id, domain_name: d.domain_name, label: displayDomain(d.domain_name) ?? d.domain_name }))} />
          <Panel title="Domains on the platform" icon={Boxes} action={<Link href="/domains" className="text-xs font-medium text-primary hover:underline">Manage domains</Link>}>
            {!active.length ? <p className="text-sm text-muted-foreground">No domains registered yet.</p> : (
              <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                {active.map((d) => (
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
        </section>
      )}

      {kind === "application" && (
        <section aria-label="Application onboarding" className="max-w-3xl"><ApplicationCard /></section>
      )}
    </div>
  );
}
