import Link from "next/link";
import { ArrowRight, BookOpen, Bot, CheckCircle2, Hand, Layers, Rocket, ShieldCheck, Sparkles, TriangleAlert } from "lucide-react";
import { whoami } from "@/lib/api";
import { GATES, stateLabel } from "@/lib/run-labels";
import type { RunSummary } from "@/lib/types";
import { currentPersona, loadDomains, loadKnowledgeOverview, loadRuns } from "@/lib/os/data";
import { currentStep, lifecycleOf, progressOf } from "@/lib/os/lifecycle";
import { Bar, Btn, Card, Empty, Kpi, SourceError, Stepper, Tag, ago, parseTs, pretty, type Tone } from "@/components/os/ui";

export const dynamic = "force-dynamic";

const greeting = () => {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
};
const firstName = (user: string | null | undefined) => {
  if (!user) return "";
  const w = user.split(/[._@\s-]+/)[0];
  return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
};

type QueueRow = { run: RunSummary; reason: string; tone: Tone; action: string; href: string; mine: boolean; rank: number };

// as on the classic My Work: failures count for 14 days, then they are history, not work
const RECENT = 14 * 24 * 3600 * 1000;

function queueRow(run: RunSummary, personaStates: string[]): QueueRow | null {
  const s = run.current_state;
  const mine = personaStates.includes(s);
  const at = (rank: number, reason: string, tone: Tone, action: string, page: "review" | "build") =>
    ({ run, reason, tone, action, href: `/os/${page}?run=${run.run_id}`, mine, rank });
  if (s in GATES) return at(0, `${GATES[s].name} awaits ${GATES[s].role.toLowerCase()}`, "amber", "Review", "review");
  if (s === "VALIDATION_FAILED") return at(1, "Validation failed: below the gate", "red", "Fix", "review");
  if (mine && run.lifecycle === "RUNNING") return at(2, stateLabel(s), "blue", "Open", "build");
  if (s === "FAILED" && Date.now() - parseTs(run.updated_at ?? run.created_at) < RECENT) return at(3, "Run failed", "red", "Investigate", "build");
  return null;
}

export default async function MissionControl() {
  const persona = currentPersona();
  const [me, runs, domains, knowledge] = await Promise.all([whoami(), loadRuns("sort=updated&limit=150"), loadDomains(), loadKnowledgeOverview()]);
  const live = runs.data.runs.filter((r) => !r.is_archived);
  const queue = live.map((r) => queueRow(r, persona.states)).filter((x): x is QueueRow => !!x)
    .sort((a, b) => Number(b.mine) - Number(a.mine) || a.rank - b.rank || (b.run.updated_at ?? b.run.created_at).localeCompare(a.run.updated_at ?? a.run.created_at));
  const atGate = live.filter((r) => r.current_state in GATES);
  const autonomous = live.filter((r) => r.lifecycle === "RUNNING" && !(r.current_state in GATES));
  const releaseReady = live.filter((r) => ["DBT_REVIEW", "DBT_APPROVED"].includes(r.current_state));
  const completed = live.filter((r) => r.current_state === "COMPLETED");
  const recent = live.filter((r) => r.lifecycle !== "DRAFT").slice(0, 3);
  const k = knowledge.data;
  const gateCounts = Object.entries(GATES).map(([state, g]) => ({ state, g, n: atGate.filter((r) => r.current_state === state).length }));
  const estate = domains.data.filter((d) => d.knowledge_items > 0 || d.target_tables > 0);

  return (
    <>
      <section className="os-hero os-rise mb-4 px-7 py-6 sm:px-8">
        <span className="shape1" /><span className="shape2" /><span className="shape3" />
        <div className="relative flex flex-wrap items-end justify-between gap-4">
          <div className="max-w-2xl">
            <div className="mb-2 text-[10.5px] font-extrabold uppercase tracking-[0.12em] text-[#bfeeda]">Mission control / {persona.label}</div>
            <h1 className="text-[26px] font-extrabold tracking-[-0.03em]">{greeting()}{me?.user ? `, ${firstName(me.user)}` : ""}.</h1>
            <p className="mt-2 text-[13px] leading-relaxed text-[#d8f1e7]">
              {atGate.length
                ? <>{atGate.length} decision{atGate.length === 1 ? "" : "s"} wait on a person across {new Set(atGate.map((r) => r.domain_name)).size} domain{new Set(atGate.map((r) => r.domain_name)).size === 1 ? "" : "s"}; </>
                : <>No gate waits on a person right now; </>}
              {autonomous.length} workflow{autonomous.length === 1 ? " is" : "s are"} moving forward on {autonomous.length === 1 ? "its" : "their"} own. Focus on the decisions that need human judgment.
            </p>
          </div>
          <div className="flex gap-2">
            <Link href="/os/intake" className="os-btn border-white/30 bg-white/10 text-white hover:bg-white/20"><Rocket className="h-3.5 w-3.5" /> New onboarding</Link>
            {queue[0] && <Link href={queue[0].href} className="os-btn border-white bg-white text-[#004c3b] hover:bg-[#eafaf2]">Resume {queue[0].run.domain_name ?? "latest"} <ArrowRight className="h-3.5 w-3.5" /></Link>}
          </div>
        </div>
      </section>

      <SourceError what="Workflow runs" error={runs.error} />

      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi label="Ready for decision" value={atGate.length} hint={`${queue.filter((q) => q.mine).length} in your role's queue`} icon={Hand} tone="amber" href="#queue" />
        <Kpi label="Autonomous workflows" value={autonomous.length} hint="progressing without intervention" icon={Bot} tone="blue" href="/os/build" />
        <Kpi label="Ready to release" value={releaseReady.length} hint={`${completed.length} completed end to end`} icon={Rocket} tone="green" href="/os/release" />
        <Kpi label="Knowledge contributions" value={k?.totals.learned_7d ?? "–"} hint={k ? `learned in 7 days; ${k.totals.inbox} to curate` : "Enterprise Memory unavailable"} icon={Sparkles} tone="purple" href="/os/knowledge" />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Card id="queue" title="My action queue" subtitle={`Decisions and blocked work, ${persona.short.toLowerCase()} items first`}
                action={<Btn href="/approvals">All approvals</Btn>} pad={false}>
            {queue.length ? (
              <div className="overflow-x-auto">
                <table className="os-table">
                  <thead><tr><th>Workflow</th><th>Current step</th><th>Status</th><th /></tr></thead>
                  <tbody>
                    {queue.slice(0, 8).map((q) => {
                      const step = currentStep(lifecycleOf(q.run));
                      const failed = q.run.current_state === "FAILED";
                      return (
                        <tr key={q.run.run_id}>
                          <td className="max-w-[280px]">
                            <Link href={`/os/build?run=${q.run.run_id}`} className="block truncate font-bold hover:underline" title={q.run.run_name}>{q.run.run_name}</Link>
                            <div className="os-faint truncate text-[11px]">{q.run.domain_name ?? "No domain"} · {q.run.source_system_name ?? "No source"} · {ago(q.run.updated_at ?? q.run.created_at)}</div>
                          </td>
                          <td className="max-w-[240px]">
                            <div className="font-semibold">{failed ? "Stopped" : step.label}</div>
                            <div className="os-faint truncate text-[11px]" title={q.reason}>{failed ? q.reason : `${step.owner} · ${q.reason}`}</div>
                          </td>
                          <td className="whitespace-nowrap"><Tag tone={q.tone}>{stateLabel(q.run.current_state)}</Tag>{q.mine && <Tag tone="green" className="ml-1">Your role</Tag>}</td>
                          <td className="text-right"><Btn href={q.href} variant={q.tone === "red" ? "red" : q.tone === "amber" ? "warn" : "default"}>{q.action}</Btn></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="p-5"><Empty icon={CheckCircle2} title="Nothing waits on you">No run is at a gate, failed validation or in your role&apos;s steps right now.</Empty></div>
            )}
          </Card>

          <Card title="Recently active workflows" subtitle="The latest runs, with where each one is in the lifecycle" action={<Btn href="/runs">All runs</Btn>}>
            {recent.length ? (
              <div className="grid gap-3 lg:grid-cols-3">
                {recent.map((r) => {
                  const steps = lifecycleOf(r);
                  const step = currentStep(steps);
                  return (
                    <Link key={r.run_id} href={`/os/build?run=${r.run_id}`} className="os-card block p-4 transition hover:-translate-y-0.5 hover:shadow-[var(--os-shadow-pop)]">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="truncate text-[13px] font-extrabold" title={r.run_name}>{r.run_name}</div>
                          <div className="os-faint truncate text-[11px]">{r.domain_name ?? "No domain"} · {r.source_system_name ?? "no source"}</div>
                        </div>
                        <Tag tone={step.status === "failed" ? "red" : step.status === "waiting" ? "amber" : step.status === "done" ? "green" : "blue"}>{step.label}</Tag>
                      </div>
                      <div className="my-3"><Stepper steps={steps} compact /></div>
                      <Bar value={progressOf(r) * 100} tone={step.status === "failed" ? "red" : step.status === "waiting" ? "amber" : "green"} />
                      <div className="os-faint mt-2 text-[11px]">{stateLabel(r.current_state)} · {ago(r.updated_at ?? r.created_at)}</div>
                    </Link>
                  );
                })}
              </div>
            ) : <Empty title="No runs yet">Start an onboarding to see it here.</Empty>}
          </Card>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          <Card title="Autonomy center" subtitle="What the agents do alone, and where policy hands to a person" action={<Btn href="/os/build">Open workflows</Btn>}>
            <div className="os-divide">
              <div className="flex items-center justify-between gap-3 pb-3">
                <div><div className="text-[12.5px] font-bold">Auto-advancing</div><div className="os-faint text-[11px]">Profiling, generation and validation run as tools and agents</div></div>
                <Tag tone="blue">{autonomous.length} runs</Tag>
              </div>
              {gateCounts.map(({ state, g, n }) => (
                <div key={state} className="flex items-center justify-between gap-3 py-3">
                  <div className="min-w-0"><div className="text-[12.5px] font-bold">{g.name}</div><div className="os-faint text-[11px]">Human gate · {g.role}</div></div>
                  <Tag tone={n ? "amber" : "neutral"}>{n ? `${n} waiting` : "Clear"}</Tag>
                </div>
              ))}
              <div className="pt-3 text-[11.5px] leading-relaxed os-muted">
                <ShieldCheck className="mr-1 inline h-3.5 w-3.5 text-[var(--os-emerald)]" />
                A run reaches code review only when every validation check passes with no errors. Code is never merged or deployed automatically.
              </div>
            </div>
          </Card>

          <Card title="Knowledge pulse" subtitle="Enterprise Memory learned from runs, reviews and operations" action={<Btn href="/os/knowledge">Open knowledge</Btn>}>
            <SourceError what="Enterprise Memory" error={knowledge.error} />
            {k ? (
              <>
                <div className="mb-3 grid grid-cols-3 gap-2 text-center">
                  {[["Active", k.totals.active], ["Learned 7d", k.totals.learned_7d], ["To curate", k.totals.inbox]].map(([l, v]) => (
                    <div key={l} className="os-subtle rounded-lg py-2"><div className="text-[18px] font-extrabold tabular-nums">{v}</div><div className="os-faint text-[10.5px]">{l}</div></div>
                  ))}
                </div>
                <div className="os-muted mb-1.5 text-[10.5px] font-bold uppercase tracking-wide">Most used by runs (30 days)</div>
                <div className="os-divide">
                  {k.top_used.slice(0, 4).map((t) => (
                    <Link key={t.knowledge_id} href={`/os/knowledge?id=${t.knowledge_id}`} className="flex items-center justify-between gap-2 py-2 hover:underline">
                      <span className="min-w-0"><span className="block truncate text-[12px] font-semibold">{t.title}</span>
                        <span className="os-faint text-[10.5px]">{t.domain_name} · {pretty(t.knowledge_type)}</span></span>
                      <Tag tone="green">{t.runs} runs</Tag>
                    </Link>
                  ))}
                </div>
              </>
            ) : <Empty icon={BookOpen} title="Enterprise Memory unavailable" />}
          </Card>
        </div>
      </div>

      <Card className="mt-4" title="Onboarded domains" subtitle="The estate the agents build on: each domain's model, memory and runs" action={<Btn href="/domains">Domains</Btn>}>
        <SourceError what="Domains" error={domains.error} />
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {estate.map((d) => {
            const dr = live.filter((r) => r.domain_name === d.domain_name);
            return (
              <Link key={d.domain_id} href={`/os/design?domain=${d.domain_id}`} className="os-card block p-4 transition hover:-translate-y-0.5 hover:shadow-[var(--os-shadow-pop)]">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 text-[14px] font-extrabold"><Layers className="h-4 w-4 text-[var(--os-emerald)]" />{d.domain_name}</div>
                  {d.mdm_role && <Tag tone={d.mdm_role === "MASTERED" ? "green" : "blue"}>{pretty(d.mdm_role)}</Tag>}
                </div>
                <p className="os-muted mt-1.5 line-clamp-2 min-h-[34px] text-[11.5px] leading-snug">{d.description}</p>
                <div className="mt-3 grid grid-cols-3 gap-2 text-center">
                  <div><div className="text-[16px] font-extrabold tabular-nums">{d.target_tables}</div><div className="os-faint text-[10px]">tables</div></div>
                  <div><div className="text-[16px] font-extrabold tabular-nums">{d.knowledge_items}</div><div className="os-faint text-[10px]">memory</div></div>
                  <div><div className="text-[16px] font-extrabold tabular-nums">{dr.length}</div><div className="os-faint text-[10px]">runs</div></div>
                </div>
                {dr.some((r) => r.current_state === "FAILED" || r.current_state === "VALIDATION_FAILED") &&
                  <div className="mt-2 flex items-center gap-1 text-[11px] text-[var(--os-red-ink)]"><TriangleAlert className="h-3 w-3" /> has failed runs</div>}
              </Link>
            );
          })}
        </div>
      </Card>
    </>
  );
}
