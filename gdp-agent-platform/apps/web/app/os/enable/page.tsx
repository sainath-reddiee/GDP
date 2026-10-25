import Link from "next/link";
import { CheckCircle2, CircleDashed, CloudCog, Database, Inbox, KeyRound, ShieldCheck, Workflow, Wrench, X } from "lucide-react";
import { stateLabel } from "@/lib/run-labels";
import { loadIngestion, loadRequests, loadRuns, loadSources, type IngestionControl, type IngestionPipeline } from "@/lib/os/data";
import type { OnboardingRequest, RequestItem } from "@/lib/os/requests";
import type { SourceOverviewItem } from "@/lib/types";
import { guard } from "@/lib/os/session";
import { has } from "@/lib/os/access";
import { Action } from "@/components/os/action";
import { ActivityRow, Banner, Btn, Card, Empty, Kpi, PageHeader, SourceError, Tag, ago, pretty, type Tone } from "@/components/os/ui";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const metadata = { title: "Source readiness" };

const ENABLE_STATES = ["SOURCE_REGISTERED", "ACCESS_VALIDATION", "ACCESS_APPROVED", "LANDING_PENDING", "LANDING_RUNNING"];
// never echoed to the page, even though the control row carries them: only whether one is set
const SECRET = /api_key|secret|password|token|credential/i;
// the framework fields worth showing in the configuration preview, in order
const CONFIG_FIELDS = ["source_name", "source_type", "domain_name", "parent_pipeline_name", "child_pipeline_name", "snaplogic_project_name", "source_account",
  "source_database_path", "source_schema_file", "source_table_sql", "source_primary_keys", "source_delta_key", "load_type", "load_frequency",
  "landing_database", "landing_schema", "landing_table", "bronze_database", "bronze_schema", "bronze_table", "is_active"];

function yaml(c: IngestionControl) {
  return CONFIG_FIELDS.filter((k) => !SECRET.test(k) && c[k] !== null && c[k] !== undefined && c[k] !== "")
    .map((k) => `${k}: ${typeof c[k] === "string" && /[:#]/.test(String(c[k])) ? JSON.stringify(c[k]) : c[k]}`).join("\n");
}

const HEALTH: Record<string, { tone: Tone; label: string }> = {
  HEALTHY: { tone: "green", label: "Ready" }, NOT_LANDED: { tone: "amber", label: "Not landed" }, UNREACHABLE: { tone: "red", label: "Unreachable" },
};

const ITEM_STATUS: Record<string, { tone: Tone; label: string }> = {
  MISSING: { tone: "amber", label: "Not registered" }, UNREACHABLE: { tone: "red", label: "Unreachable" },
  NOT_LANDED: { tone: "amber", label: "Not landed" }, ATTENTION: { tone: "red", label: "Needs a new run" },
  NO_RUN: { tone: "blue", label: "Run not started" },
};

/** Names compared the way requests match them: case, spacing and punctuation ignored. */
const nameKey = (s: string | null | undefined) => (s ?? "").toUpperCase().replace(/[^A-Z0-9]/g, "");

/** The onboarding wizard for a request's source (the wizard asks how the source arrives: a share, a database, files). */
const wizardHref = (r: OnboardingRequest, i: RequestItem) =>
  `/os/enable/onboarding?${new URLSearchParams({ type: "source", request: r.request_id, item: i.item_id, name: i.source_name })}`;

/** One source as configuration: how it is reached, with which credential reference, where it lands, when it runs, which
 *  SnapLogic pipelines carry it and whether it is ready. No table catalog: that lives in the source store. */
function SourceConfig({ s, controls, pipeline }: { s: SourceOverviewItem; controls: IngestionControl[]; pipeline: IngestionPipeline | undefined }) {
  const h = HEALTH[s.health] ?? { tone: "neutral" as Tone, label: pretty(s.health) };
  const c = controls.find((x) => x.is_active) ?? controls[0];
  const secretSet = !!pipeline?.has_secret || controls.some((x) => Object.keys(x).some((k) => SECRET.test(k) && x[k] !== null && x[k] !== ""));
  // the framework's landing target, else the platform's landing, else (a Snowflake database or share) read where it is
  const inPlace = /SNOWFLAKE/i.test(`${s.source_type} ${s.connection_type ?? ""}`);
  const landing = c?.landing_database ? `${c.landing_database}.${c.landing_schema ?? "LANDING"}`
    : s.landed_tables ? `Platform landing · ${s.landed_tables} table${s.landed_tables === 1 ? "" : "s"}`
    : inPlace ? "Read in place in Snowflake" : null;
  const schedule = pipeline?.schedule?.frequency ? `${pretty(pipeline.schedule.frequency)}${pipeline.schedule.cron ? ` (${pipeline.schedule.cron})` : ""}`
    : c?.load_frequency ? pretty(String(c.load_frequency)) : s.oracle?.schedule ?? null;
  const rows: [string, React.ReactNode][] = [
    ["Connection", <>{pretty(s.source_type)}{s.connection_type ? ` · ${pretty(s.connection_type)}` : ""}<span className="os-faint block font-mono text-[10.5px]">{s.oracle ? `${s.oracle.host}:${s.oracle.port}/${s.oracle.service}` : s.location ?? ([s.database_name, s.schema_name].filter(Boolean).join(".") || "location not recorded")}</span></>],
    ["Credential", s.oracle ? `Runs on ${s.oracle.runtime === "api_host" ? "the API host" : "Snowflake"}` : secretSet ? <span className="inline-flex items-center gap-1"><KeyRound className="h-3 w-3" />Secret reference set</span> : "Snowflake role access"],
    ["Lands in", landing ?? <span className="os-faint">not landed yet</span>],
    ["Schedule", schedule ?? <span className="os-faint">on demand</span>],
    ["SnapLogic", c?.parent_pipeline_name || c?.child_pipeline_name ? <span className="font-mono text-[10.5px]">{c.parent_pipeline_name ?? "?"} → {c.child_pipeline_name ?? "?"}</span>
      : pipeline?.snaplogic_pipeline_name ? <span className="font-mono text-[10.5px]">{pipeline.snaplogic_pipeline_name}</span> : <span className="os-faint">not configured</span>],
  ];
  return (
    <div className="rounded-xl border border-[var(--os-line)] p-3.5" data-testid="source-config">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[13px] font-extrabold">{s.source_system_name}</div>
          <div className="os-faint text-[10.5px]">{s.owner ? `Owner ${s.owner}` : "No owner recorded"}{s.security_classification ? ` · ${pretty(s.security_classification)}` : ""}</div>
        </div>
        <Tag tone={h.tone} title={s.health_detail || undefined}>{h.label}</Tag>
      </div>
      <dl className="mt-2.5 grid gap-1.5 text-[11.5px]">
        {rows.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[84px_minmax(0,1fr)] gap-2">
            <dt className="os-faint">{k}</dt><dd className="min-w-0 break-words">{v}</dd>
          </div>
        ))}
      </dl>
      {s.health !== "HEALTHY" && s.health_detail && <div className="mt-2 text-[11px] text-[var(--os-amber-ink)]">{s.health_detail}</div>}
      <div className="os-faint mt-2 text-[10.5px]">{controls.length ? `${controls.length} framework entr${controls.length === 1 ? "y" : "ies"}` : "No framework entry"} · {s.runs} run{s.runs === 1 ? "" : "s"}{s.last_landed_at ? ` · landed ${ago(s.last_landed_at)}` : ""}</div>
    </div>
  );
}

export default async function SourceReadiness({ searchParams }: { searchParams: { config?: string } }) {
  const access = await guard("enable");
  const mayConnect = has(access, "SOURCE.CONNECT");
  const [sources, runs, ingestion, requests] = await Promise.all([loadSources(), loadRuns("sort=updated&limit=150"), loadIngestion(), loadRequests("all", "OPEN")]);
  // the administrator's part of the open requests: each source still to register, reach or land, oldest request first
  const asks = requests.data.requests
    .flatMap((r) => r.items.filter((i) => !i.removed_at && i.stage === "ENABLE").map((i) => ({ r, i })))
    .sort((a, b) => a.r.created_at.localeCompare(b.r.created_at));
  // and each new domain a data owner asked for, still to onboard
  const domainAsks = requests.data.requests.filter((r) => r.stage === "DOMAIN" && !r.domain_id && r.proposed_domain)
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
  const list = sources.data.sources;
  const ready = list.filter((s) => s.health === "HEALTHY" && (s.staged_tables > 0 || (s.landed_tables ?? 0) > 0));
  const needs = list.filter((s) => s.health !== "HEALTHY");
  const owned = list.filter((s) => s.owner).length;
  const waiting = runs.data.runs.filter((r) => !r.is_archived && ENABLE_STATES.includes(r.current_state));
  const configs = ingestion.controls;
  const config = configs.find((c) => String(c.child_id) === searchParams.config) ?? configs.find((c) => c.is_active) ?? configs[0];
  const controlsOf = (s: SourceOverviewItem) => configs.filter((c) => nameKey(c.source_name) === nameKey(s.source_system_name));
  const pipelineOf = (s: SourceOverviewItem) => ingestion.pipelines.find((p) => nameKey(p.source_name) === nameKey(s.source_system_name));
  // needing work first, then by name
  const ordered = [...list].sort((a, b) => Number(a.health === "HEALTHY") - Number(b.health === "HEALTHY") || a.source_system_name.localeCompare(b.source_system_name));

  return (
    <>
      {/* onboarding a source or a domain are Enable's own tabs above, not repeated here */}
      <PageHeader eyebrow="Initiate / Data OS Administrator" title="Source readiness"
                  subtitle="The Data OS Administrator onboards domains and sources (the Onboard source and Onboard domain tabs): connection, credential reference, landing target, schedule and SnapLogic configuration. Tables and profiles live in the Source Store; this page is configuration."
                  actions={<Btn href="/os/design/sources">Open Source Store</Btn>} />
      <SourceError what="Sources" error={sources.error} />
      <SourceError what="Requests" error={requests.error} />
      {asks.length || domainAsks.length
        ? <Banner tone="amber" icon={Inbox}><b>{[domainAsks.length ? `${domainAsks.length} domain${domainAsks.length === 1 ? "" : "s"}` : "", asks.length ? `${asks.length} source${asks.length === 1 ? "" : "s"}` : ""].filter(Boolean).join(" and ")} asked for by data owners</b> wait on onboarding. Once onboarded, each request moves on to the Data Designer on its own.</Banner>
        : waiting.length
          ? <Banner tone="amber" icon={Wrench} action={<Action access={access} id="connectSource" label="Open next waiting run" href={`/os/build/runs/${waiting[0].run_id}/source`} variant="primary" />}><b>{waiting.length} run{waiting.length === 1 ? "" : "s"} wait on enablement</b>: access, landing or configuration before the designer can start.</Banner>
          : <Banner icon={ShieldCheck}>Nothing waits on onboarding. Sources already connected and landed skip this step automatically.</Banner>}

      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi label="Asked for by requests" value={asks.length} hint={`${new Set(asks.map((a) => a.r.request_id)).size} open requests`} icon={Inbox} tone={asks.length ? "amber" : "green"} />
        <Kpi label="Registered sources" value={list.length} hint={`${ready.length} ready`} icon={Database} tone="green" />
        <Kpi label="Need work" value={needs.length + waiting.length} hint={`${needs.length} sources · ${waiting.length} runs`} icon={Wrench} tone={needs.length + waiting.length ? "amber" : "green"} />
        <Kpi label="Framework entries" value={configs.length} hint={`${ingestion.pipelines.length} pipelines registered`} icon={Workflow} tone="purple" />
      </div>

      {domainAsks.length > 0 && (
        <Card className="mb-4" title="Requests waiting on a domain" subtitle="Data owners asked for these new domains. Onboard one for its request: a domain of the requested name is attached to the request on its own." pad={false}>
          <div className="os-divide" data-testid="enable-domain-asks">
            {domainAsks.map((r) => (
              <div key={r.request_id} className="flex flex-wrap items-start justify-between gap-3 px-5 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2"><span className="text-[13px] font-extrabold">{r.proposed_domain}</span><Tag tone="amber">New domain</Tag></div>
                  <div className="os-faint mt-0.5 text-[11px]">For <Link href={`/os/requests/${r.request_id}`} className="font-semibold underline">{r.title}</Link> · filed by {r.requested_by} {ago(r.created_at)}</div>
                  {r.business_outcome && <div className="os-muted mt-0.5 text-[11.5px]">{r.business_outcome}</div>}
                </div>
                <Action access={access} id="onboardDomain" label="Onboard domain for request" href={`/os/enable/new-domain?${new URLSearchParams({ request: r.request_id })}`} variant="primary" />
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card className="mb-4" title="Requests waiting on a source" subtitle="Data owners asked for these sources. Onboard one for its request and the request picks it up on its own." pad={false}>
        {asks.length ? (
          <div className="os-divide" data-testid="enable-asks">
            {asks.map(({ r, i }) => {
              const st = ITEM_STATUS[i.status] ?? { tone: "neutral" as Tone, label: pretty(i.status) };
              // registered but unreachable or not landed: resumed on its run's source step (access check and landing),
              // or in the Source Store when it has no run yet; anything else opens the wizard for the request
              const registered = !!i.source_system_id && (i.status === "UNREACHABLE" || i.status === "NOT_LANDED");
              const resume = i.run_id ? `/os/build/runs/${encodeURIComponent(i.run_id)}/source` : "/os/design/sources";
              return (
                <div key={i.item_id} className="flex flex-wrap items-start justify-between gap-3 px-5 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[13px] font-extrabold">{i.source_name}</span>
                      <Tag tone={st.tone}>{st.label}</Tag>
                    </div>
                    <div className="os-faint mt-0.5 text-[11px]">
                      For <Link href={`/os/requests/${r.request_id}`} className="font-semibold underline">{r.title}</Link> · {r.domain_name ?? r.proposed_domain ?? "no domain"} · filed by {r.requested_by} {ago(r.created_at)}
                    </div>
                    {i.source_note && <div className="os-muted mt-0.5 text-[11.5px]">{i.source_note}</div>}
                    {i.status_detail && <div className="os-faint mt-0.5 text-[11px]">{i.status_detail}</div>}
                  </div>
                  <div className="flex shrink-0 flex-wrap gap-2">
                    {registered
                      ? <Action access={access} id="connectSource" variant="primary" href={resume}
                                label={!i.run_id ? "Open in Source Store" : i.status === "NOT_LANDED" ? "Resume landing" : "Recheck access"} />
                      : <Action access={access} id="connectSource" label="Onboard source for request" href={wizardHref(r, i)} variant="primary" />}
                  </div>
                </div>
              );
            })}
          </div>
        ) : <div className="p-5"><Empty icon={CheckCircle2} title="No request waits on a source">Data owners attach sources already on the platform; new ones they ask for appear here.</Empty></div>}
      </Card>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Card title="Source configurations" subtitle="Every registered source as configuration; the ones needing work first. Credentials show only whether a reference is set." pad={false}>
            {ordered.length ? (
              <div className="grid max-h-[720px] gap-3 overflow-auto p-4 lg:grid-cols-2">
                {ordered.map((s) => <SourceConfig key={s.source_system_id} s={s} controls={controlsOf(s)} pipeline={pipelineOf(s)} />)}
              </div>
            ) : <div className="p-5"><Empty icon={Database} title="No sources registered">Onboard the first one from the Onboard source tab.</Empty></div>}
          </Card>

          <Card title="Framework configuration" subtitle="Runtime ingestion through the config-driven SnapLogic framework (CORE.INGESTION_CONTROL_CONFIG); secrets are never shown. Edit it in the Source Store.">
            <SourceError what="Ingestion configuration" error={ingestion.error} />
            {configs.length ? (
              <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
                <div className="os-divide max-h-[360px] overflow-auto">
                  {configs.map((c) => (
                    <Link key={c.child_id} href={`/os/enable?config=${c.child_id}`} scroll={false}
                          className={cn("flex items-center justify-between gap-2 rounded-md px-2 py-2 os-hover", c.child_id === config?.child_id && "bg-[var(--os-hover)]")}>
                      <span className="min-w-0">
                        <span className="block truncate text-[12px] font-bold">{c.source_name ?? c.source_account}</span>
                        <span className="os-faint block truncate text-[10.5px]">{c.source_table_sql}</span>
                      </span>
                      <Tag tone={c.is_active ? "green" : "neutral"}>{c.is_active ? "Active" : "Inactive"}</Tag>
                    </Link>
                  ))}
                </div>
                {config && <pre className="os-code max-h-[360px] text-[11px]">{yaml(config)}</pre>}
              </div>
            ) : <Empty icon={Workflow} title="No framework configuration yet" />}
          </Card>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          <Card title="Readiness agent" subtitle="What is checked before a source reaches the designer">
            <div className="os-divide">
              <ActivityRow tone={owned < list.length ? "amber" : "green"} icon={owned < list.length ? Wrench : CheckCircle2} title="Source registry"
                           sub={`${list.length} sources registered · ${owned} with an owner recorded`} />
              <ActivityRow tone={needs.length ? "amber" : "green"} icon={needs.length ? Wrench : CheckCircle2} title="Design-time access" sub={needs.length ? `${needs.length} source${needs.length === 1 ? "" : "s"} not reachable or not landed` : "Every source is reachable"} />
              <ActivityRow tone={sources.data.totals.staged ? "green" : "neutral"} icon={sources.data.totals.staged ? CheckCircle2 : CircleDashed} title="Landed for profiling" sub={`${ready.length} of ${list.length} sources landed and ready`} />
              <ActivityRow tone={ingestion.pipelines.some((p) => p.status === "ACTIVE") ? "green" : "neutral"} icon={CloudCog} title="Runtime ingestion"
                           sub={`${ingestion.pipelines.length} pipeline${ingestion.pipelines.length === 1 ? "" : "s"} registered · ${ingestion.pipelines.filter((p) => p.status === "ACTIVE").length} active`} />
            </div>
          </Card>

          <Card title="Runs waiting on enablement" subtitle="Access, landing or configuration before the designer can start" pad={false}>
            {waiting.length ? (
              <div className="os-divide">
                {waiting.map((r) => {
                  const row = (
                    <>
                      <span className="min-w-0"><span className="block truncate text-[12px] font-bold">{r.run_name}</span>
                        <span className="os-faint text-[10.5px]">{r.source_system_name ?? "no source"} · {ago(r.updated_at ?? r.created_at)}</span></span>
                      <Tag tone="amber">{stateLabel(r.current_state)}</Tag>
                    </>
                  );
                  // the run's source step is the administrator's (SOURCE.CONNECT); others see the row without a link
                  return mayConnect
                    ? <Link key={r.run_id} href={`/os/build/runs/${r.run_id}/source`} title="Open the run's source step" className="flex items-center justify-between gap-2 px-5 py-2.5 os-hover">{row}</Link>
                    : <div key={r.run_id} className="flex items-center justify-between gap-2 px-5 py-2.5">{row}</div>;
                })}
              </div>
            ) : <div className="p-5"><Empty icon={CheckCircle2} title="Nothing to hand off" /></div>}
          </Card>

          <Card title="Ingestion pipelines" subtitle="Registered runtime pipelines and their schedule" pad={false}>
            {ingestion.pipelines.length ? (
              <div className="os-divide">
                {ingestion.pipelines.map((p) => (
                  <div key={p.source_id} className="flex items-center justify-between gap-2 px-5 py-2.5">
                    <span className="min-w-0"><span className="block truncate text-[12px] font-bold">{p.source_name}</span>
                      <span className="os-faint block truncate text-[10.5px]">{pretty(p.source_type)} · {p.schedule?.frequency ? pretty(p.schedule.frequency) : "no schedule"}{p.schedule?.cron ? ` (${p.schedule.cron})` : ""} · {p.snaplogic_pipeline_name}</span></span>
                    <Tag tone={p.status === "ACTIVE" ? "green" : p.status === "REGISTERED" ? "blue" : "neutral"}>{pretty(p.status)}</Tag>
                  </div>
                ))}
              </div>
            ) : <div className="p-5"><Empty icon={X} title="No pipelines registered" /></div>}
          </Card>
        </div>
      </div>
    </>
  );
}
