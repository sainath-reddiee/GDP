"use client";

import { useEffect, useState } from "react";
import {
  Bot, Building2, CircleDollarSign, Code2, Database, FileSpreadsheet, FlaskConical, GitCompareArrows,
  GitPullRequest, ScanSearch, ShieldCheck, Sparkles, UserCheck, Users, type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

/** A run told as agents at work: each stage is an agent, three stages stop for a person, and after the STTM the
 *  QA, Data Quality and dbt agents work in parallel before everything merges into code review. */

type Scenario = {
  key: string; label: string; icon: LucideIcon; schema: string; tables: string; columns: number; domain: string;
  confidence: number; target: string; mappings: number; open: number; join: string; tests: number; checks: number;
  model: string;
};

const SCENARIOS: Scenario[] = [
  { key: "property", label: "Property", icon: Building2, schema: "BRONZE.LIGHTBOX", tables: "BUILDINGS, PARCELS", columns: 41,
    domain: "Property", confidence: 70, target: "PROPERTY_CORE", mappings: 27, open: 3, join: "BUILDINGS to PARCELS on PARCEL_LID (N:1, 98%)",
    tests: 18, checks: 12, model: "property_core.sql" },
  { key: "company", label: "Company", icon: Users, schema: "BRONZE.CRM", tables: "CUSTOMER, CUSTOMER_ADDRESS", columns: 36,
    domain: "Company", confidence: 63, target: "COMPANY_CORE", mappings: 18, open: 2, join: "CUSTOMER_ADDRESS to CUSTOMER on CUST_ID (N:1, 97%)",
    tests: 15, checks: 10, model: "company_core.sql" },
  { key: "opportunity", label: "Opportunity", icon: CircleDollarSign, schema: "BRONZE.SALESFORCE", tables: "OPPORTUNITY, ACCOUNT", columns: 52,
    domain: "Opportunity", confidence: 57, target: "OPPORTUNITY_CORE", mappings: 34, open: 4, join: "OPPORTUNITY to ACCOUNT on ACCOUNTID (N:1, 96%)",
    tests: 21, checks: 14, model: "opportunity_core.sql" },
];

type NodeDef = { id: string; label: string; agent: string; icon: LucideIcon; x: number; y: number; step: number; gate?: boolean };

const NODES: NodeDef[] = [
  { id: "source", label: "Source", agent: "Supervisor", icon: Database, x: 44, y: 150, step: 0 },
  { id: "profile", label: "Profile", agent: "Profiler", icon: ScanSearch, x: 132, y: 150, step: 1 },
  { id: "domain", label: "Domain", agent: "Domain", icon: Sparkles, x: 220, y: 150, step: 2 },
  { id: "mapping", label: "Mapping", agent: "Mapper", icon: GitCompareArrows, x: 308, y: 150, step: 3, gate: true },
  { id: "sttm", label: "STTM", agent: "Contract", icon: FileSpreadsheet, x: 396, y: 150, step: 4, gate: true },
  { id: "qa", label: "QA tests", agent: "QA", icon: FlaskConical, x: 512, y: 58, step: 5 },
  { id: "dq", label: "Data Quality", agent: "Quality", icon: ShieldCheck, x: 512, y: 150, step: 5 },
  { id: "dbt", label: "dbt", agent: "dbt", icon: Code2, x: 512, y: 242, step: 5 },
  { id: "validate", label: "Validate", agent: "Validator", icon: ShieldCheck, x: 610, y: 242, step: 6 },
  { id: "review", label: "Code review", agent: "Reviewer", icon: GitPullRequest, x: 716, y: 150, step: 7, gate: true },
];

const at = (id: string) => NODES.find((n) => n.id === id) as NodeDef;
const curve = (a: NodeDef, b: NodeDef) => {
  const ax = a.x + 20, bx = b.x - 20, mx = (ax + bx) / 2;
  return a.y === b.y ? `M${ax} ${a.y} L${bx} ${b.y}` : `M${ax} ${a.y} C${mx} ${a.y} ${mx} ${b.y} ${bx} ${b.y}`;
};
const EDGES = [
  ["source", "profile", 1], ["profile", "domain", 2], ["domain", "mapping", 3], ["mapping", "sttm", 4],
  ["sttm", "qa", 5], ["sttm", "dq", 5], ["sttm", "dbt", 5], ["dbt", "validate", 6],
  ["qa", "review", 7], ["dq", "review", 7], ["validate", "review", 7],
].map(([from, to, step]) => ({ id: `${from}-${to}`, d: curve(at(from as string), at(to as string)), step: step as number }));

type Line = { agent: string; tone: string; text: string; human?: boolean };

function script(s: Scenario): Line[][] {
  return [
    [{ agent: "Supervisor", tone: "bg-sky-500", text: `New run on ${s.schema}: ${s.tables}` }],
    [{ agent: "Profiler", tone: "bg-cyan-500", text: `Profiled ${s.columns} columns in place. Nothing copied; profile staged for reuse` }],
    [{ agent: "Domain", tone: "bg-violet-500", text: `Matched the ${s.domain} contract (${s.confidence}%), target ${s.target}` }],
    [{ agent: "Mapper", tone: "bg-indigo-500", text: `Proposed ${s.mappings} column mappings with evidence; ${s.open} need a decision` },
     { agent: "Reviewer", tone: "bg-amber-500", text: "Approved the mapping", human: true }],
    [{ agent: "Contract", tone: "bg-blue-500", text: `STTM built. Join plan: ${s.join}` },
     { agent: "Reviewer", tone: "bg-amber-500", text: "Approved the STTM", human: true }],
    [{ agent: "QA", tone: "bg-fuchsia-500", text: `Wrote ${s.tests} functional test queries` },
     { agent: "Quality", tone: "bg-emerald-500", text: `Backtested ${s.checks} Soda checks on today's data` },
     { agent: "dbt", tone: "bg-orange-500", text: `Generated ${s.model} with hub and history` }],
    [{ agent: "Validator", tone: "bg-teal-500", text: "Model compiles; contract and checks line up" }],
    [{ agent: "Reviewer", tone: "bg-amber-500", text: "Approved the code. Ready to ship", human: true }],
  ];
}

/** What the agents hand over, revealed as the run reaches the step that produces it. */
function deliverables(s: Scenario): { step: number; icon: LucideIcon; title: string; detail: string; tone: string }[] {
  return [
    { step: 1, icon: ScanSearch, title: "Column profiles", detail: `${s.columns} columns, staged`, tone: "text-cyan-300" },
    { step: 3, icon: GitCompareArrows, title: "Mappings", detail: `${s.mappings} with evidence`, tone: "text-indigo-300" },
    { step: 4, icon: FileSpreadsheet, title: "STTM contract", detail: `into ${s.target}`, tone: "text-blue-300" },
    { step: 5, icon: FlaskConical, title: "QA suite", detail: `${s.tests} SQL tests`, tone: "text-fuchsia-300" },
    { step: 5, icon: ShieldCheck, title: "Soda + GX checks", detail: `${s.checks} backtested`, tone: "text-emerald-300" },
    { step: 5, icon: Code2, title: "dbt model", detail: s.model, tone: "text-orange-300" },
  ];
}

const STEP_MS = 1900;
const HOLD_MS = 3600;
const LAST = 7;

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

function GraphNode({ n, step }: { n: NodeDef; step: number }) {
  const state = step < n.step ? "idle" : step === n.step ? "active" : "done";
  const Icon = n.icon;
  const ring = state === "active" ? (n.gate ? "#F59E0B" : "#22D3EE") : state === "done" ? "#10B981" : "#334155";
  return (
    <g>
      {state === "active" && (
        <circle cx={n.x} cy={n.y} r="20" fill="none" stroke={ring} strokeWidth="2" opacity="0.6">
          <animate attributeName="r" values="20;31;20" dur="1.6s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="0.6;0;0.6" dur="1.6s" repeatCount="indefinite" />
        </circle>
      )}
      <circle cx={n.x} cy={n.y} r="20" fill={state === "idle" ? "#0B1220" : "#0F172A"} stroke={ring} strokeWidth={state === "idle" ? 1.2 : 2} />
      <Icon x={n.x - 9} y={n.y - 9} width={18} height={18}
            color={state === "idle" ? "#475569" : state === "done" ? "#6EE7B7" : n.gate ? "#FCD34D" : "#A5F3FC"} strokeWidth={2} />
      {n.gate && (
        <g>
          <circle cx={n.x + 15} cy={n.y - 15} r="8" fill={state === "done" ? "#10B981" : "#F59E0B"} stroke="#080B10" strokeWidth="2" />
          <UserCheck x={n.x + 10} y={n.y - 20} width={10} height={10} color="#0B1220" strokeWidth={2.5} />
        </g>
      )}
      <text x={n.x} y={n.y + 36} textAnchor="middle" fontSize="11" fontWeight={600}
            fill={state === "idle" ? "#64748B" : "#E2E8F0"}>{n.label}</text>
      <text x={n.x} y={n.y + 49} textAnchor="middle" fontSize="9" fill={state === "active" ? (n.gate ? "#FBBF24" : "#67E8F9") : "#475569"}>
        {state === "active" ? (n.gate ? "awaiting approval" : `${n.agent} agent working`) : state === "done" ? (n.gate ? "approved" : "done") : `${n.agent} agent`}
      </text>
    </g>
  );
}

export function AgentOrchestration() {
  const [scenarioKey, setScenarioKey] = useState(SCENARIOS[0].key);
  const [step, setStep] = useState(0);
  const [run, setRun] = useState(0);
  const [paused, setPaused] = useState(false);
  const reduced = useReducedMotion();
  const scenario = SCENARIOS.find((s) => s.key === scenarioKey) ?? SCENARIOS[0];
  const shown = reduced ? LAST : step;

  useEffect(() => {
    if (paused || reduced) return;
    const timer = setTimeout(() => {
      if (step >= LAST) { setStep(0); setRun((r) => r + 1); } else setStep(step + 1);
    }, step >= LAST ? HOLD_MS : STEP_MS);
    return () => clearTimeout(timer);
  }, [step, paused, reduced]);

  const pick = (key: string) => { setScenarioKey(key); setStep(0); setRun((r) => r + 1); };
  const lines = script(scenario).slice(0, shown + 1).flatMap((group, i) => group.map((line, j) => ({ ...line, key: `${run}-${i}-${j}` })));
  const visible = lines.slice(-6);

  return (
    <section aria-label="How an agentic pipeline run works" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}
             className="relative flex h-full flex-col overflow-hidden rounded-3xl border border-slate-800/60 bg-[rgba(15,23,42,0.65)] shadow-[0_30px_80px_-30px_rgba(2,6,23,0.9)] backdrop-blur-xl">
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-800/60 px-5 py-3">
        <span className="flex gap-1.5" aria-hidden>
          <span className="h-2.5 w-2.5 rounded-full bg-rose-400/70" />
          <span className="h-2.5 w-2.5 rounded-full bg-amber-400/70" />
          <span className="h-2.5 w-2.5 rounded-full bg-emerald-400/70" />
        </span>
        <span className="flex items-center gap-1.5 font-mono text-[11px] text-slate-400">
          <Bot className="h-3.5 w-3.5 text-cyan-400" /> agentic-pipeline · orchestration
        </span>
        <span className="ml-auto rounded-full border border-slate-700 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-slate-400">
          Illustrative run
        </span>
      </div>

      <div className="flex flex-wrap items-end gap-3 px-5 pt-4">
        <div className="min-w-0 flex-1">
          <p className="text-base font-semibold text-white">Agents do the work. People approve the gates.</p>
          <p className="text-xs text-slate-400">Every stage is an agent on Snowflake Cortex; after the STTM three agents work in parallel.</p>
        </div>
        <div className="flex gap-1.5" role="group" aria-label="Example source">
          {SCENARIOS.map(({ key, label, icon: Icon }) => (
            <button key={key} type="button" onClick={() => pick(key)} aria-pressed={key === scenarioKey}
                    className={cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium transition",
                      key === scenarioKey ? "border-indigo-400/60 bg-indigo-500/15 text-indigo-100" : "border-slate-700/80 text-slate-400 hover:text-slate-200")}>
              <Icon className="h-3 w-3" /> {label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex min-h-0 flex-1 items-center px-3 pt-2">
        <svg viewBox="0 0 760 300" className="h-auto max-h-full w-full" role="img"
             aria-label="Source, profile, domain, mapping and STTM run in sequence; QA tests, data quality and dbt then validation run in parallel; all merge into code review. Mapping, STTM and code review wait for a person.">
          <defs>
            <linearGradient id="edge-done" x1="0" x2="1">
              <stop offset="0%" stopColor="#22D3EE" />
              <stop offset="100%" stopColor="#10B981" />
            </linearGradient>
            <filter id="packet-glow" x="-200%" y="-200%" width="500%" height="500%">
              <feGaussianBlur stdDeviation="3" result="blur" />
              <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
          </defs>
          <rect x="452" y="24" width="210" height="262" rx="18" fill="rgba(99,102,241,0.05)" stroke="rgba(99,102,241,0.25)" strokeDasharray="4 6" />
          <text x="557" y="18" textAnchor="middle" fontSize="9" letterSpacing="2" fill="#818CF8">IN PARALLEL</text>
          {EDGES.map((e) => {
            const done = shown > e.step || (shown === e.step && !paused);
            return (
              <path key={e.id} d={e.d} fill="none" strokeLinecap="round"
                    stroke={shown >= e.step ? "url(#edge-done)" : "#1E293B"} strokeWidth={shown >= e.step ? 2.4 : 1.6}
                    strokeDasharray={shown >= e.step ? undefined : "4 6"} opacity={done || shown > e.step ? 1 : 0.9} />
            );
          })}
          {!reduced && EDGES.filter((e) => e.step === step).map((e) => (
            <circle key={`${run}-${step}-${e.id}`} r="4.5" fill="#E0F2FE" filter="url(#packet-glow)">
              <animateMotion dur="1.1s" fill="freeze" path={e.d} />
            </circle>
          ))}
          {NODES.map((n) => <GraphNode key={n.id} n={n} step={shown} />)}
        </svg>
      </div>

      <div className="grid grid-cols-2 gap-2 px-5 pb-4 sm:grid-cols-3">
        {deliverables(scenario).map((d) => {
          const ready = shown >= d.step;
          const Icon = d.icon;
          return (
            <div key={d.title}
                 className={cn("flex items-center gap-2.5 rounded-xl border px-3 py-2 transition-all duration-500",
                   ready ? "border-slate-700/80 bg-slate-900/70" : "border-dashed border-slate-800 bg-transparent opacity-45")}>
              <span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-lg", ready ? "bg-slate-800" : "bg-slate-900")}>
                <Icon className={cn("h-3.5 w-3.5", ready ? d.tone : "text-slate-600")} />
              </span>
              <span className="min-w-0">
                <span className={cn("block truncate text-xs font-semibold", ready ? "text-slate-100" : "text-slate-500")}>{d.title}</span>
                <span className="block truncate font-mono text-[10px] text-slate-500">{ready ? d.detail : "pending"}</span>
              </span>
            </div>
          );
        })}
      </div>

      <div className="border-t border-slate-800/60 bg-black/25 px-5 py-3" aria-live="polite">
        <p className="mb-2 flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.16em] text-slate-500">
          <span className="h-1.5 w-1.5 animate-glow rounded-full bg-emerald-400" /> agent console
        </p>
        <ol className="space-y-1.5">
          {visible.map((l) => (
            <li key={l.key} className="animate-stage-in flex items-center gap-2.5 text-[12px]">
              <span className={cn("inline-flex w-[92px] shrink-0 items-center gap-1.5 rounded-md px-1.5 py-0.5 font-mono text-[10px] font-semibold text-white", l.tone)}>
                {l.human ? <UserCheck className="h-3 w-3" /> : <Bot className="h-3 w-3" />}
                {l.agent}
              </span>
              <span className={cn("truncate", l.human ? "text-amber-200" : "text-slate-300")}>{l.text}</span>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
