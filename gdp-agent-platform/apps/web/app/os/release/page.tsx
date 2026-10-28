import Link from "next/link";
import { ArrowLeft, ArrowRight, CheckCircle2, CircleDashed, FileCode2, FlaskConical, GitPullRequest, Hand, Rocket, ShieldCheck, Sparkles, X } from "lucide-react";
import { stateLabel } from "@/lib/run-labels";
import {
  defaultRun, gatePassed, loadKnowledgeFeed, loadPrs, loadRunBundle, loadRunQa, loadRuns, runOptions, scoreOf, tableOf,
} from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import { Action } from "@/components/os/action";
import { leafRuns, resolveBoardRun, runNeedsMe, type BoardView } from "@/lib/os/work";
import { ActivityRow, Banner, Btn, Card, Empty, Field, Kpi, PageHeader, SourceError, Tag, ago, pretty, type Tone } from "@/components/os/ui";
import { BoardList, BoardTabs, StageFilterBar, WorkflowGroupCard, WorkflowJourneyBar, groupRunsByWorkflow } from "@/components/os/stage-board";
import { HomeSearch } from "@/components/os/home-search";
import { RunPicker } from "@/components/os/run-picker";
import { PublishPr, QaSignoffPanel } from "@/components/os/decide";
import { DeleteRunButton } from "@/components/os/request-ui";
import { ACTIONS, actMode } from "@/lib/os/access";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const metadata = { title: "Publish" };

type Gate = { label: string; sub: string; state: "done" | "active" | "blocked" | "pending" };
const GATE_STYLE: Record<Gate["state"], { tone: Tone; icon: typeof CheckCircle2 }> = {
  done: { tone: "green", icon: CheckCircle2 }, active: { tone: "amber", icon: Hand }, blocked: { tone: "red", icon: X }, pending: { tone: "neutral", icon: CircleDashed },
};
const AFTER_REVIEW = ["DBT_APPROVED", "COMPLETED", "DEPLOYMENT_PENDING", "DEPLOYED", "PIPELINE_ENABLED"];

type Params = { view?: string; run?: string; q?: string; domain?: string; status?: string };

export default async function CodeAndRelease({ searchParams }: { searchParams: Params }) {
  const access = await guard("release");
  const persona = access.persona;
  const [runs, prs] = await Promise.all([loadRuns("sort=updated&limit=150"), loadPrs()]);
  const live = runs.data.runs.filter((r) => !r.is_archived && r.current_state !== "CANCELLED");
  const leaves = leafRuns(live);
  const q = (searchParams.q ?? "").trim().toLowerCase();
  const domainFilter = searchParams.domain ?? "all";
  const statusFilter = searchParams.status ?? "all";

  const searched = leaves.filter((r) => !q || r.run_name.toLowerCase().includes(q) || (r.domain_name ?? "").toLowerCase().includes(q) || (r.source_system_name ?? "").toLowerCase().includes(q));
  const byDomain = searched.filter((r) => domainFilter === "all" || (r.domain_name ?? "No domain") === domainFilter);
  const matchesStatus = (r: (typeof leaves)[number]) => {
    if (statusFilter === "review") return r.current_state === "DBT_REVIEW";
    if (statusFilter === "ready") return r.current_state === "VALIDATION_PASSED" || r.current_state === "DBT_REVIEW";
    if (statusFilter === "published") return AFTER_REVIEW.includes(r.current_state);
    return true;
  };
  const listed = byDomain.filter(matchesStatus);
  const inPublish = listed.filter((r) => ["VALIDATION_PASSED", "DBT_REVIEW", "DEPLOYMENT_PENDING"].includes(r.current_state));
  const lists = {
    action: inPublish.filter((r) => runNeedsMe(r, access) || r.current_state === "DBT_REVIEW"),
    active: inPublish,
    completed: listed.filter((r) => ["DBT_APPROVED", "COMPLETED", "DEPLOYED", "PIPELINE_ENABLED"].includes(r.current_state)),
  };

  let runId = resolveBoardRun(live, searchParams.run, access, "review")?.run_id
    ?? searchParams.run
    ?? defaultRun(leaves, ["DBT_REVIEW", "DBT_APPROVED", "COMPLETED", "VALIDATION_PASSED"])?.run_id;
  let [bundle, qa, feed] = runId
    ? await Promise.all([loadRunBundle(runId), loadRunQa(runId), loadKnowledgeFeed(150)])
    : [{ data: null, error: null }, null, { data: [], error: null }];
  if (bundle.data?.run.table_runs?.length && !bundle.data.run.run.parent_run_id) {
    const kids = bundle.data.run.table_runs;
    const pick = kids.find((t) => ["DBT_REVIEW", "VALIDATION_PASSED"].includes(t.current_state))
      ?? kids.find((t) => ["DBT_APPROVED", "COMPLETED"].includes(t.current_state))
      ?? kids[0];
    if (pick && pick.run_id !== runId) {
      runId = pick.run_id;
      [bundle, qa] = await Promise.all([loadRunBundle(runId), loadRunQa(runId)]);
    }
  }
  const b = bundle.data;
  const view: BoardView = (searchParams.view === "action" || searchParams.view === "active" || searchParams.view === "completed")
    ? searchParams.view
    : b && qa ? "selected" : "active";
  const mine = (r: (typeof leaves)[number]) => runNeedsMe(r, access) || r.current_state === "DBT_REVIEW";
  const groupedLists = {
    action: groupRunsByWorkflow(lists.action, live, mine),
    active: groupRunsByWorkflow(lists.active, live, mine),
    completed: groupRunsByWorkflow(lists.completed, live, mine),
  };
  const grouped = view === "selected" ? [] : groupedLists[view];
  const select = (id: string) => `/os/release?${new URLSearchParams({ view: "selected", run: id })}`;

  const domainCounts = new Map<string, number>();
  for (const r of searched) {
    const d = r.domain_name ?? "No domain";
    domainCounts.set(d, (domainCounts.get(d) ?? 0) + 1);
  }
  const domainOptions = [
    { key: "all", label: "All domains", count: searched.length },
    ...[...domainCounts.entries()].sort((a, b) => b[1] - a[1]).map(([k, count]) => ({ key: k, label: k, count })),
  ];
  const statusOptions = [
    { key: "all", label: "All states", count: byDomain.length },
    { key: "review", label: "Awaiting review", count: byDomain.filter((r) => r.current_state === "DBT_REVIEW").length, dot: byDomain.some((r) => r.current_state === "DBT_REVIEW") },
    { key: "ready", label: "Ready for PR", count: byDomain.filter((r) => r.current_state === "VALIDATION_PASSED" || r.current_state === "DBT_REVIEW").length },
    { key: "published", label: "Published", count: byDomain.filter((r) => AFTER_REVIEW.includes(r.current_state)).length },
  ];
  const filterUrl = (patch: Partial<Params>) => {
    const next = { view: view === "selected" ? "active" : view, q: searchParams.q ?? "", domain: domainFilter, status: statusFilter, ...patch };
    const p = new URLSearchParams(Object.entries(next).filter(([k, v]) => v && (k === "view" || v !== "all")) as [string, string][]);
    return `/os/release?${p}`;
  };
  const releaseQueue = leaves.filter((r) => !r.is_archived && ["DBT_REVIEW", "DBT_APPROVED", "DEPLOYMENT_PENDING"].includes(r.current_state));

  if (!b || !qa || view !== "selected") {
    return (
      <div data-testid="publish-board">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="os-eyebrow">Publish · {persona.label}</div>
            <h1 className="text-[22px] font-extrabold tracking-[-0.03em]">Code, test and release</h1>
          </div>
          <HomeSearch placeholder="Search workflows, tables or domains" />
        </div>
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <BoardTabs base="/os/release" view={view} selection={runId ? ["run", runId] : null}
                     fallback={lists.action[0] ? ["run", lists.action[0].run_id] : lists.active[0] ? ["run", lists.active[0].run_id] : null}
                     counts={{ action: groupedLists.action.length, active: groupedLists.active.length, completed: groupedLists.completed.length }} />
          <span className="mx-1 hidden h-5 w-px bg-[var(--os-line)] sm:block" aria-hidden />
          <StageFilterBar
            domains={domainOptions}
            activeDomain={domainFilter}
            domainHref={(d) => filterUrl({ domain: d })}
            statuses={statusOptions}
            activeStatus={statusFilter}
            statusHref={(s) => filterUrl({ status: s })}
          />
        </div>
        <SourceError what="Release data" error={bundle.error ?? runs.error} />
        <BoardList count={grouped.length} empty={view === "action"
          ? { title: "No publish step waits on you", body: "Runs awaiting code review, PR publication or QA signoff appear here." }
          : view === "active" ? { title: "Nothing is currently in publish review" }
          : { title: "No published runs match these filters" }}>
          {grouped.map((g) => <WorkflowGroupCard key={g.key} group={g} selectHref={select} currentRunId={runId} />)}
        </BoardList>
      </div>
    );
  }

  const { run } = b;
  const score = scoreOf(b.checks);
  const validated = gatePassed(score, b.gate);
  const pubPr = b.publication && (b.publication.status === "PUBLISHED" || b.publication.pr_url) ? [{
    title: `${run.run.domain_name ?? "dbt"} · ${tableOf(run.run.target_model) ?? run.run.run_name}` + (b.publication.pr_number ? ` (#${b.publication.pr_number})` : ""),
    asset_name: run.run.target_model ?? run.run.run_name,
    state: "OPEN",
    pr_url: b.publication.pr_url ?? null,
    opened_by: b.publication.head_branch ?? "autopilot",
    opened_at: b.publication.created_at ?? run.run.created_at,
    href: `/os/release?run=${run.run_id}`,
  }] : [];
  const runPrs = [...pubPr, ...prs.data.prs.filter((p) => (p.href ?? "").includes(run.run_id) || (p.asset_name && run.run.target_model?.includes(p.asset_name)))];
  const reviewed = AFTER_REVIEW.includes(run.current_state);
  const released = ["COMPLETED", "DEPLOYED", "PIPELINE_ENABLED"].includes(run.current_state);
  const qaPassed = qa.results.filter((r) => r.outcome === "PASSED").length;
  const qaFailed = qa.results.filter((r) => r.outcome === "FAILED" || r.outcome === "ERROR").length;
  const learned = feed.data.filter((k) => k.source_run_id === run.run_id);
  const repo = prs.data.repos[0];
  const publishMode = actMode(access, ACTIONS.publishPr.privilege);
  const signoffMode = actMode(access, ACTIONS.signoffQa.privilege);
  const tableRuns = run.table_runs ?? [];

  const gates: Gate[] = [
    { label: "Validation", sub: score.score === null ? "not run" : `${score.score}% · ${score.critical} errors`, state: score.score === null ? "pending" : validated ? "done" : "blocked" },
    { label: "Pull request", sub: runPrs.length ? `${runPrs.length} PR · ${pretty(runPrs[0].state)}` : "not published", state: runPrs.length ? "done" : validated ? "active" : "pending" },
    { label: "QA signoff", sub: qa.signoff ? `${pretty(qa.signoff.decision)} by ${qa.signoff.decided_by}` : `${qa.tests.length} tests planned`, state: qa.signoff ? (qa.signoff.decision === "APPROVED" ? "done" : "blocked") : run.lanes?.QA?.status === "REVIEW_REQUIRED" ? "active" : "pending" },
    { label: "Code review", sub: reviewed ? "approved" : run.current_state === "DBT_REVIEW" ? "awaiting reviewer" : "not yet", state: reviewed ? "done" : run.current_state === "DBT_REVIEW" ? "active" : "pending" },
    { label: "Publish", sub: released ? stateLabel(run.current_state) : "controlled, never automatic", state: released ? "done" : reviewed ? "active" : "pending" },
  ];

  return (
    <div data-testid="publish-board">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <BoardTabs base="/os/release" view={view} selection={["run", run.run_id]}
                   fallback={lists.action[0] ? ["run", lists.action[0].run_id] : ["run", run.run_id]}
                   counts={{ action: groupedLists.action.length, active: groupedLists.active.length, completed: groupedLists.completed.length }} />
        <HomeSearch placeholder="Search workflows, tables or domains" />
      </div>

      <PageHeader
        eyebrow={`Publish · ${persona.label}`}
        title={<>Code, test and release · {run.run.domain_name ?? ""} {tableOf(run.run.target_model)}</>}
        subtitle="Agent-generated code enters the normal SDLC: a pull request, CI evidence, QA and UAT signoff, then a controlled release. Nothing is merged or deployed automatically."
        actions={<>
          <Btn href="/os/release?view=active"><ArrowLeft className="h-3.5 w-3.5" /> All runs</Btn>
          <RunPicker base="/os/release" current={run.run_id} runs={runOptions(leaves)} />
          <Action access={access} id="openDbtStudio" label="dbt studio" href={`/os/build/runs/${run.run_id}/dbt`} icon={<FileCode2 className="h-3.5 w-3.5" />} />
          {run.current_state === "DBT_REVIEW"
            ? <Action access={access} id="reviewCode" label="Validate and decide" href={`/os/review?view=selected&run=${run.run_id}`} variant="primary" />
            : <Btn href="/os/release/source-control" variant="primary"><GitPullRequest className="h-3.5 w-3.5" /> Source control</Btn>}
          <DeleteRunButton runIds={[run.run_id]} redirectTo="/os/release?view=active" previewing={!!access.preview} />
        </>}
      />

      <WorkflowJourneyBar
        runId={run.run_id}
        currentStage="publish"
        generated={!!b.generation}
        validated={score.total > 0}
        validationPassed={validated}
        score={score.score}
        prPublished={runPrs.length > 0}
        released={released}
      />

      {tableRuns.length > 1 && (
        <div className="os-card mb-4 p-3.5" data-testid="table-runs-switcher">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-[12px]">
            <span><b>Target tables in this workflow ({tableRuns.length})</b></span>
            <Link href={`/os/build?view=selected&run=${run.run_id}`} className="os-faint text-[11.5px] font-bold hover:text-[var(--os-ink)]">Build workbench →</Link>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {tableRuns.map((t) => {
              const name = t.model_entity ?? tableOf(t.target_model) ?? t.run_name;
              const active = t.run_id === run.run_id;
              const done = t.current_state === "COMPLETED" || t.current_state === "DBT_APPROVED";
              const review = t.current_state === "DBT_REVIEW" || t.current_state === "VALIDATION_PASSED";
              const isFailed = t.current_state === "VALIDATION_FAILED" || t.current_state === "FAILED";
              return (
                <Link key={t.run_id} href={`/os/release?run=${encodeURIComponent(t.run_id)}`} scroll={false}
                      aria-current={active ? "page" : undefined}
                      className={cn("inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11.5px] font-bold transition",
                        active ? "border-[var(--os-forest)] bg-[var(--os-green-bg)] text-[var(--os-ink)] shadow-sm"
                               : "border-[var(--os-line)] bg-[var(--os-surface)] hover:border-[var(--os-forest)]")}>
                  <span>{name}</span>
                  <Tag tone={done ? "green" : isFailed ? "red" : review ? "amber" : "blue"}>{stateLabel(t.current_state)}</Tag>
                </Link>
              );
            })}
          </div>
        </div>
      )}
      {validated
        ? <Banner icon={ShieldCheck}>Independent validation passed ({score.score}%, {score.critical} critical) for generation v{b.generation?.generation_version ?? "–"}. Code review, QA and UAT remain in human control.</Banner>
        : <Banner tone="amber" icon={Hand} action={<Btn href={`/os/review?run=${run.run_id}`} variant="warn">Open the gate</Btn>}>This run has not passed validation, so it is not ready for a pull request.</Banner>}
      <SourceError what="Source control" error={prs.error} />
      {qa.error && <SourceError what="QA" error={qa.error} />}

      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi label="Generated artifacts" value={b.artifacts.length} hint={b.generation ? `generation v${b.generation.generation_version} · ${ago(b.generation.created_at)}` : "no generation"} icon={FileCode2} tone="green" />
        <Kpi label="Validation score" value={score.score === null ? "–" : `${score.score}%`} hint={`${score.passed}/${score.total} checks`} icon={ShieldCheck} tone={validated ? "green" : "red"} />
        <Kpi label="QA tests" value={qa.tests.length} hint={qa.run ? `${qaPassed} passed · ${qaFailed} failed · ${qa.run.not_run} not run` : "pack generated, not executed"} icon={FlaskConical} tone="blue" />
        <Kpi label="Open pull requests" value={prs.data.counts.open} hint={`${prs.data.counts.merged} merged across the platform`} icon={GitPullRequest} tone="purple" href="/os/release/source-control" />
      </div>

      <Card className="mb-4" title="Promotion stages" subtitle="Every gate between generated code and production, from the run's real state">
        <ol className="grid gap-2 sm:grid-cols-5">
          {gates.map((g, i) => {
            const s = GATE_STYLE[g.state];
            const I = s.icon;
            return (
              <li key={g.label} className={cn("relative rounded-xl border p-3", g.state === "active" ? "border-[var(--os-amber-line)] bg-[var(--os-amber-bg)]" : "border-[var(--os-line)]")}>
                <div className="flex items-center gap-2"><Tag tone={s.tone} className="h-6 w-6 justify-center p-0"><I className="h-3.5 w-3.5" /></Tag><span className="text-[12.5px] font-extrabold">{g.label}</span></div>
                <div className="os-muted mt-1.5 text-[11px]">{g.sub}</div>
                {i < gates.length - 1 && <ArrowRight className="os-faint absolute -right-2.5 top-1/2 z-10 hidden h-3.5 w-3.5 -translate-y-1/2 sm:block" />}
              </li>
            );
          })}
        </ol>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Card title="Pull request" subtitle={repo ? `${repo.name} · ${repo.git_url.replace(/^https?:\/\//, "")}` : "No repository connected"}
                action={runPrs[0]?.pr_url ? <Btn href={runPrs[0].pr_url} external>Open on Git</Btn> : <Action access={access} id="publishPr" href={`/os/build/runs/${run.run_id}/dbt`} />}>
            {runPrs.length ? (
              <div className="os-divide">
                {runPrs.map((p, i) => (
                  <div key={i} className="flex items-center justify-between gap-3 py-2.5">
                    <div className="min-w-0"><div className="truncate text-[13px] font-bold">{p.title ?? p.asset_name}</div><div className="os-faint text-[11px]">{p.opened_by} · {ago(p.opened_at)}</div></div>
                    <Tag tone={p.state === "MERGED" ? "green" : "blue"}>{pretty(p.state)}</Tag>
                  </div>
                ))}
              </div>
            ) : (
              <>
                <Empty icon={GitPullRequest} title={b.publication?.status && b.publication.status !== "NOT_REQUESTED" ? `Last publication: ${pretty(b.publication.status)}` : "No pull request published for this run"}>
                  {b.publication?.detail ?? (validated ? "Validation passed, so the generated code can be published as a pull request." : "A pull request is published only after validation passes.")}
                </Empty>
                {validated && b.generation && publishMode !== "none" && (
                  <div className="mt-3 rounded-xl border border-[var(--os-line)] p-3">
                    <PublishPr runId={run.run_id} mode={publishMode} previewing={!!access.preview}
                               repo={b.branch?.repo ?? b.branch?.origin} base={b.branch?.base_branch} cut={b.branch?.cut_branch} />
                  </div>
                )}
              </>
            )}
            <div className="os-muted mb-1.5 mt-4 text-[10.5px] font-bold uppercase tracking-wide">Change summary</div>
            <div className="max-h-[300px] overflow-auto rounded-lg border border-[var(--os-line)]">
              <table className="os-table">
                <thead className="sticky top-0"><tr><th>File</th><th>Kind</th><th>Origin</th><th>Checksum</th></tr></thead>
                <tbody>
                  {b.artifacts.map((a) => (
                    <tr key={a.artifact_id}>
                      <td className="font-mono text-[11px]">{a.file_path}</td>
                      <td className="os-muted whitespace-nowrap">{pretty(a.artifact_type)}</td>
                      <td><Tag tone="blue">Agent generated</Tag></td>
                      <td className="os-faint font-mono text-[10.5px]">{a.content_sha256.slice(0, 10)}</td>
                    </tr>
                  ))}
                  {!b.artifacts.length && <tr><td colSpan={4} className="os-faint text-center">No files generated yet.</td></tr>}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="Learning from this run" subtitle="Knowledge the run contributed to Enterprise Memory (patterns, approved checks, branch plans)"
                action={<Btn href={`/os/knowledge?q=${encodeURIComponent(run.run_id)}`}>See in knowledge</Btn>}>
            {learned.length ? (
              <div className="os-divide">
                {learned.slice(0, 6).map((k) => (
                  <ActivityRow key={k.knowledge_id} tone="purple" icon={Sparkles} title={k.title}
                               sub={`${pretty(k.knowledge_type)} · from ${pretty(k.origin)} · v${k.version}`} when={ago(k.created_at)} />
                ))}
              </div>
            ) : <Empty icon={Sparkles} title="No knowledge recorded from this run yet">Approved mappings, data quality checks and code reviews are captured as memory automatically.</Empty>}
          </Card>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          <Card title="QA and UAT workbench" subtitle="The generated test pack and its signoff" action={<Action access={access} id="runQa" href={`/os/build/runs/${run.run_id}/qa`} />}>
            {qa.tests.length ? (
              <>
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(qa.tests.reduce<Record<string, number>>((m, t) => ({ ...m, [t.category]: (m[t.category] ?? 0) + 1 }), {}))
                    .map(([c, n]) => <Tag key={c} tone="blue">{pretty(c)} · {n}</Tag>)}
                </div>
                <div className="os-divide mt-3">
                  {(qa.results.length ? qa.results : qa.tests.map((t) => ({ ...t, outcome: "PLANNED", result_id: t.test_id, detail: null, severity: t.severity ?? "" })))
                    .slice(0, 6).map((t) => (
                      <div key={t.result_id} className="flex items-center justify-between gap-2 py-2">
                        <span className="min-w-0"><span className="block truncate text-[12px] font-semibold">{t.title}</span><span className="os-faint text-[10.5px]">{t.test_id} · {pretty(t.category)}</span></span>
                        <Tag tone={t.outcome === "PASSED" ? "green" : t.outcome === "FAILED" || t.outcome === "ERROR" ? "red" : "neutral"}>{pretty(t.outcome)}</Tag>
                      </div>
                    ))}
                </div>
              </>
            ) : <Empty icon={FlaskConical} title="No QA pack yet" />}
            <div className="mt-3">
              {!qa.signoff && qa.tests.length > 0 && signoffMode !== "none" && (
                <div className="mb-3"><QaSignoffPanel runId={run.run_id} mode={signoffMode} previewing={!!access.preview} /></div>
              )}
              <Field label="Signoff">{qa.signoff ? <>{pretty(qa.signoff.decision)} by {qa.signoff.decided_by} · {ago(qa.signoff.decided_at)}{qa.signoff.note ? <div className="os-faint mt-0.5 text-[11px]">{qa.signoff.note}</div> : null}</> : "Waiting on the QA and UAT lead"}</Field>
            </div>
          </Card>

          <Card title="Release control" subtitle="Controlled promotion within the SDLC">
            <div className="grid gap-2.5">
              <Field label="Current state">{stateLabel(run.current_state)}</Field>
              <Field label="Code review">{reviewed ? "Approved" : run.current_state === "DBT_REVIEW" ? <Link className="underline" href={`/os/build/runs/${run.run_id}/review`}>Awaiting the data developer</Link> : "Not reached"}</Field>
              <Field label="Environment">{run.run.environment} · {run.run.target_model}</Field>
            </div>
          </Card>

          <Card title="Release queue" subtitle="Runs waiting on review or release" pad={false}>
            {releaseQueue.length ? (
              <div className="os-divide">
                {releaseQueue.map((r) => (
                  <Link key={r.run_id} href={`/os/release?run=${r.run_id}`} className="flex items-center justify-between gap-2 px-5 py-2.5 os-hover">
                    <span className="min-w-0 truncate text-[12px] font-semibold">{r.run_name}</span>
                    <Tag tone="amber">{stateLabel(r.current_state)}</Tag>
                  </Link>
                ))}
              </div>
            ) : <div className="p-5"><Empty title="Queue is empty" /></div>}
          </Card>
        </div>
      </div>
    </div>
  );
}
