import Link from "next/link";
import { AlertTriangle, ArrowRight, Ban, CalendarClock, Check, CheckCircle2, CircleDashed, Hand, Inbox, Link2, Sparkles, X } from "lucide-react";
import { stateLabel } from "@/lib/run-labels";
import { loadDomains, loadRequest, loadRuns, loadSources } from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import { waitIsMine } from "@/lib/os/work";
import { PRIORITY_TONE, actHref, enhancesSource, intentLabel, overdue, phaseOf, requestSteps, waitCta, waitRole, type RequestEvent, type RequestItem } from "@/lib/os/requests";
import { ActivityRow, Banner, Btn, Card, Empty, Field, PageHeader, SourceError, Tag, ago, pretty, type Tone } from "@/components/os/ui";
import { AddSource, ItemControls, LinkDomain, RequestControls } from "@/components/os/request-ui";
import { RequestFiles } from "@/components/os/request-files";
import { personaForPrivilege } from "@/lib/os/personas";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const metadata = { title: "Onboarding request" };

const ITEM_TONE: Record<string, Tone> = {
  MISSING: "amber", UNREACHABLE: "red", NOT_LANDED: "amber", NO_RUN: "blue", DESIGN: "blue", BUILD: "purple", REVIEW: "amber",
  DONE: "green", ATTENTION: "red", REMOVED: "neutral",
};
const ITEM_LABEL: Record<string, string> = {
  MISSING: "Not registered", UNREACHABLE: "Unreachable", NOT_LANDED: "Not landed", NO_RUN: "Ready, no run", DESIGN: "In design",
  BUILD: "In build", REVIEW: "In review", DONE: "Done", ATTENTION: "Needs attention", REMOVED: "Removed",
};

const EVENT: Record<string, (e: RequestEvent) => string> = {
  SUBMITTED: () => "Filed the request",
  ROUTED: (e) => `Routed to ${pretty(e.to_value)}`,
  STAGE: (e) => `Moved from ${pretty(e.from_value)} to ${pretty(e.to_value)}`,
  SOURCE_ADDED: (e) => `Added the source ${e.to_value}`,
  SOURCE_LINKED: () => "Linked a source registration",
  RUN_LINKED: () => "Linked a run",
  RUN_UNLINKED: () => "Unlinked a run",
  SOURCE_REMOVED: (e) => `Removed the source ${e.from_value}`,
  DOMAIN_LINKED: () => "Linked the domain",
  COMMENT: () => "Commented",
  CANCELLED: () => "Cancelled the request",
  REJECTED: () => "Rejected the request",
  REOPENED: () => "Reopened the request",
  DONE: () => "Every part is done: the request is complete",
  SOURCE_ONBOARDED: () => "Source onboarded",
  DESIGN_STORED: () => "Design approved and stored",
  FILE_ADDED: () => "Attached files",
  FILE_REMOVED: () => "Removed a file",
  FILES_HANDED_OVER: () => "Files added to the domain's documents",
};

export default async function RequestPage({ params, searchParams }: { params: { id: string }; searchParams?: { files?: string } }) {
  const access = await guard("requests");
  const [detail, sources, runs, domains] = await Promise.all([loadRequest(params.id), loadSources(), loadRuns("sort=updated&limit=200"), loadDomains()]);
  const r = detail.data;
  if (!r) {
    return (
      <>
        <PageHeader eyebrow="Onboarding request" title="Request not found" />
        <SourceError what="The request" error={detail.error} />
        <Empty icon={Inbox} title="This request does not exist, or it belongs to a domain you are not a member of" action={<Btn href="/os/requests">All requests</Btn>} />
      </>
    );
  }
  const previewing = !!access.preview;
  const product = r.request_type === "PRODUCT";
  const steps = requestSteps(r);
  const live = r.items.filter((i) => !i.removed_at);
  const removed = r.items.filter((i) => i.removed_at);
  const registered = sources.data.sources.map((s) => ({ source_system_id: s.source_system_id, source_system_name: s.source_system_name }));
  const runsFor = (i: RequestItem) => runs.data.runs
    .filter((x) => !x.is_archived && x.current_state !== "CANCELLED" && (!r.domain_id || x.domain_name === r.domain_name)
      && (product ? x.pipeline === "GOLD" : x.source_system_name === i.source_name))
    .map((x) => ({ run_id: x.run_id, run_name: x.run_name, current_state: x.current_state }));
  const mine = r.waits.filter((w) => waitIsMine(w, r.items, access));

  return (
    <>
      <PageHeader
        eyebrow={`Onboarding request · ${intentLabel(r)}`}
        title={r.title}
        subtitle={r.business_outcome ?? undefined}
        actions={<Btn href="/os/requests">All requests</Btn>}
        meta={<>
          <Tag tone={r.status === "OPEN" ? phaseOf(r).tone : r.status === "DONE" ? "green" : "neutral"}>{r.status === "OPEN" ? phaseOf(r).label : pretty(r.status)}</Tag>
          <span className="os-muted">Domain: <b className="os-ink">{r.domain_name ?? (r.proposed_domain ? `${r.proposed_domain} (to create)` : "none")}</b></span>
          <Tag tone={PRIORITY_TONE[r.priority] ?? "neutral"}>{pretty(r.priority)} priority</Tag>
          {r.needed_by && <span className={cn("inline-flex items-center gap-1", overdue(r) ? "text-[var(--os-red-ink)]" : "os-muted")}><CalendarClock className="h-3.5 w-3.5" />Needed by {r.needed_by.slice(0, 10)}{overdue(r) ? " (overdue)" : ""}</span>}
          <span className="os-muted">Filed by <b className="os-ink">{r.requested_by}</b> · {ago(r.created_at)}</span>
        </>}
      />
      <SourceError what="Part of the request" error={sources.error ?? runs.error} />
      {searchParams?.files && <Banner tone="amber" icon={AlertTriangle}><b>Not every file was attached.</b> {searchParams.files} Attach them again under Files.</Banner>}
      {r.status === "DONE" && <Banner icon={CheckCircle2}><b>Done.</b> The domain is ready and every source is onboarded{r.closed_at ? ` (${ago(r.closed_at)})` : ""}.</Banner>}
      {(r.status === "CANCELLED" || r.status === "REJECTED") && (
        <Banner tone="amber" icon={r.status === "REJECTED" ? X : Ban}><b>{pretty(r.status)} by {r.closed_by}</b>{r.close_reason ? `: ${r.close_reason}` : "."}</Banner>
      )}
      {r.status === "OPEN" && r.attention && <Banner tone="red" icon={AlertTriangle}><b>Needs attention.</b> {r.attention}</Banner>}
      {r.duplicates.length > 0 && r.status === "OPEN" && (
        <Banner tone="blue" icon={Link2}>Another open request asks for the same thing: {r.duplicates.map((d, i) => (
          <span key={d.request_id}>{i ? ", " : ""}<Link className="underline" href={`/os/requests/${d.request_id}`}>{d.title}</Link> ({d.source_name})</span>))}.</Banner>
      )}

      <ol className="os-card os-rise mb-4 grid gap-2 p-4 sm:grid-cols-3 xl:grid-cols-6" aria-label="Request path">
        {steps.map((s, i) => (
          <li key={s.stage} aria-current={s.state === "current" || s.state === "attention" ? "step" : undefined}
              className={cn("rounded-xl border p-3", s.state === "current" ? "border-[var(--os-forest)] bg-[var(--os-green-bg)]"
                : s.state === "attention" ? "border-[var(--os-red-line)] bg-[var(--os-red-bg)]" : "border-[var(--os-line)]")}>
            <div className="flex items-center gap-2">
              <span className={cn("grid h-6 w-6 place-items-center rounded-full text-[11px] font-extrabold",
                s.state === "done" ? "bg-[var(--os-green-bg)] text-[var(--os-green-ink)]" : s.state === "current" ? "bg-[var(--os-forest)] text-white"
                  : s.state === "attention" ? "bg-[var(--os-red-ink)] text-white" : "bg-[var(--os-neutral-bg)] os-faint")}>
                {s.state === "done" ? <Check className="h-3 w-3" /> : s.state === "attention" ? <AlertTriangle className="h-3 w-3" /> : i + 1}
              </span>
              <span className="text-[13px] font-extrabold">{s.label}</span>
            </div>
            <div className="os-faint mt-1 text-[10.5px]">{s.owner || (r.status === "DONE" ? "Complete" : "")}</div>
          </li>
        ))}
      </ol>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Card title="Sources" subtitle="Each source's own status, worked out from its registration, health and run" pad={false}>
            {live.length ? (
              <div className="os-divide">
                {live.map((i) => (
                  <div key={i.item_id} className="flex flex-wrap items-start justify-between gap-3 px-5 py-3.5">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[13.5px] font-extrabold">{i.source_name}</span>
                        <Tag tone={i.status === "ATTENTION" ? "red" : phaseOf(i).tone}>{phaseOf(i).label}</Tag>
                        {["MISSING", "UNREACHABLE", "NOT_LANDED", "ATTENTION"].includes(i.status) && <Tag tone={ITEM_TONE[i.status] ?? "neutral"}>{ITEM_LABEL[i.status] ?? pretty(i.status)}</Tag>}
                        {!i.source_system_id && !product && <Tag tone="amber">named only</Tag>}
                      </div>
                      {i.source_note && <div className="os-muted mt-0.5 text-[11.5px]">{i.source_note}</div>}
                      <div className="os-faint mt-1 text-[11.5px]">{i.status_detail}</div>
                      {i.run_id && (
                        <div className="mt-1 text-[11.5px]">Run: <Link href={`/os/build?view=selected&run=${i.active_run_id ?? i.run_id}`} className="font-semibold underline">{i.run_name ?? i.run_id.slice(0, 8)}</Link>
                          {i.run_state && <span className="os-faint"> · {stateLabel(i.run_state)}</span>}</div>
                      )}
                      {i.waits.map((w) => (
                        <div key={w.label} className="mt-1.5 flex items-center gap-1.5 text-[11.5px]">
                          <Hand className="h-3 w-3 text-[var(--os-amber-ink)]" /><span>{w.label}</span>
                          {waitIsMine(w, r.items, access) && <Tag tone="green">You</Tag>}
                        </div>
                      ))}
                    </div>
                    <ItemControls request={r} item={i} sources={registered} runs={runsFor(i)} previewing={previewing} />
                  </div>
                ))}
              </div>
            ) : (
              <div className="p-5"><Empty icon={CircleDashed} title={r.request_type === "DOMAIN" ? "No source asked for yet" : "No source"}>
                {r.request_type === "DOMAIN" ? "A domain request can be only the domain and its model; add the sources it should onboard when they are known." : undefined}
              </Empty></div>
            )}
            {r.may.add_source && <div className="border-t border-[var(--os-line)] px-5 py-3.5"><AddSource requestId={r.request_id} sources={registered} previewing={previewing} /></div>}
            {removed.length > 0 && <div className="os-faint border-t border-[var(--os-line)] px-5 py-2.5 text-[11px]">Removed: {removed.map((i) => i.source_name).join(", ")}</div>}
          </Card>

          <Card title="History" subtitle="Everything that happened, with who did it; the platform's own steps are marked" pad={false}>
            <div className="os-divide px-5">
              {[...r.events].reverse().map((e) => (
                <ActivityRow key={e.event_id} tone={e.kind === "REJECTED" || e.kind === "CANCELLED" ? "red" : e.kind === "DONE" ? "green" : e.automatic ? "blue" : "neutral"}
                             icon={e.automatic ? Sparkles : e.kind === "DONE" ? CheckCircle2 : e.kind === "COMMENT" ? Inbox : Check}
                             title={<>{(EVENT[e.kind] ?? ((x: RequestEvent) => pretty(x.kind)))(e)} <span className="os-faint font-semibold">· {e.automatic ? "platform" : e.actor}</span></>}
                             sub={e.note ?? undefined} when={ago(e.created_at)} />
              ))}
            </div>
          </Card>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          <Card title="Who acts now" subtitle={r.status === "OPEN" ? "Every role with work on this request, current stage first" : "Nobody: the request is closed"}>
            {r.status === "OPEN" && r.waits.length ? (
              <div className="os-divide">
                {r.waits.map((w) => {
                  const item = r.items.find((i) => i.item_id === w.item_id);
                  const href = actHref(w, item, r.domain_id, product, r.request_id, enhancesSource(r));
                  const you = waitIsMine(w, r.items, access);
                  return (
                    <div key={`${w.privilege}-${w.label}-${w.item_id ?? ""}`} className="flex items-start justify-between gap-3 py-2.5">
                      <div className="min-w-0">
                        <div className="text-[12.5px] font-bold leading-snug">{w.label}</div>
                        <div className="os-faint text-[10.5px]">{item ? `${item.source_name} · ` : ""}{personaForPrivilege(w.privilege)}</div>
                      </div>
                      {you && href ? <Btn href={href} variant="primary" title={w.label}>{waitCta(w)} <ArrowRight className="h-3.5 w-3.5" /></Btn> : you ? <Tag tone="green">Yours</Tag> : <Tag>Waiting on {waitRole(w) ?? "another role"}</Tag>}
                    </div>
                  );
                })}
              </div>
            ) : r.status === "OPEN" ? <Empty icon={CheckCircle2} title="Nothing is waiting" /> : null}
            {mine.length > 0 && r.status === "OPEN" && <div className="os-banner mt-3 text-[12px]"><b>It is your turn</b> on {mine.length} part{mine.length === 1 ? "" : "s"} of this request.</div>}
          </Card>

          {r.may.link_domain && (
            <Card title="The domain" subtitle={`Asked for a new domain${r.proposed_domain ? `, ${r.proposed_domain}` : ""}. It links itself when a domain of that name exists; or link it here.`}>
              <LinkDomain requestId={r.request_id} domains={domains.data.map((d) => ({ domain_id: d.domain_id, domain_name: d.domain_name }))} previewing={previewing} />
            </Card>
          )}

          {(r.request_type === "DOMAIN" || (r.files ?? []).length > 0 || r.may.add_files) && (
            <Card title="Files" subtitle={r.domain_id ? "Handed to the domain's documents, read there for review" : "Kept on the request until the domain exists"}>
              <RequestFiles requestId={r.request_id} files={r.files ?? []} mayAdd={!!r.may.add_files && r.request_type === "DOMAIN"} previewing={previewing} domainId={r.domain_id} />
            </Card>
          )}

          <Card title="Request details">
            <div className="grid gap-2.5">
              <Field label="Type">{intentLabel(r)} · {r.pipeline === "GOLD" ? "Gold" : "Silver"} pipeline</Field>
              <Field label="Stage since">{ago(r.stage_changed_at) || "–"}</Field>
              <Field label="Last activity">{ago(r.updated_at)}</Field>
            </div>
          </Card>

          <Card title="Discuss or close" subtitle="Comments, cancel (the requester), reject (with a reason), reopen">
            <RequestControls request={r} previewing={previewing} />
          </Card>
        </div>
      </div>
    </>
  );
}
