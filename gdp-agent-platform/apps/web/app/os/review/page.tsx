import Link from "next/link";
import { ArrowRight, CheckCircle2, CircleDashed, ClipboardList, Flag, Hand, ShieldAlert, ShieldCheck, Sparkles, TriangleAlert } from "lucide-react";
import { GATES, stateLabel } from "@/lib/run-labels";
import {
  PASS_THRESHOLD, checkPassed, checkSkipped, defaultRun, findingsOf, loadRunBundle, loadRuns, runOptions, scoreOf, tableOf,
  type ValidationCheck,
} from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import { actMode, has } from "@/lib/os/access";
import { Action } from "@/components/os/action";
import { lifecycleOfRun, classicHref } from "@/lib/os/lifecycle";
import { ActivityRow, Banner, Bar, Btn, Card, Empty, Field, Kpi, PageHeader, SourceError, Stepper, Tag, ago, pretty } from "@/components/os/ui";
import { RunPicker } from "@/components/os/run-picker";
import { GateDecision } from "@/components/os/decide";

export const dynamic = "force-dynamic";
export const metadata = { title: "Validation gate" };

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

function severity(c: ValidationCheck): { label: string; tone: "red" | "amber" | "neutral" | "green" } {
  if (c.error_count > 0 || c.status === "FAILED" || c.status === "ERROR") return { label: "Critical", tone: "red" };
  if (checkSkipped(c)) return { label: "Skipped", tone: "neutral" };
  if (c.warning_count > 0) return { label: "Warning", tone: "amber" };
  return { label: "Passed", tone: "green" };
}

export default async function ReviewGate({ searchParams }: { searchParams: { run?: string } }) {
  const access = await guard("review");
  const persona = access.persona;
  const runs = await loadRuns("sort=updated&limit=150");
  // the gates this person decides (or may request a decision on); governance leads see them all
  const oversees = has(access, "APPROVAL.VIEW");
  const gated = runs.data.runs.filter((r) => !r.is_archived && (
    r.current_state in GATES ? oversees || actMode(access, GATES[r.current_state].privilege) !== "none"
      : r.current_state === "VALIDATION_FAILED" && (oversees || has(access, "RUN.OPERATE"))));
  const runId = searchParams.run ?? defaultRun(runs.data.runs, ["VALIDATION_FAILED", "DBT_REVIEW", "VALIDATION_PASSED"])?.run_id;
  const bundle = runId ? await loadRunBundle(runId) : { data: null, error: null };
  const b = bundle.data;

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

  if (!b) {
    return (
      <>
        <PageHeader eyebrow={`${persona.label} / Human in the loop`} title="Validation gate" />
        <SourceError what="This run" error={bundle.error ?? runs.error} />
        {queue}
      </>
    );
  }

  const { run } = b;
  const score = scoreOf(b.checks);
  const steps = lifecycleOfRun(run);
  const failed = run.current_state === "VALIDATION_FAILED" || score.critical > 0;
  const passed = score.score !== null && score.score >= PASS_THRESHOLD && score.critical === 0;
  const gate = GATES[run.current_state];
  const rows = b.checks.map((c) => ({ c, sev: severity(c), findings: findingsOf(c) }))
    .sort((x, y) => ["Critical", "Warning", "Skipped", "Passed"].indexOf(x.sev.label) - ["Critical", "Warning", "Skipped", "Passed"].indexOf(y.sev.label));
  const lastValidation = b.events.filter((e) => e.to_state.startsWith("VALIDATION_")).pop();
  const validationSkill = b.skills.find((s) => /VALIDATION|READINESS/.test(s.skill_name)) ?? b.skills[0];
  const title = `${run.run.domain_name ?? "Run"} · ${tableOf(run.run.target_model) ?? run.run.run_name}`;
  // the gate decision, from the run's own allowed human transitions (as the run console derives them)
  // (a person's transitions only: cancelling a run, open to anyone, is not a send-back)
  const human = run.allowed_transitions.filter((t) => t.actor === "HUMAN" && t.to_state !== "CANCELLED");
  const approveTo = human.find((t) => t.to_state.endsWith("_APPROVED"))?.to_state ?? gate?.approveTo;
  const sendBack = human.filter((t) => !t.to_state.endsWith("_APPROVED")).map((t) => t.to_state);
  const gateMode = gate ? actMode(access, gate.privilege) : "none";

  return (
    <>
      <PageHeader
        eyebrow={`${persona.label} / Human in the loop`}
        title={<>Validation gate · <span className={failed ? "text-[var(--os-red-ink)]" : passed ? "text-[var(--os-emerald)]" : "os-muted"}>{failed ? "Needs attention" : passed ? "Passed" : "Not validated"}</span></>}
        subtitle={<>{title}. A separate reviewer agent checks the generated code, mapping consistency, data quality config and naming before any pull request.</>}
        actions={<>
          <RunPicker base="/os/review" current={run.run_id} runs={runOptions(runs.data.runs)} />
          <Btn href={`/os/build?run=${run.run_id}`}>Build workbench</Btn>
          {failed
            ? <Action access={access} id="editSkill" label="Improve skill and regenerate" href={`/os/skills${validationSkill ? `?skill=${encodeURIComponent(validationSkill.skill_name)}` : ""}`} variant="primary" icon={<Sparkles className="h-3.5 w-3.5" />} />
            : run.current_state === "DBT_REVIEW" ? <Action access={access} id="reviewCode" href={`/runs/${run.run_id}/review`} variant="primary" />
            : gate ? <Action access={access} id="decideGate" privilege={gate.privilege} label={`Open ${gate.name.toLowerCase()}`} href={`/runs/${run.run_id}/${gate.slug}`} variant="primary" /> : null}
        </>}
      />

      {failed && (
        <Banner tone="red" icon={ShieldAlert}>
          <b>Validation blocked.</b> Score {score.score ?? 0}% with {score.critical} critical error{score.critical === 1 ? "" : "s"}; the gate needs {PASS_THRESHOLD}% and no errors. The pull request is held until the failing checks pass.
        </Banner>
      )}
      {!failed && gate && (
        <Banner tone="amber" icon={Hand}><b>{gate.name} waits on a {gate.role.toLowerCase()}.</b> Agents prepared the proposal; a person approves it before the workflow continues.</Banner>
      )}
      {b.errors.length > 0 && <SourceError what="Part of this run" error={b.errors.join(" · ")} />}

      <div className="mb-4"><Stepper steps={steps} hrefOf={(s) => s.status === "pending" ? null : classicHref(run.run_id, s)} /></div>

      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi label="Validation score" value={score.score === null ? "–" : `${score.score}%`} hint={`Policy threshold ${PASS_THRESHOLD}%`} icon={passed ? ShieldCheck : ShieldAlert} tone={score.score === null ? "neutral" : passed ? "green" : "red"} />
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
                      const fix = FIX_AT[c.validation_type];
                      const msg = findings.map((f) => String(f.message ?? f.detail ?? "")).find(Boolean);
                      return (
                        <tr key={c.validation_id}>
                          <td><Tag tone={sev.tone}>{sev.label}</Tag></td>
                          <td className="whitespace-nowrap font-semibold">{pretty(c.validation_type)}</td>
                          <td className="max-w-[440px]">
                            <div className="os-muted line-clamp-2 text-[11.5px]" title={msg}>{msg ? msg.split("\n")[0] : `${c.error_count} errors · ${c.warning_count} warnings`}</div>
                            {findings.length > 1 && <div className="os-faint text-[10.5px]">+{findings.length - 1} more finding{findings.length > 2 ? "s" : ""}</div>}
                          </td>
                          <td>{fix && !checkPassed(c) ? <Action access={access} id="workStep" privilege={fix.privilege} label={fix.label} href={`/runs/${run.run_id}/${fix.slug}`} /> : <span className="os-faint">–</span>}</td>
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
              <Field label="Reviewer">{b.skills.filter((s) => /VALIDATION|READINESS/.test(s.skill_name)).map((s) => `${s.skill_name.toLowerCase()}@${s.version}`).join(", ") || "Independent review agent"}</Field>
              <Field label="Evidence">{b.generation ? <code className="text-[11px]">{b.generation.stage_path}</code> : "No generation yet"}</Field>
              <Field label="Decision history">{b.reviews.length ? b.reviews.map((r) => `${pretty(r.stage)}: ${pretty(r.decision)} by ${r.reviewer}`).join(" · ") : "No human decision yet"}</Field>
            </div>
          </Card>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          {gate && approveTo && gateMode !== "none" && (
            <Card title={<>{gate.name}: your decision</>} subtitle={`Human gate · ${gate.role}. The agents prepared the proposal; it continues only once a person approves.`}
                  className="border-[var(--os-amber-line)]" action={<Btn href={`/runs/${run.run_id}/${gate.slug}`}>Read the evidence</Btn>}>
              <GateDecision runId={run.run_id} gate={gate.name} role={gate.role} approveTo={approveTo} sendBack={sendBack}
                            mode={gateMode} approver={access.approvers[gate.privilege]} previewing={!!access.preview} />
            </Card>
          )}
          <Card title="Human decision" subtitle="Policy-based gate. A score alone never bypasses a critical check.">
            <div className="flex items-end justify-between">
              <div className={`text-[34px] font-extrabold leading-none tracking-tight tabular-nums ${failed ? "text-[var(--os-red-ink)]" : passed ? "text-[var(--os-emerald)]" : "os-faint"}`}>
                {score.score ?? "–"}<span className="text-[15px]">%</span>
              </div>
              <span className="os-faint text-[11px]">required {PASS_THRESHOLD}%</span>
            </div>
            <div className="relative mt-2">
              <Bar className="h-2.5" value={score.score ?? 0} tone={failed ? "red" : "green"} />
              <span className="absolute top-[-3px] h-4 w-0.5 bg-[var(--os-ink)]" style={{ left: `${PASS_THRESHOLD}%` }} title={`Gate ${PASS_THRESHOLD}%`} />
            </div>
            <div className="os-subtle mt-4 rounded-lg p-3 text-[12px] leading-relaxed">
              <b>Recommendation.</b>{" "}
              {failed ? "Fix the critical checks at their source (mapping, transformations or data quality), or improve the skill that produced them, then regenerate and validate again. Approved steps stay approved."
                : passed ? (run.current_state === "DBT_REVIEW" ? "The gate passed. A data developer reviews the generated code before it is merged." : "The gate passed; the workflow continues on its own.")
                : "Validation has not run yet for this run."}
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <Action access={access} id="revalidate" href={`/runs/${run.run_id}/validation`} variant={failed ? "primary" : "default"} />
              {run.current_state === "DBT_REVIEW" && <Action access={access} id="reviewCode" label="Review the code" href={`/runs/${run.run_id}/review`} variant="mint" />}
              {failed && validationSkill && <Action access={access} id="editSkill" label="Open skill studio" href={`/os/skills?skill=${encodeURIComponent(validationSkill.skill_name)}`} variant="warn" />}
            </div>
          </Card>

          <Card title="What happens next" subtitle="Controlled recovery, no work redone">
            <div className="os-divide">
              <ActivityRow tone={failed ? "amber" : "green"} icon={Sparkles} title="Skill or source fix" sub="A versioned skill change or a corrected mapping, owned by a person" />
              <ActivityRow tone="blue" icon={ArrowRight} title="Regenerate affected outputs" sub="Only generation and validation rerun; approved mapping and DQ stay approved" />
              <ActivityRow tone="green" icon={ShieldCheck} title="Independent validation" sub={`Must reach ${PASS_THRESHOLD}% with no critical error before code review`} />
            </div>
          </Card>
          {queue}
        </div>
      </div>
    </>
  );
}
