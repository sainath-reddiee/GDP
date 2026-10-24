import Link from "next/link";
import { ArrowRight, CircleHelp, FileText, GitBranch, Network, ScanSearch } from "lucide-react";
import { GATES, stateLabel } from "@/lib/run-labels";
import {
  loadDomainContract, loadDomainModel, loadDomainQuestions, loadDomains, loadKnowledge, loadMapping, loadRuns, loadSources,
} from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import { actMode } from "@/lib/os/access";
import { Action } from "@/components/os/action";
import { gateHref } from "@/lib/os/lifecycle";
import { boardView, pastStage, runNeedsMe, stageOfRun } from "@/lib/os/work";
import { Bar, Btn, Card, Empty, Field, SourceError, Tag, ago, pretty } from "@/components/os/ui";
import { BoardList, BoardTabs, RunCard, Segmented, Track, runTrack } from "@/components/os/stage-board";
import { HomeSearch } from "@/components/os/home-search";
import { DomainEr } from "@/components/os/client";

export const dynamic = "force-dynamic";
export const metadata = { title: "Design" };

// Design: the runs being profiled, modelled and mapped, by Selected, Action required, Active and Completed, and by
// design step, Profile, Model and Map: on the lists the step keeps the runs at that step; a selected run opens on that
// step for its domain (the step it is at by default).

const TABS = [{ key: "profile", label: "Profile" }, { key: "model", label: "Model" }, { key: "map", label: "Map" }] as const;
type Tab = (typeof TABS)[number]["key"];
const tabOf = (state: string): Tab => (state.startsWith("PROFILING") || state.startsWith("LANDING") ? "profile"
  : /^(MAPPING|STTM|SODA)_/.test(state) ? "map" : "model");

type Params = { view?: string; run?: string; domain?: string; tab?: string; q?: string };

export default async function DesignBoard({ searchParams }: { searchParams: Params }) {
  const access = await guard("design");
  const [domains, runs, sources] = await Promise.all([loadDomains(), loadRuns("sort=updated&limit=200"), loadSources()]);
  const q = (searchParams.q ?? "").trim().toLowerCase();
  const live = runs.data.runs.filter((r) => !r.is_archived && r.current_state !== "CANCELLED");
  const listed = live.filter((r) => !q || r.run_name.toLowerCase().includes(q) || (r.domain_name ?? "").toLowerCase().includes(q));
  const step = TABS.find((t) => t.key === searchParams.tab)?.key ?? null;
  const atStep = (r: (typeof live)[number]) => !step || tabOf(r.current_state) === step;
  const active = listed.filter((r) => stageOfRun(r) === "design" && atStep(r));
  const lists = { action: active.filter((r) => runNeedsMe(r, access)), active, completed: listed.filter((r) => pastStage(r, "design")) };
  // ?domain= (older links) selects that domain's newest run in design, else its newest run
  const byDomain = searchParams.domain ? domains.data.find((d) => d.domain_id === searchParams.domain)?.domain_name : null;
  const run = live.find((r) => r.run_id === searchParams.run)
    ?? (byDomain ? active.find((r) => r.domain_name === byDomain) ?? live.find((r) => r.domain_name === byDomain) : undefined) ?? null;
  // Selected with nothing selected opens the first run waiting on the viewer, else the first in design
  const fallback = lists.action[0] ?? lists.active[0] ?? lists.completed[0] ?? null;
  const picked = run ?? (searchParams.view === "selected" ? fallback : null);
  const view = boardView(searchParams.view ?? (byDomain && run ? "selected" : undefined), !!picked, lists.action.length);
  const rows = view === "selected" ? [] : lists[view];
  const select = (id: string) => `/os/design?${new URLSearchParams({ view: "selected", run: id, ...(step ? { tab: step } : {}) })}`;
  // Profile, Model, Map: on a selected run, its tab; on a list, the runs at that step (again to show every step)
  const stepHref = (k: Tab) => view === "selected" && picked
    ? `/os/design?${new URLSearchParams({ view: "selected", run: picked.run_id, tab: k })}`
    : `/os/design?${new URLSearchParams({ view, ...(step === k ? {} : { tab: k }) })}`;
  const shownStep = view === "selected" && picked ? (step ?? tabOf(picked.current_state)) : step;

  return (
    <div data-testid="design-board">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="os-eyebrow">Design</div>
          <h1 className="text-[22px] font-extrabold tracking-[-0.03em]">Profile, model and map</h1>
        </div>
        <HomeSearch placeholder="Search runs by name or domain" />
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <BoardTabs base="/os/design" view={view} selection={picked ? ["run", picked.run_id] : null}
                   fallback={fallback ? ["run", fallback.run_id] : null} extra={step ? { tab: step } : undefined}
                   counts={{ action: lists.action.length, active: lists.active.length, completed: lists.completed.length }} />
        <span className="mx-1 hidden h-5 w-px bg-[var(--os-line)] sm:block" aria-hidden />
        <Segmented label="Design step" testId="design-steps" options={TABS.map((x) => ({
          key: x.key, label: x.label, href: stepHref(x.key), active: x.key === shownStep,
          ...(view !== "selected" ? { count: listed.filter((r) => stageOfRun(r) === "design" && tabOf(r.current_state) === x.key).length } : {}),
        }))} />
      </div>
      <SourceError what="Workflow runs" error={runs.error} />
      {view === "selected" && picked ? (
        <Selected run={picked} tab={shownStep ?? tabOf(picked.current_state)}
                  domainId={domains.data.find((d) => d.domain_name === picked.domain_name)?.domain_id ?? null}
                  sources={sources.data.sources} access={access} />
      ) : (
        <BoardList count={rows.length} empty={view === "action"
          ? { title: "No design decision waits on you", body: "Field mappings, transformations and data quality checks your role decides show here." }
          : view === "active" ? { title: step ? `No run is at ${TABS.find((t) => t.key === step)?.label}` : "No run is in design" } : { title: "No run has finished design yet" }}>
          {rows.map((r) => <RunCard key={r.run_id} run={r} href={select(r.run_id)} mine={runNeedsMe(r, access)} />)}
        </BoardList>
      )}
    </div>
  );
}

async function Selected({ run, tab, domainId, sources, access }: {
  run: Awaited<ReturnType<typeof loadRuns>>["data"]["runs"][number]; tab: Tab; domainId: string | null;
  sources: Awaited<ReturnType<typeof loadSources>>["data"]["sources"]; access: Awaited<ReturnType<typeof guard>>;
}) {
  const t = runTrack(run);
  const gate = GATES[run.current_state];
  return (
    <section className="flex flex-col gap-4" data-testid="design-selected">
      <div className="os-card os-rise p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="truncate text-[18px] font-extrabold tracking-[-0.02em]">{run.run_name}</h2>
            <div className="os-faint mt-1 flex flex-wrap items-center gap-x-1.5 text-[11.5px]">
              <span className="os-muted font-semibold">{run.domain_name ?? "No domain"}</span>
              {run.source_system_name && <span>· {run.source_system_name}</span>}
              <span>· {t.pending ? `${t.pending} of 8 steps to go` : "all 8 steps done"} · updated {ago(run.updated_at ?? run.created_at)}</span>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Tag tone={gate ? "amber" : "blue"}>{stateLabel(run.current_state)}</Tag>
            {gate && <Action access={access} id="decideGate" privilege={gate.privilege} label={`Decide: ${gate.name.toLowerCase()}`} href={gateHref(run.run_id, gate.slug)} variant="primary" />}
            <Btn href={`/os/build/runs/${run.run_id}`}>Run workspace <ArrowRight className="h-3.5 w-3.5" /></Btn>
          </div>
        </div>
        <Track steps={t.steps} note={t.note} since={t.since} className="mt-5" />
      </div>

      <h3 className="os-eyebrow -mb-1">{TABS.find((x) => x.key === tab)?.label}</h3>

      {tab === "profile" && <ProfileTab run={run} sources={sources} />}
      {tab === "model" && <ModelTab domainId={domainId} access={access} />}
      {tab === "map" && <MapTab run={run} />}
    </section>
  );
}

function ProfileTab({ run, sources }: { run: { run_id: string; source_system_name?: string | null }; sources: Awaited<ReturnType<typeof loadSources>>["data"]["sources"] }) {
  const mine = sources.filter((s) => s.source_system_name === run.source_system_name);
  return (
    <Card title="Source profiling" subtitle={run.source_system_name ? `${run.source_system_name}, as profiled for this run` : "This run has no source yet"} pad={false}
          action={<div className="flex gap-2"><Btn href={`/os/build/runs/${run.run_id}/profile`} variant="primary"><ScanSearch className="h-3.5 w-3.5" /> Open profile</Btn><Btn href="/os/design/profiling">Profiling store</Btn></div>}>
      {mine.length ? (
        <table className="os-table">
          <thead><tr><th>Source</th><th className="text-right">Tables</th><th className="text-right">Staged</th><th>Profile</th></tr></thead>
          <tbody>
            {mine.map((s) => (
              <tr key={s.source_system_id}>
                <td><div className="font-semibold">{s.source_system_name}</div><div className="os-faint text-[10.5px]">{s.database_name}.{s.schema_name}</div></td>
                <td className="text-right tabular-nums">{s.table_count ?? "–"}</td>
                <td className="text-right tabular-nums">{s.staged_tables}</td>
                <td><Tag tone={s.failed_tables ? "red" : s.last_profiled_at ? "green" : "neutral"}>{s.failed_tables ? "Failures" : s.last_profiled_at ? `Ready · ${ago(s.last_profiled_at)}` : "Not profiled"}</Tag></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <div className="p-5"><Empty icon={ScanSearch} title="No profile for this run's source yet" /></div>}
    </Card>
  );
}

async function ModelTab({ domainId, access }: { domainId: string | null; access: Awaited<ReturnType<typeof guard>> }) {
  if (!domainId) return <Empty icon={Network} title="This run has no domain yet" />;
  const [model, questions, contract, memory] = await Promise.all([
    loadDomainModel(domainId), loadDomainQuestions(domainId), loadDomainContract(domainId), loadKnowledge({ domain_id: domainId }),
  ]);
  const diagram = model.data.diagram;
  const patterns = memory.data.items.filter((i) => ["MAPPING_PATTERN", "TRANSFORMATION_RULE", "MODEL_DEFINITION", "GLOSSARY"].includes(i.knowledge_type));
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)]">
      <Card title="Business model and relationships" subtitle={diagram ? `Model v${diagram.version} · ${pretty(diagram.status)}${diagram.approved_by ? ` · approved by ${diagram.approved_by}` : ""}` : "No model yet"}
            action={<Action access={access} id="editModel" href={`/os/knowledge/domains/${domainId}`} />}>
        <SourceError what="Domain model" error={model.error} />
        {diagram?.entities.length
          ? <DomainEr className="h-[440px] rounded-xl border border-[var(--os-line)]" entities={diagram.entities} relationships={diagram.relationships} />
          : <Empty icon={Network} title="The domain has no data model yet" />}
      </Card>
      <div className="flex min-w-0 flex-col gap-4">
        <Card title="Open design questions" subtitle="What the agents could not settle on their own">
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
            </div>
          ) : <Empty icon={CircleHelp} title="No open questions" />}
        </Card>
        <Card title="Design artifacts" subtitle="Governed outputs handed to the build">
          <div className="grid gap-2.5">
            <Field label="Domain model">{model.data.model ? `Approved v${model.data.model.approved_version ?? "–"}${model.data.model.open_version ? ` · draft v${model.data.model.open_version} open` : ""}` : "None"}</Field>
            <Field label="Domain contract">{contract.data ? <>{contract.data.file}{contract.data.skill ? ` · ${contract.data.skill.name.toLowerCase()}@${contract.data.skill.version}` : ""}</> : "None"}</Field>
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
  );
}

async function MapTab({ run }: { run: { run_id: string; run_name: string } }) {
  const mapping = await loadMapping(run.run_id);
  const m = mapping.data;
  const decided = new Map((m?.decisions ?? []).map((d) => [d.candidate_id, d]));
  const top = (m?.candidates ?? []).filter((c) => c.rank === 1).sort((a, b) => b.confidence - a.confidence);
  const bands = m?.bands ?? { confident: 0.8, weak: 0.45 };
  const tableName = (id: string | null | undefined) => m?.targets.find((t) => t.target_table_id === id)?.target_table ?? "";
  return (
    <Card title="Source-to-domain mappings" pad={false}
          subtitle={m?.status ? `${m.status.decided} of ${m.status.source_columns} source columns decided${m.status.complete ? " · complete" : ""}` : "Best proposal per source column"}
          action={<div className="flex gap-2">
            <Btn href={`/os/build/runs/${run.run_id}/modeling?tab=mapping`} variant="primary"><GitBranch className="h-3.5 w-3.5" /> Open mapping</Btn>
            <Btn href={`/os/build/runs/${run.run_id}/modeling?tab=transformations`}>Transformations</Btn>
          </div>}>
      <SourceError what="Mapping" error={mapping.error} />
      {top.length ? (
        <div className="max-h-[520px] overflow-auto">
          <table className="os-table">
            <thead className="sticky top-0"><tr><th>Source field</th><th>Target field</th><th>Method</th><th className="text-right">Confidence</th><th>Status</th></tr></thead>
            <tbody>
              {top.slice(0, 60).map((c) => {
                const d = decided.get(c.candidate_id);
                const conf = Math.round(c.confidence * 100);
                return (
                  <tr key={c.candidate_id} title={c.generated_reason ?? undefined}>
                    <td className="font-mono text-[11px]">{c.source_table}.{c.source_column}</td>
                    <td className="font-mono text-[11px]">{tableName(c.target_table_id)}{tableName(c.target_table_id) && "."}{c.target_column}</td>
                    <td className="os-muted whitespace-nowrap">{pretty(c.recommendation)}</td>
                    <td className="w-28 text-right">
                      <span className="tabular-nums">{conf}%</span>
                      <Bar className="mt-1 h-1.5" value={conf} tone={c.confidence >= bands.confident ? "green" : c.confidence >= bands.weak ? "amber" : "red"} />
                    </td>
                    <td>{d ? <Tag tone={d.decision === "APPROVED" ? "green" : d.decision === "REJECTED" ? "red" : "blue"}>{pretty(d.decision)}</Tag> : <Tag tone="amber">Review</Tag>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : <div className="p-5"><Empty icon={GitBranch} title="No mapping proposals yet">They appear once the run is profiled and matched to its domain.</Empty></div>}
    </Card>
  );
}
