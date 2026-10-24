import Link from "next/link";
import { Bot, CheckCircle2, CircleDashed, FileCode2, Hand, ShieldAlert, ShieldCheck, Wrench, X } from "lucide-react";
import { stateLabel } from "@/lib/run-labels";
import { labelFor, pipelineOf } from "@/lib/stages";
import type { StageStatusValue } from "@/lib/types";
import {
  artifactGroups, gatePassed, gateText, defaultRun, loadRunBundle, loadRuns, scoreOf, tableOf,
} from "@/lib/os/data";
import { guard, type OsAccess } from "@/lib/os/session";
import { boardView, pastStage, runNeedsMe, stageOfRun } from "@/lib/os/work";
import { BoardList, BoardTabs, RunCard } from "@/components/os/stage-board";
import { HomeSearch } from "@/components/os/home-search";
import { BuildEndToEnd } from "@/components/os/build-run";
import { actMode } from "@/lib/os/access";
import { Action } from "@/components/os/action";
import { currentStep, gateHref, lifecycleOfRun, osHref } from "@/lib/os/lifecycle";
import { ActivityRow, Banner, Bar, Btn, Card, Empty, Field, PageHeader, SourceError, Stepper, Tag, ago, pretty, type Tone } from "@/components/os/ui";

export const dynamic = "force-dynamic";
export const metadata = { title: "Build" };

const STAGE_TONE: Record<StageStatusValue, { tone: Tone; label: string }> = {
  COMPLETE: { tone: "green", label: "Completed" }, ACTIVE: { tone: "blue", label: "Ready" }, REVIEW_REQUIRED: { tone: "amber", label: "Needs review" },
  LOCKED: { tone: "neutral", label: "Waiting" }, FAILED: { tone: "red", label: "Failed" }, BLOCKED: { tone: "red", label: "Blocked" },
  CANCELLED: { tone: "neutral", label: "Cancelled" },
};
// ACTIVE on the rail means the stage is open; only these states mean an agent is working on it right now
const WORKING = new Set(["PROFILING_RUNNING", "DBT_GENERATING", "VALIDATION_RUNNING", "LANDING_RUNNING", "ACCESS_VALIDATION"]);
const DONE: { tone: Tone; label: string } = { tone: "green", label: "Completed" };
const FAIL: { tone: Tone; label: string } = { tone: "red", label: "Failed" };

type Params = { view?: string; run?: string; q?: string };

/** Build: the runs being generated and validated, by Selected, Action required, Active and Completed. A selected run
 *  opens the workbench: its eight steps, the agents, the artifacts and the gates. */
export default async function BuildBoard({ searchParams }: { searchParams: Params }) {
  const access = await guard("build");
  const runs = await loadRuns("sort=updated&limit=200");
  const q = (searchParams.q ?? "").trim().toLowerCase();
  const live = runs.data.runs.filter((r) => !r.is_archived && r.current_state !== "CANCELLED");
  const listed = live.filter((r) => !q || r.run_name.toLowerCase().includes(q) || (r.domain_name ?? "").toLowerCase().includes(q));
  const active = listed.filter((r) => stageOfRun(r) === "build");
  const lists = { action: active.filter((r) => runNeedsMe(r, access)), active, completed: listed.filter((r) => pastStage(r, "build")) };
  // a run named in the URL is selected, any run (older links name runs at every step); Selected alone opens the latest build
  // Selected with nothing selected opens the first build waiting on the viewer, else the first building, else the latest
  const fallback = lists.action[0] ?? lists.active[0]
    ?? defaultRun(live, ["DBT_REVIEW", "VALIDATION_FAILED", "VALIDATION_PASSED", "DBT_GENERATING", "DBT_PENDING", "DBT_APPROVED"]) ?? null;
  const run = live.find((r) => r.run_id === searchParams.run) ?? (searchParams.view === "selected" ? fallback : null) ?? null;
  const view = boardView(searchParams.view ?? (searchParams.run && run ? "selected" : undefined), !!run, lists.action.length);
  const rows = view === "selected" ? [] : lists[view];
  const select = (id: string) => `/os/build?${new URLSearchParams({ view: "selected", run: id })}`;

  return (
    <div data-testid="build-board">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="os-eyebrow">Build</div>
          <h1 className="text-[22px] font-extrabold tracking-[-0.03em]">Generate, validate and hand off</h1>
        </div>
        <HomeSearch placeholder="Search runs by name or domain" />
      </div>
      <div className="mb-4">
        <BoardTabs base="/os/build" view={view} selection={run ? ["run", run.run_id] : null} fallback={fallback ? ["run", fallback.run_id] : null}
                   counts={{ action: lists.action.length, active: lists.active.length, completed: lists.completed.length }} />
      </div>
      <SourceError what="Workflow runs" error={runs.error} />
      {view === "selected" && run ? <Workbench runId={run.run_id} access={access} /> : (
        <BoardList count={rows.length} empty={view === "action"
          ? { title: "No build waits on you", body: <>Runs whose design is done are on <Link className="underline" href="/os/build/ready">Ready to build</Link>.</> }
          : view === "active" ? { title: "No run is building", body: <>See <Link className="underline" href="/os/build/ready">Ready to build</Link>.</> }
          : { title: "No run has finished its build yet" }}>
          {rows.map((r) => <RunCard key={r.run_id} run={r} href={select(r.run_id)} mine={runNeedsMe(r, access)} />)}
        </BoardList>
      )}
    </div>
  );
}

/** The selected run: today's workbench. */
async function Workbench({ runId, access }: { runId: string; access: OsAccess }) {
  const persona = access.persona;
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
  const score = scoreOf(b.checks);
  // the code standards agent's areas in the latest validation (STANDARDS_<AREA>, or STANDARDS when it could not run)
  const standardsAreas = b.checks.filter((c) => c.validation_type.startsWith("STANDARDS"));
  const passed = gatePassed(score, b.gate);
  const steps = lifecycleOfRun(run, { generated: !!b.generation, validated: score.total > 0, validationPassed: passed });
  const now = currentStep(steps);
  const domainId = run.run.domain_id ?? null;
  const stage = (s: string) => run.stages.find((x) => x.stage === s);
  const groups = artifactGroups(b.artifacts);
  const table = tableOf(run.run.target_model);
  const events = [...b.events].reverse().slice(0, 8);
  const validationFailed = run.current_state === "VALIDATION_FAILED";
  const approvals = b.reviews.filter((r) => r.decision === "APPROVE");

  const standardsRan = standardsAreas.filter((c) => c.status !== "SKIPPED");
  // a card's status comes from what its agent produced; the rail's stage status is only the fallback before that
  const canvas = [
    { n: "01", title: "Profile", actor: "Deterministic tool", stage: "PROFILING", what: "Snowflake profiling and sampling" },
    { n: "02", title: "Design agent", actor: "Agent + approval", stage: "MAPPING", what: "Domain model and field mapping" },
    { n: "03", title: "Build agent", actor: "Agent", stage: "DBT", what: b.generation ? `dbt v${b.generation.generation_version}: ${b.generation.files_generated} files` : "dbt models, tests and config",
      evidence: run.current_state === "DBT_GENERATING" ? null : b.generation ? DONE : null },
    { n: "04", title: "Review agent", actor: "Independent agent", stage: "VALIDATION", what: score.total ? `${score.passed}/${score.total} checks · gate ${b.gate.min_score}%` : `Policy threshold ${b.gate.min_score}%`,
      evidence: score.total ? (passed ? DONE : FAIL) : null },
    { n: "05", title: "Standards agent", actor: "Independent agent", stage: "VALIDATION",
      what: standardsAreas.length
        ? `${standardsAreas.filter((c) => c.status === "PASSED").length}/${standardsRan.length} standard areas pass`
        : b.gate.standards_review === false ? "Off for this domain" : "Reviews the code against the written standards at validation",
      evidence: standardsRan.length ? (standardsRan.every((c) => c.status === "PASSED") ? DONE : FAIL)
        : b.gate.standards_review === false ? { tone: "neutral" as Tone, label: "Off" } : null },
  ];
  const cardStatus = (c: (typeof canvas)[number]) => {
    if ("evidence" in c && c.evidence) return c.evidence;
    const s = stage(c.stage)?.status;
    if (!s) return { tone: "neutral" as Tone, label: "Not started" };
    return s === "ACTIVE" && WORKING.has(run.current_state) ? { tone: "blue" as Tone, label: "Running" } : STAGE_TONE[s];
  };
  const autopilotDecisions = b.reviews.filter((r) => r.decision === "APPROVE" && /^Autopilot:/.test(r.business_justification ?? ""));

  // design done and the code not yet approved: autopilot takes it end to end right here (the same as Ready to build)
  const buildable = ["MAPPING_REVIEW", "STTM_REVIEW", "STTM_APPROVED", "SODA_PENDING", "SODA_REVIEW", "SODA_APPROVED", "DBT_PENDING",
                     "VALIDATION_PENDING", "VALIDATION_PASSED", "DBT_REVIEW"].includes(run.current_state)
    && pipeline !== "GOLD" && actMode(access, "RUN.OPERATE") === "do";
  const primary = validationFailed
    ? <Btn href={`/os/review?run=${runId}`} variant="red"><ShieldAlert className="h-3.5 w-3.5" /> See validation findings</Btn>
    : buildable ? <BuildEndToEnd runId={runId} previewing={!!access.preview} />
    : <Action access={access} id="workStep" href={osHref(runId, now, domainId)} label={`Open ${now.label.toLowerCase()}`} variant="primary" />;

  return (
    <>
      <PageHeader
        eyebrow="Selected workflow · build workbench"
        title={<>{run.run.domain_name ?? "No domain"}{table && <span className="os-muted"> · </span>}{table}<span className="os-muted text-[18px] font-bold"> · {pipeline === "GOLD" ? "Gold" : "Silver"} build</span></>}
        subtitle="Deterministic tools, specialised agents, independent validation and the delivery handoff, in one workspace."
        actions={<>
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
      {autopilotDecisions.length > 0 && (
        <Banner icon={Bot} action={<Btn href={gateHref(runId, "soda")}>Open the DQ store</Btn>}>
          <b>Autopilot decided {autopilotDecisions.length} gate{autopilotDecisions.length === 1 ? "" : "s"}</b> ({autopilotDecisions.map((r) => labelFor(r.stage, pipeline).toLowerCase()).join(", ")}). Review or reopen them from the stores and the Review page.
        </Banner>
      )}

      <p className="os-muted mb-1.5 text-[12px]" data-testid="build-steps-summary">
        <b className="os-ink">{steps.filter((x) => x.status === "done").length} of {steps.length} steps done</b>
        {steps.some((x) => x.status !== "done") && <> · {steps.filter((x) => x.status !== "done").length} pending · now at {now.label.toLowerCase()} ({now.owner})</>}
      </p>
      <div className="mb-4"><Stepper steps={steps} hrefOf={(s) => s.status === "pending" ? null : osHref(runId, s, domainId)} /></div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]">
        <Card title="Live workflow canvas" subtitle="Hybrid orchestration: deterministic tools plus agent decisions"
              action={<Btn href={`/os/review?run=${runId}`}>See validation details</Btn>}>
          <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-3">
            {canvas.map((c) => {
              const st = cardStatus(c);
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
                const auto = human && /^review: APPROVE/.test(e.reason ?? "") && autopilotDecisions.some((r) => r.to_state === e.to_state);
                const bad = /FAILED|CANCELLED/.test(e.to_state);
                return (
                  <ActivityRow key={e.event_id} tone={bad ? "red" : human ? "amber" : "green"} icon={bad ? X : human ? Hand : CheckCircle2}
                               title={<>{stateLabel(e.to_state)} <span className="os-faint font-semibold">· {auto ? `autopilot, as ${e.actor}` : human ? `by ${e.actor}` : "system"}</span></>}
                               sub={e.reason ?? undefined} when={ago(e.created_at)} />
                );
              })}
            </div>
          ) : <Empty title="No activity yet" />}
        </Card>

        <div className="flex min-w-0 flex-col gap-4">
          <Card title="Generated artifacts" subtitle="Every file the build agent wrote, from the run's latest generation"
                action={<Action access={access} id="openDbtStudio" href={`/os/build/runs/${runId}/dbt`} icon={<FileCode2 className="h-3.5 w-3.5" />} />} pad={false}>
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

      <Card className="mt-4" title="Gates and quality" subtitle="Who owns the next decision, and what has to be true before a pull request"
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
                       sub={approvals.length ? `Approved: ${approvals.map((r) => `${labelFor(r.stage, pipeline).toLowerCase()} (${autopilotDecisions.includes(r) ? "autopilot" : r.reviewer})`).join(", ")}` : "No design approval recorded yet"} />
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
