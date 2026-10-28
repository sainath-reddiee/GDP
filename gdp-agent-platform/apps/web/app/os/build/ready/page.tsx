import Link from "next/link";
import { ArrowRight, Check, CircleDashed, FastForward, Hammer, Hourglass, ShieldAlert, ShieldCheck, X } from "lucide-react";
import { loadReadiness, type BuildRun, type BuildStatus } from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import { actMode, canSee } from "@/lib/os/access";
import { Banner, Bar, Btn, Card, Empty, Kpi, PageHeader, SourceError, Tag, ago, type Tone } from "@/components/os/ui";
import { BuildAllReady, BuildEndToEnd } from "@/components/os/build-run";
import { DeleteRunButton } from "@/components/os/request-ui";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const metadata = { title: "Ready to build" };

const STATUS: Record<BuildStatus, { tone: Tone; label: string }> = {
  READY: { tone: "green", label: "Ready to build" }, FLAGGED: { tone: "red", label: "Validation flagged" },
  BUILDING: { tone: "blue", label: "Building" }, REVIEW: { tone: "amber", label: "In review" },
  MODEL: { tone: "amber", label: "Model not approved" }, DESIGN: { tone: "neutral", label: "In design" },
  ENABLE: { tone: "neutral", label: "Being onboarded" }, ATTENTION: { tone: "red", label: "Failed" }, BUILT: { tone: "green", label: "Built" },
};

/** Where the next step of a run that is not ready happens, by who acts. */
function elsewhere(r: BuildRun): { href: string; label: string } | null {
  if (r.status === "ENABLE") return { href: "/os/enable", label: "Initiate" };
  if (r.status === "DESIGN" || r.status === "MODEL") return { href: `/os/review?run=${r.run_id}`, label: "Design gate" };
  if (r.status === "FLAGGED" || r.status === "REVIEW") return { href: `/os/review?run=${r.run_id}`, label: "Validate" };
  if (r.status === "BUILT") return { href: `/os/release?run=${r.run_id}`, label: "Publish" };
  return null;
}

function Check3({ on, label }: { on: boolean; label: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1 text-[10.5px] font-semibold", on ? "text-[var(--os-green-ink)]" : "os-faint")}>
      {on ? <Check className="h-3 w-3" /> : <CircleDashed className="h-3 w-3" />}{label}
    </span>
  );
}

/** Build, Ready to build: the Forward Engineering Lead's landing. Initiate asks and onboards, Design models and maps;
 *  here every run whose design is done (domain model, mapping and transformations approved) is built end to end by
 *  autopilot up to the code review gate. Runs still on their way show where they are and who acts next. */
export default async function ReadyToBuild({ searchParams }: { searchParams: { built?: string } }) {
  const access = await guard("ready");
  const persona = access.persona;
  const readiness = await loadReadiness();
  const data = readiness.data;
  const t = data?.totals;
  const mode = actMode(access, "RUN.OPERATE");
  const openRun = canSee(access, "build");
  const showBuilt = searchParams.built === "1";
  const waitingDesign = t ? t.DESIGN + t.MODEL + t.ENABLE : 0;

  const flow: { label: string; n: number | undefined; href: string; who: string }[] = [
    { label: "Initiate", n: t?.ENABLE, href: "/os/intake", who: "Request & source setup" },
    { label: "Design", n: t ? t.DESIGN + t.MODEL : undefined, href: "/os/design", who: "Designer models and maps" },
    { label: "Build", n: t ? t.READY + t.BUILDING + t.FLAGGED : undefined, href: "/os/build/ready", who: "Autopilot builds" },
    { label: "Validate", n: t?.REVIEW, href: "/os/review", who: "Reviewer & gate checks" },
    { label: "Publish", n: t?.BUILT, href: "/os/release", who: "PR, QA & release" },
  ];

  return (
    <>
      <PageHeader eyebrow={`${persona.label} / Build`} title="Ready to build"
                  subtitle="Runs whose design is done, domain by domain: the domain model, field mapping and transformations are approved. Build end to end generates the code and its checks and validates it with the review and standards agents, then stops at code review." />
      <SourceError what="Readiness" error={readiness.error} />

      <ol className="os-card mb-4 grid grid-cols-2 gap-2 p-3 sm:grid-cols-5" aria-label="Flow">
        {flow.map((f, i) => (
          <li key={f.label}>
            <Link href={f.href} className={cn("os-hover flex h-full flex-col rounded-lg border px-3 py-2", f.label === "Build" ? "border-[var(--os-forest)] bg-[var(--os-green-bg)]" : "border-[var(--os-line)]")}>
              <span className="os-faint text-[10.5px] font-bold">{String(i + 1).padStart(2, "0")}{f.n !== undefined ? ` · ${f.n} run${f.n === 1 ? "" : "s"}` : ""}</span>
              <span className="text-[13px] font-extrabold">{f.label}</span>
              <span className="os-faint text-[10.5px]">{f.who}</span>
            </Link>
          </li>
        ))}
      </ol>

      {t && t.READY > 0
        ? <Banner icon={FastForward}><b>{t.READY} run{t.READY === 1 ? " is" : "s are"} ready to build end to end.</b> Autopilot runs every step and decides the gates on the evidence; review or reopen its decisions from the stores.</Banner>
        : <Banner tone="amber" icon={Hourglass}>Nothing is ready to build yet: {waitingDesign} run{waitingDesign === 1 ? " is" : "s are"} still with the administrator or in design. Each one below says who acts next.</Banner>}

      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-5">
        <Kpi label="Ready to build" value={t?.READY ?? 0} hint="design done" icon={FastForward} tone="green" />
        <Kpi label="Building" value={t?.BUILDING ?? 0} hint="generating or validating" icon={Hammer} tone="blue" />
        <Kpi label="Flagged" value={t?.FLAGGED ?? 0} hint="below the validation gate" icon={ShieldAlert} tone={t?.FLAGGED ? "red" : "green"} href="/os/review" />
        <Kpi label="In review" value={t?.REVIEW ?? 0} hint="QA and code review" icon={ShieldCheck} tone="amber" href="/os/review" />
        <Kpi label="Waiting on design" value={waitingDesign} hint="administrator or designer" icon={Hourglass} tone="neutral" href="/os/design" />
      </div>

      {data && data.domains.length ? (
        <div className="flex flex-col gap-4" data-testid="ready-board">
          {data.domains.map((d) => {
            const rows = d.runs.filter((r) => showBuilt || r.status !== "BUILT");
            const ready = d.runs.filter((r) => r.status === "READY").map((r) => r.run_id);
            return (
              <Card key={d.domain_id ?? d.domain_name} pad={false}
                    title={<span className="flex flex-wrap items-center gap-2">{d.domain_name}
                      {d.model_approved ? <Tag tone="green">Model v{d.approved_version} approved</Tag> : d.domain_id ? <Tag tone="amber">Model not approved</Tag> : <Tag>{d.domain_name === "Inactive domain" ? "Retired or gone" : "No domain"}</Tag>}</span>}
                    subtitle={`${d.runs.length} run${d.runs.length === 1 ? "" : "s"} · design ${d.percent}% done${d.counts.BUILT ? ` · ${d.counts.BUILT} built` : ""}`}
                    action={ready.length > 0 && mode === "do" ? <BuildAllReady runIds={ready} previewing={!!access.preview} /> : undefined}>
                <div className="px-5 pt-1"><Bar value={d.percent} tone={d.percent === 100 ? "green" : "amber"} className="h-1.5" /></div>
                {rows.length ? (
                  <div className="os-divide mt-2">
                    {rows.map((r) => {
                      const st = STATUS[r.status];
                      const next = elsewhere(r);
                      return (
                        <div key={r.run_id} className="grid gap-2 px-5 py-3 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto]" data-testid="ready-row">
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              {openRun ? <Link href={`/os/build?run=${r.run_id}`} className="truncate text-[13px] font-extrabold hover:underline">{r.target_model ?? r.run_name}</Link>
                                : <span className="truncate text-[13px] font-extrabold">{r.target_model ?? r.run_name}</span>}
                              <Tag tone={st.tone}>{st.label}</Tag>
                              {r.score !== null && <Tag tone={r.status === "FLAGGED" ? "red" : "green"}>{Math.round(r.score)}%{r.attempts > 1 ? ` · ${r.attempts} attempts` : ""}</Tag>}
                            </div>
                            <div className="os-faint mt-0.5 truncate text-[11px]">
                              {r.parent_name ? `${r.parent_name} · ` : r.target_model ? `${r.run_name} · ` : ""}{r.source_system_name ?? "no source"}{r.updated_at ? ` · ${ago(r.updated_at)}` : ""}
                            </div>
                            {r.request && <div className="mt-0.5 text-[11px]">For <Link href={`/os/requests/${r.request.request_id}`} className="font-semibold underline">{r.request.title}</Link></div>}
                          </div>
                          <div className="min-w-0">
                            <div className="flex flex-wrap gap-x-3 gap-y-1">
                              <Check3 on={r.design.model} label="Model" /><Check3 on={r.design.mapping} label="Mapping" />
                              <Check3 on={r.design.sttm} label="Transformations" /><Check3 on={r.design.dq} label="DQ checks" />
                            </div>
                            <Bar value={r.percent} tone={r.percent === 100 ? "green" : "amber"} className="mt-1.5 h-1" />
                            {r.waiting && <div className="os-muted mt-1 text-[11px]">{r.waiting}</div>}
                          </div>
                          <div className="flex items-start justify-end gap-1.5">
                            {(r.status === "READY" || r.status === "BUILDING") && mode !== "none"
                              ? <BuildEndToEnd key={`${r.run_id}-${r.status}`} runId={r.run_id} previewing={!!access.preview} disabled={r.status === "BUILDING" || mode !== "do"}
                                               label={mode === "do" ? "Build end to end" : "Needs RUN.OPERATE"} />
                              : next ? <Btn href={next.href}>{next.label} <ArrowRight className="h-3 w-3" /></Btn> : null}
                            <DeleteRunButton runIds={[r.run_id]} iconOnly previewing={!!access.preview} />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : <div className="p-5"><Empty icon={Check} title="Every run of this domain is built">{d.counts.BUILT} built run{d.counts.BUILT === 1 ? "" : "s"} hidden.</Empty></div>}
              </Card>
            );
          })}
          <div className="text-center text-[11.5px]">
            {showBuilt ? <Link href="/os/build/ready" className="underline">Hide built runs</Link> : <Link href="/os/build/ready?built=1" className="underline">Show built runs too</Link>}
          </div>
        </div>
      ) : data ? <Card><Empty icon={X} title="No Silver run yet">Runs appear here once the administrator onboards a source into a domain.</Empty></Card> : null}
    </>
  );
}
