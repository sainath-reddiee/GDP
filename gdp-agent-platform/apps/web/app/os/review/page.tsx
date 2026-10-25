import Link from "next/link";
import { ArrowRight, CheckCircle2, CircleDashed, ClipboardList, Flag, Hand, ShieldAlert, ShieldCheck, Sparkles, TriangleAlert } from "lucide-react";
import { GATES, stateLabel } from "@/lib/run-labels";
import {
  checkPassed, checkSkipped, defaultRun, findingsOf, gatePassed, gateText, loadRunBundle, loadRuns, runOptions, scoreOf, tableOf,
  type ValidationAttempt, type ValidationCheck,
} from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import { ACTIONS, actMode, has } from "@/lib/os/access";
import { Action } from "@/components/os/action";
import { BuildEndToEnd } from "@/components/os/build-run";
import { gateHref, lifecycleOfRun, osHref } from "@/lib/os/lifecycle";
import { leafRuns, resolveBoardRun, runNeedsMe, type BoardView } from "@/lib/os/work";
import { ActivityRow, Banner, Bar, Btn, Card, Empty, Field, Kpi, PageHeader, SourceError, Stepper, Tag, ago, pretty } from "@/components/os/ui";
import { BoardList, BoardTabs, StageFilterBar, WorkflowGroupCard, WorkflowJourneyBar, groupRunsByWorkflow } from "@/components/os/stage-board";
import { HomeSearch } from "@/components/os/home-search";
import { RunPicker } from "@/components/os/run-picker";
import { GateDecision, ReviewStandards, ValidateAgain } from "@/components/os/decide";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const metadata = { title: "Validate" };

/** Where a failing check is fixed in the classic run workspace. */
const FIX_AT: Record<string, { slug: string; label: string; privilege: string }> = {
  NAMING: { slug: "modeling?tab=mapping", label: "Field mapping", privilege: "MAPPING.DECIDE" },
  REQUIRED_COLUMNS: { slug: "modeling?tab=mapping", label: "Field mapping", privilege: "MAPPING.DECIDE" },
  DATATYPE: { slug: "modeling?tab=mapping", label: "Field mapping", privilege: "MAPPING.DECIDE" },
  SCHEMA: { slug: "modeling?tab=mapping", label: "Field mapping", privilege: "MAPPING.DECIDE" },
  STTM_CONSISTENCY: { slug: "modeling?tab=transformations", label: "Transformations", privilege: "STTM.EDIT" },
  SODA_CONFIG: { slug: "soda", label: "Data quality", privilege: "SODA.EDIT" },
  DBT_COMPILE: { slug: "dbt", label: "dbt studio", privilege: "DBT.EDIT" },
  SKILL: { slug: "dbt", label: "dbt studio", privilege: "DBT.EDIT" },
};
// the code standards agent's areas: fixed in the generated code (dbt studio) or in the playbook that produced it
const fixAt = (kind: string) => FIX_AT[kind] ?? (kind.startsWith("STANDARDS") ? { slug: "dbt", label: "dbt studio", privilege: "DBT.EDIT" } : undefined);

function severity(c: ValidationCheck): { label: string; tone: "red" | "amber" | "neutral" | "green" } {
  if (c.error_count > 0 || c.status === "FAILED" || c.status === "ERROR") return { label: "Critical", tone: "red" };
  if (checkSkipped(c)) return { label: "Skipped", tone: "neutral" };
  if (c.warning_count > 0) return { label: "Warning", tone: "amber" };
  return { label: "Passed", tone: "green" };
}

type Params = { view?: string; run?: string; q?: string; domain?: string; status?: string };

export default async function ReviewGate({ searchParams }: { searchParams: Params }) {
  const access = await guard("review");
  const persona = access.persona;
  const runs = await loadRuns("sort=updated&limit=150");
  const live = runs.data.runs.filter((r) => !r.is_archived && r.current_state !== "CANCELLED");
  const leaves = leafRuns(live);
  const q = (searchParams.q ?? "").trim().toLowerCase();
  const domainFilter = searchParams.domain ?? "all";
  const statusFilter = searchParams.status ?? "all";

  // the gates this person decides (or may request a decision on); governance leads see them all
  const oversees = has(access, "APPROVAL.VIEW");
  const isGatedForMe = (r: (typeof leaves)[number]) =>
    r.current_state in GATES
      ? oversees || actMode(access, GATES[r.current_state].privilege) !== "none"
      : r.current_state === "VALIDATION_FAILED" && (oversees || has(access, "RUN.OPERATE"));
  const gated = leaves.filter(isGatedForMe);

  const searched = leaves.filter((r) => !q || r.run_name.toLowerCase().includes(q) || (r.domain_name ?? "").toLowerCase().includes(q) || (r.source_system_name ?? "").toLowerCase().includes(q));
  const byDomain = searched.filter((r) => domainFilter === "all" || (r.domain_name ?? "No domain") === domainFilter);
  const matchesStatus = (r: (typeof leaves)[number]) => {
    if (statusFilter === "flagged") return r.current_state === "VALIDATION_FAILED" || r.current_state === "FAILED";
    if (statusFilter === "review") return r.current_state === "DBT_REVIEW";
    if (statusFilter === "passed") return ["VALIDATION_PASSED", "DBT_APPROVED", "COMPLETED"].includes(r.current_state);
    if (statusFilter === "gates") return ["MAPPING_REVIEW", "STTM_REVIEW", "SODA_REVIEW"].includes(r.current_state);
    return true;
  };
  const listed = byDomain.filter(matchesStatus);
  const inValidate = listed.filter((r) => ["VALIDATION_PENDING", "VALIDATION_RUNNING", "VALIDATION_FAILED", "VALIDATION_PASSED", "DBT_REVIEW", "MAPPING_REVIEW", "STTM_REVIEW", "SODA_REVIEW"].includes(r.current_state));
  const lists = {
    action: inValidate.filter((r) => isGatedForMe(r) || runNeedsMe(r, access)),
    active: inValidate,
    completed: listed.filter((r) => ["DBT_APPROVED", "COMPLETED", "DEPLOYED", "PIPELINE_ENABLED"].includes(r.current_state)),
  };

  let runId = resolveBoardRun(live, searchParams.run, access, "review")?.run_id
    ?? searchParams.run
    ?? defaultRun(leaves, ["VALIDATION_FAILED", "DBT_REVIEW", "VALIDATION_PASSED"])?.run_id;
  let bundle = runId ? await loadRunBundle(runId) : { data: null, error: null };
  if (bundle.data?.run.table_runs?.length && !bundle.data.run.run.parent_run_id) {
    const kids = bundle.data.run.table_runs;
    const pick = kids.find((t) => t.current_state === "VALIDATION_FAILED")
      ?? kids.find((t) => t.current_state in GATES)
      ?? kids.find((t) => t.current_state !== "COMPLETED" && t.current_state !== "DBT_APPROVED")
      ?? kids[0];
    if (pick && pick.run_id !== runId) {
      runId = pick.run_id;
      bundle = await loadRunBundle(runId);
    }
  }
  const b = bundle.data;
  const view: BoardView = (searchParams.view === "action" || searchParams.view === "active" || searchParams.view === "completed")
    ? searchParams.view
    : b ? "selected" : "active";
  const boardRows = view === "selected" ? [] : lists[view];
  const grouped = groupRunsByWorkflow(boardRows, live, (r) => isGatedForMe(r) || runNeedsMe(r, access));
  const select = (id: string) => `/os/review?${new URLSearchParams({ view: "selected", run: id })}`;

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
    { key: "all", label: "All gates", count: byDomain.length },
    { key: "flagged", label: "Flagged", count: byDomain.filter((r) => r.current_state === "VALIDATION_FAILED" || r.current_state === "FAILED").length, dot: byDomain.some((r) => r.current_state === "VALIDATION_FAILED" || r.current_state === "FAILED") },
    { key: "review", label: "Code review", count: byDomain.filter((r) => r.current_state === "DBT_REVIEW").length },
    { key: "passed", label: "Passed", count: byDomain.filter((r) => ["VALIDATION_PASSED", "DBT_APPROVED", "COMPLETED"].includes(r.current_state)).length },
    { key: "gates", label: "Design gates", count: byDomain.filter((r) => ["MAPPING_REVIEW", "STTM_REVIEW", "SODA_REVIEW"].includes(r.current_state)).length },
  ];
  const filterUrl = (patch: Partial<Params>) => {
    const next = { view: view === "selected" ? "active" : view, q: searchParams.q ?? "", domain: domainFilter, status: statusFilter, ...patch };
    const p = new URLSearchParams(Object.entries(next).filter(([k, v]) => v && (k === "view" || v !== "all")) as [string, string][]);
    return `/os/review?${p}`;
  };

  const queue = (
    <Card title="Gates waiting on people" subtitle="Every human gate across the domains, oldest first" pad={false}>
      {gated.length ? (
        <div className="os-divide">
          {[...gated].reverse().slice(0, 10).map((r) => (
            <Link key={r.run_id} href={`/os/review?run=${r.run_id}`} className="flex items-center justify-between gap-3 px-5 py-2.5 os-hover">
              <span className="min-w-0">
                <span className="block truncate text-[12.5px] font-bold">{r.run_name}</span>
                <span className="os-faint text-[11px]">{r.domain_name ?? "No domain"} · {GATES[r.current_state]?.role ?? "Forward Engineering Lead"} · {ago(r.updated_at ?? r.created_at)}</span>
              </span>
              <Tag tone={r.current_state === "VALIDATION_FAILED" ? "red" : "amber"}>{GATES[r.current_state]?.name ?? "Validation failed"}</Tag>
            </Link>
          ))}
        </div>
      ) : <div className="p-5"><Empty icon={CheckCircle2} title="No gate is waiting">Every run is moving or done.</Empty></div>}
    </Card>
  );

  if (!b || view !== "selected") {
    return (
      <div data-testid="validate-board">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="os-eyebrow">Validate · {persona.label}</div>
            <h1 className="text-[22px] font-extrabold tracking-[-0.03em]">Validation gate</h1>
          </div>
          <HomeSearch placeholder="Search workflows, tables or domains" />
        </div>
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <BoardTabs base="/os/review" view={view} selection={runId ? ["run", runId] : null}
                     fallback={lists.action[0] ? ["run", lists.action[0].run_id] : lists.active[0] ? ["run", lists.active[0].run_id] : null}
                     counts={{ action: lists.action.length, active: lists.active.length, completed: lists.completed.length }} />
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
        <SourceError what="This run" error={bundle.error ?? runs.error} />
        <BoardList count={grouped.length} empty={view === "action"
          ? { title: "No validation gate waits on you", body: "Runs flagged by validation or awaiting code review appear here." }
          : view === "active" ? { title: "No run is currently in validation" }
          : { title: "No validated runs match these filters" }}>
          {grouped.map((g) => <WorkflowGroupCard key={g.key} group={g} selectHref={select} currentRunId={runId} />)}
        </BoardList>
      </div>
    );
  }

  const { run } = b;
  const score = scoreOf(b.checks);
  const passed = gatePassed(score, b.gate);
  const steps = lifecycleOfRun(run, { generated: !!b.generation, validated: score.total > 0, validationPassed: passed });
  const failed = run.current_state === "VALIDATION_FAILED" || (score.score !== null && !passed);
  const min = b.gate.min_score;
  // why the gate held the run: the reasons VALIDATE_DBT recorded on its latest attempt, else worked out here
  const latest = b.attempts[0];
  const reasons = latest?.reasons.length ? latest.reasons : [
    ...(score.exact !== null && score.exact < min ? [`score ${score.score}% is below the ${min}% minimum`] : []),
    ...(b.gate.block_on_errors && score.critical ? [`${score.critical} error${score.critical === 1 ? "" : "s"} must be fixed`] : []),
  ];
  const validateMode = actMode(access, ACTIONS.validateAgain.privilege);
  const gate = GATES[run.current_state];
  const rows = b.checks.map((c) => ({ c, sev: severity(c), findings: findingsOf(c) }))
    .sort((x, y) => ["Critical", "Warning", "Skipped", "Passed"].indexOf(x.sev.label) - ["Critical", "Warning", "Skipped", "Passed"].indexOf(y.sev.label));
  const lastValidation = b.events.filter((e) => e.to_state.startsWith("VALIDATION_")).pop();
  // the skill to improve: the code standards agent's when its areas failed, else the validation playbook
  const standardsFailed = b.checks.some((c) => c.validation_type.startsWith("STANDARDS") && c.status === "FAILED");
  const validationSkill = (standardsFailed ? b.skills.find((s) => s.skill_name === "CODE-STANDARDS-REVIEW") : undefined)
    ?? b.skills.find((s) => /VALIDATION|READINESS/.test(s.skill_name)) ?? b.skills[0];
  const title = `${run.run.domain_name ?? "Run"} · ${tableOf(run.run.target_model) ?? run.run.run_name}`;
  // the gate decision, from the run's own allowed human transitions (as the run console derives them)
  // (a person's transitions only: cancelling a run, open to anyone, is not a send-back)
  const human = run.allowed_transitions.filter((t) => t.actor === "HUMAN" && t.to_state !== "CANCELLED");
  const approveTo = human.find((t) => t.to_state.endsWith("_APPROVED"))?.to_state ?? gate?.approveTo;
  const sendBack = human.filter((t) => !t.to_state.endsWith("_APPROVED")).map((t) => t.to_state);
  const gateMode = gate ? actMode(access, gate.privilege) : "none";
  const tableRuns = run.table_runs ?? [];
  const skillHref = `/os/skills?${new URLSearchParams({
    ...(validationSkill ? { skill: validationSkill.skill_name } : {}),
    run: run.run_id,
  })}`;

  return (
    <div data-testid="validate-board">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <BoardTabs base="/os/review" view={view} selection={["run", run.run_id]}
                   fallback={lists.action[0] ? ["run", lists.action[0].run_id] : ["run", run.run_id]}
                   counts={{ action: lists.action.length, active: lists.active.length, completed: lists.completed.length }} />
        <HomeSearch placeholder="Search workflows, tables or domains" />
      </div>

      <PageHeader
        eyebrow={`Validate · ${persona.label}`}
        title={<>Validation gate · <span className={failed ? "text-[var(--os-red-ink)]" : passed ? "text-[var(--os-emerald)]" : "os-muted"}>{failed ? "Needs attention" : passed ? "Passed" : "Not validated"}</span></>}
        subtitle={<>{title}. Independent reviewer and standards agents check generated dbt code, mapping consistency, data quality and naming before any pull request.</>}
        actions={<>
          <RunPicker base="/os/review" current={run.run_id} runs={runOptions(leaves)} />
          <Btn href={`/os/build?view=selected&run=${run.run_id}`}>Build workbench</Btn>
          {passed && <Btn href={`/os/release?view=selected&run=${run.run_id}`} variant="mint">Continue to Publish <ArrowRight className="h-3.5 w-3.5" /></Btn>}
          {failed
            ? <Action access={access} id="editSkill" label="Improve skill and regenerate" href={skillHref} variant="primary" icon={<Sparkles className="h-3.5 w-3.5" />} />
            : run.current_state === "DBT_REVIEW" ? <Action access={access} id="reviewCode" href={`/os/build/runs/${run.run_id}/review`} variant="primary" />
            : gate ? <Action access={access} id="decideGate" privilege={gate.privilege} label={`Open ${gate.name.toLowerCase()}`} href={gateHref(run.run_id, gate.slug)} variant="primary" /> : null}
        </>}
      />

      <WorkflowJourneyBar
        runId={run.run_id}
        currentStage="validate"
        generated={!!b.generation}
        validated={score.total > 0}
        validationPassed={passed}
        score={score.score}
        prPublished={!!(b.publication?.pr_url || b.publication?.status === "PUBLISHED")}
        released={["COMPLETED", "DEPLOYED", "PIPELINE_ENABLED"].includes(run.current_state)}
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
                <Link key={t.run_id} href={`/os/review?run=${encodeURIComponent(t.run_id)}`} scroll={false}
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

      {failed && (
        <Banner tone="red" icon={ShieldAlert}>
          <b>Validation flagged.</b> {reasons.length ? `${reasons.join("; ")}.` : `Score ${score.score ?? 0}%.`} The gate needs {gateText(b.gate)}, so the pull request is held: improve the failing checks at their source or the skill that produced them, then validate again.
        </Banner>
      )}
      {!failed && gate && (
        <Banner tone="amber" icon={Hand}><b>{gate.name} waits on the {gate.role}.</b> Agents prepared the proposal. Approve it here, or run Build end to end and autopilot decides it on the evidence; you can reopen it later from the store.</Banner>
      )}
      {b.errors.length > 0 && <SourceError what="Part of this run" error={b.errors.join(" · ")} />}

      <div className="mb-4"><Stepper steps={steps} hrefOf={(s) => s.status === "pending" ? null : osHref(run.run_id, s, run.run.domain_id)} /></div>

      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi label="Validation score" value={score.score === null ? "–" : `${score.score}%`} hint={`Gate ${gateText(b.gate)}`} icon={passed ? ShieldCheck : ShieldAlert} tone={score.score === null ? "neutral" : passed ? "green" : "red"} />
        <Kpi label="Critical findings" value={score.critical} hint={score.critical ? "must be fixed" : "none blocking"} icon={Flag} tone={score.critical ? "red" : "green"} />
        <Kpi label="Warnings" value={score.warnings} hint="advisory, not blocking" icon={TriangleAlert} tone={score.warnings ? "amber" : "green"} />
        <Kpi label="Checks evaluated" value={`${score.passed}/${score.total}`} hint={score.skipped ? `${score.skipped} skipped` : "all evaluated"} icon={ClipboardList} tone="blue" />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Card title="Reviewer agent findings" subtitle="The latest result of each independent check, with its evidence" pad={false}>
            {rows.length ? (
              <div className="overflow-x-auto">
                <table className="os-table">
                  <thead><tr><th>Severity</th><th>Check</th><th>Evidence</th><th>Fix in</th></tr></thead>
                  <tbody>
                    {rows.map(({ c, sev, findings }) => {
                      const fix = fixAt(c.validation_type);
                      const msg = findings.map((f) => String(f.message ?? f.detail ?? "")).find(Boolean);
                      return (
                        <tr key={c.validation_id}>
                          <td><Tag tone={sev.tone}>{sev.label}</Tag></td>
                          <td className="whitespace-nowrap font-semibold">{pretty(c.validation_type)}</td>
                          <td className="max-w-[440px]">
                            <div className="os-muted line-clamp-2 text-[11.5px]" title={msg}>{msg ? msg.split("\n")[0] : `${c.error_count} errors · ${c.warning_count} warnings`}</div>
                            {findings.length > 1 && <div className="os-faint text-[10.5px]">+{findings.length - 1} more finding{findings.length > 2 ? "s" : ""}</div>}
                          </td>
                          <td>{fix && !checkPassed(c) ? <Action access={access} id="workStep" privilege={fix.privilege} label={fix.label} href={`/os/build/runs/${run.run_id}/${fix.slug}`} /> : <span className="os-faint">–</span>}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : <div className="p-5"><Empty icon={CircleDashed} title="Not validated yet">Validation runs after the build agent generates code.</Empty></div>}
          </Card>

          <Card title="Run trace and evidence" subtitle="Immutable inputs the reviewer used">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Validation run">{lastValidation ? `${stateLabel(lastValidation.to_state)} · ${ago(lastValidation.created_at)}` : "None yet"}</Field>
              <Field label="Reviewer">{b.skills.filter((s) => /STANDARDS|VALIDATION|READINESS/.test(s.skill_name)).map((s) => `${s.skill_name.toLowerCase()}@${s.version}`).join(", ") || "Independent review agent"}</Field>
              <Field label="Evidence">{b.generation ? <code className="text-[11px]">{b.generation.stage_path}</code> : "No generation yet"}</Field>
              <Field label="Decision history">{b.reviews.length ? b.reviews.map((r) => `${pretty(r.stage)}: ${pretty(r.decision)} by ${/^Autopilot:/.test(r.business_justification ?? "") ? `autopilot (as ${r.reviewer})` : r.reviewer}`).join(" · ") : "No decision yet"}</Field>
            </div>
          </Card>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          {gate && approveTo && gateMode !== "none" && (
            <Card title={<>{gate.name}: your decision</>} subtitle={`Human gate · ${gate.role}. The agents prepared the proposal; it continues only once a person approves.`}
                  className="border-[var(--os-amber-line)]" action={<Btn href={gateHref(run.run_id, gate.slug)}>Read the evidence</Btn>}>
              <GateDecision runId={run.run_id} gate={gate.name} role={gate.role} approveTo={approveTo} sendBack={sendBack}
                            mode={gateMode} approver={access.approvers[gate.privilege]} previewing={!!access.preview} />
            </Card>
          )}
          <Card title="Human decision" subtitle="Policy-based gate. A score alone never bypasses a critical check.">
            <div className="flex items-end justify-between">
              <div className={`text-[34px] font-extrabold leading-none tracking-tight tabular-nums ${failed ? "text-[var(--os-red-ink)]" : passed ? "text-[var(--os-emerald)]" : "os-faint"}`}>
                {score.score ?? "–"}<span className="text-[15px]">%</span>
              </div>
              <span className="os-faint text-[11px]">required {min}%{b.gate.block_on_errors ? ", no errors" : ""}</span>
            </div>
            <div className="relative mt-2">
              <Bar className="h-2.5" value={score.score ?? 0} tone={failed ? "red" : "green"} />
              <span className="absolute top-[-3px] h-4 w-0.5 bg-[var(--os-ink)]" style={{ left: `${Math.min(100, Math.max(0, min))}%` }} title={`Gate ${min}%`} />
            </div>
            <div className="os-subtle mt-4 rounded-lg p-3 text-[12px] leading-relaxed">
              <b>Recommendation.</b>{" "}
              {failed ? "Fix the critical checks at their source (mapping, transformations or data quality), or improve the skill that produced them, then regenerate and validate again. Approved steps stay approved."
                : passed ? (run.current_state === "DBT_REVIEW" ? "The gate passed. A data developer reviews the generated code before it is merged." : "The gate passed; the workflow continues on its own.")
                : "Validation has not run yet for this run."}
            </div>
            <div className="mt-4 flex flex-wrap items-start gap-2">
              {failed && actMode(access, "RUN.OPERATE") === "do" && (
                <BuildEndToEnd runId={run.run_id} previewing={!!access.preview} label="Resume from checkpoint" />
              )}
              {b.generation && validateMode !== "none"
                ? <ValidateAgain runId={run.run_id} mode={validateMode} previewing={!!access.preview} variant={failed && actMode(access, "RUN.OPERATE") !== "do" ? "primary" : "default"} />
                : null}
              <Action access={access} id="revalidate" href={`/os/build/runs/${run.run_id}/validation`} />
            </div>
            {b.generation && actMode(access, ACTIONS.reviewStandards.privilege) !== "none" && (
              <div className="mt-3 border-t border-[var(--os-line)] pt-3">
                <div className="os-faint mb-2 text-[11px]">
                  Code standards agent{b.gate.standards_review === false ? " (off for this run's domain: it is not part of the gate)" : b.gate.standards_block === false ? " (advisory for this domain)" : ""}: reviews the generated code against the written standards, area by area.
                </div>
                <ReviewStandards runId={run.run_id} mode={actMode(access, ACTIONS.reviewStandards.privilege) as "do" | "request"} previewing={!!access.preview} />
              </div>
            )}
            <div className="mt-3 flex flex-wrap gap-2">
              {run.current_state === "DBT_REVIEW" && <Action access={access} id="reviewCode" label="Review the code" href={`/os/build/runs/${run.run_id}/review`} variant="mint" />}
              {failed && validationSkill && <Action access={access} id="editSkill" label="Open skill studio" href={skillHref} variant="warn" />}
            </div>
          </Card>

          <Attempts attempts={b.attempts} min={min} />
          <Card title="What happens next" subtitle="Controlled recovery, no work redone">
            <div className="os-divide">
              <ActivityRow tone={failed ? "amber" : "green"} icon={Sparkles} title="Skill or source fix" sub="A versioned skill change or a corrected mapping, owned by a person" />
              <ActivityRow tone="blue" icon={ArrowRight} title="Regenerate affected outputs" sub="Only generation and validation rerun; approved mapping and DQ stay approved" />
              <ActivityRow tone="green" icon={ShieldCheck} title="Independent validation" sub={`Must reach ${gateText(b.gate)} before code review`} />
            </div>
          </Card>
          {queue}
        </div>
      </div>
    </div>
  );
}

/** Every validation of the run, newest first: its score against the gate of the time, and how far the run came. */
function Attempts({ attempts, min }: { attempts: ValidationAttempt[]; min: number }) {
  if (!attempts.length) return null;
  const scored = attempts.filter((a) => a.score !== null);
  const first = scored[scored.length - 1];
  const last = scored[0];
  const from = scored.length > 1 ? first?.score ?? null : null;
  const to = last?.score ?? null;
  const trend = from === null || to === null ? `${attempts.length} attempt${attempts.length === 1 ? "" : "s"}`
    : to > from ? `Improved ${from}% → ${to}% over ${attempts.length} attempts`
    : to < from ? `Went from ${from}% to ${to}% over ${attempts.length} attempts`
    : `Steady at ${to}% over ${attempts.length} attempts`;
  return (
    <Card title="Validation attempts" subtitle={trend} pad={false}>
      <div className="os-divide" data-testid="validation-attempts">
        {attempts.slice(0, 8).map((a, i) => {
          const gate = a.min_score ?? min;
          const ok = a.status === "PASSED";
          return (
            <div key={a.validation_id} className="flex items-start justify-between gap-3 px-5 py-2.5">
              <div className="min-w-0">
                <div className="text-[12.5px] font-bold tabular-nums">
                  {a.score === null ? "No score" : `${a.score}%`}
                  <span className="os-faint ml-1.5 text-[10.5px] font-normal">gate {gate}% · {a.errors} error{a.errors === 1 ? "" : "s"} · {a.warnings} warning{a.warnings === 1 ? "" : "s"}{i === 0 ? " · latest" : ""}</span>
                </div>
                {a.reasons.length > 0 && <div className="os-muted text-[11px]">{a.reasons.join("; ")}</div>}
                <div className="os-faint text-[10.5px]">{ago(a.at)}</div>
              </div>
              <Tag tone={ok ? "green" : "red"}>{ok ? "Passed" : "Flagged"}</Tag>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
