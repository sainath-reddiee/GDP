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
import { NextChapter, reveal, Section } from "./landing-ui";
import { SignInDialog } from "./sign-in-dialog";
import type { AuthMode } from "./form";

// Every claim on this page is traceable to the code: stages, gates and tools (snowflake/database/seed/workflow_graph.json,
// snowflake/agents/supervisor.sql), where AI is used (services/common/llm.py call sites), the Copilot (services/agentic),
// governance and audit (services/governance/policy.py, CORE.AGENT_RUN) and integrations (services/*). Keep it that way.

// The page as a story: why it matters, how it works, see it run, why it gets smarter, what is in it, why you can trust
// it, and how it fits your stack. The nav follows the same order.
const NAV = [
  { href: "#value", label: "Why" },
  { href: "#how", label: "How it works" },
  { href: "#agent", label: "See it run" },
  { href: "#knowledge", label: "Knowledge loop" },
  { href: "#features", label: "Features" },
  { href: "#governance", label: "Trust" },
  { href: "#sources", label: "Integrations" },
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
  { item: "Data Mapping: transformations and joins", human: "Data Mapping approver" },
  { item: "SodaCL and Great Expectations checks", human: "DQ approver" },
  { item: "QA test suite", human: "QA lead" },
  { item: "Silver dbt models, compiled" },
  { item: "Code review and GitHub PR", human: "Code reviewer" },
];

const FACTS = [
  { value: "4", label: "human approval gates in every run" },
  { value: "3", label: "agent lanes in parallel after the Data Mapping" },
  { value: "0", label: "auto-approvals by any agent" },
  { value: "100%", label: "of AI calls on Snowflake Cortex" },
];

/** The Silver pipeline, as the platform runs it. tool: the agent tool or procedure behind the stage. */
/** The Silver pipeline in plain words, for anyone: what each stage does, what it hands over, where AI helps and who
 *  signs it off. (The engineering detail, tool by tool, lives in the product and the guidebook.) */
const STAGES: { name: string; does: string; gives: string; ai?: string; human?: string; parallel?: boolean }[] = [
  { name: "Connect the source", does: "Connects to the source and brings its tables into Snowflake, or reads them where they already are.",
    gives: "Source tables, ready to work on" },
  { name: "Profile the data", does: "Reads every column to learn what the data looks like: types, patterns, keys, gaps and personal data.",
    gives: "A profile of every column", ai: "Describes each column and flags personal data" },
  { name: "Recognise the business area", does: "Works out which part of the business the data belongs to, such as customers or orders, so the right rules apply.",
    gives: "The matching business domain", ai: "Searches your team's knowledge", human: "Confirmed by a person if unsure" },
  { name: "Map the fields", does: "Suggests which source column feeds each target column, and shows the evidence for every match.",
    gives: "A source-to-target map", ai: "Explains the uncertain matches", human: "Model approver" },
  { name: "Write the transformation rules", does: "Writes how source data becomes target data: calculations, joins and business rules (the Data Mapping).",
    gives: "Versioned transformation rules", ai: "Turns plain-English instructions into rules", human: "Data Mapping approver" },
  { name: "Add data quality checks", does: "Writes checks that catch bad data, and tries them on real data before anyone approves them.",
    gives: "Data quality checks", human: "Data quality approver", parallel: true },
  { name: "Test the results", does: "Writes tests that prove the target data matches the source: counts, keys, totals and joins.",
    gives: "A test suite", ai: "Drafts tests from plain English", human: "QA lead", parallel: true },
  { name: "Generate the code", does: "Writes the dbt code that builds the tables, and checks that it compiles in Snowflake.",
    gives: "Ready-to-run dbt code", parallel: true },
  { name: "Review and ship", does: "Reviews the generated code and packages everything as a pull request for your team.",
    gives: "A pull request in GitHub", ai: "Gives a second-opinion review", human: "Code reviewer" },
];

const VALUE: { task: string; before: string; now: string }[] = [
  { task: "Source discovery", before: "Ad hoc profiling queries, table by table", now: "Every column profiled by the agent, reused while the table is unchanged" },
  { task: "Source-to-target mapping", before: "Spreadsheets maintained by hand", now: "Ranked candidates with evidence; the approver decides each column" },
  { task: "STTM", before: "Transformations and joins written and versioned manually", now: "Generated from the approved mapping, versioned and editable" },
  { task: "Data quality", before: "Soda and GX rules written one by one", now: "Checks generated from the Data Mapping and profile, backtested before approval" },
  { task: "QA testing", before: "Reconciliation SQL copied between projects", now: "Tests in 8 categories traced to the Data Mapping, plus reusable domain packs" },
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
      <div className="mx-auto flex h-16 max-w-[1200px] 2xl:max-w-[1360px] items-center gap-6 px-5 sm:px-8">
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
      <div className="relative mx-auto grid max-w-[1200px] 2xl:max-w-[1360px] gap-12 px-5 pb-16 pt-14 sm:px-8 lg:grid-cols-12 lg:pt-20">
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
            Cortex orchestrates profiling, mapping, Data Mapping, data quality, QA and code generation, and stops at every human
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
                    <span className="inline-flex items-center gap-1 rounded-full bg-[#1d4ed8]/10 px-2 py-0.5 text-[10px] font-semibold text-[color:var(--agent)]"><Bot className="h-3 w-3" /> Agent</span>
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

      <div className="mx-auto max-w-[1200px] 2xl:max-w-[1360px] px-5 sm:px-8">
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
    <section id="agent" className="scroll-mt-16 bg-[#0e0d0a] pb-12 pt-6 text-[#f3f1ea]">
      <div className="mx-auto max-w-[1200px] 2xl:max-w-[1360px] px-5 sm:px-8">
        <motion.div {...reveal} className="flex items-center gap-4 border-t border-[#f3f1ea] pt-4">
          <span className="lp-label !text-[#f3f1ea]">§ 03</span><span className="lp-label !text-[#9b978c]">See it run</span>
        </motion.div>
        <div className="mt-5 grid gap-4 lg:grid-cols-12">
          <motion.h2 {...reveal} className="lp-display text-[32px] leading-[1.05] sm:text-[38px] lg:col-span-7">
            Watch the agent work. <em className="text-[#ff8a5c]">Approve</em> what it delivers.
          </motion.h2>
          <motion.p {...reveal} className="self-end text-[16px] leading-relaxed text-[#c9c5ba] lg:col-span-5">
            A replay of a Silver run with the platform&apos;s real stages and roles: the agent runs each stage and pauses at every gate until an approver signs off. Source tables are illustrative.
          </motion.p>
        </div>
        <motion.div {...reveal} className="mt-6"><RunPreview /></motion.div>
        <div className="mt-6 flex flex-wrap gap-x-6 gap-y-2 text-[12px] text-[#9b978c]">
          <span className="flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-[#8b89ff]" /> Agent tool call</span>
          <span className="flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-[#29b5e8]" /> Cortex AI assist</span>
          <span className="flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-[#b45309]" /> Human approval</span>
        </div>
        <NextChapter href="#knowledge" label="Why it gets smarter with every run" dark />
      </div>
    </section>
  );
}

function HowItWorks() {
  return (
    <Section id="how" n="02" label="How it works" next={{ href: "#agent", label: "See a run, start to finish" }}
             title={<>Nine stages. Four <em className="text-[color:var(--human)]">human approval</em> gates. One pull request.</>}
             body="From the moment a source is connected to the moment the code is ready, the agent does the work in your Snowflake account, using the same rules every time. AI helps where judgement is needed, and the run stops at each approval gate until the right person signs off.">
      {/* one row per stage on wide screens; one card per stage on narrow ones, so nothing scrolls sideways or gets cut */}
      <ol className="space-y-3 lg:hidden">
        {STAGES.map((s, i) => (
          <motion.li key={s.name} {...reveal} className="rounded-[6px] border border-[color:var(--line)] bg-[color:var(--panel)] p-4">
            <p className="flex flex-wrap items-baseline gap-x-2">
              <span className="lp-mono text-[12px] text-[color:var(--muted)]">{String(i + 1).padStart(2, "0")}</span>
              <span className="text-[16px] font-semibold">{s.name}</span>
              {s.parallel && <span className="lp-label !text-[10px] !text-[color:var(--agent)]">in parallel</span>}
            </p>
            <p className="mt-2 text-[14px] text-[color:var(--ink-2)]">{s.does}</p>
            <p className="mt-2 flex gap-2 text-[14px] font-medium"><Check className="mt-0.5 h-4 w-4 shrink-0 text-[color:var(--ok)]" />{s.gives}</p>
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[13px]">
              {s.ai && <span className="inline-flex items-center gap-1.5 text-[color:var(--cortex)]"><Sparkles className="h-3.5 w-3.5" /> {s.ai}</span>}
              {s.human && <span className="inline-flex items-center gap-1.5 font-medium text-[color:var(--human)]"><UserCheck className="h-3.5 w-3.5" /> {s.human}</span>}
            </div>
          </motion.li>
        ))}
      </ol>
      <motion.div {...reveal} className="hidden lg:block">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-[color:var(--ink)]">
              {["#", "Stage", "What happens", "You get", "AI helps by", "Who approves"].map((h) => (
                <th key={h} className="lp-label py-3 pr-4 font-normal">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {STAGES.map((s, i) => (
              <tr key={s.name} className="group border-b border-[color:var(--line)] align-top transition-colors hover:bg-[color:var(--panel)]">
                <td className="lp-mono py-4 pr-4 text-[12px] text-[color:var(--muted)]">{String(i + 1).padStart(2, "0")}</td>
                <td className="w-[17%] py-4 pr-4 text-[15px] font-semibold">
                  {s.name}{s.parallel && <span className="lp-label mt-1 block !text-[10px] !text-[color:var(--agent)]">in parallel</span>}
                </td>
                <td className="w-[32%] py-4 pr-4 text-[14px] text-[color:var(--ink-2)]">{s.does}</td>
                <td className="py-4 pr-4 text-[14px] font-medium">{s.gives}</td>
                <td className="py-4 pr-4 text-[13px] text-[color:var(--cortex)]">{s.ai ?? <span className="text-[color:var(--line)]">-</span>}</td>
                <td className="py-4 text-[13px]">
                  {s.human
                    ? <span className="inline-flex items-center gap-1.5 font-medium text-[color:var(--human)]"><UserCheck className="h-3.5 w-3.5 shrink-0" /> {s.human}</span>
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
      body: "An agentic assistant across sources, models, Data Mapping, QA packs, Soda checks, Jira and Git. It drafts changes; a human publishes them, through the same governance as a click." },
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
    <Section id="governance" n="06" label="Why you can trust it" next={{ href: "#sources", label: "How it fits your stack" }} title={<>Agents propose. <em className="text-[color:var(--human)]">Humans approve.</em> Everything is audited.</>}
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
    <Section id="value" n="01" label="Why it matters" next={{ href: "#how", label: "How it works" }} title={<>The pipeline work your team writes by hand, <em className="text-[color:var(--agent)]">delivered for review.</em></>}
             body="Engineers move from writing boilerplate to approving evidence-backed artifacts, so each new source reaches a reviewed pull request with far less manual effort.">
      <ul className="space-y-3 md:hidden">
        {VALUE.map((r) => (
          <motion.li key={r.task} {...reveal} className="rounded-[6px] border border-[color:var(--line)] bg-[color:var(--panel)] p-4">
            <p className="text-[16px] font-semibold">{r.task}</p>
            <p className="mt-2 text-[13px] text-[color:var(--muted)]"><span className="lp-label mr-2 !text-[10px]">Manual</span>{r.before}</p>
            <p className="mt-1.5 flex gap-2 text-[14px]"><Check className="mt-0.5 h-4 w-4 shrink-0 text-[color:var(--ok)]" />{r.now}</p>
          </motion.li>
        ))}
      </ul>
      <motion.div {...reveal} className="hidden md:block">
        <table className="w-full border-collapse text-left">
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
    <Section id="sources" n="07" label="Fits your stack" title={<>Onboard <em className="text-[color:var(--agent)]">any</em> source. Deliver into the stack you already run.</>}
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
    <section className="mx-auto max-w-[1200px] 2xl:max-w-[1360px] px-5 sm:px-8">
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

/** The product page. `signIn` opens the sign-in dialog straight away (/login, or an ended session); the home page
 *  (/) opens with the page itself and sign-in one click away in the top bar. */
export function Landing({ mode, expired, signIn = false }: { mode: AuthMode; expired: boolean; signIn?: boolean }) {
  const [open, setOpen] = useState(expired || signIn);
  const close = useCallback(() => setOpen(false), []);
  const show = useCallback(() => setOpen(true), []);
  return (
    <MotionConfig reducedMotion="user">
      <div className="lp min-h-screen overflow-x-clip font-sans antialiased">
        <TopBar onSignIn={show} />
        <main className="space-y-28 pb-24">
          <Hero onSignIn={show} />
          <Value />
          <HowItWorks />
          <AgentAtWork />
          <KnowledgeLoop />
          <FeatureExplorer />
          <Governance />
          <Sources />
          <Closing onSignIn={show} />
        </main>
        <footer className="border-t border-[color:var(--ink)]">
          <div className="mx-auto flex max-w-[1200px] 2xl:max-w-[1360px] flex-wrap items-center gap-4 px-5 py-8 text-[13px] text-[color:var(--muted)] sm:px-8">
            <span className="flex items-center gap-2 text-[color:var(--ink)]"><LogoMark size="sm" className="scale-75" /> {PRODUCT}</span>
            <span className="lp-label ml-auto">Agentic · Knowledge-driven · Human-approved · Snowflake-native</span>
          </div>
        </footer>
        <SignInDialog open={open} onClose={close} mode={mode} expired={expired} />
      </div>
    </MotionConfig>
  );
}
