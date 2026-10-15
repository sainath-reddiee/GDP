"use client";

import { useEffect, useState } from "react";
import { Bot, Check, CircleDot, Lock, Pause, Play, RotateCcw, Sparkles, UserCheck, Zap } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * One Silver run, replayed the way the app runs it: the supervisor agent (or Autopilot) calls the stage tools and
 * stops at each of the four human approval gates; an approver with the right role approves; after the STTM is approved, QA,
 * Data Quality and Code Generation then Validation run in parallel and merge into Code review. Node names, state
 * names, actions and roles are the app's own (workflow_graph.json, components/stage-rail, services/workflow/autopilot,
 * services/governance/policy). The source tables are illustrative.
 */

type NodeId = "source" | "profiling" | "modeling" | "qa" | "dq" | "code" | "validation" | "review";
type Status = "locked" | "progress" | "review" | "complete";
type Actor = "platform" | "agent" | "ai" | "human" | "gate";
type Line = { actor: Actor; who: string; text: string };
type Step = { state: string; nodes: Partial<Record<NodeId, Status>>; lines: Line[]; gate?: number; hold?: boolean };

const GATES = ["Field mapping", "STTM", "Data quality and QA sign-off", "Code review"];

const STEPS: Step[] = [
  { state: "CREATED", nodes: {}, lines: [{ actor: "platform", who: "Run", text: "Silver run opened on CRM.CUSTOMER, CUSTOMER_ADDRESS" }] },
  { state: "LANDING_COMPLETE", nodes: { source: "complete" },
    lines: [{ actor: "platform", who: "Platform", text: "prepare_source: access validated, tables landed in Snowflake" }] },
  { state: "PROFILING_COMPLETE", nodes: { source: "complete", profiling: "complete" },
    lines: [{ actor: "agent", who: "Agent", text: "run_profiling(): statistics, patterns, keys and drift for every column" },
            { actor: "ai", who: "Cortex AI", text: "column descriptions and PII hints added to the profile" }] },
  { state: "DOMAIN_IDENTIFIED", nodes: { source: "complete", profiling: "complete" },
    lines: [{ actor: "agent", who: "Agent", text: "identify_domain(): knowledge pack matched with Cortex Search" }] },
  { state: "MAPPING_REVIEW", nodes: { source: "complete", profiling: "complete", modeling: "review" }, gate: 0, hold: true,
    lines: [{ actor: "agent", who: "Agent", text: "generate_mapping_candidates(): ranked with evidence on 7 signals" },
            { actor: "gate", who: "Awaiting approval", text: "Field mapping · MAPPING.APPROVE · model approver" }] },
  { state: "MAPPING_APPROVED", nodes: { source: "complete", profiling: "complete", modeling: "progress" }, gate: 0,
    lines: [{ actor: "human", who: "Human approval", text: "model approver approved the mapping, justification logged" }] },
  { state: "STTM_REVIEW", nodes: { source: "complete", profiling: "complete", modeling: "review" }, gate: 1, hold: true,
    lines: [{ actor: "agent", who: "Agent", text: "generate_sttm(): STTM v1 with transformations and joins" },
            { actor: "gate", who: "Awaiting approval", text: "STTM · STTM.APPROVE · STTM approver" }] },
  { state: "STTM_APPROVED", nodes: { source: "complete", profiling: "complete", modeling: "complete", qa: "progress", dq: "progress", code: "progress" }, gate: 1,
    lines: [{ actor: "human", who: "Human approval", text: "STTM approver approved; three lanes start in parallel" }] },
  { state: "VALIDATION_PASSED", nodes: { source: "complete", profiling: "complete", modeling: "complete", qa: "review", dq: "review", code: "complete", validation: "complete" },
    gate: 2, hold: true,
    lines: [{ actor: "agent", who: "Agent", text: "generate_soda(): SodaCL checks and a Great Expectations suite" },
            { actor: "agent", who: "Agent", text: "generate_dbt(), validate_dbt(): Silver dbt compiled on Snowflake" },
            { actor: "gate", who: "Awaiting approval", text: "Data quality (DQ approver) and QA sign-off (QA lead)" }] },
  { state: "DBT_REVIEW", nodes: { source: "complete", profiling: "complete", modeling: "complete", qa: "complete", dq: "complete", code: "complete", validation: "complete", review: "review" },
    gate: 3, hold: true,
    lines: [{ actor: "human", who: "Human approval", text: "DQ approver approved the checks; QA lead signed off" },
            { actor: "ai", who: "Cortex AI", text: "advisory code review attached to each generated file" },
            { actor: "gate", who: "Awaiting approval", text: "Code review · REVIEW.APPROVE · code reviewer" }] },
  { state: "COMPLETED", nodes: { source: "complete", profiling: "complete", modeling: "complete", qa: "complete", dq: "complete", code: "complete", validation: "complete", review: "complete" },
    gate: 4, hold: true,
    lines: [{ actor: "human", who: "Human approval", text: "code reviewer approved the generated code" },
            { actor: "platform", who: "GitHub", text: "dbt files ready to publish as a pull request" }] },
];

const STEP_MS = 1700;
const HOLD_MS = 2900;

function useReducedMotion() {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const q = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(q.matches);
    const on = () => setReduced(q.matches);
    q.addEventListener("change", on);
    return () => q.removeEventListener("change", on);
  }, []);
  return reduced;
}

const STATUS_WORD: Record<Status, string> = { locked: "locked", progress: "agent working", review: "awaiting approval", complete: "done" };

function Node({ label, sub, status, className }: { label: string; sub?: string; status: Status; className?: string }) {
  return (
    <div className={cn("relative rounded-xl border px-3 py-2.5 transition-all duration-500",
      status === "complete" ? "border-emerald-500/40 bg-emerald-500/[0.07]"
        : status === "review" ? "border-[#ff7a3d]/60 bg-[#ff7a3d]/[0.09] shadow-[0_0_0_3px_rgba(255,122,61,0.14)]"
        : status === "progress" ? "border-cyan-400/50 bg-cyan-400/[0.07]"
        : "border-white/[0.07] bg-white/[0.02]", className)}>
      <div className="flex items-center gap-1.5">
        {status === "complete" ? <Check className="h-3.5 w-3.5 text-emerald-400" />
          : status === "review" ? <UserCheck className="h-3.5 w-3.5 text-[#ffb68f]" />
          : status === "progress" ? <CircleDot className="h-3.5 w-3.5 animate-glow text-cyan-300" />
          : <Lock className="h-3 w-3 text-slate-600" />}
        <span className={cn("truncate text-[12px] font-semibold", status === "locked" ? "text-slate-500" : "text-slate-100")}>{label}</span>
      </div>
      {sub && <p className="mt-0.5 truncate pl-5 text-[10px] text-slate-500">{sub}</p>}
      <p className={cn("mt-1 pl-5 font-mono text-[10px]",
        status === "complete" ? "text-emerald-400/90" : status === "review" ? "text-[#ffb68f]" : status === "progress" ? "text-cyan-300" : "text-slate-600")}>
        {STATUS_WORD[status]}
      </p>
    </div>
  );
}

const TAG: Record<Actor, { cls: string; icon: typeof Bot }> = {
  platform: { cls: "bg-white/[0.06] text-slate-300 ring-white/15", icon: Zap },
  agent: { cls: "bg-[#6d6af8]/20 text-[#c7c6ff] ring-[#8b89ff]/40", icon: Bot },
  ai: { cls: "bg-[#29b5e8]/15 text-[#9be3fb] ring-[#29b5e8]/35", icon: Sparkles },
  human: { cls: "bg-[#ff7a3d]/15 text-[#ffc2a3] ring-[#ff7a3d]/40", icon: UserCheck },
  gate: { cls: "bg-[#ff7a3d]/10 text-[#ffb68f] ring-[#ff7a3d]/30", icon: Lock },
};

export function RunPreview() {
  const reduced = useReducedMotion();
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [loop, setLoop] = useState(0);
  const shown = reduced ? STEPS.length - 1 : step;
  const s = STEPS[shown];

  useEffect(() => {
    if (!playing || reduced) return;
    const t = setTimeout(() => {
      if (step >= STEPS.length - 1) { setStep(0); setLoop((l) => l + 1); } else setStep(step + 1);
    }, STEPS[step].hold || step === STEPS.length - 1 ? HOLD_MS : STEP_MS);
    return () => clearTimeout(t);
  }, [step, playing, reduced]);

  const st = (id: NodeId): Status => s.nodes[id] ?? "locked";
  const lines = STEPS.slice(0, shown + 1).flatMap((x, i) => x.lines.map((l, j) => ({ ...l, key: `${loop}-${i}-${j}` }))).slice(-6);
  const gatesDone = s.gate == null ? 0 : s.hold ? s.gate : s.gate + 1;
  const autopilotOn = !s.hold && shown < STEPS.length - 1;

  return (
    <section aria-label="An illustrative Silver run replayed stage by stage" data-testid="run-preview"
             className="flex h-full flex-col overflow-hidden rounded-[6px] border border-white/[0.14] bg-[#191812]">
      {/* run header, as on the run page */}
      <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.06] px-5 py-3">
        <span className="flex gap-1.5" aria-hidden>
          <span className="h-2.5 w-2.5 rounded-full bg-rose-400/70" /><span className="h-2.5 w-2.5 rounded-full bg-amber-400/70" /><span className="h-2.5 w-2.5 rounded-full bg-emerald-400/70" />
        </span>
        <span className="ml-2 text-sm font-semibold text-white">crm_customer_onboarding</span>
        <span className="rounded-full bg-slate-200/10 px-2 py-0.5 text-[10px] font-semibold text-slate-200 ring-1 ring-inset ring-white/10">Silver run</span>
        <span key={s.state} className="animate-stage-in rounded-md bg-black/40 px-2 py-0.5 font-mono text-[10px] text-cyan-200 ring-1 ring-inset ring-cyan-400/20">{s.state}</span>
        <span className="ml-auto flex items-center gap-2">
          <span className={cn("inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[11px] font-semibold transition",
            autopilotOn ? "bg-indigo-500/20 text-indigo-200 ring-1 ring-inset ring-indigo-400/40" : "bg-white/[0.04] text-slate-500 ring-1 ring-inset ring-white/10")}>
            <Bot className={cn("h-3 w-3", autopilotOn && "animate-glow")} /> {autopilotOn ? "Agent working…" : shown === STEPS.length - 1 ? "Run complete" : "Awaiting human approval"}
          </span>
          <button type="button" onClick={() => setPlaying((p) => !p)} aria-label={playing ? "Pause" : "Play"} disabled={reduced}
                  className="rounded-lg p-1.5 text-slate-400 ring-1 ring-inset ring-white/10 transition hover:bg-white/5 hover:text-white disabled:opacity-40">
            {playing ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
          </button>
          <button type="button" onClick={() => { setStep(0); setLoop((l) => l + 1); setPlaying(true); }} aria-label="Restart" disabled={reduced}
                  className="rounded-lg p-1.5 text-slate-400 ring-1 ring-inset ring-white/10 transition hover:bg-white/5 hover:text-white disabled:opacity-40">
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
        </span>
      </div>

      {/* the stage rail: sequence, then three lanes in parallel, merging into code review */}
      <div className="overflow-x-auto px-5 pt-5">
        <div className="grid min-w-[720px] grid-cols-[1fr_1fr_1.15fr_auto_1.7fr_auto_1fr] items-center gap-2.5">
          <Node label="Source Ingestion" sub="validate and land" status={st("source")} />
          <Node label="Profiling" sub="profile, match domain" status={st("profiling")} />
          <Node label="Modeling" sub={shown < 6 ? "field mapping" : "transformations and joins"} status={st("modeling")} />
          <Fork active={["complete", "progress", "review"].includes(st("qa"))} />
          <div className="space-y-2">
            <Node label="QA tests" sub="SQL tests from the STTM" status={st("qa")} />
            <Node label="Data Quality" sub="SodaCL and GX checks" status={st("dq")} />
            <div className="grid grid-cols-2 gap-2">
              <Node label="Silver Code Gen" sub="dbt models" status={st("code")} />
              <Node label="Validation" sub="compile on Snowflake" status={st("validation")} />
            </div>
          </div>
          <Fork merge active={st("review") !== "locked"} />
          <Node label="Code review" sub="then GitHub PR" status={st("review")} />
        </div>
      </div>

      <div className="grid flex-1 gap-4 px-5 py-5 md:grid-cols-[1fr_220px]">
        {/* agent console */}
        <div className="flex min-h-[200px] flex-col rounded-2xl border border-white/[0.06] bg-black/30 p-4" aria-live="polite">
          <p className="mb-3 flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.16em] text-slate-500">
            <span className="h-1.5 w-1.5 animate-glow rounded-full bg-emerald-400" /> agent console
          </p>
          <ol className="space-y-2">
            {lines.map((l) => {
              const t = TAG[l.actor];
              const Icon = t.icon;
              return (
                <li key={l.key} className="animate-stage-in flex items-start gap-2.5 text-[12px]">
                  <span className={cn("inline-flex w-[118px] shrink-0 items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[10px] font-semibold ring-1 ring-inset", t.cls)}>
                    <Icon className="h-3 w-3 shrink-0" /> <span className="truncate">{l.who}</span>
                  </span>
                  <span className={cn("pt-px", l.actor === "gate" ? "text-[#ffc2a3]" : l.actor === "human" ? "text-[#ffe0cf]" : "text-slate-300")}>{l.text}</span>
                </li>
              );
            })}
          </ol>
        </div>
        {/* gates */}
        <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-slate-500">Human approval gates</p>
          <ol className="mt-3 space-y-2.5">
            {GATES.map((g, i) => {
              const done = i < gatesDone;
              const waiting = s.hold && s.gate === i;
              return (
                <li key={g} className="flex items-start gap-2 text-[12px]">
                  <span className={cn("mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full ring-1 ring-inset transition",
                    done ? "bg-emerald-500 ring-emerald-500" : waiting ? "bg-[#ff7a3d]/20 ring-[#ff7a3d]" : "ring-white/15")}>
                    {done ? <Check className="h-2.5 w-2.5 text-[#191812]" /> : waiting ? <span className="h-1.5 w-1.5 animate-glow rounded-full bg-[#ffb68f]" /> : null}
                  </span>
                  <span className={cn(done ? "text-slate-300" : waiting ? "font-medium text-[#ffc2a3]" : "text-slate-500")}>{g}</span>
                </li>
              );
            })}
          </ol>
          <p className="mt-4 border-t border-white/[0.06] pt-3 text-[11px] leading-relaxed text-slate-500">
            Agents never approve. Each gate needs an approver role and a written justification, recorded in the audit trail.
          </p>
        </div>
      </div>
    </section>
  );
}

/** The fork after the STTM, and the merge into code review. */
function Fork({ merge, active }: { merge?: boolean; active: boolean }) {
  const c = active ? "#34d399" : "#334155";
  return (
    <svg viewBox="0 0 28 120" className="h-[150px] w-6" aria-hidden>
      {merge ? (
        <path d="M0 18 C18 18 10 60 28 60 M0 60 L28 60 M0 102 C18 102 10 60 28 60" fill="none" stroke={c} strokeWidth="1.6" className="transition-colors duration-500" />
      ) : (
        <path d="M0 60 C18 60 10 18 28 18 M0 60 L28 60 M0 60 C18 60 10 102 28 102" fill="none" stroke={c} strokeWidth="1.6" className="transition-colors duration-500" />
      )}
    </svg>
  );
}
