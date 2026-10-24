import Link from "next/link";
import { ArrowRight, Boxes, CircleHelp, FileText, GitBranch, Layers, ListChecks, Network, Sparkles } from "lucide-react";
import { GATES, stateLabel } from "@/lib/run-labels";
import {
  loadDomainContract, loadDomainModel, loadDomainQuestions, loadDomains, loadKnowledge, loadMapping, loadRuns, loadSources,
} from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import { loadRequests } from "@/lib/os/data";
import { RequestQueue } from "@/components/os/request-queue";
import { actMode } from "@/lib/os/access";
import { Action } from "@/components/os/action";
import { gateHref } from "@/lib/os/lifecycle";
import { Banner, Bar, Btn, Card, Empty, Field, Kpi, PageHeader, SourceError, Tag, ago, pretty } from "@/components/os/ui";
import { DomainEr } from "@/components/os/client";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const metadata = { title: "Designer studio" };

// a run whose mapping proposals exist (mapping review onwards)
const MAPPED = ["MAPPING_REVIEW", "MAPPING_APPROVED", "STTM_PENDING", "STTM_REVIEW", "STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED",
  "DBT_PENDING", "DBT_GENERATING", "VALIDATION_PENDING", "VALIDATION_RUNNING", "VALIDATION_PASSED", "VALIDATION_FAILED", "DBT_REVIEW", "DBT_APPROVED", "COMPLETED"];

export default async function DesignerStudio({ searchParams }: { searchParams: { domain?: string } }) {
  const access = await guard("design");
  const persona = access.persona;
  const [domains, runs, sources] = await Promise.all([loadDomains(), loadRuns("sort=updated&limit=150"), loadSources()]);
  const designable = domains.data.filter((d) => d.target_tables > 0);
  const domain = designable.find((d) => d.domain_id === searchParams.domain) ?? designable.find((d) => d.domain_name === "COMPANY") ?? designable[0];
  if (!domain) {
    return (<><PageHeader eyebrow={`${persona.label} / Design studio`} title="Profile, model and map" /><SourceError what="Domains" error={domains.error} /><Empty icon={Boxes} title="No domain model yet" action={<Action access={access} id="startOnboarding" privilege="DOMAIN.ONBOARD" label="Onboard a domain" href="/os/enable/new-domain" variant="primary" />} /></>);
  }
  const domainRuns = runs.data.runs.filter((r) => !r.is_archived && r.domain_name === domain.domain_name);
  // the run whose mapping is shown: the newest mapped one, preferring the multi-table parent (its proposals cover every table)
  const mapped = domainRuns.filter((r) => MAPPED.includes(r.current_state));
  const mapRun = mapped.find((r) => !/: [A-Z_]+$/.test(r.run_name)) ?? mapped[0] ?? null;
  const [requests, model, questions, contract, memory, mapping] = await Promise.all([
    loadRequests("all", "OPEN", domain.domain_id),
    loadDomainModel(domain.domain_id), loadDomainQuestions(domain.domain_id), loadDomainContract(domain.domain_id),
    loadKnowledge({ domain_id: domain.domain_id }), mapRun ? loadMapping(mapRun.run_id) : Promise.resolve(null),
  ]);
  const diagram = model.data.diagram;
  const atGate = domainRuns.filter((r) => r.current_state in GATES && ["MAPPING_REVIEW", "STTM_REVIEW", "SODA_REVIEW"].includes(r.current_state));
  const domainSources = sources.data.sources.filter((s) => domainRuns.some((r) => r.source_system_name === s.source_system_name));

  const decided = new Map((mapping?.data.decisions ?? []).map((d) => [d.candidate_id, d]));
  const top = (mapping?.data.candidates ?? []).filter((c) => c.rank === 1);
  const avgConfidence = top.length ? Math.round((top.reduce((n, c) => n + c.confidence, 0) / top.length) * 100) : null;
  const bands = mapping?.data.bands ?? { confident: 0.8, weak: 0.45 };
  const tableName = (id: string | null | undefined) => mapping?.data.targets.find((t) => t.target_table_id === id)?.target_table ?? "";
  const patterns = memory.data.items.filter((i) => ["MAPPING_PATTERN", "TRANSFORMATION_RULE", "MODEL_DEFINITION", "GLOSSARY"].includes(i.knowledge_type));

  return (
    <>
      <PageHeader
        eyebrow={`${persona.label} / Design studio`}
        title="Profile, model and map"
        subtitle="Profile only the source tables the domain needs, reuse its approved model, and approve mappings before the build agent generates code."
        actions={<>
          <Btn href={`/os/knowledge/domains/${domain.domain_id}`}>Domain workspace</Btn>
          {atGate[0] && <Action access={access} id="decideGate" privilege={GATES[atGate[0].current_state].privilege} label="Approve model and mappings" href={gateHref(atGate[0].run_id, GATES[atGate[0].current_state].slug)} variant="primary" />}
        </>}
      />
      <div className="mb-4 flex flex-wrap gap-2">
        {designable.map((d) => (
          <Link key={d.domain_id} href={`/os/design?domain=${d.domain_id}`}
                className={cn("os-btn", d.domain_id === domain.domain_id && "os-btn-primary")}>
            <Layers className="h-3.5 w-3.5" /> {d.domain_name}
          </Link>
        ))}
      </div>
      {atGate.length > 0 && (
        <Banner tone="amber" icon={CircleHelp} action={<Btn href="/os/review" variant="warn">Open gates</Btn>}>
          <b>{atGate.length} design decision{atGate.length === 1 ? "" : "s"} wait{atGate.length === 1 ? "s" : ""} on a designer</b> in {domain.domain_name}: {atGate.map((r) => stateLabel(r.current_state).toLowerCase()).filter((v, i, a) => a.indexOf(v) === i).join(", ")}.
        </Banner>
      )}
      <SourceError what="Domain model" error={model.error} />

      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi label="Entities in the model" value={diagram?.entities.length ?? domain.target_tables} hint={`${diagram?.relationships.length ?? 0} relationships`} icon={Network} tone="green" />
        <Kpi label="Source tables staged" value={domainSources.reduce((n, s) => n + s.staged_tables, 0)} hint={`${domainSources.length} source${domainSources.length === 1 ? "" : "s"} feed this domain`} icon={ListChecks} tone="blue" />
        <Kpi label="Mapping confidence" value={avgConfidence === null ? "–" : `${avgConfidence}%`} hint={top.length ? `${top.length} best proposals` : "no mapping yet"} icon={GitBranch} tone={avgConfidence !== null && avgConfidence >= bands.confident * 100 ? "green" : "amber"} />
        <Kpi label="Domain memory" value={domain.knowledge_items} hint={`${patterns.length} patterns, rules and terms`} icon={Sparkles} tone="purple" href={`/os/knowledge?domain=${domain.domain_id}`} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Card title="Business model and relationships" subtitle={diagram ? `${domain.domain_name} model v${diagram.version} · ${pretty(diagram.status)}${diagram.approved_by ? ` · approved by ${diagram.approved_by}` : ""}` : "No model yet"}
                action={<Action access={access} id="editModel" href={`/os/knowledge/domains/${domain.domain_id}`} />}>
            {diagram?.entities.length
              ? <DomainEr className="h-[420px] rounded-xl border border-[var(--os-line)]" entities={diagram.entities} relationships={diagram.relationships} />
              : <Empty icon={Network} title="The domain has no data model yet" />}
          </Card>

          <Card title="Source-to-domain mappings" subtitle={mapRun ? <>Best proposal per source column from <Link className="underline" href={`/os/build?run=${mapRun.run_id}`}>{mapRun.run_name}</Link></> : "No run of this domain has reached mapping"}
                action={mapRun ? <Action access={access} id="answerQuestion" label="Open mapping" href={`/os/build/runs/${mapRun.run_id}/modeling?tab=mapping`} /> : undefined} pad={false}>
            {top.length ? (
              <div className="max-h-[460px] overflow-auto">
                <table className="os-table">
                  <thead className="sticky top-0"><tr><th>Source field</th><th>Target field</th><th>Method</th><th className="text-right">Confidence</th><th>Status</th></tr></thead>
                  <tbody>
                    {top.sort((a, b) => b.confidence - a.confidence).slice(0, 40).map((c) => {
                      const d = decided.get(c.candidate_id);
                      const conf = Math.round(c.confidence * 100);
                      return (
                        <tr key={c.candidate_id} title={c.generated_reason ?? undefined}>
                          <td className="font-mono text-[11px]">{c.source_table}.{c.source_column}</td>
                          <td className="font-mono text-[11px]">{tableName(c.target_table_id)}{tableName(c.target_table_id) && "."}{c.target_column}</td>
                          <td className="os-muted whitespace-nowrap">{pretty(c.recommendation)}</td>
                          <td className="w-28 text-right">
                            <div className="flex items-center justify-end gap-2"><span className="tabular-nums">{conf}%</span></div>
                            <Bar className="mt-1 h-1.5" value={conf} tone={c.confidence >= bands.confident ? "green" : c.confidence >= bands.weak ? "amber" : "red"} />
                          </td>
                          <td>{d ? <Tag tone={d.decision === "APPROVED" ? "green" : d.decision === "REJECTED" ? "red" : "blue"}>{pretty(d.decision)}</Tag> : <Tag tone="amber">Review</Tag>}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : <div className="p-5"><Empty icon={GitBranch} title="No mapping proposals yet">Mapping proposals appear once a run of this domain is profiled and matched.</Empty></div>}
          </Card>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          <Card title="Source profiling" subtitle="Sources that feed this domain, as profiled" action={<Btn href="/os/design/profiling">Profiling store</Btn>} pad={false}>
            {domainSources.length ? (
              <table className="os-table">
                <thead><tr><th>Source</th><th className="text-right">Tables</th><th className="text-right">Staged</th><th>Profile</th></tr></thead>
                <tbody>
                  {domainSources.map((s) => (
                    <tr key={s.source_system_id}>
                      <td><div className="font-semibold">{s.source_system_name}</div><div className="os-faint text-[10.5px]">{s.database_name}.{s.schema_name}</div></td>
                      <td className="text-right tabular-nums">{s.table_count ?? "–"}</td>
                      <td className="text-right tabular-nums">{s.staged_tables}</td>
                      <td><Tag tone={s.failed_tables ? "red" : s.last_profiled_at ? "green" : "neutral"}>{s.failed_tables ? "Failures" : s.last_profiled_at ? `Ready · ${ago(s.last_profiled_at)}` : "Not profiled"}</Tag></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div className="p-5"><Empty title="No source feeds this domain yet" /></div>}
          </Card>

          <RequestQueue title={`Requests for ${domain.domain_name}`} subtitle="Open onboarding requests of this domain and the part each waits on"
                        requests={requests.data.requests} access={access} error={requests.error} empty="No open request for this domain" max={5} />

          <Card title="Open design questions" subtitle="Ambiguities the agents could not settle on their own">
            <SourceError what="Questions" error={questions.error} />
            {questions.data.length ? (
              <div className="os-divide">
                {questions.data.slice(0, 6).map((q) => (
                  <div key={q.question_id} className="py-2.5">
                    <div className="text-[12.5px] font-semibold leading-snug">{q.question}</div>
                    <div className="os-faint mt-0.5 flex items-center gap-2 text-[11px]">
                      <span>{pretty(q.stage ?? q.kind)} · {ago(q.created_at)}</span>
                      {q.run_id && actMode(access, "MAPPING.DECIDE") !== "none" && <Link className="underline" href={`/os/build/runs/${q.run_id}/modeling?tab=mapping`}>answer</Link>}
                    </div>
                  </div>
                ))}
                {questions.data.length > 6 && <div className="os-faint pt-2 text-[11px]">+{questions.data.length - 6} more in the domain workspace</div>}
              </div>
            ) : <Empty icon={CircleHelp} title="No open questions" />}
          </Card>

          <Card title="Design artifacts" subtitle="Governed outputs handed to the build">
            <div className="grid gap-2.5">
              <Field label="Domain model">{model.data.model ? `Approved v${model.data.model.approved_version ?? "–"}${model.data.model.open_version ? ` · draft v${model.data.model.open_version} open` : ""}` : "None"}</Field>
              <Field label="Domain contract">{contract.data ? <>{contract.data.file}{contract.data.skill ? ` · ${contract.data.skill.name.toLowerCase()}@${contract.data.skill.version}` : ""}</> : "None"}</Field>
              <Field label="Mapping decisions">{mapping?.data.status ? `${mapping.data.status.decided} of ${mapping.data.status.source_columns} source columns decided${mapping.data.status.complete ? " · complete" : ""}` : "None yet"}</Field>
              <Field label="Reusable memory">{patterns.length} mapping patterns, rules and glossary terms</Field>
            </div>
            {contract.data?.text && (
              <details className="mt-3">
                <summary className="os-accent cursor-pointer text-[12px] font-bold"><FileText className="mr-1 inline h-3.5 w-3.5" />Read the contract</summary>
                <div className="os-code mt-2 max-h-[260px] whitespace-pre-wrap text-[11px]">{contract.data.text.slice(0, 2500)}</div>
              </details>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
