import Link from "next/link";
import { ArrowRight, Lock } from "lucide-react";
import { LIFECYCLE } from "@/lib/os/lifecycle";
import { PERSONAS } from "@/lib/os/personas";
import { SCREENS, canSee, type ScreenKey } from "@/lib/os/access";
import { osAccess } from "@/lib/os/session";
import { Card, PageHeader, Tag } from "@/components/os/ui";
import { DemoPaths } from "@/components/os/demo-paths";
import { loadRequests, loadRuns } from "@/lib/os/data";

export const dynamic = "force-dynamic";
export const metadata = { title: "How it works" };

const TOUR: { key: ScreenKey; href: string; title: string; what: string }[] = [
  { key: "work", href: "/os", title: "My Work", what: "Decisions waiting on a person, autonomous runs, knowledge pulse and the onboarded domains." },
  { key: "intake", href: "/os/intake", title: "Intake", what: "Link a domain and its sources; see what is reused and where the request routes." },
  { key: "enable", href: "/os/enable", title: "Enable", what: "Source readiness and the config-driven ingestion framework." },
  { key: "design", href: "/os/design", title: "Design", what: "The domain model, mapping proposals with confidence, open questions and the contract." },
  { key: "ready", href: "/os/build/ready", title: "Build", what: "Ready to build: every run whose design is done, built end to end by autopilot up to code review; the Workbench shows one run; Run history lists every run." },
  { key: "review", href: "/os/review", title: "Review", what: "The validation gate (review and code standards agents, minimum score) and every human gate across domains." },
  { key: "skills", href: "/os/skills", title: "Skills", what: "Versioned skills, real version diffs, tests and controlled recovery." },
  { key: "release", href: "/os/release", title: "Release", what: "Pull request, promotion stages, QA and UAT signoff and what the run taught memory." },
  { key: "knowledge", href: "/os/knowledge", title: "Knowledge", what: "Enterprise Memory with provenance, reuse by runs and the curation lifecycle." },
  { key: "run", href: "/os/run", title: "Run & DQ", what: "Pipeline health, live data quality, incidents with RCA and operational learning." },
];

/** How the Agentic Data OS experience is organised: the lifecycle, the personas, a walk through the screens and the
 *  access list that decides who sees what (lib/os/access), marked for the person reading it. */
export default async function Help() {
  const access = await osAccess();
  const [runs, requests] = await Promise.all([loadRuns("sort=updated&limit=100"), loadRequests("all", "OPEN")]);
  const mine = new Set(access.personas.map((p) => p.id));
  return (
    <>
      <PageHeader eyebrow="Agentic Data OS / How it works" title="One workflow, many personas"
                  subtitle="Every screen reads the live platform: the onboarded domains, their runs, validation, skills and Enterprise Memory. Agents work autonomously; people own the gates." />
      <Card className="mb-4" title="The lifecycle" subtitle="Eight steps, mapped from each run's real workflow state">
        <ol className="grid gap-2 sm:grid-cols-4 xl:grid-cols-8">
          {LIFECYCLE.map((s, i) => (
            <li key={s.key} className="rounded-xl border border-[var(--os-line)] p-3">
              <div className="os-faint text-[10.5px] font-bold">{String(i + 1).padStart(2, "0")} · {s.actor.toUpperCase()}</div>
              <div className="text-[13.5px] font-extrabold">{s.label}</div>
              <div className="os-muted mt-1 text-[11px]">{s.what}</div>
              <div className="os-faint mt-1 text-[10.5px]">{s.owner}</div>
            </li>
          ))}
        </ol>
      </Card>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Walk the platform" subtitle="The screens in the order work flows" pad={false}>
          <div className="os-divide">
            {TOUR.map((t, i) => {
              const inner = (
                <>
                  <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-[var(--os-green-bg)] text-[11px] font-extrabold text-[var(--os-green-ink)]">{i + 1}</span>
                  <span className="min-w-0 flex-1"><span className="block text-[13px] font-bold">{t.title}</span><span className="os-faint block text-[11.5px]">{t.what}</span></span>
                </>
              );
              return canSee(access, t.key)
                ? <Link key={t.href} href={t.href} className="flex items-center gap-3 px-5 py-3 os-hover">{inner}<ArrowRight className="os-faint h-4 w-4" /></Link>
                : <div key={t.href} className="flex items-center gap-3 px-5 py-3 opacity-55" title="Not part of your role">{inner}<Lock className="os-faint h-4 w-4" /></div>;
            })}
          </div>
        </Card>
        <Card title="Personas" subtitle="A super admin gives you personas; each grants a bundle of roles. The one you view as changes what comes first, never what you may do." pad={false}>
          <div className="os-divide">
            {PERSONAS.map((p) => (
              <div key={p.id} className="flex items-center justify-between gap-3 px-5 py-3">
                <span className="min-w-0"><span className="block text-[13px] font-bold">{p.label}</span>
                  <span className="os-faint block text-[11.5px]">{p.responsibility}</span>
                  <span className="os-faint block text-[10.5px]">{p.id === "viewer" ? "Anyone without a persona" : p.id === "super" ? "Super admins only" : "Given by a super admin in Admin, People"}</span></span>
                {mine.has(p.id) ? <Tag tone="green">Yours</Tag> : <Tag>Not your role</Tag>}
              </div>
            ))}
          </div>
        </Card>
      </div>

      <DemoPaths runs={runs.data.runs} requests={requests.data.requests} />

      <Card className="mt-4" title="Access list" subtitle="Who each screen is open to. Actions inside a screen need their own privilege; without it, people who may raise change requests send the action to the approver role." pad={false}>
        <table className="os-table">
          <thead><tr><th>Screen</th><th>Open to</th><th>Privileges (any of)</th><th>You</th></tr></thead>
          <tbody>
            {SCREENS.map((s) => (
              <tr key={s.key}>
                <td className="font-bold">{s.label}</td>
                <td className="os-muted">{s.why}</td>
                <td className="font-mono text-[11px]">{s.view?.join(", ") ?? "any signed-in person"}</td>
                <td>{canSee(access, s.key) ? <Tag tone="green">Open</Tag> : <Tag>Hidden</Tag>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  );
}
