"use client";

import "@fontsource/instrument-serif/400.css";
import "@fontsource/instrument-serif/400-italic.css";
import "@fontsource-variable/jetbrains-mono";
import "./landing.css";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { MotionConfig, motion } from "framer-motion";
import { ArrowRight, ArrowUpRight, Bot, Check, Cpu, LogIn, ShieldCheck, Snowflake, Sparkles, UserCheck } from "lucide-react";
import { LogoMark } from "@/components/brand/logo-mark";
import { PRODUCT, PRODUCT_SHORT } from "@/lib/brand";
import { cn } from "@/lib/utils";
import { RunPreview } from "./run-preview";
import { FeatureExplorer, KnowledgeLoop } from "./engine-sections";
import { reveal, Section } from "./landing-ui";
import { SignInDialog } from "./sign-in-dialog";
import type { AuthMode } from "./form";

// Every claim on this page is traceable to the code: stages, gates and tools (snowflake/database/seed/workflow_graph.json,
// snowflake/agents/supervisor.sql), where AI is used (services/common/llm.py call sites), the Copilot (services/agentic),
// governance and audit (services/governance/policy.py, CORE.AGENT_RUN) and integrations (services/*). Keep it that way.

const NAV = [
  { href: "#agent", label: "Agent at work" },
  { href: "#knowledge", label: "Knowledge loop" },
  { href: "#features", label: "Features" },
  { href: "#how", label: "How it works" },
  { href: "#governance", label: "Governance" },
  { href: "#sources", label: "Sources" },
];

const KEYWORDS = [
  "Agentic data engineering", "Knowledge-driven engine", "Human-in-the-loop approval", "Self-improving feedback loop", "Snowflake Cortex", "Source-to-target mapping", "STTM",
  "Data quality as code", "dbt", "Medallion architecture", "Explainable AI", "Role-based approval", "Audit trail",
  "GitHub pull requests", "Continuous learning", "Domain knowledge packs", "Any source onboarding",
];

/** What every run hands over, and who signs it off. */
const MANIFEST: { item: string; human?: string }[] = [
  { item: "Column profiles and PII hints" },
  { item: "Domain match" },
  { item: "Source-to-target mapping", human: "Model approver" },
  { item: "STTM: transformations and joins", human: "STTM approver" },
  { item: "SodaCL and Great Expectations checks", human: "DQ approver" },
  { item: "QA test suite", human: "QA lead" },
  { item: "Silver dbt models, compiled" },
  { item: "Code review and GitHub PR", human: "Code reviewer" },
];

const FACTS = [
  { value: "4", label: "human approval gates in every run" },
  { value: "3", label: "agent lanes in parallel after the STTM" },
  { value: "0", label: "auto-approvals by any agent" },
  { value: "100%", label: "of AI calls on Snowflake Cortex" },
];

/** The Silver pipeline, as the platform runs it. tool: the agent tool or procedure behind the stage. */
const STAGES: { name: string; tool: string; delivers: string; ai?: string; human?: string; platform?: boolean }[] = [
  { name: "Source onboarding", tool: "prepare_source", platform: true, delivers: "Access validated; tables landed, as Iceberg, or read in place" },
  { name: "Profiling", tool: "run_profiling", delivers: "Statistics, patterns, keys and drift for every column", ai: "Descriptions, PII hints" },
  { name: "Domain identification", tool: "identify_domain", delivers: "Matched domain knowledge pack with a confidence score", ai: "Cortex Search", human: "Confirms if confidence is low" },
  { name: "Field mapping", tool: "generate_mapping_candidates", delivers: "Ranked source-to-target candidates with evidence on 7 signals", ai: "Embeddings, explanations", human: "Model approver" },
  { name: "STTM", tool: "generate_sttm", delivers: "Versioned transformations, joins and business rules", ai: "Natural-language refinement", human: "STTM approver" },
  { name: "Data quality", tool: "generate_soda", delivers: "SodaCL checks and a Great Expectations suite, backtested", human: "DQ approver" },
  { name: "QA testing", tool: "qa_suite", platform: true, delivers: "SQL tests in 8 categories, traced to the STTM", ai: "Plain-language test drafting", human: "QA lead sign-off" },
  { name: "Code generation", tool: "generate_dbt · validate_dbt", delivers: "Silver dbt models compiled on dbt Projects on Snowflake" },
  { name: "Code review", tool: "review", delivers: "Approved code published as a GitHub pull request", ai: "Advisory review per file", human: "Code reviewer" },
];

const VALUE: { task: string; before: string; now: string }[] = [
  { task: "Source discovery", before: "Ad hoc profiling queries, table by table", now: "Every column profiled by the agent, reused while the table is unchanged" },
  { task: "Source-to-target mapping", before: "Spreadsheets maintained by hand", now: "Ranked candidates with evidence; the approver decides each column" },
  { task: "STTM", before: "Transformations and joins written and versioned manually", now: "Generated from the approved mapping, versioned and editable" },
  { task: "Data quality", before: "Soda and GX rules written one by one", now: "Checks generated from the STTM and profile, backtested before approval" },
  { task: "QA testing", before: "Reconciliation SQL copied between projects", now: "Tests in 8 categories traced to the STTM, plus reusable domain packs" },
  { task: "dbt engineering", before: "Models, sources and schema files by hand", now: "Silver dbt generated and compiled on Snowflake before review" },
  { task: "Gold modeling", before: "Star schemas designed from scratch", now: "Proposed from mastered Silver, built table by table into one PR" },
  { task: "Governance", before: "Approvals in chat and email", now: "Role-based human approval, written justifications, full audit trail" },
];

const INTEGRATIONS: { name: string; how: string }[] = [
  { name: "Snowflake", how: "Cortex Agents, Cortex AI, Cortex Search, dbt Projects on Snowflake, Iceberg tables" },
  { name: "SnapLogic", how: "Ingestion framework for external sources, configured dynamically by the platform" },
  { name: "GitHub", how: "Branches and pull requests for every generated artifact" },
  { name: "Jira and Confluence", how: "Run tracking, QA defects and requirements in context" },
  { name: "Soda and Great Expectations", how: "Data quality as code, generated and versioned" },
  { name: "Apache Airflow", how: "Pipeline monitoring and incident management on MWAA" },
];

// ------------------------------------------------------------------------------------------------ pieces

function SignInButton({ onClick, big, children, testId }: { onClick: () => void; big?: boolean; children?: ReactNode; testId?: string }) {
  return (
    <button type="button" onClick={onClick} data-testid={testId}
            className={cn("group inline-flex items-center gap-2 rounded-full bg-[color:var(--ink)] font-medium text-[color:var(--paper)] transition hover:bg-[#2a2822] active:translate-y-px",
              big ? "h-12 px-6 text-[15px]" : "h-9 px-4 text-sm")}>
      {children ?? <><LogIn className="h-4 w-4" /> Sign in</>}
    </button>
  );
}

function TopBar({ onSignIn }: { onSignIn: () => void }) {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 8);
    on();
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);
  return (
    <header className={cn("sticky top-0 z-40 transition-colors", scrolled ? "border-b border-[color:var(--line)] bg-[#f3f1ea]/90 backdrop-blur" : "border-b border-transparent")}>
      <div className="mx-auto flex h-16 max-w-[1200px] items-center gap-6 px-5 sm:px-8">
        <a href="#top" className="flex items-center gap-2.5">
          <LogoMark size="sm" />
          <span className="text-[15px] font-semibold tracking-tight">
            <span className="hidden sm:inline">{PRODUCT}</span><span className="sm:hidden">{PRODUCT_SHORT}</span>
          </span>
        </a>
        <nav aria-label="Sections" className="ml-4 hidden items-center gap-6 lg:flex">
          {NAV.map((n) => <a key={n.href} href={n.href} className="lp-link text-sm text-[color:var(--ink-2)] hover:text-[color:var(--ink)]">{n.label}</a>)}
        </nav>
        <div className="ml-auto"><SignInButton onClick={onSignIn} testId="open-sign-in" /></div>
      </div>
    </header>
  );
}

function Hero({ onSignIn }: { onSignIn: () => void }) {
  return (
    <section id="top" className="relative">
      <div aria-hidden className="lp-grid pointer-events-none absolute inset-x-0 top-0 h-[620px]" />
      <div className="relative mx-auto grid max-w-[1200px] gap-12 px-5 pb-16 pt-14 sm:px-8 lg:grid-cols-12 lg:pt-20">
        <div className="lg:col-span-7">
          <motion.p initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5 }}
                    className="lp-label flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-[color:var(--agent)]" /> Agentic data engineering
            <span className="text-[color:var(--line)]">/</span>
            <Snowflake className="h-3 w-3 text-[color:var(--cortex)]" /> Native to Snowflake
          </motion.p>
          <motion.h1 initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.7, delay: 0.05 }}
                     className="lp-display mt-6 text-[52px] leading-[0.98] sm:text-[76px] lg:text-[88px]">
            AI agents engineer your data. <em className="text-[color:var(--human)]">Humans approve</em> what ships.
          </motion.h1>
          <motion.p initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.7, delay: 0.12 }}
                    className="mt-7 max-w-xl text-[17px] leading-relaxed text-[color:var(--ink-2)]">
            The {PRODUCT} takes any source from onboarding to a reviewed dbt pull request. A supervisor agent on Snowflake
            Cortex orchestrates profiling, mapping, STTM, data quality, QA and code generation, and stops at every human
            approval gate. Governed, explainable and auditable by design.
          </motion.p>
          <motion.div initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.7, delay: 0.18 }}
                      className="mt-9 flex flex-wrap items-center gap-5">
            <SignInButton onClick={onSignIn} big><Snowflake className="h-4 w-4" /> Sign in with Snowflake <ArrowRight className="h-4 w-4 transition group-hover:translate-x-0.5" /></SignInButton>
            <a href="#agent" className="lp-link inline-flex items-center gap-1.5 text-[15px] font-medium">See the agent at work <ArrowUpRight className="h-4 w-4" /></a>
          </motion.div>
        </div>

        {/* run manifest: what a run delivers and who approves it */}
        <motion.aside initial={{ opacity: 0, y: 24 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.8, delay: 0.2 }}
                      className="self-end lg:col-span-5" aria-label="What every run delivers">
          <div className="rounded-[6px] border border-[color:var(--ink)] bg-[color:var(--panel)] shadow-[8px_8px_0_0_rgba(21,20,15,0.9)]">
            <div className="flex items-center justify-between border-b border-[color:var(--ink)] px-5 py-3">
              <span className="lp-label !text-[color:var(--ink)]">Run manifest</span>
              <span className="lp-mono text-[11px] text-[color:var(--muted)]">silver · one run</span>
            </div>
            <ul className="divide-y divide-dashed divide-[color:var(--line)]">
              {MANIFEST.map((m, i) => (
                <li key={m.item} className="flex items-center gap-3 px-5 py-2.5">
                  <span className="lp-mono w-5 text-[11px] text-[color:var(--muted)]">{String(i + 1).padStart(2, "0")}</span>
                  <span className="flex-1 text-[14px]">{m.item}</span>
                  <span className="flex items-center gap-1.5">
                    <span className="inline-flex items-center gap-1 rounded-full bg-[#4b44e8]/10 px-2 py-0.5 text-[10px] font-semibold text-[color:var(--agent)]"><Bot className="h-3 w-3" /> Agent</span>
                    {m.human && <span title={m.human} className="inline-flex items-center gap-1 rounded-full bg-[#e4572e]/10 px-2 py-0.5 text-[10px] font-semibold text-[color:var(--human)]"><UserCheck className="h-3 w-3" /> Approval</span>}
                  </span>
                </li>
              ))}
            </ul>
            <div className="flex items-center justify-between border-t border-[color:var(--ink)] px-5 py-3 text-[12px]">
              <span className="text-[color:var(--ink-2)]">Agents prepare. Approvers decide.</span>
              <span className="lp-mono text-[color:var(--human)]">4 gates · 0 auto-approvals</span>
            </div>
          </div>
        </motion.aside>
      </div>

      <div className="mx-auto max-w-[1200px] px-5 sm:px-8">
        <dl className="grid grid-cols-2 border-y border-[color:var(--ink)] md:grid-cols-4">
          {FACTS.map((f, i) => (
            <div key={f.label} className={cn("px-4 py-6", i > 0 && "md:border-l md:border-[color:var(--line)]", i % 2 === 1 && "border-l border-[color:var(--line)] md:border-l")}>
              <dt className="sr-only">{f.label}</dt>
              <dd className="lp-display text-[56px] leading-none">{f.value}</dd>
              <dd className="mt-2 max-w-[14rem] text-[13px] text-[color:var(--ink-2)]">{f.label}</dd>
            </div>
          ))}
        </dl>
      </div>

      <div className="lp-marquee-wrap mt-10 overflow-hidden border-y border-[color:var(--line)] py-3" aria-hidden>
        <div className="lp-marquee flex w-max gap-8 whitespace-nowrap">
          {[...KEYWORDS, ...KEYWORDS].map((k, i) => (
            <span key={i} className="lp-label flex items-center gap-8 !text-[color:var(--ink-2)]">{k}<span className="text-[color:var(--human)]">✦</span></span>
          ))}
        </div>
      </div>
    </section>
  );
}

function AgentAtWork() {
  return (
    <section id="agent" className="scroll-mt-16 bg-[#0e0d0a] py-24 text-[#f3f1ea]">
      <div className="mx-auto max-w-[1200px] px-5 sm:px-8">
        <motion.div {...reveal} className="flex items-center gap-4 border-t border-[#f3f1ea] pt-4">
          <span className="lp-label !text-[#f3f1ea]">§ 01</span><span className="lp-label !text-[#9b978c]">The agent at work</span>
        </motion.div>
        <div className="mt-8 grid gap-6 lg:grid-cols-12">
          <motion.h2 {...reveal} className="lp-display text-[40px] leading-[1.02] sm:text-[56px] lg:col-span-7">
            Watch the agent work. <em className="text-[#ff8a5c]">Approve</em> what it delivers.
          </motion.h2>
          <motion.p {...reveal} className="self-end text-[16px] leading-relaxed text-[#c9c5ba] lg:col-span-5">
            A replay of a Silver run with the platform&apos;s real stages, tools, states and roles. The agent calls each stage
            tool, runs three lanes in parallel after the STTM, and pauses at every gate until an approver signs off.
            Source tables are illustrative.
          </motion.p>
        </div>
        <motion.div {...reveal} className="mt-12"><RunPreview /></motion.div>
        <div className="mt-6 flex flex-wrap gap-x-6 gap-y-2 text-[12px] text-[#9b978c]">
          <span className="flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-[#8b89ff]" /> Agent tool call</span>
          <span className="flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-[#29b5e8]" /> Cortex AI assist</span>
          <span className="flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-[#ff7a3d]" /> Human approval</span>
        </div>
      </div>
    </section>
  );
}

function HowItWorks() {
  return (
    <Section id="how" n="04" label="How it works" title={<>Nine stages. Four <em className="text-[color:var(--human)]">human approval</em> gates. One pull request.</>}
             body="Seven stages are tools the supervisor agent calls, the other two are platform steps; all run on deterministic engines in your Snowflake account, so results are repeatable and explainable. Cortex AI adds judgement where it helps, and nothing advances past a gate without an approver.">
      <motion.div {...reveal} className="overflow-x-auto">
        <table className="w-full min-w-[860px] border-collapse text-left">
          <thead>
            <tr className="border-b border-[color:var(--ink)]">
              {["#", "Stage", "Tool", "Delivers", "Cortex AI", "Human approval"].map((h) => (
                <th key={h} className="lp-label py-3 pr-4 font-normal">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {STAGES.map((s, i) => (
              <tr key={s.name} className="group border-b border-[color:var(--line)] align-top transition-colors hover:bg-[color:var(--panel)]">
                <td className="lp-mono py-4 pr-4 text-[12px] text-[color:var(--muted)]">{String(i + 1).padStart(2, "0")}</td>
                <td className="py-4 pr-4 text-[15px] font-semibold">{s.name}{i >= 5 && i <= 7 && <span className="lp-label ml-2 !text-[10px] !text-[color:var(--agent)]">parallel</span>}</td>
                <td className={cn("lp-mono py-4 pr-4 text-[12px]", s.platform ? "text-[color:var(--muted)]" : "text-[color:var(--agent)]")}>
                  {s.tool}(){s.platform && <span className="mt-0.5 block font-sans text-[11px]">platform step</span>}
                </td>
                <td className="py-4 pr-4 text-[14px] text-[color:var(--ink-2)]">{s.delivers}</td>
                <td className="py-4 pr-4 text-[13px] text-[color:var(--cortex)]">{s.ai ?? <span className="text-[color:var(--line)]">-</span>}</td>
                <td className="py-4 text-[13px]">
                  {s.human
                    ? <span className="inline-flex items-center gap-1.5 font-medium text-[color:var(--human)]"><UserCheck className="h-3.5 w-3.5" /> {s.human}</span>
                    : <span className="text-[color:var(--line)]">-</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </motion.div>
    </Section>
  );
}

function Governance() {
  const actors = [
    { icon: Bot, title: "Supervisor agent", tag: "Snowflake Cortex Agent",
      body: "Orchestrates the run through ten tools: knowledge search, workflow state, versioned skills and every stage engine. It plans, calls tools and recommends. It cannot approve." },
    { icon: Sparkles, title: "Copilot", tag: "33 tools",
      body: "An agentic assistant across sources, models, STTM, QA packs, Soda checks, Jira and Git. It drafts changes; a human publishes them, through the same governance as a click." },
    { icon: Cpu, title: "Cortex AI in the stages", tag: "AI_COMPLETE · embeddings · search",
      body: "Explains, enriches and suggests: profile descriptions, mapping explanations, test and transformation drafting, advisory code review. Rules decide; if a call fails, the engine result stands." },
  ];
  const guardrails = [
    "No agent can approve. Approval privileges belong to approver roles only.",
    "Every approval carries a written business justification.",
    "Every agent run, tool call and decision is recorded in the audit trail.",
    "Agents act with the signed-in user's permissions; anything more becomes a change request.",
    "All model calls run on Snowflake Cortex, inside your Snowflake account's security boundary.",
  ];
  return (
    <Section id="governance" n="05" label="Agentic, governed" title={<>Agents propose. <em className="text-[color:var(--human)]">Humans approve.</em> Everything is audited.</>}
             body="Agentic speed with enterprise control: the agent does the engineering, approver roles own every decision, and the platform keeps the evidence.">
      <div className="grid gap-px overflow-hidden rounded-[6px] border border-[color:var(--ink)] bg-[color:var(--ink)] lg:grid-cols-3">
        {actors.map((a, i) => {
          const Icon = a.icon;
          return (
            <motion.div key={a.title} {...reveal} transition={{ ...reveal.transition, delay: i * 0.06 }} className="bg-[color:var(--panel)] p-7">
              <div className="flex items-center justify-between">
                <Icon className="h-6 w-6 text-[color:var(--agent)]" />
                <span className="lp-label !text-[10px]">{a.tag}</span>
              </div>
              <h3 className="lp-display mt-6 text-[30px] leading-none">{a.title}</h3>
              <p className="mt-3 text-[14px] leading-relaxed text-[color:var(--ink-2)]">{a.body}</p>
            </motion.div>
          );
        })}
      </div>
      <motion.ul {...reveal} className="mt-10 grid gap-x-10 md:grid-cols-2">
        {guardrails.map((g) => (
          <li key={g} className="flex gap-3 border-b border-[color:var(--line)] py-3.5 text-[15px]">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-[color:var(--human)]" /> {g}
          </li>
        ))}
      </motion.ul>
    </Section>
  );
}

function Value() {
  const more = [
    { title: "Learns from every approval", body: "Repeated reviewer decisions become proposed rules, replayed against past runs before a data steward approves them." },
    { title: "Reusable domain knowledge", body: "Domain knowledge packs and versioned skills carry your standards into every run and every team." },
    { title: "Gold, not just Silver", body: "Star schemas proposed from mastered Silver, approved by a second model approver, delivered as one pull request." },
  ];
  return (
    <Section id="value" n="06" label="Acceleration" title={<>The pipeline work your team writes by hand, <em className="text-[color:var(--agent)]">delivered for review.</em></>}
             body="Engineers move from writing boilerplate to approving evidence-backed artifacts, so each new source reaches a reviewed pull request with far less manual effort.">
      <motion.div {...reveal} className="overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-left">
          <thead>
            <tr className="border-b border-[color:var(--ink)]">
              <th className="lp-label py-3 pr-4 font-normal">Work</th>
              <th className="lp-label py-3 pr-4 font-normal">Manual delivery</th>
              <th className="lp-label py-3 font-normal !text-[color:var(--agent)]">With agentic delivery</th>
            </tr>
          </thead>
          <tbody>
            {VALUE.map((r) => (
              <tr key={r.task} className="border-b border-[color:var(--line)] align-top">
                <td className="py-3.5 pr-4 text-[15px] font-semibold">{r.task}</td>
                <td className="py-3.5 pr-4 text-[14px] text-[color:var(--muted)]">{r.before}</td>
                <td className="py-3.5 text-[14px]"><span className="flex gap-2"><Check className="mt-0.5 h-4 w-4 shrink-0 text-[color:var(--ok)]" />{r.now}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </motion.div>
      <div className="mt-12 grid gap-8 md:grid-cols-3">
        {more.map((m, i) => (
          <motion.div key={m.title} {...reveal} transition={{ ...reveal.transition, delay: i * 0.06 }} className="border-t-2 border-[color:var(--agent)] pt-4">
            <h3 className="lp-display text-[28px] leading-tight">{m.title}</h3>
            <p className="mt-2 text-[14px] leading-relaxed text-[color:var(--ink-2)]">{m.body}</p>
          </motion.div>
        ))}
      </div>
    </Section>
  );
}

function Sources() {
  const ways = [
    { n: "A", title: "Snowflake shares and databases", body: "Mount a share or point at a database. Tables are profiled and onboarded in place or landed as managed or Iceberg tables." },
    { n: "B", title: "Any external system", body: "Connect any source through an external connection. The platform configures the SnapLogic ingestion framework for it dynamically, so new sources need no new pipeline code." },
    { n: "C", title: "Files and object storage", body: "Bring files from cloud storage or upload them; they land in Snowflake ready for profiling." },
  ];
  return (
    <Section id="sources" n="07" label="Sources and integrations" title={<>Onboard <em className="text-[color:var(--agent)]">any</em> source. Deliver into the stack you already run.</>}
             body="One onboarding flow for every source, and native integration with the tools your data, engineering and operations teams use every day.">
      <div className="grid gap-px overflow-hidden rounded-[6px] border border-[color:var(--ink)] bg-[color:var(--ink)] md:grid-cols-3">
        {ways.map((w) => (
          <motion.div key={w.n} {...reveal} className="bg-[color:var(--panel)] p-7">
            <span className="lp-display text-[44px] leading-none text-[color:var(--agent)]">{w.n}</span>
            <h3 className="mt-4 text-[17px] font-semibold">{w.title}</h3>
            <p className="mt-2 text-[14px] leading-relaxed text-[color:var(--ink-2)]">{w.body}</p>
          </motion.div>
        ))}
      </div>
      <ul className="mt-12 border-t border-[color:var(--ink)]">
        {INTEGRATIONS.map((x) => (
          <motion.li key={x.name} {...reveal} className="group grid gap-1 border-b border-[color:var(--line)] py-4 md:grid-cols-12 md:items-baseline">
            <span className="lp-display text-[32px] leading-none transition-colors group-hover:text-[color:var(--agent)] md:col-span-5">{x.name}</span>
            <span className="text-[15px] text-[color:var(--ink-2)] md:col-span-7">{x.how}</span>
          </motion.li>
        ))}
      </ul>
    </Section>
  );
}

function Closing({ onSignIn }: { onSignIn: () => void }) {
  return (
    <section className="mx-auto max-w-[1200px] px-5 sm:px-8">
      <motion.div {...reveal} className="relative overflow-hidden rounded-[6px] bg-[color:var(--ink)] px-8 py-14 text-[color:var(--paper)] sm:px-14">
        <div aria-hidden className="lp-grid pointer-events-none absolute inset-0 opacity-[0.25] invert" />
        <div className="relative grid items-end gap-8 lg:grid-cols-12">
          <h2 className="lp-display text-[44px] leading-[1.02] sm:text-[64px] lg:col-span-8">
            Bring your next source. <em className="text-[#ff8a5c]">Ship it with human approval.</em>
          </h2>
          <div className="lg:col-span-4 lg:text-right">
            <button type="button" onClick={onSignIn}
                    className="group inline-flex h-12 items-center gap-2 rounded-full bg-[color:var(--paper)] px-6 text-[15px] font-medium text-[color:var(--ink)] transition hover:bg-[#e7e3d8]">
              <Snowflake className="h-4 w-4" /> Sign in with Snowflake <ArrowRight className="h-4 w-4 transition group-hover:translate-x-0.5" />
            </button>
            <p className="mt-3 text-[13px] text-[#9b978c]">Your Snowflake role and grants decide what you can see.</p>
          </div>
        </div>
      </motion.div>
    </section>
  );
}

export function Landing({ mode, expired }: { mode: AuthMode; expired: boolean }) {
  const [open, setOpen] = useState(expired);
  const close = useCallback(() => setOpen(false), []);
  const show = useCallback(() => setOpen(true), []);
  return (
    <MotionConfig reducedMotion="user">
      <div className="lp min-h-screen overflow-x-clip font-sans antialiased">
        <TopBar onSignIn={show} />
        <main className="space-y-28 pb-24">
          <Hero onSignIn={show} />
          <AgentAtWork />
          <KnowledgeLoop />
          <FeatureExplorer />
          <HowItWorks />
          <Governance />
          <Value />
          <Sources />
          <Closing onSignIn={show} />
        </main>
        <footer className="border-t border-[color:var(--ink)]">
          <div className="mx-auto flex max-w-[1200px] flex-wrap items-center gap-4 px-5 py-8 text-[13px] text-[color:var(--muted)] sm:px-8">
            <span className="flex items-center gap-2 text-[color:var(--ink)]"><LogoMark size="sm" className="scale-75" /> {PRODUCT}</span>
            <span className="lp-label ml-auto">Agentic · Knowledge-driven · Human-approved · Snowflake-native</span>
          </div>
        </footer>
        <SignInDialog open={open} onClose={close} mode={mode} expired={expired} />
      </div>
    </MotionConfig>
  );
}
