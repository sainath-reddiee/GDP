import Link from "next/link";
import { ArrowRight, Bot, Boxes, CheckCircle2, CircleDashed, Crown, Database, GitMerge, Layers, Route, Wrench } from "lucide-react";
import { stateLabel } from "@/lib/run-labels";
import { currentPersona, loadDomainModel, loadDomains, loadRuns, loadSources } from "@/lib/os/data";
import { ActivityRow, Btn, Card, Empty, PageHeader, SourceError, Tag, ago, pretty } from "@/components/os/ui";
import { AutoForm } from "@/components/os/client";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const metadata = { title: "Request and define" };

const TYPES = [
  { key: "source", label: "New source", icon: Database, start: "/dashboard/onboarding?type=source", hint: "Bring a source system onto the platform and map it to a domain" },
  { key: "domain", label: "New domain", icon: Boxes, start: "/dashboard/onboarding?type=domain", hint: "Define a new business domain: its model, contract and memory" },
  { key: "product", label: "New data product", icon: Crown, start: "/gold", hint: "Build a governed Gold product from mastered Silver domains" },
] as const;

export default async function Intake({ searchParams }: { searchParams: { type?: string; domain?: string; source?: string | string[] } }) {
  const persona = currentPersona();
  const type = TYPES.find((t) => t.key === searchParams.type) ?? TYPES[0];
  const [domains, sources, runs] = await Promise.all([loadDomains(), loadSources(), loadRuns("sort=updated&limit=150")]);
  const picked = new Set(([] as string[]).concat(searchParams.source ?? []));
  const domain = domains.data.find((d) => d.domain_id === searchParams.domain) ?? null;
  const chosen = sources.data.sources.filter((s) => picked.has(s.source_system_id));
  const model = domain ? await loadDomainModel(domain.domain_id) : null;
  const priorRuns = runs.data.runs.filter((r) => !r.is_archived && (!domain || r.domain_name === domain.domain_name)
    && (chosen.length === 0 || chosen.some((s) => s.source_system_name === r.source_system_name)));
  const notReady = chosen.filter((s) => s.health !== "HEALTHY" || (s.staged_tables === 0 && !(s.landed_tables ?? 0)));
  const entities = model?.data.diagram?.entities ?? [];
  const startHref = type.start;

  return (
    <>
      <PageHeader eyebrow={`${persona.label} / Request and define`} title="Create an onboarding request"
                  subtitle="One intake for a source, a domain or a data product. Link what already exists so the agents reuse it instead of re-entering it."
                  actions={<Btn href="/dashboard/onboarding">Classic onboarding</Btn>} />
      <div className="mb-4 flex flex-wrap gap-2" role="tablist">
        {TYPES.map((t) => (
          <Link key={t.key} href={`/os/intake?type=${t.key}`} role="tab" aria-selected={t.key === type.key} className={cn("os-btn", t.key === type.key && "os-btn-primary")}>
            <t.icon className="h-3.5 w-3.5" /> {t.label}
          </Link>
        ))}
      </div>
      <SourceError what="Domains" error={domains.error} />
      <SourceError what="Sources" error={sources.error} />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Card title={type.label} subtitle={type.hint}>
            <AutoForm action="/os/intake" className="grid gap-4">
              <input type="hidden" name="type" value={type.key} />
              <div>
                <label className="os-label" htmlFor="domain">Business domain</label>
                <select id="domain" name="domain" defaultValue={domain?.domain_id ?? ""} className="os-input">
                  <option value="">Choose an onboarded domain...</option>
                  {domains.data.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}{d.description ? ` · ${d.description.slice(0, 60)}` : ""}</option>)}
                </select>
              </div>
              {type.key !== "domain" && (
                <fieldset>
                  <legend className="os-label">Source systems to link</legend>
                  <div className="grid max-h-[300px] gap-1.5 overflow-auto pr-1 sm:grid-cols-2">
                    {sources.data.sources.map((s) => (
                      <label key={s.source_system_id} className={cn("flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-[12px] transition os-hover",
                        picked.has(s.source_system_id) ? "border-[var(--os-emerald)] bg-[var(--os-green-bg)]" : "border-[var(--os-line)]")}>
                        <input type="checkbox" name="source" value={s.source_system_id} defaultChecked={picked.has(s.source_system_id)} className="accent-[var(--os-forest)]" />
                        <span className="min-w-0 flex-1"><span className="block truncate font-bold">{s.source_system_name}</span>
                          <span className="os-faint block truncate text-[10.5px]">{pretty(s.source_type)} · {s.table_count ?? "?"} tables</span></span>
                        <span className={cn("h-2 w-2 shrink-0 rounded-full", s.health === "HEALTHY" ? "bg-[var(--os-leaf)]" : "bg-[#dfa94b]")} title={pretty(s.health)} />
                      </label>
                    ))}
                  </div>
                </fieldset>
              )}
            </AutoForm>
            <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-[var(--os-line)] pt-4">
              <span className="os-muted text-[12px]">The request starts in the onboarding workflow; access, profiling and mapping then run as agents with human gates.</span>
              <Btn href={startHref} variant="primary">Start {type.label.toLowerCase()} <ArrowRight className="h-3.5 w-3.5" /></Btn>
            </div>
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
