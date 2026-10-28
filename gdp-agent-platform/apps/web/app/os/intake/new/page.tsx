import Link from "next/link";
import { Boxes, Clock, Crown, Database } from "lucide-react";
import type { RunSummary, SourceOverviewItem } from "@/lib/types";
import { stateLabel } from "@/lib/run-labels";
import { loadDomainModel, loadDomains, loadRuns, loadSources } from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import { Card, Empty, PageHeader, SourceError, Tag, ago, pretty } from "@/components/os/ui";
import { AutoForm } from "@/components/os/client";
import { SubmitRequest } from "@/components/os/request-ui";
import { SourceIntakeForm } from "@/components/os/source-intake";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const metadata = { title: "Request and define" };

const TYPES = [
  { key: "source", label: "Source", icon: Database, soon: false, hint: "Ask for a source system for a domain: attach one already on the platform, or request a new one from the Data OS Administrator",
    enhance: "More from a source already onboarded: say what to add; the Data Designer maps only the delta" },
  { key: "domain", label: "Domain", icon: Boxes, soon: false, hint: "Ask for a business domain that is not on the platform yet: the Data OS Administrator onboards its model, contract and memory",
    enhance: "Extend a domain already on the platform: the Data Designer adds what you name to its model and a model approver approves the new version" },
  { key: "product", label: "Data product", icon: Crown, soon: true, hint: "Governed Gold products from mastered Silver domains", enhance: "Change a data product already published" },
] as const;

const INTENTS = [{ key: "new", label: "New" }, { key: "enhance", label: "Enhance" }] as const;

/** How a request to extend a domain travels, in order. */
const ENHANCE_DOMAIN_ROUTE = [
  { t: "File the request", s: "Pick the domain and say what its model needs: entities, attributes, rules" },
  { t: "Data Designer extends the model", s: "In the domain's model workspace, reusing its memory and contract" },
  { t: "A model approver approves the new version", s: "The request moves on once a newer model version than today's is approved" },
  { t: "Its sources follow", s: "Sources named on the request are profiled and mapped into the extended model" },
];

/** How a request for a new domain travels, in order. */
const DOMAIN_ROUTE = [
  { t: "File the request", s: "Name the new domain and what the business gets from it" },
  { t: "Data OS Administrator onboards it", s: "Its model, contract and memory, from a pack, a draft or a design session; the request attaches the domain of that name on its own" },
  { t: "Data Designer approves the model", s: "The domain's model is reviewed before any run maps into it" },
  { t: "Its sources follow", s: "Sources named here go to the administrator too; once onboarded, the designer profiles and maps them" },
];


/** Registered, reachable and landed: attaching it skips the administrator. */
const sourceReady = (s: SourceOverviewItem) => s.health === "HEALTHY" && (s.staged_tables > 0 || (s.landed_tables ?? 0) > 0);

export default async function Intake({ searchParams }: { searchParams: { type?: string; intent?: string; domain?: string; source?: string | string[] } }) {
  const access = await guard("intakeNew");
  const persona = access.persona;
  const type = TYPES.find((t) => t.key === searchParams.type) ?? TYPES[0];
  const intent = searchParams.intent === "enhance" ? "enhance" : "new";
  const enhance = intent === "enhance";
  const INTENT = enhance ? "ENHANCE" : "NEW";
  const link = (t: string, i: string) => `/os/intake/new?${new URLSearchParams({ type: t, intent: i, ...(searchParams.domain ? { domain: searchParams.domain } : {}) })}`;
  const [domains, sources, runs] = await Promise.all([loadDomains(), loadSources(), loadRuns("sort=updated&limit=500")]);
  const picked = new Set(([] as string[]).concat(searchParams.source ?? []));
  const domain = domains.data.find((d) => d.domain_id === searchParams.domain) ?? null;
  // the domain's model today, for an Enhance domain request
  const model = domain && type.key === "domain" && enhance ? await loadDomainModel(domain.domain_id) : null;
  const entities = model?.data.diagram?.entities ?? [];

  const head = (
    <>
      <PageHeader eyebrow={`${persona.label} / Request and define`} title="Create an onboarding request"
                  subtitle="Request a new or enhanced source, domain, or data product." />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex flex-wrap gap-2" role="tablist" aria-label="What is requested">
          {TYPES.map((t) => (
            <Link key={t.key} href={link(t.key, intent)} role="tab" aria-selected={t.key === type.key} className={cn("os-btn", t.key === type.key && "os-btn-primary")}>
              <t.icon className="h-3.5 w-3.5" /> {t.label}{t.soon && <Tag className="ml-1">Soon</Tag>}
            </Link>
          ))}
        </div>
        <div className="inline-flex rounded-lg border border-[var(--os-line)] bg-[var(--os-subtle)] p-0.5" role="tablist" aria-label="New or enhance" data-testid="intake-intent">
          {INTENTS.map((i) => (
            <Link key={i.key} href={link(type.key, i.key)} role="tab" aria-selected={i.key === intent}
                  className={cn("rounded-md px-3 py-1.5 text-[12px] font-bold", i.key === intent ? "bg-[var(--os-surface)] shadow-[var(--os-shadow)]" : "os-muted")}>{i.label}</Link>
          ))}
        </div>
      </div>
    </>
  );

  if (type.key === "product") {
    return (
      <>
        {head}
        <Card title="Data product requests" subtitle="Coming soon">
          <div data-testid="product-coming-soon"><Empty icon={Clock} title="Coming soon">
            Requesting a governed Gold data product from mastered Silver domains will open here. Until then, ask for the domains and sources it needs with a domain or source request.
          </Empty></div>
        </Card>
      </>
    );
  }

  if (type.key === "domain" && enhance) {
    // a domain already on the platform, extended: picked from the onboarded domains
    return (
      <>
        {head}
        <SourceError what="Domains" error={domains.error} />
        <div className="max-w-3xl">
          <Card title="Enhance a domain" subtitle={type.enhance}>
            <div data-testid="domain-enhance" className="grid gap-4">
              <AutoForm action="/os/intake/new">
                <input type="hidden" name="type" value="domain" />
                <input type="hidden" name="intent" value="enhance" />
                <label className="os-label" htmlFor="domain">Domain to enhance</label>
                <select id="domain" name="domain" defaultValue={domain?.domain_id ?? ""} className="os-input">
                  <option value="">Choose an onboarded domain...</option>
                  {domains.data.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
                </select>
              </AutoForm>
              {domain && entities.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 text-[11px]"><span className="os-faint">Its model today:</span>
                  {entities.map((e) => <Tag key={e.entity_name} tone={e.kind === "HUB" ? "green" : "neutral"}>{e.entity_name}</Tag>)}</div>
              )}
              <SubmitRequest
                type="DOMAIN"
                intent="ENHANCE"
                domainId={domain?.domain_id ?? null}
                domainName={domain?.domain_name ?? null}
                picked={[]}
                availableSources={sources.data.sources.map((s) => ({ source_system_id: s.source_system_id, source_name: s.source_system_name }))}
                previewing={!!access.preview}
              />
            </div>
          </Card>
        </div>
      </>
    );
  }

  if (type.key === "domain") {
    // a domain not on the platform yet: named, never picked from the domains that exist (those take source requests)
    return (
      <>
        {head}
        <div className="max-w-3xl">
          <Card title="New domain" subtitle={type.hint}>
            <div data-testid="domain-request">
              <SubmitRequest
                type="DOMAIN"
                domainId={null}
                domainName={null}
                picked={[]}
                availableSources={sources.data.sources.map((s) => ({ source_system_id: s.source_system_id, source_name: s.source_system_name }))}
                previewing={!!access.preview}
              />
            </div>
            <p className="os-faint mt-4 border-t border-[var(--os-line)] pt-3 text-[11.5px]">
              Domains are onboarded by the Data OS Administrator. For a domain already on the platform, <Link href={link("domain", "enhance")} className="underline">enhance it</Link> or file a <Link href={link("source", "new")} className="underline">source request</Link>.
            </p>
          </Card>
        </div>
      </>
    );
  }

  // the source form: the sources on the platform with the domains their runs feed, for grouping by domain
  const live = runs.data.runs.filter((r) => !r.is_archived && r.source_system_name);
  const feeds = (name: string) => {
    const byDomain = new Map<string, RunSummary[]>();
    for (const r of live) if (r.source_system_name === name && r.domain_name) byDomain.set(r.domain_name, [...(byDomain.get(r.domain_name) ?? []), r]);
    return [...byDomain.entries()].map(([d, list]) => ({ domain: d, runs: list.length, latest: stateLabel(list[0].current_state).toLowerCase() }));
  };
  return (
    <>
      {head}
      <SourceError what="Domains" error={domains.error} />
      <SourceError what="Sources" error={sources.error} />
      <SourceIntakeForm
        enhance={enhance} previewing={!!access.preview}
        initialDomain={domain?.domain_id ?? null} initialSources={[...picked]}
        domains={domains.data.map(({ domain_id, domain_name, description, target_tables, knowledge_items }) => ({ domain_id, domain_name, description, target_tables, knowledge_items }))}
        sources={sources.data.sources.map((x) => ({
          id: x.source_system_id, name: x.source_system_name, type: pretty(x.source_type), tables: x.table_count ?? null,
          ready: sourceReady(x), health: pretty(x.health).toLowerCase(), feeds: feeds(x.source_system_name),
        }))}
        runs={live.slice(0, 200).map((r) => ({ run_id: r.run_id, run_name: r.run_name, domain_name: r.domain_name ?? null,
          source_system_name: r.source_system_name ?? null, state: stateLabel(r.current_state), updated: ago(r.updated_at ?? r.created_at) }))} />
    </>
  );
}
