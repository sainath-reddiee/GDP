import Link from "next/link";
import type { ReactNode } from "react";
import {
  AlertTriangle, ArrowRight, Bot, BookOpen, CircleHelp, Gauge, GitPullRequest, LayoutDashboard, PlugZap, ShieldCheck, Siren, Ticket,
  UserCheck, type LucideIcon,
} from "lucide-react";
import { whoami } from "@/lib/api";
import { loadMyWork } from "@/lib/my-work";
import { ago, runHref } from "@/lib/run-insights";
import { stateLabel } from "@/lib/run-labels";
import { stageLabel, pipelineOf } from "@/lib/stages";
import { STAGE_ORDER, stageInfo } from "@/lib/run-status";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export const metadata = { title: "My Work" };

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** A section of the page: a title with its count and tone (human approval, agent work, a fix, knowledge), a link to
 *  the full list, and rows; `empty` when there is nothing. */
function Section({ id, title, icon: Icon, tone, count, href, hrefLabel, empty, children }: {
  id: string; title: string; icon: LucideIcon; tone: "human" | "agent" | "fix" | "knowledge" | "plain"; count?: number;
  href?: string; hrefLabel?: string; empty: ReactNode; children?: ReactNode;
}) {
  const toneCls = { human: "bg-human/10 text-human", agent: "bg-agent/10 text-agent", fix: "bg-rose-500/10 text-rose-600",
    knowledge: "bg-violet-500/10 text-violet-600", plain: "bg-muted text-muted-foreground" }[tone];
  return (
    <section id={id} className="surface scroll-mt-24 overflow-hidden" data-testid={`work-${id}`}>
      <header className="flex items-center gap-2.5 border-b px-5 py-3.5">
        <span className={cn("grid h-8 w-8 place-items-center rounded-lg", toneCls)}><Icon className="h-4 w-4" /></span>
        <h2 className="text-sm font-semibold">{title}</h2>
        {count != null && <span className="rounded-full bg-muted px-2 py-0.5 text-xs tabular-nums text-muted-foreground">{count}</span>}
        {href && <Link href={href} className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">{hrefLabel ?? "Open"} <ArrowRight className="h-3 w-3" /></Link>}
      </header>
      {children || <div className="px-5 py-6 text-sm text-muted-foreground">{empty}</div>}
    </section>
  );
}

function Row({ href, children, action }: { href: string; children: ReactNode; action?: ReactNode }) {
  return (
    <li className="group flex items-center gap-3 px-5 py-3 transition hover:bg-muted/40">
      <Link href={href} className="min-w-0 flex-1">{children}</Link>
      {action}
    </li>
  );
}

function Tile({ href, label, value, hint, tone }: { href: string; label: string; value: number; hint: string; tone: "human" | "agent" | "fix" | "knowledge" }) {
  const ring = { human: "before:bg-human", agent: "before:bg-agent", fix: "before:bg-rose-500", knowledge: "before:bg-violet-500" }[tone];
  const text = { human: "text-human", agent: "text-agent", fix: "text-rose-600", knowledge: "text-violet-600" }[tone];
  return (
    <a href={href} className={cn("surface card-lift relative overflow-hidden p-4 before:absolute before:inset-y-0 before:left-0 before:w-1", ring)}>
      <p className="text-xs font-medium uppercase tracking-[0.1em] text-muted-foreground">{label}</p>
      <p className={cn("mt-1 text-[32px] font-semibold leading-none tabular-nums", value ? text : "text-foreground/40")}>{value}</p>
      <p className="mt-1.5 text-xs text-muted-foreground">{hint}</p>
    </a>
  );
}

/** My Work: what the signed-in person must do now, in the order a developer works through it. Gates they can approve
 *  and requests waiting on them first; then their runs the agent is working on; what broke; then the questions,
 *  proposals, tickets, incidents and pull requests that are theirs. The Command Center stays the estate-wide view. */
export default async function MyWorkPage() {
  const me = await whoami();
  const w = await loadMyWork(me);
  const user = (me?.user ?? "").split(/[._@ ]/)[0];
  const name = user ? user.charAt(0) + user.slice(1).toLowerCase() : "";
  const approvals = w.gates.length + w.requests.length + w.fixApprovals.length;
  const knowledge = w.questions.length + w.inbox.length;
  const summary = approvals
    ? `${plural(approvals, "decision")} ${approvals === 1 ? "waits" : "wait"} on you.`
    : "Nothing waits on your approval.";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-4">
        <div className="min-w-[min(100%,24rem)] flex-1">
          <p className="eyebrow">My Work</p>
          <h1 className="mt-1">{greeting()}{name ? `, ${name}` : ""}</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            {summary} {w.running.length ? `The agent is working on ${plural(w.running.length, "run")} of yours.` : ""}
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs">
            {w.approves.length ? (
              <>
                <span className="text-muted-foreground">You approve:</span>
                {w.approves.map((g) => (
                  <span key={g.privilege} className="inline-flex items-center gap-1 rounded-full bg-human/10 px-2 py-0.5 font-medium text-human ring-1 ring-inset ring-human/25">
                    <UserCheck className="h-3 w-3" />{g.name}
                  </span>
                ))}
              </>
            ) : <span className="text-muted-foreground">You don&apos;t approve gates; your changes go to an approver as requests.</span>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link href="/dashboard" className={buttonVariants({ variant: "outline" })}><LayoutDashboard className="h-4 w-4" />Command Center</Link>
          <Link href="/dashboard/onboarding?type=source" className={buttonVariants()}><PlugZap className="h-4 w-4" />Onboard source</Link>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" data-testid="work-tiles">
        <Tile href="#approve" label="Awaiting your approval" value={approvals} tone="human"
              hint={`${plural(w.gates.length, "gate")} · ${plural(w.requests.length, "change request")}${w.fixApprovals.length ? ` · ${w.fixApprovals.length} fix${w.fixApprovals.length === 1 ? "" : "es"}` : ""}`} />
        <Tile href="#runs" label="Your runs in flight" value={w.running.length} tone="agent"
              hint={`${w.inFlightTotal} in flight across the platform`} />
        <Tile href="#fix" label="Needs a fix" value={w.fix.length} tone="fix" hint="your failed runs, last 14 days" />
        <Tile href="#knowledge" label="Questions and proposals" value={knowledge} tone="knowledge"
              hint={`${plural(w.questions.length, "question")} · ${plural(w.inbox.length, "proposal")}`} />
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-6">
          <Section id="approve" title="Awaiting your approval" icon={UserCheck} tone="human" count={approvals} href="/approvals" hrefLabel="All approvals"
                   empty={<span className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-success" />Nothing waits on you. Gates you can approve appear here as soon as the agent reaches them.</span>}>
            {approvals > 0 && (
              <ul className="divide-y">
                {w.gates.map(({ run, gate }) => (
                  <Row key={run.run_id} href={`/runs/${run.run_id}/${gate.slug}`}
                       action={<Link href={`/runs/${run.run_id}/${gate.slug}`} className="inline-flex items-center gap-1 rounded-lg bg-human px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-human/90">Review <ArrowRight className="h-3 w-3" /></Link>}>
                    <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                      <span className="rounded-full bg-human/10 px-2 py-0.5 text-[11px] font-semibold text-human">{gate.name}</span>
                      <span className="truncate group-hover:text-primary">{run.run_name}</span>
                    </p>
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">
                      {run.domain_name ?? "No domain"}{run.source_system_name ? ` · ${run.source_system_name}` : ""} · waiting since {ago(run.updated_at ?? run.created_at)} · as {gate.role}
                    </p>
                  </Row>
                ))}
                {w.fixApprovals.map((a) => (
                  <Row key={a.approval_id} href={`/ops/resolutions/${encodeURIComponent(a.failure_id)}`}
                       action={<Link href={`/ops/resolutions/${encodeURIComponent(a.failure_id)}`} className="inline-flex items-center gap-1 rounded-lg bg-human px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-human/90">Decide <ArrowRight className="h-3 w-3" /></Link>}>
                    <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                      <span className="rounded-full bg-human/10 px-2 py-0.5 text-[11px] font-semibold text-human">Known fix</span>
                      <span className="truncate group-hover:text-primary">{a.issue_title ?? a.fix_name} on {a.dag_id} · {a.task_id}</span>
                    </p>
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">Airflow failure · waiting since {ago(a.requested_at)} · {a.approver ? `routed to ${a.approver}` : "any fix approver"}</p>
                  </Row>
                ))}
                {w.requests.map((r) => (
                  <Row key={r.request_id} href="/approvals"
                       action={<Link href="/approvals" className="inline-flex items-center gap-1 rounded-lg border border-human/40 px-3 py-1.5 text-xs font-semibold text-human transition hover:bg-human/10">Decide <ArrowRight className="h-3 w-3" /></Link>}>
                    <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                      <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-semibold text-muted-foreground">Change request</span>
                      <span className="truncate group-hover:text-primary">{r.title ?? r.summary ?? r.path}</span>
                    </p>
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">from {r.requested_by} · {ago(r.created_at)} · needs {r.privilege}</p>
                  </Row>
                ))}
              </ul>
            )}
          </Section>

          <Section id="runs" title="Your runs the agent is working on" icon={Bot} tone="agent" count={w.running.length} href="/runs?status=running" hrefLabel="All runs"
                   empty={<span>You have no runs in flight. <Link href="/dashboard/onboarding?type=source" className="font-medium text-primary hover:underline">Onboard a source</Link> to start one.</span>}>
            {w.running.length > 0 && (
              <ul className="divide-y">
                {w.running.map((r) => {
                  const info = stageInfo(r);
                  return (
                    <Row key={r.run_id} href={runHref(r)}>
                      <div className="flex items-center gap-3">
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium group-hover:text-primary">{r.run_name}</p>
                          <p className="truncate text-xs text-muted-foreground">
                            {r.current_stage ? stageLabel(r.current_stage === "DOMAIN" ? "PROFILING" : r.current_stage, pipelineOf(r.pipeline)) : "Not started"}
                            {" · "}<span title={r.current_state}>{stateLabel(r.current_state)}</span> · updated {ago(r.updated_at ?? r.created_at)}
                          </p>
                        </div>
                        <span className="hidden items-center gap-0.5 sm:flex" aria-label={`Step ${info.index + 1} of ${STAGE_ORDER.length}`}>
                          {STAGE_ORDER.map((st, i) => (
                            <span key={st} className={cn("h-1.5 w-3 rounded-full", i < info.index ? "bg-emerald-400" : i === info.index ? "bg-agent" : "bg-muted")} />
                          ))}
                        </span>
                      </div>
                    </Row>
                  );
                })}
              </ul>
            )}
          </Section>

          <Section id="fix" title="Needs a fix" icon={AlertTriangle} tone="fix" count={w.fix.length} href="/runs?status=failed" hrefLabel="Failed runs"
                   empty="None of your runs failed in the last 14 days.">
            {w.fix.length > 0 && (
              <ul className="divide-y">
                {w.fix.map(({ run, reason }) => (
                  <Row key={run.run_id} href={`/runs/${run.run_id}`}>
                    <p className="truncate text-sm font-medium group-hover:text-primary">{run.run_name}</p>
                    <p className="line-clamp-2 text-xs text-rose-600 dark:text-rose-400">{reason ?? stateLabel(run.current_state)}</p>
                    <p className="text-[11px] text-muted-foreground">{ago(run.updated_at ?? run.created_at)}</p>
                  </Row>
                ))}
              </ul>
            )}
          </Section>
        </div>

        <div className="min-w-0 space-y-6">
          <Section id="knowledge" title="Questions for you" icon={CircleHelp} tone="knowledge" count={w.questions.length}
                   empty="No open questions in the domains you steward. The engine asks here when it is unsure of a mapping.">
            {w.questions.length > 0 && (
              <ul className="divide-y">
                {w.questions.map((q) => (
                  <Row key={q.question_id} href={`/domains/${encodeURIComponent(q.domain_id)}?tab=playbook`}>
                    <p className="line-clamp-2 text-sm group-hover:text-primary">{q.question}</p>
                    <p className="text-xs text-muted-foreground">{q.domain_name}{q.stage ? ` · ${q.stage.toLowerCase()}` : ""} · {ago(q.created_at)}</p>
                  </Row>
                ))}
              </ul>
            )}
          </Section>

          <Section id="proposals" title="Knowledge proposals to review" icon={BookOpen} tone="knowledge" count={w.inbox.length}
                   href="/knowledge?tab=inbox" hrefLabel="Inbox" empty="No proposals wait for you in the knowledge inbox.">
            {w.inbox.length > 0 && (
              <ul className="divide-y">
                {w.inbox.map((k) => (
                  <Row key={k.knowledge_id} href="/knowledge?tab=inbox">
                    <p className="truncate text-sm group-hover:text-primary">{k.title}</p>
                    <p className="text-xs text-muted-foreground">
                      {k.knowledge_type.replace(/_/g, " ").toLowerCase()}{k.domain_name ? ` · ${k.domain_name}` : ""}{k.origin === "LEARNED" ? " · learned from reviews" : ""} · {ago(k.created_at)}
                    </p>
                  </Row>
                ))}
              </ul>
            )}
          </Section>

          {w.tickets && (
            <Section id="tickets" title="Your tickets" icon={Ticket} tone="plain" count={w.tickets.length} href="/tickets?assignee=me" hrefLabel="Tickets"
                     empty="No open tickets assigned to you.">
              {w.tickets.length > 0 && (
                <ul className="divide-y">
                  {w.tickets.map((t) => (
                    <Row key={t.id} href={`/tickets?tab=ticket&key=${encodeURIComponent(t.key ?? t.id)}`}>
                      <p className="truncate text-sm group-hover:text-primary">{t.key ? <span className="mr-1.5 font-mono text-xs text-muted-foreground">{t.key}</span> : null}{t.title}</p>
                      <p className="text-xs text-muted-foreground">{t.status}{t.priority_name ? ` · ${t.priority_name}` : ""}{t.sla_breached ? " · SLA breached" : ""}</p>
                    </Row>
                  ))}
                </ul>
              )}
            </Section>
          )}

          {w.incidents && (
            <Section id="incidents" title="Incidents assigned to you" icon={Siren} tone="plain" count={w.incidents.length} href="/incidents" hrefLabel="Incidents"
                     empty="No open incidents assigned to you.">
              {w.incidents.length > 0 && (
                <ul className="divide-y">
                  {w.incidents.map((i) => (
                    <Row key={i.incident_id} href={`/incidents/${encodeURIComponent(i.incident_id)}`}>
                      <p className="truncate text-sm group-hover:text-primary"><span className="mr-1.5 font-mono text-xs font-semibold">{i.severity}</span>{i.title}</p>
                      <p className="text-xs text-muted-foreground">{i.dag_id} · {i.status.toLowerCase()} · {ago(i.last_seen)}</p>
                    </Row>
                  ))}
                </ul>
              )}
            </Section>
          )}

          <Section id="prs" title="Your open pull requests" icon={GitPullRequest} tone="plain" count={w.prs.length} href="/dashboard/source-control" hrefLabel="Source control"
                   empty="No open pull requests opened by you.">
            {w.prs.length > 0 && (
              <ul className="divide-y">
                {w.prs.map((p, i) => (
                  <Row key={`${p.pr_url ?? p.href ?? i}`} href={p.href ?? "/dashboard/source-control"}>
                    <p className="truncate text-sm group-hover:text-primary">{p.title ?? p.asset_name ?? p.label}</p>
                    <p className="text-xs text-muted-foreground">{p.kind.replace(/_/g, " ").toLowerCase()} · {p.state.toLowerCase()}{p.opened_at ? ` · ${ago(p.opened_at)}` : ""}</p>
                  </Row>
                ))}
              </ul>
            )}
          </Section>

          <p className="flex items-center gap-2 px-1 text-xs text-muted-foreground"><Gauge className="h-3.5 w-3.5" />
            The estate-wide view is in the <Link href="/dashboard" className="font-medium text-primary hover:underline">Command Center</Link>.</p>
        </div>
      </div>
    </div>
  );
}
