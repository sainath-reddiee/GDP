"use client";

import { useEffect, useState, type ReactNode } from "react";
import { motion } from "framer-motion";
import {
  ArrowRight, BookOpen, Bot, Brain, Check, CircleHelp, FileClock, GitBranch, History, Scale, UserCheck, type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { reveal, Section } from "./landing-ui";

// Facts on this page come from the code: knowledge each stage reads (services/knowledge/playbook.py NEEDS), signals
// kept as knowledge (services/knowledge/signals.py, mapping/procedures._store_feedback, workflow store_approved_set,
// validation _remember_fixed, common/ai_suggest), learning and replay (services/learning), owner questions
// (services/learning/questions.py), documents (services/knowledge/documents.py). Mock values in the explorer are
// illustrative; their structure is the app's.

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

// ------------------------------------------------------------------------------------------------ knowledge loop

const LOOP = [
  { title: "Knowledge", sub: ["packs, rules, patterns,", "skills, documents"], color: "#1d4ed8" },
  { title: "Agent proposes", sub: ["reads the knowledge,", "shows its evidence"], color: "#1d4ed8" },
  { title: "Human approval", sub: ["approve, or send back", "with a reason"], color: "#e4572e" },
  { title: "Signals captured", sub: ["decisions, rejections,", "fixes, answers"], color: "#1c8fc4" },
  { title: "Learning", sub: ["patterns replayed,", "steward approves"], color: "#17794b" },
];
const CX = 320, CY = 280, R = 180, PERIOD = 12;
const at = (k: number) => {
  const a = ((-90 + 72 * k) * Math.PI) / 180;
  return { x: CX + R * Math.cos(a), y: CY + R * Math.sin(a) };
};
/** label placement per node: outside the ring */
const LABEL = [
  { dx: 0, dy: -80, anchor: "middle" }, { dx: 36, dy: -8, anchor: "start" }, { dx: 20, dy: 46, anchor: "start" },
  { dx: -20, dy: 46, anchor: "end" }, { dx: -36, dy: -8, anchor: "end" },
] as const;

function LoopDiagram() {
  const reduced = useReducedMotion();
  const ring = `M${CX},${CY - R} a${R},${R} 0 1,1 0,${2 * R} a${R},${R} 0 1,1 0,${-2 * R}`;
  return (
    <svg viewBox="-60 0 760 560" className="h-auto w-full" role="img"
         aria-label="The core engine loop: knowledge, the agent proposes, human approval, signals captured, learning, and back to knowledge.">
      <circle cx={CX} cy={CY} r={R + 22} fill="none" stroke="#d9d4c7" strokeDasharray="2 6" />
      <circle cx={CX} cy={CY} r={R} fill="none" stroke="#15140f" strokeWidth="1" />
      {/* direction ticks */}
      {LOOP.map((_, k) => {
        const a = ((-90 + 72 * k + 36) * Math.PI) / 180;
        const x = CX + R * Math.cos(a), y = CY + R * Math.sin(a);
        const deg = -90 + 72 * k + 36 + 90;
        return <path key={k} d="M-5,-5 L3,0 L-5,5" fill="none" stroke="#15140f" strokeWidth="1.4" transform={`translate(${x},${y}) rotate(${deg})`} />;
      })}
      <text x={CX} y={CY - 6} textAnchor="middle" className="lp-display" fontSize="44" fill="#15140f">Core engine</text>
      <text x={CX} y={CY + 22} textAnchor="middle" className="lp-mono" fontSize="11" letterSpacing="2" fill="#6e6b62">KNOWLEDGE-DRIVEN · SELF-IMPROVING</text>
      {!reduced && [0, PERIOD / 2].map((delay) => (
        <circle key={delay} r="6" fill="#e4572e">
          <animateMotion dur={`${PERIOD}s`} begin={`-${delay}s`} repeatCount="indefinite" path={ring} />
        </circle>
      ))}
      {LOOP.map((n, k) => {
        const p = at(k);
        const l = LABEL[k];
        return (
          <g key={n.title}>
            <circle cx={p.x} cy={p.y} r="26" fill="#fbfaf6" stroke={n.color} strokeWidth="1.6">
              {!reduced && <animate attributeName="r" values="26;32;26;26" keyTimes="0;0.04;0.1;1" dur={`${PERIOD}s`} begin={`${(k * PERIOD) / 5}s`} repeatCount="indefinite" />}
            </circle>
            <text x={p.x} y={p.y + 5} textAnchor="middle" className="lp-mono" fontSize="13" fill={n.color}>{String(k + 1).padStart(2, "0")}</text>
            <text x={p.x + l.dx} y={p.y + l.dy} textAnchor={l.anchor} fontSize="19" fontWeight="600" fill="#15140f">{n.title}</text>
            {n.sub.map((s, i) => (
              <text key={s} x={p.x + l.dx} y={p.y + l.dy + 21 + i * 18} textAnchor={l.anchor} fontSize="14.5" fill="#6e6b62">{s}</text>
            ))}
          </g>
        );
      })}
    </svg>
  );
}

const SIGNALS: [string, string][] = [
  ["A mapping decision is approved", "a mapping pattern the next run ranks first"],
  ["A stage is sent back with a reason", "gate feedback that later prompts read"],
  ["Data quality checks are approved", "a Soda pattern reused for the domain"],
  ["A validation failure is fixed", "a dbt pattern for the next generation and review"],
  ["An AI suggestion is accepted or rejected", "a rule the engine follows, or one never offered again"],
  ["A model or proposal is rejected", "negative evidence, so it is not proposed again"],
  ["The domain owner answers a question", "an active pattern, used from the next run"],
  ["An incident postmortem is written", "a knowledge item queued for a steward"],
];

/** Which knowledge each stage of a Silver run reads (services/knowledge/playbook.py). */
const STAGE_COLS = ["Mapping", "STTM", "Data quality", "QA", "Code gen", "Validation"];
const READS: { kind: string; why: string; stages: string[] }[] = [
  { kind: "Glossary", why: "Business terms and synonyms", stages: ["Mapping"] },
  { kind: "Mapping patterns", why: "Mappings reviewers approved before", stages: ["Mapping"] },
  { kind: "Model definitions", why: "Purpose, grain and keys of each table", stages: ["Mapping", "STTM", "Code gen"] },
  { kind: "Transformation rules", why: "How a target column is computed", stages: ["STTM", "Data quality"] },
  { kind: "Business rules", why: "Rules the data must follow", stages: ["STTM", "Data quality", "QA"] },
  { kind: "Examples", why: "Rows exactly as the table must hold them", stages: ["STTM", "QA"] },
  { kind: "Soda patterns", why: "Checks that worked for this domain", stages: ["Data quality"] },
  { kind: "QA tests", why: "Tests a tester wrote or approved", stages: ["QA"] },
  { kind: "Naming standards", why: "Naming the code must follow", stages: ["Code gen", "Validation"] },
];

const INPUTS: { title: string; body: string }[] = [
  { title: "Domain knowledge packs", body: "Glossary, rules, model definitions and patterns per domain, for Silver and Gold." },
  { title: "Confluence and documents", body: "AI reads the pages and proposes items with the source passage; a steward accepts each one." },
  { title: "Every human decision", body: "Approvals, send-backs, rejections and fixes, captured as signals." },
  { title: "Versioned skills", body: "The engineering playbook each stage loads, pinned per domain and layer." },
];

export function KnowledgeLoop() {
  const [col, setCol] = useState<string | null>(null);
  return (
    <Section id="knowledge" n="04" label="Why it gets smarter" next={{ href: "#features", label: "What is in the core engine" }}
             title={<>Every task starts from <em className="text-[color:var(--agent)]">knowledge.</em> Every decision feeds it back.</>}
             body="The core engine is knowledge-driven. Each stage reads the domain's knowledge before it proposes anything, and every human approval, send-back and fix is written back as knowledge. The next run starts smarter than the last.">
      <div className="grid items-center gap-10 lg:grid-cols-12">
        <motion.div {...reveal} className="lg:col-span-7"><LoopDiagram /></motion.div>
        <motion.div {...reveal} className="lg:col-span-5">
          <p className="lp-label !text-[color:var(--ink)]">What the engine learns from</p>
          <ul className="mt-4 border-t border-[color:var(--ink)]">
            {SIGNALS.map(([from, to]) => (
              <li key={from} className="border-b border-[color:var(--line)] py-3">
                <p className="text-[14px] font-semibold">{from}</p>
                <p className="mt-0.5 flex items-start gap-2 text-[13px] text-[color:var(--ink-2)]">
                  <ArrowRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[color:var(--human)]" /> becomes {to}
                </p>
              </li>
            ))}
          </ul>
        </motion.div>
      </div>

      <motion.div {...reveal} className="mt-16 rounded-[6px] border border-[color:var(--ink)] bg-[color:var(--panel)]">
        <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-[color:var(--ink)] px-5 py-3">
          <span className="lp-label !text-[color:var(--ink)]">Which knowledge each stage reads</span>
          <span className="text-[12px] text-[color:var(--muted)]">Silver pipeline. Gold adds MDM rules and gold model definitions.</span>
        </div>
        {/* narrow screens: each knowledge type with the stages that read it */}
        <ul className="divide-y divide-[color:var(--line)] md:hidden">
          {READS.map((r) => (
            <li key={r.kind} className="px-5 py-3">
              <p className="text-[14px] font-semibold">{r.kind} <span className="text-[12px] font-normal text-[color:var(--muted)]">{r.why}</span></p>
              <p className="mt-1.5 flex flex-wrap gap-1.5">
                {r.stages.map((st) => <span key={st} className="rounded-full bg-[#1d4ed8]/10 px-2 py-0.5 text-[11px] font-medium text-[color:var(--agent)]">{st}</span>)}
              </p>
            </li>
          ))}
        </ul>
        <div className="hidden md:block">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="border-b border-[color:var(--line)]">
                <th className="lp-label px-5 py-3 font-normal">Knowledge</th>
                {STAGE_COLS.map((c) => (
                  <th key={c} onMouseEnter={() => setCol(c)} onMouseLeave={() => setCol(null)}
                      className={cn("lp-label cursor-default px-2 py-3 text-center font-normal transition-colors", col === c && "!text-[color:var(--agent)]")}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {READS.map((r) => (
                <tr key={r.kind} className="border-b border-[color:var(--line)] last:border-0">
                  <td className="px-5 py-2.5">
                    <span className="text-[14px] font-semibold">{r.kind}</span>
                    <span className="ml-2 text-[12px] text-[color:var(--muted)]">{r.why}</span>
                  </td>
                  {STAGE_COLS.map((c) => {
                    const on = r.stages.includes(c);
                    return (
                      <td key={c} onMouseEnter={() => setCol(c)} onMouseLeave={() => setCol(null)}
                          className={cn("px-2 py-2.5 text-center transition-colors", col === c && "bg-[#1d4ed8]/[0.06]")}>
                        {on ? <span className="inline-block h-3 w-3 rounded-[2px] bg-[color:var(--agent)]" aria-label={`${c} reads it`} />
                            : <span className="inline-block h-px w-3 bg-[color:var(--line)]" />}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </motion.div>

      <div className="mt-10 grid gap-8 md:grid-cols-4">
        {INPUTS.map((x, i) => (
          <motion.div key={x.title} {...reveal} transition={{ ...reveal.transition, delay: i * 0.05 }} className="border-t-2 border-[color:var(--agent)] pt-3">
            <p className="lp-label">Knowledge in · {String(i + 1).padStart(2, "0")}</p>
            <h3 className="mt-2 text-[16px] font-semibold">{x.title}</h3>
            <p className="mt-1 text-[13px] leading-relaxed text-[color:var(--ink-2)]">{x.body}</p>
          </motion.div>
        ))}
      </div>
    </Section>
  );
}

// ------------------------------------------------------------------------------------------------ feature explorer

function Mock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-[6px] border border-[color:var(--ink)] bg-[color:var(--panel)] shadow-[8px_8px_0_0_rgba(21,20,15,0.9)]">
      <div className="flex items-center justify-between border-b border-[color:var(--ink)] px-4 py-2.5">
        <span className="lp-mono text-[12px] font-semibold">{title}</span>
        <span className="lp-label !text-[10px]">illustrative data</span>
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

const Pill = ({ tone, children }: { tone: "agent" | "human" | "ok" | "muted"; children: ReactNode }) => (
  <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold",
    tone === "agent" ? "bg-[#1d4ed8]/10 text-[color:var(--agent)]" : tone === "human" ? "bg-[#e4572e]/10 text-[color:var(--human)]"
      : tone === "ok" ? "bg-[#17794b]/10 text-[color:var(--ok)]" : "bg-[#15140f]/[0.06] text-[color:var(--muted)]")}>{children}</span>
);

function PlaybookMock() {
  const rows = [["Glossary", 42, "Mapping"], ["Mapping patterns", 118, "Mapping"], ["Business rules", 17, "STTM · DQ · QA"],
                ["Soda patterns", 9, "Data quality"], ["Examples", 0, "STTM · QA"], ["Naming standards", 6, "Code gen · Validation"]] as const;
  return (
    <Mock title="Domain · CUSTOMER · Playbook (Silver)">
      <ul className="divide-y divide-dashed divide-[color:var(--line)]">
        {rows.map(([k, n, s]) => (
          <li key={k} className="flex items-center gap-3 py-2 text-[13px]">
            <span className="w-36 font-semibold">{k}</span>
            <span className="flex-1 text-[12px] text-[color:var(--muted)]">{s}</span>
            {n ? <span className="lp-mono text-[12px]">{n} items</span> : <Pill tone="human">gap: add before the next run</Pill>}
          </li>
        ))}
      </ul>
    </Mock>
  );
}

function MappingMock() {
  const signals = [["semantic", 0.94], ["keyword", 0.8], ["datatype", 1], ["statistical", 0.72], ["domain", 0.9], ["context", 0.66], ["historical", 0.95]] as const;
  return (
    <Mock title="Field mapping · CUST_EMAIL_ADDR">
      <div className="flex items-center justify-between">
        <span className="text-[14px] font-semibold">→ CUSTOMER.EMAIL_ADDRESS</span>
        <span className="lp-mono text-[13px] text-[color:var(--agent)]">score 0.91</span>
      </div>
      <ul className="mt-3 space-y-1.5">
        {signals.map(([k, v]) => (
          <li key={k} className="flex items-center gap-3 text-[12px]">
            <span className="lp-mono w-24 text-[color:var(--muted)]">{k}</span>
            <span className="h-1.5 flex-1 rounded-full bg-[#15140f]/[0.08]"><span className="block h-full rounded-full bg-[color:var(--agent)]" style={{ width: `${v * 100}%` }} /></span>
          </li>
        ))}
      </ul>
      <p className="mt-3 flex items-center gap-2 border-t border-dashed border-[color:var(--line)] pt-3 text-[12px] text-[color:var(--ink-2)]">
        <Pill tone="muted">runner-up</Pill> CUSTOMER.CONTACT_EMAIL · 0.64
      </p>
      <div className="mt-3 flex gap-2"><Pill tone="human"><UserCheck className="h-3 w-3" /> Approve</Pill><Pill tone="muted">Choose another</Pill><Pill tone="muted">Not loaded</Pill></div>
    </Mock>
  );
}

function QuestionMock() {
  return (
    <Mock title="Knowledge inbox · Open question">
      <p className="flex items-center gap-2 text-[12px] text-[color:var(--muted)]"><CircleHelp className="h-3.5 w-3.5 text-[color:var(--human)]" /> Asked of the domain owner · confidence below 0.6</p>
      <p className="lp-display mt-2 text-[24px] leading-tight">Which target does SRC_STATUS_CD feed?</p>
      <ul className="mt-4 space-y-2 text-[13px]">
        {["CUSTOMER.CUSTOMER_STATUS", "ACCOUNT.ACCOUNT_STATUS", "Not loaded"].map((o, i) => (
          <li key={o} className={cn("flex items-center gap-2 rounded-[4px] border px-3 py-2", i === 0 ? "border-[color:var(--agent)] bg-[#1d4ed8]/[0.05]" : "border-[color:var(--line)]")}>
            <span className={cn("h-3 w-3 rounded-full border", i === 0 ? "border-[color:var(--agent)] bg-[color:var(--agent)]" : "border-[color:var(--muted)]")} /> {o}
          </li>
        ))}
      </ul>
      <p className="mt-3 text-[12px] text-[color:var(--ink-2)]">The answer is saved as an active mapping pattern; every later run ranks it first.</p>
    </Mock>
  );
}

function LearningMock() {
  return (
    <Mock title="Learning · Proposed rule">
      <div className="flex items-center gap-2"><Pill tone="agent"><Brain className="h-3 w-3" /> LEARNED</Pill><Pill tone="muted">TRANSFORMATION</Pill></div>
      <p className="mt-3 text-[15px] font-semibold">Trim and upper-case status code columns</p>
      <dl className="mt-3 grid grid-cols-3 gap-3 border-y border-dashed border-[color:var(--line)] py-3 text-[12px]">
        <div><dt className="lp-label !text-[10px]">Decisions</dt><dd className="lp-mono mt-1 text-[14px]">5</dd></div>
        <div><dt className="lp-label !text-[10px]">Runs</dt><dd className="lp-mono mt-1 text-[14px]">3</dd></div>
        <div><dt className="lp-label !text-[10px]">Agreement</dt><dd className="lp-mono mt-1 text-[14px]">80%</dd></div>
      </dl>
      <p className="mt-3 flex items-center gap-2 text-[13px]"><History className="h-4 w-4 text-[color:var(--ok)]" /> Replayed on past runs: <Pill tone="ok">BETTER</Pill></p>
      <p className="mt-2 flex items-center gap-2 text-[13px]"><UserCheck className="h-4 w-4 text-[color:var(--human)]" /> Waiting for a data steward to approve</p>
    </Mock>
  );
}

function VersionsMock() {
  const items = [
    { v: "STTM v1", what: "sent back: \"join on the active address only\"", tone: "human" as const, tag: "Send-back" },
    { v: "STTM v2", what: "approved by STTM approver, justification recorded", tone: "ok" as const, tag: "Approved" },
    { v: "dbt v2", what: "generated from STTM v2, compiled on Snowflake", tone: "agent" as const, tag: "Validated" },
    { v: "PR", what: "branch and pull request opened on GitHub", tone: "agent" as const, tag: "Published" },
  ];
  return (
    <Mock title="crm_customer_onboarding · History">
      <ol className="relative space-y-3 pl-5 before:absolute before:left-[5px] before:top-1 before:h-[calc(100%-8px)] before:w-px before:bg-[color:var(--line)]">
        {items.map((x) => (
          <li key={x.v} className="relative">
            <span className="absolute -left-5 top-1 h-[11px] w-[11px] rounded-full border-2 border-[color:var(--panel)] bg-[color:var(--ink)]" />
            <div className="flex flex-wrap items-center gap-2"><span className="lp-mono text-[13px] font-semibold">{x.v}</span><Pill tone={x.tone}>{x.tag}</Pill></div>
            <p className="text-[12px] text-[color:var(--ink-2)]">{x.what}</p>
          </li>
        ))}
      </ol>
    </Mock>
  );
}

function AuditMock() {
  const ev = [
    ["10:02", "agent", "tool_call run_profiling", "SUCCEEDED · 14s"],
    ["10:03", "agent", "tool_call generate_mapping_candidates", "SUCCEEDED · 9s"],
    ["10:41", "human", "MAPPING_REVIEW → MAPPING_APPROVED", "model approver · justification"],
    ["10:42", "agent", "tool_call generate_sttm", "SUCCEEDED · 6s"],
    ["11:15", "human", "STTM_REVIEW → STTM_APPROVED", "STTM approver · justification"],
  ] as const;
  return (
    <Mock title="Audit trail">
      <ul className="divide-y divide-dashed divide-[color:var(--line)]">
        {ev.map(([t, who, what, detail]) => (
          <li key={what} className="grid grid-cols-[44px_70px_1fr] items-baseline gap-2 py-2 text-[12px]">
            <span className="lp-mono text-[color:var(--muted)]">{t}</span>
            <Pill tone={who === "agent" ? "agent" : "human"}>{who === "agent" ? "AGENT" : "HUMAN"}</Pill>
            <span><span className="lp-mono font-semibold">{what}</span><span className="block text-[color:var(--muted)]">{detail}</span></span>
          </li>
        ))}
      </ul>
    </Mock>
  );
}

const FEATURES: { id: string; icon: LucideIcon; title: string; lede: string; points: string[]; mock: () => JSX.Element }[] = [
  { id: "playbook", icon: BookOpen, title: "Knowledge playbook", mock: PlaybookMock,
    lede: "Every domain shows exactly which knowledge each stage reads, what it already holds, and the gaps the next run would hit.",
    points: ["Silver and Gold layers, per domain", "Knowledge from packs, documents and decisions", "Gaps flagged before a run starts"] },
  { id: "mapping", icon: Scale, title: "Explainable mapping", mock: MappingMock,
    lede: "Every source-to-target candidate is scored on seven signals, including Cortex embeddings and past reviewer decisions, so approvers see why.",
    points: ["Semantic, keyword, datatype, statistical, domain, context, historical", "Ambiguous columns explained by Cortex AI", "Every column decided before approval"] },
  { id: "ask", icon: CircleHelp, title: "Ask, don't guess", mock: QuestionMock,
    lede: "When the engine is unsure, it asks the domain owner a targeted question instead of guessing.",
    points: ["Raised for low-confidence or ambiguous columns", "Asked once per subject", "The answer becomes an active pattern"] },
  { id: "learning", icon: Brain, title: "Learning with replay", mock: LearningMock,
    lede: "Repeated reviewer decisions become proposed rules, tested on past runs before a data steward approves them.",
    points: ["Transformation, override and rename patterns", "At least 3 decisions across 2 runs, 60% agreement", "Rules that make results worse are held back"] },
  { id: "versions", icon: GitBranch, title: "Versioned and reviewable", mock: VersionsMock,
    lede: "STTM, dbt generations, data quality suites, skills and models are versioned, and governed assets round-trip through GitHub pull requests.",
    points: ["Send back with a reason, regenerate, re-review", "Every version kept with its approver and reason", "Branches and PRs per change"] },
  { id: "audit", icon: FileClock, title: "Complete audit trail", mock: AuditMock,
    lede: "Every agent run, tool call, state change and human approval is recorded with who, when and why.",
    points: ["Agent tool calls with status and timing", "Approvals with written justification", "Evidence ready for compliance reviews"] },
];

export function FeatureExplorer() {
  const [active, setActive] = useState(FEATURES[0].id);
  const f = FEATURES.find((x) => x.id === active) ?? FEATURES[0];
  const Mocked = f.mock;
  return (
    <Section id="features" n="05" label="What is in it" next={{ href: "#governance", label: "Why you can trust it" }}
             title={<>The core engine, <em className="text-[color:var(--agent)]">feature by feature.</em></>}
             body="What sets the engine apart, shown the way it appears in the platform. Pick a feature to see it.">
      <div className="grid gap-10 lg:grid-cols-12">
        <div role="tablist" aria-label="Core engine features" className="border-t border-[color:var(--ink)] lg:col-span-5">
          {FEATURES.map((x, i) => {
            const Icon = x.icon;
            const on = x.id === active;
            return (
              <button key={x.id} type="button" role="tab" aria-selected={on} onClick={() => setActive(x.id)} data-testid={`feature-${x.id}`}
                      className={cn("group flex w-full items-start gap-4 border-b border-[color:var(--line)] py-4 text-left transition-colors", on ? "" : "hover:bg-[color:var(--panel)]")}>
                <span className="lp-mono pt-1 text-[12px] text-[color:var(--muted)]">{String(i + 1).padStart(2, "0")}</span>
                <span className="min-w-0 flex-1">
                  <span className={cn("flex items-center gap-2 text-[17px] font-semibold", on && "text-[color:var(--agent)]")}>
                    <Icon className="h-4 w-4" /> {x.title}
                  </span>
                  {on && <span className="mt-1.5 block text-[14px] leading-relaxed text-[color:var(--ink-2)]">{x.lede}</span>}
                  {on && (
                    <ul className="mt-3 space-y-1.5">
                      {x.points.map((p) => <li key={p} className="flex gap-2 text-[13px]"><Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[color:var(--ok)]" />{p}</li>)}
                    </ul>
                  )}
                </span>
                <ArrowRight className={cn("mt-1.5 h-4 w-4 transition", on ? "text-[color:var(--agent)]" : "text-[color:var(--line)] group-hover:text-[color:var(--ink)]")} />
              </button>
            );
          })}
        </div>
        <div className="lg:col-span-7 lg:pt-4" role="tabpanel" aria-label={f.title}>
          <motion.div key={f.id} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35 }}>
            <Mocked />
          </motion.div>
          <p className="mt-6 flex items-center gap-2 text-[12px] text-[color:var(--muted)]"><Bot className="h-3.5 w-3.5" /> Agents prepare it. <UserCheck className="ml-2 h-3.5 w-3.5 text-[color:var(--human)]" /> Approvers decide.</p>
        </div>
      </div>
    </Section>
  );
}
