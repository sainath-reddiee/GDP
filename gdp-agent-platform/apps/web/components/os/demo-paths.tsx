import Link from "next/link";
import { ArrowRight, CheckCircle2, CircleDashed, PlayCircle } from "lucide-react";
import { stateLabel } from "@/lib/run-labels";
import type { RunSummary } from "@/lib/types";
import { PHASE, phaseOf, type OnboardingRequest } from "@/lib/os/requests";
import { leafRuns } from "@/lib/os/work";
import { Card, Tag } from "./ui";
import { cn } from "@/lib/utils";

// The two demonstrations on live records: each step names the persona who acts and opens the real run or request it
// uses. Nothing is staged: scores are whatever the agents find, and a step that has not happened yet says so.

type Step = { who: string; title: string; what: string; href: string | null; state: "done" | "now" | "next" };

/** A run that has passed validation and waits on (or finished) code review: the Forward Engineering Lead's happy path. */
export function happyRun(runs: RunSummary[]): RunSummary | null {
  const rank = ["DBT_REVIEW", "VALIDATION_PASSED", "DBT_APPROVED", "COMPLETED"];
  return [...leafRuns(runs)].filter((r) => !r.is_archived && rank.includes(r.current_state))
    .sort((a, b) => rank.indexOf(a.current_state) - rank.indexOf(b.current_state) || (b.updated_at ?? "").localeCompare(a.updated_at ?? ""))[0] ?? null;
}

/** An open onboarding request to walk end to end: the one furthest along that has a run, else the oldest open one. */
export function onboardingRequest(requests: OnboardingRequest[]): OnboardingRequest | null {
  const order = ["DOMAIN", "ENABLE", "DESIGN", "BUILD", "REVIEW"];
  const withRun = requests.filter((r) => r.items.some((i) => (i.active_run_id || i.run_id) && !i.removed_at));
  const pool = withRun.length ? withRun : requests;
  return [...pool].sort((a, b) => order.indexOf(b.stage) - order.indexOf(a.stage) || a.created_at.localeCompare(b.created_at))[0] ?? null;
}

function Steps({ steps }: { steps: Step[] }) {
  return (
    <ol className="flex flex-col gap-2">
      {steps.map((s, i) => {
        const body = (
          <>
            <span className={cn("grid h-6 w-6 shrink-0 place-items-center rounded-full text-[11px] font-extrabold",
              s.state === "done" ? "bg-[var(--os-green-bg)] text-[var(--os-green-ink)]" : s.state === "now" ? "bg-[var(--os-forest)] text-white" : "bg-[var(--os-neutral-bg)] os-faint")}>
              {s.state === "done" ? <CheckCircle2 className="h-3.5 w-3.5" /> : i + 1}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[12.5px] font-bold">{s.title}{s.state === "now" && <Tag tone="green" className="ml-2">Now</Tag>}</span>
              <span className="os-faint block text-[11px]">{s.who} · {s.what}</span>
            </span>
            {s.href ? <ArrowRight className="os-faint h-4 w-4 shrink-0" /> : <CircleDashed className="os-faint h-4 w-4 shrink-0" />}
          </>
        );
        return (
          <li key={s.title}>
            {s.href ? <Link href={s.href} className="os-hover flex items-center gap-2.5 rounded-lg px-2 py-1.5">{body}</Link>
              : <div className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 opacity-70">{body}</div>}
          </li>
        );
      })}
    </ol>
  );
}

export function DemoPaths({ runs, requests }: { runs: RunSummary[]; requests: OnboardingRequest[] }) {
  const run = happyRun(runs);
  const happy: Step[] = run ? [
    { who: "Forward Engineering Lead", title: "Store: Lock Models & Start Build", what: "pick staged source tables, lock domain target models, start autonomous build", href: "/os/design/sources", state: "done" },
    { who: "Forward Engineering Lead", title: "Build workbench", what: `${run.domain_name ?? "run"} · ${run.run_name}: generated dbt, DAG, Soda tests`, href: `/os/build?run=${run.run_id}`, state: "done" },
    { who: "Review and standards agents", title: "Validation gate", what: "the % score against the gate, each standards area, every attempt", href: `/os/review?run=${run.run_id}`, state: run.current_state === "VALIDATION_PASSED" ? "now" : "done" },
    { who: "Data Developer", title: "PR and Developer handoff", what: `publish the pull request, then review the code (${stateLabel(run.current_state).toLowerCase()})`, href: `/os/release?run=${run.run_id}`,
      state: ["DBT_APPROVED", "COMPLETED"].includes(run.current_state) ? "done" : "now" },
  ] : [];

  const req = onboardingRequest(requests);
  const item = req?.items.find((i) => (i.active_run_id || i.run_id) && !i.removed_at);
  const runId = item?.active_run_id ?? item?.run_id ?? run?.run_id ?? null;
  const at = req ? ["DOMAIN", "ENABLE", "DESIGN", "BUILD", "REVIEW", "DONE"].indexOf(req.stage) : -1;
  const flagged = req?.phase === "FLAGGED";
  const state = (i: number): Step["state"] => (!req ? "next" : at > i ? "done" : at === i ? "now" : "next");
  const onboarding: Step[] = [
    { who: "GDP Product Owner", title: "File the request", what: req ? `${req.title} · ${req.domain_name ?? req.proposed_domain ?? "no domain"}` : "Intake: a domain and the sources it needs",
      href: req ? `/os/requests/${req.request_id}` : "/os/intake/new", state: req ? "done" : "now" },
    { who: "Data OS Administrator", title: "Onboard the sources", what: "Admin readiness: skipped on its own when the sources are already onboarded", href: "/os/enable", state: state(1) },
    { who: "Data Designer", title: "Profile, model and map", what: "approved design and AI field mappings kept in the stores", href: runId ? `/os/design?view=selected&run=${runId}` : "/os/design", state: state(2) },
    { who: "Forward Engineering Lead", title: "Build end to end", what: "from Ready to build: code generated and scored by the review and standards agents", href: runId ? `/os/build?view=selected&run=${runId}` : "/os/build/ready", state: flagged ? "done" : state(3) },
    { who: "Skill owner", title: "If flagged: improve the skill, validate again", what: "if validation fails (e.g. 68%) → update skill → Resume from checkpoint → validation passes (≥ 93%)",
      href: runId ? `/os/review?run=${runId}` : "/os/skills?skill=CODE-STANDARDS-REVIEW", state: flagged ? "now" : at > 3 ? "done" : "next" },
    { who: "QA & UAT Lead, Data Developer", title: "Pull request, QA and release", what: "publish PR, sign off QA, review the code", href: runId ? `/os/release?run=${runId}` : "/os/release", state: state(4) },
  ];

  return (
    <Card className="mt-4" title="Demo paths" subtitle="Two walkthroughs on live records. Each step opens the real run or request; scores are whatever the agents find." pad={false}>
      <div className="grid gap-0 lg:grid-cols-2" data-testid="demo-paths">
        <div className="border-b border-[var(--os-line)] p-4 lg:border-b-0 lg:border-r">
          <div className="mb-1 flex items-center gap-2 text-[13px] font-extrabold"><PlayCircle className="h-4 w-4 text-[var(--os-emerald)]" />1 · Forward Engineering Lead, happy path</div>
          <p className="os-faint mb-2 px-2 text-[11px]">Store → Forward Engineering Lead → Build Workbench → Generated dbt/DAG/tests → % validation → PR and Developer handoff.</p>
          {run ? <Steps steps={happy} /> : <p className="os-faint px-2 text-[11.5px]">No run has passed validation yet. Once one does, this path walks it from Home to the pull request.</p>}
          <div className="mt-3 flex flex-wrap gap-2 px-2">
            <Link href={run ? `/os/build?view=selected&run=${run.run_id}` : "/os/build"} className="os-btn os-btn-primary text-[11.5px]">View Build Workbench <ArrowRight className="h-3.5 w-3.5" /></Link>
            <Link href="/os/design/sources" className="os-btn text-[11.5px]">Open Store</Link>
          </div>
        </div>
        <div className="p-4">
          <div className="mb-1 flex items-center gap-2 text-[13px] font-extrabold"><PlayCircle className="h-4 w-4 text-[var(--os-emerald)]" />2 · Onboarding with people in the loop</div>
          <p className="os-faint mb-2 px-2 text-[11px]">Product request → Admin readiness → Designer → Build → if Validation fails at 68% → Update skill → Resume from checkpoint → Validation passes at 93% → PR.</p>
          {req && <div className="os-faint mb-2 px-2 text-[11px]">Request now: <b>{phaseOf(req).label}</b>{req.waiting_label ? ` · ${req.waiting_label}` : ""}</div>}
          {!req && <div className="os-faint mb-2 px-2 text-[11px]">No open request: file one on Intake to start. Phases: {[PHASE.ENABLE, PHASE.ONBOARDED, PHASE.DESIGN, PHASE.STORED, PHASE.BUILD].map((p) => p.label).join(", ")}.</div>}
          <Steps steps={onboarding} />
          <div className="mt-3 flex flex-wrap gap-2 px-2">
            <Link href={runId ? `/os/review?run=${runId}` : "/os/review"} className="os-btn os-btn-primary text-[11.5px]">View Validation Gate <ArrowRight className="h-3.5 w-3.5" /></Link>
            <Link href={runId ? `/os/skills?skill=CODE-STANDARDS-REVIEW&run=${runId}` : "/os/skills?skill=CODE-STANDARDS-REVIEW"} className="os-btn text-[11.5px]">View Skill Studio</Link>
          </div>
        </div>
      </div>
    </Card>
  );
}
