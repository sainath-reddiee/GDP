import Link from "next/link";
import { ArrowRight, CheckCircle2, CircleDashed, FileCode2, FlaskConical, GitPullRequest, Hand, Rocket, ShieldCheck, Sparkles, X } from "lucide-react";
import { stateLabel } from "@/lib/run-labels";
import {
  defaultRun, gatePassed, loadKnowledgeFeed, loadPrs, loadRunBundle, loadRunQa, loadRuns, runOptions, scoreOf, tableOf,
} from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import { Action } from "@/components/os/action";
import { ActivityRow, Banner, Btn, Card, Empty, Field, Kpi, PageHeader, SourceError, Tag, ago, pretty, type Tone } from "@/components/os/ui";
import { RunPicker } from "@/components/os/run-picker";
import { PublishPr, QaSignoffPanel } from "@/components/os/decide";
import { ACTIONS, actMode } from "@/lib/os/access";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const metadata = { title: "Code and release" };

type Gate = { label: string; sub: string; state: "done" | "active" | "blocked" | "pending" };
const GATE_STYLE: Record<Gate["state"], { tone: Tone; icon: typeof CheckCircle2 }> = {
  done: { tone: "green", icon: CheckCircle2 }, active: { tone: "amber", icon: Hand }, blocked: { tone: "red", icon: X }, pending: { tone: "neutral", icon: CircleDashed },
};
const AFTER_REVIEW = ["DBT_APPROVED", "COMPLETED", "DEPLOYMENT_PENDING", "DEPLOYED", "PIPELINE_ENABLED"];

export default async function CodeAndRelease({ searchParams }: { searchParams: { run?: string } }) {
  const access = await guard("release");
  const persona = access.persona;
  const [runs, prs] = await Promise.all([loadRuns("sort=updated&limit=150"), loadPrs()]);
  const runId = searchParams.run ?? defaultRun(runs.data.runs, ["DBT_REVIEW", "DBT_APPROVED", "COMPLETED", "VALIDATION_PASSED"])?.run_id;
  const [bundle, qa, feed] = runId
    ? await Promise.all([loadRunBundle(runId), loadRunQa(runId), loadKnowledgeFeed(150)])
    : [{ data: null, error: null }, null, { data: [], error: null }];
  const b = bundle.data;
  const releaseQueue = runs.data.runs.filter((r) => !r.is_archived && ["DBT_REVIEW", "DBT_APPROVED", "DEPLOYMENT_PENDING"].includes(r.current_state));

  if (!b || !qa) {
    return (
      <>
        <PageHeader eyebrow={`${persona.label} / SDLC`} title="Code, test and release" />
        <SourceError what="Release data" error={bundle.error ?? runs.error} />
        <Empty icon={Rocket} title="Nothing to release yet">A run appears here once its generated code passes validation.</Empty>
      </>
    );
  }

  const { run } = b;
  const score = scoreOf(b.checks);
  const validated = gatePassed(score, b.gate);
  const runPrs = prs.data.prs.filter((p) => (p.href ?? "").includes(run.run_id) || (p.asset_name && run.run.target_model?.includes(p.asset_name)));
  const reviewed = AFTER_REVIEW.includes(run.current_state);
  const released = ["COMPLETED", "DEPLOYED", "PIPELINE_ENABLED"].includes(run.current_state);
  const qaPassed = qa.results.filter((r) => r.outcome === "PASSED").length;
  const qaFailed = qa.results.filter((r) => r.outcome === "FAILED" || r.outcome === "ERROR").length;
  const learned = feed.data.filter((k) => k.source_run_id === run.run_id);
  const repo = prs.data.repos[0];
  const publishMode = actMode(access, ACTIONS.publishPr.privilege);
  const signoffMode = actMode(access, ACTIONS.signoffQa.privilege);

  const gates: Gate[] = [
    { label: "Validation", sub: score.score === null ? "not run" : `${score.score}% · ${score.critical} errors`, state: score.score === null ? "pending" : validated ? "done" : "blocked" },
    { label: "Pull request", sub: runPrs.length ? `${runPrs.length} PR · ${pretty(runPrs[0].state)}` : "not published", state: runPrs.length ? "done" : validated ? "active" : "pending" },
    { label: "QA signoff", sub: qa.signoff ? `${pretty(qa.signoff.decision)} by ${qa.signoff.decided_by}` : `${qa.tests.length} tests planned`, state: qa.signoff ? (qa.signoff.decision === "APPROVED" ? "done" : "blocked") : run.lanes?.QA?.status === "REVIEW_REQUIRED" ? "active" : "pending" },
    { label: "Code review", sub: reviewed ? "approved" : run.current_state === "DBT_REVIEW" ? "awaiting reviewer" : "not yet", state: reviewed ? "done" : run.current_state === "DBT_REVIEW" ? "active" : "pending" },
    { label: "Release", sub: released ? stateLabel(run.current_state) : "controlled, never automatic", state: released ? "done" : reviewed ? "active" : "pending" },
  ];

  return (
    <>
      <PageHeader
        eyebrow={`${persona.label} / SDLC and release`}
        title={<>Code, test and release · {run.run.domain_name ?? ""} {tableOf(run.run.target_model)}</>}
        subtitle="Agent-generated code enters the normal SDLC: a pull request, CI evidence, QA and UAT signoff, then a controlled release. Nothing is merged or deployed automatically."
        actions={<>
          <RunPicker base="/os/release" current={run.run_id} runs={runOptions(runs.data.runs)} />
          <Action access={access} id="openDbtStudio" label="dbt studio" href={`/runs/${run.run_id}/dbt`} icon={<FileCode2 className="h-3.5 w-3.5" />} />
          {run.current_state === "DBT_REVIEW"
            ? <Action access={access} id="reviewCode" label="Review and decide" href={`/os/review?run=${run.run_id}`} variant="primary" />
            : <Btn href="/dashboard/source-control" variant="primary"><GitPullRequest className="h-3.5 w-3.5" /> Source control</Btn>}
        </>}
      />
      {validated
        ? <Banner icon={ShieldCheck}>Independent validation passed ({score.score}%, {score.critical} critical) for generation v{b.generation?.generation_version ?? "–"}. Code review, QA and UAT remain in human control.</Banner>
        : <Banner tone="amber" icon={Hand} action={<Btn href={`/os/review?run=${run.run_id}`} variant="warn">Open the gate</Btn>}>This run has not passed validation, so it is not ready for a pull request.</Banner>}
      <SourceError what="Source control" error={prs.error} />
      {qa.error && <SourceError what="QA" error={qa.error} />}

      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi label="Generated artifacts" value={b.artifacts.length} hint={b.generation ? `generation v${b.generation.generation_version} · ${ago(b.generation.created_at)}` : "no generation"} icon={FileCode2} tone="green" />
        <Kpi label="Validation score" value={score.score === null ? "–" : `${score.score}%`} hint={`${score.passed}/${score.total} checks`} icon={ShieldCheck} tone={validated ? "green" : "red"} />
        <Kpi label="QA tests" value={qa.tests.length} hint={qa.run ? `${qaPassed} passed · ${qaFailed} failed · ${qa.run.not_run} not run` : "pack generated, not executed"} icon={FlaskConical} tone="blue" />
        <Kpi label="Open pull requests" value={prs.data.counts.open} hint={`${prs.data.counts.merged} merged across the platform`} icon={GitPullRequest} tone="purple" href="/dashboard/source-control" />
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
                action={runPrs[0]?.pr_url ? <Btn href={runPrs[0].pr_url} external>Open on Git</Btn> : <Action access={access} id="publishPr" href={`/runs/${run.run_id}/dbt`} />}>
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
          <Card title="QA and UAT workbench" subtitle="The generated test pack and its signoff" action={<Action access={access} id="runQa" href={`/runs/${run.run_id}/qa`} />}>
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
              <Field label="Code review">{reviewed ? "Approved" : run.current_state === "DBT_REVIEW" ? <Link className="underline" href={`/runs/${run.run_id}/review`}>Awaiting the data developer</Link> : "Not reached"}</Field>
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
    </>
  );
}
