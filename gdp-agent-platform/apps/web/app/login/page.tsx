import type { ReactNode } from "react";
import {
  Database,
  GitMerge,
  ScanSearch,
  ShieldCheck,
  Sparkles,
  Workflow,
} from "lucide-react";
import { AUTH_MODE } from "@/lib/api";
import { continueDev } from "./actions";
import { LoginButton } from "./login-button";
import { LoginForm } from "./form";

const trunk = ["Source", "Landing", "Profiling", "Mapping"] as const;

const pillars = [
  {
    icon: Database,
    title: "Point at a source and a target that already exist",
    body: "Onboard from an account database or a mounted Snowflake share. Choose the silver table you are modelling into, or profile a new source and store the suggestion — instead of inventing a model in a notebook.",
  },
  {
    icon: ScanSearch,
    title: "Profile so mapping has evidence, not guesses",
    body: "Landing is a CTAS as-is. Profiling then captures SQL statistics, PII signals, and Cortex column descriptions so every later decision has evidence, not a guess.",
  },
  {
    icon: GitMerge,
    title: "Hybrid mapping, then a human review card",
    body: "Seven scores rank each candidate: datatype, cardinality, keywords, embeddings, domain rules, historical approvals, and uniqueness. Cortex only adjudicates the ambiguous band. You see the evidence and Approve, Modify, or Reject.",
  },
  {
    icon: Workflow,
    title: "From one STTM, Data Quality and dbt run as separate tracks",
    body: "Approved columns become the source-to-target contract. That contract fans out: Data Quality expectations on one track, compile-only dbt on the other. They generate in parallel. Neither waits on the other.",
  },
];

const principles = [
  {
    icon: ShieldCheck,
    title: "Gates you cannot skip",
    body: "Mapping and the STTM stop for a person. Data Quality and dbt each review on their own track after they generate from that contract. The supervisor can recommend the next step. It cannot approve a gate.",
  },
  {
    icon: Sparkles,
    title: "Every review makes the next run smarter",
    body: "Your decision is stored as domain knowledge. Later runs score historical matches higher, and Cortex sees the patterns your team already accepted.",
  },
];

function Node({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-md bg-white/10 px-2.5 py-1 text-xs font-medium text-white">
      {children}
    </span>
  );
}

function FactoryFlow() {
  return (
    <figure className="rounded-xl border border-white/10 bg-black/20 p-5">
      <figcaption className="mb-4 text-[11px] font-semibold uppercase tracking-[0.16em] text-white/50">
        How a run moves
      </figcaption>

      <ol className="flex flex-wrap items-center justify-center gap-1.5">
        {trunk.map((stage, i) => (
          <li key={stage} className="flex items-center gap-1.5">
            <Node>{stage}</Node>
            {i < trunk.length - 1 && <span aria-hidden className="text-white/30">→</span>}
          </li>
        ))}
      </ol>

      <div className="mt-1 flex flex-col items-center">
        <span aria-hidden className="py-1 text-white/30">↓</span>
        <div className="rounded-lg border border-white/25 bg-white/10 px-4 py-2 text-center">
          <p className="text-sm font-semibold text-white">STTM</p>
          <p className="text-[11px] text-white/55">Approved source-to-target contract</p>
        </div>
      </div>

      <div className="mx-auto mt-0 w-full max-w-lg">
        <div className="flex justify-center">
          <div aria-hidden className="h-4 w-px bg-white/30" />
        </div>
        <div aria-hidden className="mx-[25%] h-px bg-white/30" />
        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col items-center">
            <div aria-hidden className="h-4 w-px bg-white/30" />
            <div className="w-full rounded-lg border border-white/15 bg-white/5 px-3 py-3 text-center">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-white/45">Parallel track</p>
              <p className="mt-1 text-sm font-semibold text-white">Data Quality</p>
              <p className="mt-1 text-[11px] leading-relaxed text-white/55">
                Quality expectations generated from the contract.
              </p>
            </div>
          </div>
          <div className="flex flex-col items-center">
            <div aria-hidden className="h-4 w-px bg-white/30" />
            <div className="w-full rounded-lg border border-white/15 bg-white/5 px-3 py-3 text-center">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-white/45">Parallel track</p>
              <p className="mt-1 text-sm font-semibold text-white">dbt</p>
              <p className="mt-1 text-[11px] leading-relaxed text-white/55">
                Models compiled from the same STTM, then validated.
              </p>
            </div>
          </div>
        </div>
      </div>
    </figure>
  );
}

function LoginAction() {
  if (AUTH_MODE === "pat") {
    return (
      <div className="w-full max-w-xs rounded-lg bg-card p-4 shadow-sm">
        <LoginForm />
      </div>
    );
  }
  return (
    <form action={continueDev}>
      <LoginButton />
    </form>
  );
}

export default function LoginPage() {
  return (
    <div className="relative min-h-screen overflow-hidden bg-sidebar text-sidebar-foreground">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-40"
        style={{
          backgroundImage:
            "radial-gradient(ellipse 80% 50% at 10% -10%, hsl(213 80% 40% / 0.45), transparent 55%), radial-gradient(ellipse 50% 40% at 90% 110%, hsl(213 70% 28% / 0.35), transparent 50%)",
        }}
      />
      <header className="relative flex items-center justify-between px-6 py-5 lg:px-10">
        <p className="text-sm font-semibold text-white">Agentic pipeline</p>
        <LoginAction />
      </header>
      <main className="relative mx-auto flex max-w-3xl flex-col gap-10 px-6 pb-16 pt-4 lg:px-10">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-white/70">
            Snowflake-native · Cortex-orchestrated · Human-gated
          </p>
          <h1 className="mt-4 text-4xl font-semibold leading-tight tracking-tight text-white lg:text-[2.6rem]">
            Onboard a new source onto the Agentic pipeline you already run.
          </h1>
          <p className="mt-4 max-w-2xl text-lg leading-relaxed text-white/85">
            Profile it, map it, approve the contract — then Data Quality and dbt generate
            from that STTM in parallel.
          </p>
        </div>

        <p className="max-w-2xl text-sm leading-relaxed text-white/70">
          A customer share is mounted, or the database is already in the account.
          You point the factory at the source tables and the silver target they
          must land on. Tables copy as-is. Profiling builds the evidence. Mapping
          proposes every column. After you approve the source-to-target document,
          two tracks start from it at the same time: Data Quality writes the
          checks, dbt compiles the models. Cortex does the analysis. You sign
          every gate.
        </p>

        <FactoryFlow />

        <div className="grid gap-3 sm:grid-cols-2">
          {pillars.map(({ icon: Icon, title, body }) => (
            <article key={title} className="rounded-lg border border-white/10 bg-white/5 p-4">
              <div className="mb-2 flex h-8 w-8 items-center justify-center rounded-md bg-white/10 text-white">
                <Icon className="h-4 w-4" />
              </div>
              <p className="text-sm font-semibold text-white">{title}</p>
              <p className="mt-1.5 text-xs leading-relaxed text-white/65">{body}</p>
            </article>
          ))}
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {principles.map(({ icon: Icon, title, body }) => (
            <article key={title} className="flex gap-3 rounded-lg border border-white/10 px-4 py-3">
              <Icon className="mt-0.5 h-4 w-4 shrink-0 text-white/80" />
              <div>
                <h3 className="text-sm font-semibold text-white">{title}</h3>
                <p className="mt-1 text-xs leading-relaxed text-white/65">{body}</p>
              </div>
            </article>
          ))}
        </div>
      </main>
    </div>
  );
}
