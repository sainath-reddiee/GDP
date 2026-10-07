import { Cpu, Layers3, Snowflake } from "lucide-react";
import { AUTH_MODE } from "@/lib/api";
import { LoginForm, type AuthMode } from "./form";
import { AgentOrchestration } from "./agent-orchestration";

export const metadata = { title: "Sign in · Agentic Pipeline" };

function LogoMark() {
  return (
    <span className="relative grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-cyan-400 via-indigo-500 to-violet-600 shadow-[0_0_28px_rgba(99,102,241,0.45)]">
      <svg viewBox="0 0 24 24" className="h-5 w-5 text-white" fill="none" stroke="currentColor" strokeWidth="2"
           strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <circle cx="5" cy="6" r="2.2" />
        <circle cx="5" cy="18" r="2.2" />
        <circle cx="19" cy="12" r="2.4" />
        <path d="M7.2 6.4c4.6.6 6 2.6 9.4 4.8M7.2 17.6c4.6-.6 6-2.6 9.4-4.8" />
      </svg>
      <span aria-hidden className="absolute inset-0 rounded-xl ring-1 ring-inset ring-white/25" />
    </span>
  );
}

const TELEMETRY = [
  { icon: Snowflake, label: "Snowflake native" },
  { icon: Cpu, label: "Cortex enabled" },
  { icon: Layers3, label: "Medallion ready" },
];

export default function LoginPage() {
  const mode: AuthMode = AUTH_MODE === "pat" ? "pat" : "dev";
  return (
    <div className="relative min-h-screen overflow-clip bg-[#080B10] font-sans text-slate-200 antialiased">
      {/* ambient light and fine grid */}
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute left-1/2 top-[-20%] h-[640px] w-[900px] -translate-x-1/2 rounded-full bg-indigo-600/20 blur-[140px]" />
        <div className="absolute bottom-[-25%] right-[-10%] h-[520px] w-[620px] rounded-full bg-cyan-500/10 blur-[140px]" />
        <div className="absolute inset-0 bg-[linear-gradient(to_right,#1f293710_1px,transparent_1px),linear-gradient(to_bottom,#1f293710_1px,transparent_1px)] bg-[size:4rem_4rem] [mask-image:radial-gradient(ellipse_60%_50%_at_50%_0%,#000_70%,transparent_100%)]" />
      </div>

      <div className="relative mx-auto grid min-h-screen max-w-[1440px] grid-cols-1 gap-10 px-5 py-8 sm:px-8 lg:grid-cols-12 lg:gap-12 lg:px-12 lg:py-10">
        <div className="flex min-w-0 flex-col lg:col-span-5">
          <header className="flex items-center gap-3">
            <LogoMark />
            <div>
              <p className="text-sm font-semibold tracking-tight text-white">Agentic Pipeline</p>
              <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-slate-500">Autonomous data engineering</p>
            </div>
          </header>

          <main className="flex flex-1 flex-col justify-center py-10 lg:py-0">
            <div className="mx-auto w-full max-w-md">
              <span className="inline-flex items-center gap-2 rounded-full border border-indigo-400/25 bg-indigo-500/10 px-3 py-1 text-[11px] font-medium text-indigo-200">
                <span className="h-1.5 w-1.5 animate-glow rounded-full bg-emerald-400" /> Workspace sign-in
              </span>
              <h1 className="mt-5 text-3xl font-semibold leading-tight tracking-tight text-white sm:text-[2.1rem]">
                Sign in to your workspace
              </h1>
              <p className="mt-3 text-[15px] leading-relaxed text-slate-400">
                AI agents profile your sources, model them against your domain, write the tests and generate dbt.
                You approve every gate.
              </p>

              <div className="mt-8 rounded-2xl border border-slate-800/60 bg-[rgba(15,23,42,0.65)] p-5 shadow-[0_20px_60px_-30px_rgba(2,6,23,0.9)] backdrop-blur-xl sm:p-6">
                <LoginForm mode={mode} />
              </div>
            </div>
          </main>

          <footer className="flex flex-wrap items-center gap-2">
            {TELEMETRY.map(({ icon: Icon, label }, i) => (
              <span key={label} className="inline-flex items-center gap-1.5 rounded-full border border-slate-800 bg-slate-900/60 px-2.5 py-1 text-[11px] text-slate-400">
                <Icon className={i === 0 ? "h-3 w-3 text-cyan-400" : i === 1 ? "h-3 w-3 text-violet-400" : "h-3 w-3 text-emerald-400"} />
                {label}
              </span>
            ))}
          </footer>
        </div>

        <aside className="min-w-0 lg:col-span-7 lg:py-2">
          <div className="lg:sticky lg:top-10 lg:h-[calc(100vh-5rem)] lg:min-h-[600px]">
            <AgentOrchestration />
          </div>
        </aside>
      </div>
    </div>
  );
}
