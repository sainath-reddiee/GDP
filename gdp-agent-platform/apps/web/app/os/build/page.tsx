import Link from "next/link";
import { ArrowRight, Bot, CheckCircle2, CircleDashed, ExternalLink, FileCode2, Hand, Hammer, ShieldAlert, ShieldCheck, Wrench, X } from "lucide-react";
import { stateLabel } from "@/lib/run-labels";
import { labelFor, pipelineOf } from "@/lib/stages";
import type { StageStatusValue } from "@/lib/types";
import {
  artifactGroups, gatePassed, gateText, defaultRun, loadRunBundle, loadRuns, runOptions, scoreOf, tableOf,
} from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import { Action } from "@/components/os/action";
import { classicHref, currentStep, lifecycleOfRun } from "@/lib/os/lifecycle";
import { ActivityRow, Banner, Bar, Btn, Card, Empty, Field, PageHeader, SourceError, Stepper, Tag, ago, pretty, type Tone } from "@/components/os/ui";
import { RunPicker } from "@/components/os/run-picker";

export const dynamic = "force-dynamic";
export const metadata = { title: "Build workbench" };

const STAGE_TONE: Record<StageStatusValue, { tone: Tone; label: string }> = {
  COMPLETE: { tone: "green", label: "Completed" }, ACTIVE: { tone: "blue", label: "Running" }, REVIEW_REQUIRED: { tone: "amber", label: "Needs review" },
  LOCKED: { tone: "neutral", label: "Waiting" }, FAILED: { tone: "red", label: "Failed" }, BLOCKED: { tone: "red", label: "Blocked" },
  CANCELLED: { tone: "neutral", label: "Cancelled" },
};

export default async function BuildWorkbench({ searchParams }: { searchParams: { run?: string } }) {
  const access = await guard("build");
  const persona = access.persona;
  const runs = await loadRuns("sort=updated&limit=150");
  const runId = searchParams.run
    ?? defaultRun(runs.data.runs, ["DBT_REVIEW", "VALIDATION_FAILED", "VALIDATION_PASSED", "DBT_GENERATING", "DBT_PENDING", "DBT_APPROVED"])?.run_id;
  if (!runId) {
    return (
      <>
        <PageHeader eyebrow={`${persona.label} / Build workbench`} title="Build workbench" />
        <SourceError what="Workflow runs" error={runs.error} />
        <Empty icon={Hammer} title="No workflow to build yet" action={<Action access={access} id="startOnboarding" href="/os/intake" variant="primary" />}>
          Once a run has an approved mapping, its generated code, tests and validation appear here.
        </Empty>
      </>
    );
  }
  const bundle = await loadRunBundle(runId);
  const b = bundle.data;
  if (!b) {
    return (
      <>
        <PageHeader eyebrow={`${persona.label} / Build workbench`} title="Build workbench" />
        <SourceError what="This run" error={bundle.error} />
      </>
    );
  }
  const { run } = b;
  const pipeline = pipelineOf(run.run.pipeline);
  const steps = lifecycleOfRun(run);
  const now = currentStep(steps);
  const score = scoreOf(b.checks);
  const passed = gatePassed(score, b.gate);
  const stage = (s: string) => run.stages.find((x) => x.stage === s);
  const groups = artifactGroups(b.artifacts);
  const table = tableOf(run.run.target_model);
  const events = [...b.events].reverse().slice(0, 8);
  const validationFailed = run.current_state === "VALIDATION_FAILED";
  const approvals = b.reviews.filter((r) => r.decision === "APPROVE");

  const canvas = [
    { n: "01", title: "Profile", actor: "Deterministic tool", stage: "PROFILING", what: "Snowflake profiling and sampling" },
    { n: "02", title: "Design agent", actor: "Agent + approval", stage: "MAPPING", what: "Domain model and field mapping" },
    { n: "03", title: "Build agent", actor: "Agent", stage: "DBT", what: b.generation ? `dbt v${b.generation.generation_version}: ${b.generation.files_generated} files` : "dbt models, tests and config" },
    { n: "04", title: "Review agent", actor: "Independent agent", stage: "VALIDATION", what: score.total ? `${score.passed}/${score.total} checks · gate ${b.gate.min_score}%` : `Policy threshold ${b.gate.min_score}%` },
  ];

  const primary = validationFailed
    ? <Btn href={`/os/review?run=${runId}`} variant="red"><ShieldAlert className="h-3.5 w-3.5" /> See validation findings</Btn>
    : run.current_state === "DBT_REVIEW"
      ? <Action access={access} id="reviewCode" label="Review and decide" href={`/os/review?run=${runId}`} variant="primary" />
      : <Action access={access} id="workStep" href={classicHref(runId, now)} label={`Open ${now.label.toLowerCase()}`} variant="primary" />;

  return (
    <>
      <PageHeader
        eyebrow={`${persona.label} / Build workbench`}
        title={<>{run.run.domain_name ?? "No domain"}{table && <span className="os-muted"> · </span>}{table}<span className="os-muted text-[18px] font-bold"> · {pipeline === "GOLD" ? "Gold" : "Silver"} build</span></>}
        subtitle="Deterministic tools, specialised agents, independent validation and the delivery handoff, in one workspace."
        actions={<>
          <RunPicker base="/os/build" current={runId} runs={runOptions(runs.data.runs)} />
          <Btn href={`/runs/${runId}`}><ExternalLink className="h-3.5 w-3.5" /> Classic view</Btn>
          {primary}
        </>}
        meta={<>
          <Tag tone="blue" title={runId}>Run {runId.slice(0, 8)}</Tag>
          <span className="os-muted">Target: <b className="os-ink">{run.run.target_model ?? "not set"}</b></span>
          <span className="os-muted">Source: <b className="os-ink">{run.run.source_database ? `${run.run.source_database}.${run.run.source_schema}` : "not set"}</b></span>
          <span className="os-muted">State: <b className="os-ink">{stateLabel(run.current_state)}</b></span>
          {score.score !== null && <Tag tone={passed ? "green" : "red"} className="ml-auto">Validation {score.score}% · {passed ? "Pass" : "Below gate"}</Tag>}
        </>}
      />

      {validationFailed && (
        <Banner tone="red" icon={ShieldAlert} action={<Btn href={`/os/review?run=${runId}`} variant="red">Open the gate</Btn>}>
          <b>Validation blocked this build.</b> {score.critical} error{score.critical === 1 ? "" : "s"} across {score.total - score.passed} failing check{score.total - score.passed === 1 ? "" : "s"}. No pull request is opened until the gate passes.
        </Banner>
      )}
      {run.current_state === "FAILED" && (
        <Banner tone="red" icon={X}><b>The run stopped{run.failed_from_state ? ` at ${stateLabel(run.failed_from_state).toLowerCase()}` : ""}.</b> {run.failure_reason ?? ""}</Banner>
      )}
      {b.errors.length > 0 && <SourceError what="Part of this run" error={b.errors.join(" · ")} />}

      <div className="mb-4"><Stepper steps={steps} hrefOf={(s) => s.status === "pending" ? null : classicHref(runId, s)} /></div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
        <Card title="Live workflow canvas" subtitle="Hybrid orchestration: deterministic tools plus agent decisions"
              action={<Btn href={`/os/review?run=${runId}`}>See validation details</Btn>}>
          <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-4">
            {canvas.map((c) => {
              const s = stage(c.stage)?.status;
              const st = s ? STAGE_TONE[s] : { tone: "neutral" as Tone, label: "Not started" };
              return (
                <div key={c.n} className="rounded-xl border border-[var(--os-line)] p-3.5">
                  <div className="os-faint text-[10.5px] font-bold tracking-wide">{c.n} · {c.actor.toUpperCase()}</div>
                  <div className="mt-1 text-[14px] font-extrabold">{c.title}</div>
                  <Tag tone={st.tone} className="mt-2">{st.label}</Tag>
                  <div className="os-muted mt-2 text-[11.5px] leading-snug">{c.what}</div>
                </div>
              );
            })}
          </div>
          <div className="mt-5 flex items-center justify-between">
            <h3 className="text-[13.5px] font-extrabold">Activity stream</h3>
            <span className="os-faint text-[11px]">Every transition is recorded with its actor</span>
          </div>
          {events.length ? (
            <div className="os-divide mt-1">
              {events.map((e) => {
                const human = e.actor_type === "HUMAN";
                const bad = /FAILED|CANCELLED/.test(e.to_state);
                return (
                  <ActivityRow key={e.event_id} tone={bad ? "red" : human ? "amber" : "green"} icon={bad ? X : human ? Hand : CheckCircle2}
                               title={<>{stateLabel(e.to_state)} <span className="os-faint font-semibold">· {human ? `by ${e.actor}` : "system"}</span></>}
                               sub={e.reason ?? undefined} when={ago(e.created_at)} />
                );
              })}
            </div>
          ) : <Empty title="No activity yet" />}
        </Card>

        <div className="flex min-w-0 flex-col gap-4">
          <Card title="Generated artifacts" subtitle="Every file the build agent wrote, from the run's latest generation"
                action={<Action access={access} id="openDbtStudio" href={`/runs/${runId}/dbt`} icon={<FileCode2 className="h-3.5 w-3.5" />} />} pad={false}>
            {groups.length ? (
              <table className="os-table">
                <thead><tr><th>Asset</th><th>Type</th><th className="text-right">Files</th><th>State</th></tr></thead>
                <tbody>
                  {groups.map((g) => (
                    <tr key={g.key} title={g.files.join("\n")}>
                      <td className="font-semibold">{g.key}</td>
                      <td className="os-muted">{g.type}</td>
                      <td className="text-right tabular-nums">{g.files.length}</td>
                      <td><Tag tone="green">{pretty(b.generation?.generation_status ?? "Generated")}</Tag></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="p-5"><Empty icon={CircleDashed} title="No code generated yet">The build agent generates dbt models, tests and config once the mapping and data quality are approved.</Empty></div>
            )}
          </Card>

          <Card title="Run context" subtitle="Persisted and replayable at any step">
            <div className="grid gap-3">
              <Field label="Workflow">{pipeline === "GOLD" ? "Gold" : "Silver"} pipeline · state v{run.state_version} · {run.run.environment}</Field>
              <Field label="Pinned skills">
                {b.skills.length ? (
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {b.skills.map((s) => <Link key={s.skill_id} href={`/os/skills?skill=${encodeURIComponent(s.skill_name)}`}><Tag tone="purple">{s.skill_name.toLowerCase()}@{s.version}</Tag></Link>)}
                  </div>
                ) : "No skill loaded yet"}
              </Field>
              <Field label="Evidence">{b.generation ? <>Generation {b.generation.generation_id.slice(0, 8)} · {b.generation.skill_version} · <code className="text-[11px]">{b.generation.stage_path}</code></> : "No generation yet"}</Field>
              <Field label="Next owner">{now.status === "done" ? "None: the run is complete" : `${now.owner} · ${now.label}: ${stateLabel(run.current_state).toLowerCase()}`}</Field>
            </div>
          </Card>
        </div>
      </div>

      <Card className="mt-4" title="Autonomy and gates" subtitle="Who owns the next decision, and what has to be true before a pull request"
            action={<Btn href="/os/knowledge"><Bot className="h-3.5 w-3.5" /> Knowledge used</Btn>}>
        <div className="flex items-end justify-between gap-3">
          <h3 className="text-[14px] font-extrabold">Build confidence</h3>
          <div className={`text-[34px] font-extrabold leading-none tracking-tight tabular-nums ${score.score === null ? "os-faint" : passed ? "text-[var(--os-emerald)]" : "text-[var(--os-red-ink)]"}`}>
            {score.score ?? "–"}<span className="text-[15px]">%</span>
          </div>
        </div>
        <Bar className="mt-2 h-2.5" value={score.score ?? 0} tone={score.score === null ? "green" : passed ? "green" : "red"} />
        <div className="os-faint mt-1 text-right text-[10.5px]">Gate at {gateText(b.gate)}</div>
        <div className="os-divide mt-2">
          <ActivityRow tone={approvals.length ? "green" : "neutral"} icon={approvals.length ? CheckCircle2 : CircleDashed} title="Prerequisites"
                       sub={approvals.length ? `Approved by a person: ${approvals.map((r) => `${labelFor(r.stage, pipeline).toLowerCase()} (${r.reviewer})`).join(", ")}` : "No design approval recorded yet"} />
          <ActivityRow tone={b.generation ? "green" : "neutral"} icon={b.generation ? CheckCircle2 : CircleDashed} title="Agent generation"
                       sub={b.generation ? `${b.generation.files_generated} files generated ${ago(b.generation.created_at)} by ${b.generation.model_version ?? "the build agent"}` : "Waiting on approved design"} />
          <ActivityRow tone={score.score === null ? "neutral" : passed ? "green" : "red"} icon={score.score === null ? CircleDashed : passed ? ShieldCheck : ShieldAlert} title="Validation gate"
                       sub={score.score === null ? "Not validated yet" : `${score.passed} of ${score.total} checks passed${score.skipped ? ` (${score.skipped} skipped)` : ""} · ${score.critical} errors · ${score.warnings} warnings`} />
          {run.lanes && (
            <ActivityRow tone={run.lanes.gate.ready ? "green" : "amber"} icon={run.lanes.gate.ready ? CheckCircle2 : Wrench} title="Quality lanes"
                         sub={run.lanes.gate.ready ? "QA tests and data quality are signed off" : `Waiting on ${run.lanes.gate.waiting_on.join(", ")}`} />
          )}
        </div>
      </Card>
    </>
  );
}
