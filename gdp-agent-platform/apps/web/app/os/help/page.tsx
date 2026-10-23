import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { LIFECYCLE } from "@/lib/os/lifecycle";
import { PERSONAS } from "@/lib/os/personas";
import { Card, PageHeader, Tag } from "@/components/os/ui";

export const metadata = { title: "How it works" };

const TOUR = [
  { href: "/os", title: "My Work", what: "Decisions waiting on a person, autonomous runs, knowledge pulse and the onboarded domains." },
  { href: "/os/intake", title: "Intake", what: "Link a domain and its sources; see what is reused and where the request routes." },
  { href: "/os/enable", title: "Enable", what: "Source readiness and the config-driven ingestion framework." },
  { href: "/os/design", title: "Design", what: "The domain model, mapping proposals with confidence, open questions and the contract." },
  { href: "/os/build", title: "Build", what: "One run end to end: lifecycle, agents, generated artifacts, pinned skills and gates." },
  { href: "/os/review", title: "Review", what: "The independent validation gate and every human gate across domains." },
  { href: "/os/skills", title: "Skills", what: "Versioned skills, real version diffs, tests and controlled recovery." },
  { href: "/os/release", title: "Release", what: "Pull request, promotion stages, QA and UAT signoff and what the run taught memory." },
  { href: "/os/knowledge", title: "Knowledge", what: "Enterprise Memory with provenance, reuse by runs and the curation lifecycle." },
  { href: "/os/run", title: "Run & DQ", what: "Pipeline health, live data quality, incidents with RCA and operational learning." },
];

/** How the Agentic Data OS experience is organised: the lifecycle, the personas and a walk through the screens. */
export default function Help() {
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
            {TOUR.map((t, i) => (
              <Link key={t.href} href={t.href} className="flex items-center gap-3 px-5 py-3 os-hover">
                <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-[var(--os-green-bg)] text-[11px] font-extrabold text-[var(--os-green-ink)]">{i + 1}</span>
                <span className="min-w-0 flex-1"><span className="block text-[13px] font-bold">{t.title}</span><span className="os-faint block text-[11.5px]">{t.what}</span></span>
                <ArrowRight className="os-faint h-4 w-4" />
              </Link>
            ))}
          </div>
        </Card>
        <Card title="Personas" subtitle="“View as” changes what comes first, never what you may do: privileges still come from your governance role." pad={false}>
          <div className="os-divide">
            {PERSONAS.map((p) => (
              <div key={p.id} className="flex items-center justify-between gap-3 px-5 py-3">
                <span className="min-w-0"><span className="block text-[13px] font-bold">{p.label}</span><span className="os-faint block text-[11.5px]">{p.responsibility}</span></span>
                <Link href={p.home}><Tag tone="green">{p.home.replace("/os/", "").replace("/os", "my work")}</Tag></Link>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
