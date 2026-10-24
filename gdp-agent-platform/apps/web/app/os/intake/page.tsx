import Link from "next/link";
import { Bot, Boxes, CheckCircle2, CircleDashed, Clock, Crown, Database, GitMerge, Layers, Route, Wrench } from "lucide-react";
import type { RunSummary, SourceOverviewItem } from "@/lib/types";
import { stateLabel } from "@/lib/run-labels";
import { loadDomainModel, loadDomains, loadRuns, loadSources } from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import { ActivityRow, Card, Empty, PageHeader, SourceError, Tag, ago, pretty } from "@/components/os/ui";
import { AutoForm } from "@/components/os/client";
import { SubmitRequest } from "@/components/os/request-ui";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const metadata = { title: "Request and define" };

const TYPES = [
  { key: "source", label: "Source", icon: Database, soon: false, hint: "Ask for a source system for a domain: attach one already on the platform, or request a new one from the Data OS Administrator",
    enhance: "More from a source already registered: name the tables or scope to add; the run that onboards it into the domain picks them up" },
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

const REQUEST_TYPE = { source: "SOURCE", domain: "DOMAIN", product: "PRODUCT" } as const;

/** Registered, reachable and landed: attaching it skips the administrator. */
const sourceReady = (s: SourceOverviewItem) => s.health === "HEALTHY" && (s.staged_tables > 0 || (s.landed_tables ?? 0) > 0);

function SourceOption({ s, checked, feeding }: { s: SourceOverviewItem; checked: boolean; feeding?: RunSummary[] }) {
  const ready = sourceReady(s);
  return (
    <label className={cn("flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-[12px] transition os-hover",
      checked ? "border-[var(--os-emerald)] bg-[var(--os-green-bg)]" : "border-[var(--os-line)]")}>
      <input type="checkbox" name="source" value={s.source_system_id} defaultChecked={checked} className="accent-[var(--os-forest)]" />
      <span className="min-w-0 flex-1"><span className="block truncate font-bold">{s.source_system_name}</span>
        <span className="os-faint block truncate text-[10.5px]">
          {feeding?.length ? `${feeding.length} run${feeding.length === 1 ? "" : "s"} · latest ${stateLabel(feeding[0].current_state).toLowerCase()}`
            : `${pretty(s.source_type)} · ${s.table_count ?? "?"} table${s.table_count === 1 ? "" : "s"}`} · {ready ? "ready" : pretty(s.health).toLowerCase()}
        </span></span>
      <span className={cn("h-2 w-2 shrink-0 rounded-full", ready ? "bg-[var(--os-leaf)]" : "bg-[#dfa94b]")} title={ready ? "Ready: registered, reachable and landed" : pretty(s.health)} />
    </label>
  );
}

export default async function Intake({ searchParams }: { searchParams: { type?: string; intent?: string; domain?: string; source?: string | string[] } }) {
  const access = await guard("intake");
  const persona = access.persona;
  const type = TYPES.find((t) => t.key === searchParams.type) ?? TYPES[0];
  const intent = searchParams.intent === "enhance" ? "enhance" : "new";
  const enhance = intent === "enhance";
  const INTENT = enhance ? "ENHANCE" : "NEW";
  const link = (t: string, i: string) => `/os/intake?${new URLSearchParams({ type: t, intent: i, ...(searchParams.domain ? { domain: searchParams.domain } : {}) })}`;
  const [domains, sources, runs] = await Promise.all([loadDomains(), loadSources(), loadRuns("sort=updated&limit=150")]);
  const picked = new Set(([] as string[]).concat(searchParams.source ?? []));
  const domain = domains.data.find((d) => d.domain_id === searchParams.domain) ?? null;
  // the sources already feeding the domain: those its runs read from, newest run first
  const domainRuns = domain ? await loadRuns(`domain_id=${encodeURIComponent(domain.domain_id)}&sort=updated&limit=200`) : null;
  const feeding = new Map<string, RunSummary[]>();
  for (const r of domainRuns?.data.runs ?? []) {
    if (r.is_archived || !r.source_system_name) continue;
    feeding.set(r.source_system_name, [...(feeding.get(r.source_system_name) ?? []), r]);
  }
  const attached = sources.data.sources.filter((s) => feeding.has(s.source_system_name));
  // every other registered source, the ready ones first: attaching one of them still skips the administrator
  const elsewhere = sources.data.sources.filter((s) => !feeding.has(s.source_system_name))
    .sort((a, b) => Number(sourceReady(b)) - Number(sourceReady(a)) || a.source_system_name.localeCompare(b.source_system_name));
  const chosen = sources.data.sources.filter((s) => picked.has(s.source_system_id));
  const model = domain ? await loadDomainModel(domain.domain_id) : null;
  const priorRuns = runs.data.runs.filter((r) => !r.is_archived && (!domain || r.domain_name === domain.domain_name)
    && (chosen.length === 0 || chosen.some((s) => s.source_system_name === r.source_system_name)));
  const notReady = chosen.filter((s) => s.health !== "HEALTHY" || (s.staged_tables === 0 && !(s.landed_tables ?? 0)));
  const entities = model?.data.diagram?.entities ?? [];

  const head = (
    <>
      <PageHeader eyebrow={`${persona.label} / Request and define`} title="Create an onboarding request"
                  subtitle="One intake for a source, a domain or a data product, new or enhanced. Link what already exists so the agents reuse it instead of re-entering it." />
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
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
          <Card title="Enhance a domain" subtitle={type.enhance}>
            <div data-testid="domain-enhance" className="grid gap-4">
              <AutoForm action="/os/intake">
                <input type="hidden" name="type" value="domain" />
                <input type="hidden" name="intent" value="enhance" />
                <label className="os-label" htmlFor="domain">Domain to enhance</label>
                <select id="domain" name="domain" defaultValue={domain?.domain_id ?? ""} className="os-input">
                  <option value="">Choose an onboarded domain...</option>
                  {domains.data.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}{d.description ? ` · ${d.description.slice(0, 60)}` : ""}</option>)}
                </select>
              </AutoForm>
              {domain && entities.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 text-[11px]"><span className="os-faint">Its model today:</span>
                  {entities.map((e) => <Tag key={e.entity_name} tone={e.kind === "HUB" ? "green" : "neutral"}>{e.entity_name}</Tag>)}</div>
              )}
              <SubmitRequest type="DOMAIN" intent="ENHANCE" domainId={domain?.domain_id ?? null} domainName={domain?.domain_name ?? null} picked={[]} previewing={!!access.preview} />
            </div>
          </Card>
          <Card title="Where the request goes" subtitle="Routed by the platform, step by step">
            <ol className="flex flex-col gap-2.5 text-[12px]">
              {ENHANCE_DOMAIN_ROUTE.map((x, i) => (
                <li key={x.t} className="flex gap-2.5">
                  <span className="os-faint grid h-6 w-6 shrink-0 place-items-center rounded-full bg-[var(--os-neutral-bg)] text-[11px] font-extrabold">{i + 1}</span>
                  <span><b>{x.t}</b><span className="os-faint block text-[11px]">{x.s}</span></span>
                </li>
              ))}
            </ol>
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
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
          <Card title="New domain" subtitle={type.hint}>
            <div data-testid="domain-request">
              <SubmitRequest type="DOMAIN" domainId={null} domainName={null} picked={[]} previewing={!!access.preview} />
            </div>
            <p className="os-faint mt-4 border-t border-[var(--os-line)] pt-3 text-[11.5px]">
              Domains are onboarded by the Data OS Administrator. For a domain already on the platform, <Link href={link("domain", "enhance")} className="underline">enhance it</Link> or file a <Link href={link("source", "new")} className="underline">source request</Link>.
            </p>
          </Card>
          <Card title="Where the request goes" subtitle="Routed by the platform, step by step">
            <ol className="flex flex-col gap-2.5 text-[12px]">
              {DOMAIN_ROUTE.map((x, i) => (
                <li key={x.t} className="flex gap-2.5">
                  <span className="os-faint grid h-6 w-6 shrink-0 place-items-center rounded-full bg-[var(--os-neutral-bg)] text-[11px] font-extrabold">{i + 1}</span>
                  <span><b>{x.t}</b><span className="os-faint block text-[11px]">{x.s}</span></span>
                </li>
              ))}
            </ol>
          </Card>
        </div>
      </>
    );
  }

  return (
    <>
      {head}
      <SourceError what="Domains" error={domains.error} />
      <SourceError what="Sources" error={sources.error} />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Card title={`${enhance ? "Enhance" : "New"} source`} subtitle={enhance ? type.enhance : type.hint}>
            <AutoForm action="/os/intake" className="grid gap-4">
              <input type="hidden" name="type" value={type.key} />
              <input type="hidden" name="intent" value={intent} />
              <div>
                <label className="os-label" htmlFor="domain">Business domain</label>
                <select id="domain" name="domain" defaultValue={domain?.domain_id ?? ""} className="os-input">
                  <option value="">Choose an onboarded domain...</option>
                  {domains.data.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}{d.description ? ` · ${d.description.slice(0, 60)}` : ""}</option>)}
                </select>
              </div>
              {(
                <div className="grid gap-3" data-testid="attach-sources">
                  {domain && (
                    <fieldset data-testid="sources-feeding">
                      <legend className="os-label">Already feeding {domain.domain_name}</legend>
                      {attached.length ? (
                        <div className="grid gap-1.5 sm:grid-cols-2">
                          {attached.map((s) => <SourceOption key={s.source_system_id} s={s} checked={picked.has(s.source_system_id)} feeding={feeding.get(s.source_system_name)} />)}
                        </div>
                      ) : <p className="os-faint text-[11.5px]">No registered source feeds {domain.domain_name} yet: attach one below, or request a new one.</p>}
                    </fieldset>
                  )}
                  <fieldset data-testid="sources-registered">
                    <legend className="os-label">{domain ? "Registered and could be attached" : "Registered sources"}</legend>
                    {elsewhere.length ? (
                      <div className="grid max-h-[260px] gap-1.5 overflow-auto pr-1 sm:grid-cols-2">
                        {elsewhere.map((s) => <SourceOption key={s.source_system_id} s={s} checked={picked.has(s.source_system_id)} />)}
                      </div>
                    ) : <p className="os-faint text-[11.5px]">{sources.data.sources.length ? "Every registered source already feeds this domain." : "No source is registered on the platform yet."}</p>}
                  </fieldset>
                  <p className="os-faint text-[11px]">{enhance
                    ? "Pick the registered source to widen; name the tables or scope to add in the request below. A source not on the platform yet is a new source request."
                    : "A ready source (green) is registered, reachable and landed, so the request goes straight to the Data Designer. None fits? Name it under Request a new source below and the Data OS Administrator onboards it."}</p>
                  {!enhance && domain && chosen.some((s) => feeding.has(s.source_system_name)) && (
                    <p className="os-banner os-banner-blue text-[11.5px]" data-testid="suggest-enhance">
                      {chosen.filter((s) => feeding.has(s.source_system_name)).map((s) => s.source_system_name).join(", ")} already feed{chosen.filter((s) => feeding.has(s.source_system_name)).length === 1 ? "s" : ""} {domain.domain_name}.{" "}
                      Asking for more from {chosen.filter((s) => feeding.has(s.source_system_name)).length === 1 ? "it" : "them"}? <Link className="font-bold underline" href={`${link("source", "enhance")}&${chosen.map((s) => `source=${encodeURIComponent(s.source_system_id)}`).join("&")}`}>Enhance instead</Link>.
                    </p>
                  )}
                </div>
              )}
            </AutoForm>
          </Card>

          <Card title="Request details" subtitle="Filing the request routes it: the platform checks what exists and sends it to whoever acts first">
            <SubmitRequest type={REQUEST_TYPE[type.key]} intent={INTENT} domainId={domain?.domain_id ?? null} domainName={domain?.domain_name ?? null}
                           picked={chosen.map((s) => ({ source_system_id: s.source_system_id, source_name: s.source_system_name }))}
                           previewing={!!access.preview} />
            <p className="os-faint mt-4 border-t border-[var(--os-line)] pt-3 text-[11.5px]">
              Sources are onboarded by the Data OS Administrator: they register the source, land it and start its run, and the request moves on to the Data Designer.
            </p>
          </Card>

          <Card title="Related workflows" subtitle={domain || chosen.length ? "Runs that already touch this domain or these sources" : "Pick a domain or a source to see what already exists"} pad={false}>
            {priorRuns.length && (domain || chosen.length) ? (
              <div className="os-divide">
                {priorRuns.slice(0, 6).map((r) => (
                  <Link key={r.run_id} href={`/os/build?run=${r.run_id}`} className="flex items-center justify-between gap-3 px-5 py-2.5 os-hover">
                    <span className="min-w-0"><span className="block truncate text-[12.5px] font-bold">{r.run_name}</span>
                      <span className="os-faint text-[11px]">{r.domain_name ?? "No domain"} · {r.source_system_name ?? "no source"} · {ago(r.updated_at ?? r.created_at)}</span></span>
                    <Tag tone={r.current_state === "COMPLETED" ? "green" : r.lifecycle === "FAILED" ? "red" : "blue"}>{stateLabel(r.current_state)}</Tag>
                  </Link>
                ))}
              </div>
            ) : <div className="p-5"><Empty icon={GitMerge} title={domain || chosen.length ? "Nothing yet: this would be a first" : "No selection"} /></div>}
          </Card>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          <Card title="Linked asset preview" subtitle="Relationships are kept, not re-keyed">
            {domain || chosen.length ? (
              <div className="flex flex-col items-center gap-2">
                <div className="flex flex-wrap justify-center gap-1.5">
                  {chosen.length ? chosen.map((s) => <Tag key={s.source_system_id} tone="blue"><Database className="h-3 w-3" />{s.source_system_name}</Tag>) : <Tag>No source linked</Tag>}
                </div>
                <div className="os-faint text-[11px]">feeds</div>
                <div className="rounded-xl border-2 border-[var(--os-emerald)] bg-[var(--os-green-bg)] px-4 py-2 text-center">
                  <div className="flex items-center gap-1.5 text-[13px] font-extrabold text-[var(--os-green-ink)]"><Layers className="h-4 w-4" />{domain?.domain_name ?? "No domain chosen"}</div>
                  {domain && <div className="os-muted text-[11px]">{domain.target_tables} tables · {domain.knowledge_items} memory · {pretty(domain.mdm_role) || "domain"}</div>}
                </div>
                {entities.length > 0 && (
                  <>
                    <div className="os-faint text-[11px]">into</div>
                    <div className="flex flex-wrap justify-center gap-1.5">
                      {entities.map((e) => <Tag key={e.entity_name} tone={e.kind === "HUB" ? "green" : "neutral"}>{e.entity_name}</Tag>)}
                    </div>
                  </>
                )}
              </div>
            ) : <Empty icon={Layers} title="Nothing linked yet">Choose a domain and its sources.</Empty>}
          </Card>

          <Card title="Intake assistant" subtitle="What already exists, read from the platform">
            <div className="os-divide">
              <ActivityRow tone={domain ? "green" : "neutral"} icon={domain ? CheckCircle2 : CircleDashed} title={domain ? `Reuses the ${domain.domain_name} domain` : "No domain chosen"}
                           sub={domain ? `${entities.length || domain.target_tables} modelled tables and ${domain.knowledge_items} memory records (patterns, rules, glossary) are reused` : "A new domain is onboarded with its own model and contract"} />
              {chosen.map((s) => {
                const ok = s.health === "HEALTHY" && (s.staged_tables > 0 || (s.landed_tables ?? 0) > 0);
                return (
                  <ActivityRow key={s.source_system_id} tone={ok ? "green" : "amber"} icon={ok ? CheckCircle2 : Wrench} title={`${s.source_system_name}: ${ok ? "ready" : "needs enablement"}`}
                               sub={ok ? `${s.staged_tables} tables staged, profiled ${ago(s.last_profiled_at) || "earlier"}; the Admin step is skipped` : `${pretty(s.health)}${s.health_detail ? `: ${s.health_detail}` : ""}; routed to the Data OS Administrator`} />
                );
              })}
              {(domain || chosen.length > 0) && (
                <ActivityRow tone="blue" icon={Bot} title={`${priorRuns.length} related run${priorRuns.length === 1 ? "" : "s"}`}
                             sub={priorRuns.length ? "Earlier decisions and memory from these runs inform the new mapping" : "No earlier run to learn from"} />
              )}
            </div>
          </Card>

          <Card title="Autonomous routing" subtitle="Where the request goes next, decided by readiness">
            <ol className="flex flex-col gap-2.5 text-[12px]">
              {[
                { t: "Check linked assets", s: "Domain, sources and existing runs resolved", done: !!domain || chosen.length > 0 },
                { t: notReady.length ? "Route to Administrator" : "Skip enablement", s: notReady.length ? `${notReady.map((s) => s.source_system_name).join(", ")} need access or landing` : "Every linked source is already connected and landed", done: chosen.length > 0 },
                { t: "Route design to Data Designer", s: entities.length ? `Reuse the approved ${domain?.domain_name} model; approve mappings` : "Profile, model and map", done: !!domain },
                { t: "Generate and validate", s: "Build agent, then independent review before a pull request", done: false },
              ].map((x, i) => (
                <li key={x.t} className="flex gap-2.5">
                  <span className={cn("grid h-6 w-6 shrink-0 place-items-center rounded-full text-[11px] font-extrabold", x.done ? "bg-[var(--os-green-bg)] text-[var(--os-green-ink)]" : "bg-[var(--os-neutral-bg)] os-faint")}>{x.done ? <CheckCircle2 className="h-3.5 w-3.5" /> : i + 1}</span>
                  <span><b>{x.t}</b><span className="os-faint block text-[11px]">{x.s}</span></span>
                </li>
              ))}
            </ol>
            <div className="os-subtle mt-3 flex items-center gap-2 rounded-lg p-2.5 text-[11.5px]"><Route className="h-3.5 w-3.5 text-[var(--os-emerald)]" />No separate forms per persona: the work and its links travel with the workflow.</div>
          </Card>
        </div>
      </div>
    </>
  );
}
