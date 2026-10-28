import Link from "next/link";
import { ArrowRight, CheckCircle2, Database, Inbox, ShieldCheck, Wrench } from "lucide-react";
import { stateLabel } from "@/lib/run-labels";
import { loadRequests, loadRuns, loadSources } from "@/lib/os/data";
import type { OnboardingRequest, RequestItem } from "@/lib/os/requests";
import type { SourceOverviewItem } from "@/lib/types";
import { guard } from "@/lib/os/session";
import { has } from "@/lib/os/access";
import { Action } from "@/components/os/action";
import { DeleteRunButton, StartSourceRunButton } from "@/components/os/request-ui";
import { Segmented } from "@/components/os/stage-board";
import { HomeSearch } from "@/components/os/home-search";
import { Banner, Btn, Card, Empty, Kpi, PageHeader, SourceError, Tag, ago, pretty, type Tone } from "@/components/os/ui";

export const dynamic = "force-dynamic";
export const metadata = { title: "Source readiness" };

const ENABLE_STATES = ["SOURCE_REGISTERED", "ACCESS_VALIDATION", "ACCESS_APPROVED", "LANDING_PENDING", "LANDING_RUNNING"];

const HEALTH: Record<string, { tone: Tone; label: string }> = {
  HEALTHY: { tone: "green", label: "Ready" },
  NOT_LANDED: { tone: "amber", label: "Not landed" },
  UNREACHABLE: { tone: "red", label: "Unreachable" },
};

const ITEM_STATUS: Record<string, { tone: Tone; label: string }> = {
  MISSING: { tone: "amber", label: "Not registered" },
  UNREACHABLE: { tone: "red", label: "Unreachable" },
  NOT_LANDED: { tone: "amber", label: "Not landed" },
  ATTENTION: { tone: "red", label: "Needs a new run" },
  NO_RUN: { tone: "blue", label: "Ready to start run" },
};

const wizardHref = (r: OnboardingRequest, i: RequestItem) =>
  `/os/enable/onboarding?${new URLSearchParams({ type: "source", request: r.request_id, item: i.item_id, name: i.source_name })}`;

function SourceCard({ s }: { s: SourceOverviewItem }) {
  const h = HEALTH[s.health] ?? { tone: "neutral" as Tone, label: pretty(s.health) };
  const location = s.oracle
    ? `${s.oracle.host}:${s.oracle.port}/${s.oracle.service}`
    : s.location ?? ([s.database_name, s.schema_name].filter(Boolean).join(".") || null);
  const typeLabel = [pretty(s.source_type), s.connection_type && s.connection_type !== s.source_type ? pretty(s.connection_type) : null]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="flex flex-col justify-between rounded-xl border border-[var(--os-line)] bg-[var(--os-surface)] p-3.5 transition hover:border-[var(--os-forest)]" data-testid="source-config">
      <div>
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="truncate text-[13.5px] font-extrabold tracking-[-0.01em]">{s.source_system_name}</div>
            <div className="os-muted mt-0.5 truncate text-[11.5px]">{typeLabel}</div>
          </div>
          <Tag tone={h.tone} title={s.health_detail || undefined}>{h.label}</Tag>
        </div>
        {location && (
          <div className="mt-2 truncate rounded-md bg-[var(--os-subtle)] px-2 py-1 font-mono text-[11px] text-[var(--os-ink)]" title={location}>
            {location}
          </div>
        )}
        {s.health !== "HEALTHY" && s.health_detail && (
          <div className="mt-2 text-[11px] text-[var(--os-amber-ink)]">{s.health_detail}</div>
        )}
      </div>
      <div className="mt-3 flex items-center justify-between gap-2 border-t border-[var(--os-line-soft)] pt-2.5 text-[11px]">
        <span className="os-faint">
          {s.runs} {s.runs === 1 ? "run" : "runs"}
          {s.last_landed_at ? ` · landed ${ago(s.last_landed_at)}` : ""}
        </span>
        <Link href="/os/design/sources" className="font-bold text-[var(--os-forest)] hover:underline">
          Manage <ArrowRight className="inline h-3 w-3" />
        </Link>
      </div>
    </div>
  );
}

export default async function SourceReadiness({ searchParams }: { searchParams: { filter?: string; q?: string } }) {
  const access = await guard("enable");
  const mayConnect = has(access, "SOURCE.CONNECT");
  const [sources, runs, requests] = await Promise.all([
    loadSources(),
    loadRuns("sort=updated&limit=150"),
    loadRequests("all", "OPEN", undefined, !access.preview),
  ]);

  const asks = requests.data.requests
    .flatMap((r) => r.items.filter((i) => !i.removed_at && i.stage === "ENABLE").map((i) => ({ r, i })))
    .sort((a, b) => a.r.created_at.localeCompare(b.r.created_at));

  const domainAsks = requests.data.requests
    .filter((r) => r.stage === "DOMAIN" && !r.domain_id && r.proposed_domain)
    .sort((a, b) => a.created_at.localeCompare(b.created_at));

  const list = sources.data.sources;
  const byId = new Map(list.map((s) => [s.source_system_id, s]));
  const ready = list.filter((s) => s.health === "HEALTHY" && (s.staged_tables > 0 || (s.landed_tables ?? 0) > 0));
  const needs = list.filter((s) => s.health !== "HEALTHY");
  const waiting = runs.data.runs.filter((r) => !r.is_archived && r.current_state !== "CANCELLED" && ENABLE_STATES.includes(r.current_state));

  const q = (searchParams.q ?? "").trim().toLowerCase();
  const filter = searchParams.filter === "ready" || searchParams.filter === "attention" ? searchParams.filter : "all";
  const ordered = [...list]
    .filter((s) => !q || s.source_system_name.toLowerCase().includes(q) || (s.database_name ?? "").toLowerCase().includes(q) || (s.schema_name ?? "").toLowerCase().includes(q))
    .filter((s) => filter === "ready" ? s.health === "HEALTHY" : filter === "attention" ? s.health !== "HEALTHY" : true)
    .sort((a, b) => Number(a.health === "HEALTHY") - Number(b.health === "HEALTHY") || a.source_system_name.localeCompare(b.source_system_name));

  const filterHref = (f: string) => `/os/enable?${new URLSearchParams({ ...(f !== "all" ? { filter: f } : {}), ...(q ? { q } : {}) })}`;

  return (
    <>
      <PageHeader
        eyebrow="Initiate / Data OS Administrator"
        title="Source readiness"
        subtitle="Connect, land, and verify source systems for onboarding requests."
        actions={<Btn href="/os/design/sources">Open Source Store</Btn>}
      />
      <SourceError what="Sources" error={sources.error} />
      <SourceError what="Requests" error={requests.error} />

      {asks.length || domainAsks.length ? (
        <Banner tone="amber" icon={Inbox}>
          <b>
            {[
              domainAsks.length ? `${domainAsks.length} domain${domainAsks.length === 1 ? "" : "s"}` : "",
              asks.length ? `${asks.length} source${asks.length === 1 ? "" : "s"}` : "",
            ]
              .filter(Boolean)
              .join(" and ")}{" "}
            awaiting onboarding.
          </b>{" "}
          Once connected and started, each request advances automatically to Design.
        </Banner>
      ) : waiting.length ? (
        <Banner
          tone="amber"
          icon={Wrench}
          action={<Action access={access} id="connectSource" label="Open next waiting run" href={`/os/build/runs/${waiting[0].run_id}/source`} variant="primary" />}
        >
          <b>{waiting.length} run{waiting.length === 1 ? "" : "s"} awaiting source enablement</b> before profiling and design can start.
        </Banner>
      ) : (
        <Banner icon={ShieldCheck}>All requested sources are connected and landed.</Banner>
      )}

      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi
          label="Pending requests"
          value={asks.length + domainAsks.length}
          hint={`${new Set([...asks.map((a) => a.r.request_id), ...domainAsks.map((d) => d.request_id)]).size} open requests`}
          icon={Inbox}
          tone={asks.length + domainAsks.length ? "amber" : "green"}
        />
        <Kpi label="Registered sources" value={list.length} hint={`${ready.length} ready`} icon={Database} tone="green" />
        <Kpi label="Ready to profile" value={ready.length} hint="Connected & landed" icon={CheckCircle2} tone="green" />
        <Kpi
          label="Needs attention"
          value={needs.length + waiting.length}
          hint={`${needs.length} sources · ${waiting.length} runs`}
          icon={Wrench}
          tone={needs.length + waiting.length ? "amber" : "green"}
        />
      </div>

      {domainAsks.length > 0 && (
        <Card className="mb-4" title="Requests waiting on a domain" subtitle="New business domains requested by data owners" pad={false}>
          <div className="os-divide" data-testid="enable-domain-asks">
            {domainAsks.map((r) => (
              <div key={r.request_id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[13.5px] font-extrabold">{r.proposed_domain}</span>
                    <Tag tone="amber">New domain</Tag>
                  </div>
                  <div className="os-faint mt-0.5 text-[11.5px]">
                    Request: <Link href={`/os/intake?view=selected&request=${r.request_id}`} className="font-semibold underline">{r.title}</Link> · filed by {r.requested_by} {ago(r.created_at)}
                  </div>
                  {r.business_outcome && <div className="os-muted mt-0.5 text-[11.5px]">{r.business_outcome}</div>}
                </div>
                <Action
                  access={access}
                  id="onboardDomain"
                  label="Onboard domain for request"
                  href={`/os/enable/new-domain?${new URLSearchParams({ request: r.request_id })}`}
                  variant="primary"
                />
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card className="mb-4" title="Requests waiting on a source" subtitle="Requested sources awaiting registration, landing, or pipeline start" pad={false}>
        {asks.length ? (
          <div className="os-divide" data-testid="enable-asks">
            {asks.map(({ r, i }) => {
              const st = ITEM_STATUS[i.status] ?? { tone: "neutral" as Tone, label: pretty(i.status) };
              const registeredNeedsLanding = !!i.source_system_id && (i.status === "UNREACHABLE" || i.status === "NOT_LANDED");
              const registeredReadyForRun = !!i.source_system_id && i.status === "NO_RUN";
              const srcObj = i.source_system_id ? byId.get(i.source_system_id) : undefined;
              const resume = i.run_id ? `/os/build/runs/${encodeURIComponent(i.run_id)}/source` : "/os/design/sources";
              return (
                <div key={i.item_id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[13.5px] font-extrabold">{i.source_name}</span>
                      <Tag tone={st.tone}>{st.label}</Tag>
                      {(r.domain_name || r.proposed_domain) && (
                        <span className="os-muted text-[11.5px] font-semibold">· {r.domain_name ?? r.proposed_domain}</span>
                      )}
                    </div>
                    <div className="os-faint mt-0.5 text-[11.5px]">
                      Request: <Link href={`/os/intake?view=selected&request=${r.request_id}`} className="font-semibold underline">{r.title}</Link> · filed by {r.requested_by} {ago(r.created_at)}
                    </div>
                    {i.source_note && <div className="os-muted mt-0.5 text-[11.5px]">Scope: {i.source_note}</div>}
                  </div>
                  <div className="flex shrink-0 flex-wrap items-center gap-2">
                    {registeredReadyForRun && mayConnect && (
                      <StartSourceRunButton
                        requestId={r.request_id}
                        itemId={i.item_id}
                        sourceName={srcObj?.source_system_name ?? i.source_name}
                        sourceSystemId={i.source_system_id}
                        sourceType={srcObj?.source_type}
                        databaseName={srcObj?.database_name}
                        schemaName={srcObj?.schema_name}
                        domainId={r.domain_id}
                        domainName={r.domain_name}
                        note={i.source_note}
                        previewing={!!access.preview}
                      />
                    )}
                    {registeredNeedsLanding ? (
                      <Action
                        access={access}
                        id="connectSource"
                        variant="primary"
                        href={resume}
                        label={!i.run_id ? "Open in Source Store" : i.status === "NOT_LANDED" ? "Resume landing" : "Recheck access"}
                      />
                    ) : (
                      <Action
                        access={access}
                        id="connectSource"
                        label="Onboard source for request"
                        href={wizardHref(r, i)}
                        variant={registeredReadyForRun ? "default" : "primary"}
                      />
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="p-5">
            <Empty icon={CheckCircle2} title="No request waits on a source">
              All requested sources are registered and have active runs.
            </Empty>
          </div>
        )}
      </Card>

      {waiting.length > 0 && (
        <Card className="mb-4" title={`Runs waiting on enablement (${waiting.length})`} subtitle="Runs awaiting source connection or landing before profiling starts" pad={false}>
          <div className="os-divide">
            {waiting.map((r) => (
              <div key={r.run_id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
                <div className="min-w-0">
                  <div className="truncate text-[13px] font-bold">{r.run_name}</div>
                  <div className="os-faint text-[11px]">
                    {r.domain_name ?? "No domain"} · {r.source_system_name ?? "No source"} · {ago(r.updated_at ?? r.created_at)}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Tag tone="amber">{stateLabel(r.current_state)}</Tag>
                  {mayConnect && (
                    <Btn href={`/os/build/runs/${r.run_id}/source`} variant="primary">
                      Configure source <ArrowRight className="h-3.5 w-3.5" />
                    </Btn>
                  )}
                  <DeleteRunButton runIds={[r.run_id]} iconOnly previewing={!!access.preview} />
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card
        title={`Registered sources (${ordered.length})`}
        subtitle="Connected source systems on the platform"
        pad={false}
        action={
          <div className="flex flex-wrap items-center gap-2">
            <Segmented
              label="Source status filter"
              testId="source-filter"
              options={[
                { key: "all", label: "All", href: filterHref("all"), active: filter === "all", count: list.length },
                { key: "ready", label: "Ready", href: filterHref("ready"), active: filter === "ready", count: list.filter((s) => s.health === "HEALTHY").length },
                { key: "attention", label: "Needs attention", href: filterHref("attention"), active: filter === "attention", count: needs.length, dot: needs.length > 0 },
              ]}
            />
            <HomeSearch placeholder="Search sources..." />
          </div>
        }
      >
        {ordered.length ? (
          <div className="os-scroll-y grid max-h-[540px] gap-3 p-4 sm:grid-cols-2 xl:grid-cols-3">
            {ordered.map((s) => (
              <SourceCard key={s.source_system_id} s={s} />
            ))}
          </div>
        ) : (
          <div className="p-5">
            <Empty icon={Database} title={q || filter !== "all" ? "No sources match this filter" : "No sources registered"}>
              {q || filter !== "all" ? <Btn href="/os/enable">Clear filters</Btn> : "Onboard the first source from the Onboard source tab."}
            </Empty>
          </div>
        )}
      </Card>
    </>
  );
}
