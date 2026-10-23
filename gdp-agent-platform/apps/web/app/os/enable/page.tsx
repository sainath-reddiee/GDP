import Link from "next/link";
import { ArrowRight, CheckCircle2, CircleDashed, Database, Plug, ShieldCheck, Workflow, Wrench, X } from "lucide-react";
import { stateLabel } from "@/lib/run-labels";
import { loadIngestion, loadRequests, loadRuns, loadSources, type IngestionControl } from "@/lib/os/data";
import { RequestQueue } from "@/components/os/request-queue";
import { guard } from "@/lib/os/session";
import { Action } from "@/components/os/action";
import { ActivityRow, Banner, Btn, Card, Empty, Kpi, PageHeader, SourceError, Tag, ago, pretty, type Tone } from "@/components/os/ui";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";
export const metadata = { title: "Source readiness" };

const ENABLE_STATES = ["SOURCE_REGISTERED", "ACCESS_VALIDATION", "ACCESS_APPROVED", "LANDING_PENDING", "LANDING_RUNNING"];
// never echoed to the page, even though the control row carries them
const SECRET = /api_key|secret|password|token/i;
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

export default async function SourceReadiness({ searchParams }: { searchParams: { config?: string } }) {
  const access = await guard("enable");
  const persona = access.persona;
  const [sources, runs, ingestion, requests] = await Promise.all([loadSources(), loadRuns("sort=updated&limit=150"), loadIngestion(), loadRequests("all", "OPEN")]);
  // requests with a source to register, reach or land: the administrator's part of them
  const enabling = requests.data.requests.filter((r) => r.waits.some((w) => w.privilege === "SOURCE.CONNECT" || w.privilege === "PROFILE.RUN"));
  const list = sources.data.sources;
  const ready = list.filter((s) => s.health === "HEALTHY" && (s.staged_tables > 0 || (s.landed_tables ?? 0) > 0));
  const needs = list.filter((s) => s.health !== "HEALTHY");
  const waiting = runs.data.runs.filter((r) => !r.is_archived && ENABLE_STATES.includes(r.current_state));
  const configs = ingestion.controls;
  const config = configs.find((c) => String(c.child_id) === searchParams.config) ?? configs.find((c) => c.is_active) ?? configs[0];
  const pct = list.length ? Math.round((ready.length / list.length) * 100) : 0;

  return (
    <>
      <PageHeader eyebrow={`${persona.label} / Enablement`} title="Source readiness"
                  subtitle="Design-time access for profiling and runtime ingestion configuration, reusing the connectors and the config-driven framework already in place."
                  actions={<><Btn href="/dashboard/sources">Source store</Btn><Action access={access} id="connectSource" href="/dashboard/onboarding?type=source" variant="primary" icon={<Plug className="h-3.5 w-3.5" />} /></>} />
      <SourceError what="Sources" error={sources.error} />
      {waiting.length
        ? <Banner tone="amber" icon={Wrench} action={<Action access={access} id="connectSource" label="Open the first" href={`/runs/${waiting[0].run_id}/source`} variant="warn" />}><b>{waiting.length} run{waiting.length === 1 ? "" : "s"} wait on enablement</b>: access, landing or configuration before the designer can start.</Banner>
        : <Banner icon={ShieldCheck}>No run is waiting on enablement. Sources that are already connected and landed skip this step automatically.</Banner>}

      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi label="Registered sources" value={list.length} hint={`${sources.data.totals.tables} tables catalogued`} icon={Database} tone="green" />
        <Kpi label="Ready for profiling" value={ready.length} hint={`${sources.data.totals.staged} tables staged`} icon={CheckCircle2} tone="blue" />
        <Kpi label="Need enablement" value={needs.length + waiting.length} hint={`${needs.length} sources · ${waiting.length} runs`} icon={Wrench} tone={needs.length + waiting.length ? "amber" : "green"} />
        <Kpi label="Connector reuse" value={`${pct}%`} hint="sources ready without new work" icon={Workflow} tone="purple" />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Card title="Connection and ingestion readiness" subtitle="Every registered source: how it is fetched at design time and whether it is ready" pad={false}>
            {list.length ? (
              <div className="max-h-[520px] overflow-auto">
                <table className="os-table">
                  <thead className="sticky top-0"><tr><th>Source</th><th>Design-time access</th><th className="text-right">Tables</th><th className="text-right">Staged</th><th>Readiness</th><th>Runs</th></tr></thead>
                  <tbody>
                    {list.map((s) => {
                      const h = HEALTH[s.health] ?? { tone: "neutral" as Tone, label: pretty(s.health) };
                      return (
                        <tr key={s.source_system_id}>
                          <td><div className="font-semibold">{s.source_system_name}</div><div className="os-faint text-[10.5px]">{s.database_name}.{s.schema_name}</div></td>
                          <td className="os-muted">{pretty(s.source_type)}{s.connection_type ? ` · ${pretty(s.connection_type)}` : ""}</td>
                          <td className="text-right tabular-nums">{s.table_count ?? "–"}</td>
                          <td className="text-right tabular-nums">{s.staged_tables}</td>
                          <td><Tag tone={h.tone} title={s.health_detail || undefined}>{h.label}</Tag></td>
                          <td className="os-muted tabular-nums">{s.runs}{s.last_run_at ? <span className="os-faint"> · {ago(s.last_run_at)}</span> : null}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : <div className="p-5"><Empty icon={Database} title="No sources registered" /></div>}
          </Card>

          <Card title="Framework configuration" subtitle="Runtime ingestion through the config-driven SnapLogic framework (CORE.INGESTION_CONTROL_CONFIG)"
                action={<Action access={access} id="manageIngestion" href="/dashboard/sources" />}>
            <SourceError what="Ingestion configuration" error={ingestion.error} />
            {configs.length ? (
              <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
                <div className="os-divide">
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
              <ActivityRow tone="green" icon={CheckCircle2} title="Source registry" sub={`${list.length} sources registered with owner and classification`} />
              <ActivityRow tone={needs.length ? "amber" : "green"} icon={needs.length ? Wrench : CheckCircle2} title="Design-time access" sub={needs.length ? `${needs.length} source${needs.length === 1 ? "" : "s"} not reachable or not landed` : "Every source is reachable"} />
              <ActivityRow tone={sources.data.totals.staged ? "green" : "neutral"} icon={sources.data.totals.staged ? CheckCircle2 : CircleDashed} title="Staged for profiling" sub={`${sources.data.totals.staged} of ${sources.data.totals.tables} tables staged`} />
              <ActivityRow tone={ingestion.pipelines.some((p) => p.status === "ACTIVE") ? "green" : "neutral"} icon={Workflow} title="Runtime ingestion"
                           sub={`${ingestion.pipelines.length} pipeline${ingestion.pipelines.length === 1 ? "" : "s"} registered · ${ingestion.pipelines.filter((p) => p.status === "ACTIVE").length} active`} />
            </div>
          </Card>

          <RequestQueue title="Requests needing sources" subtitle="Data owners asked for these; a source is not registered, reachable or landed yet"
                        requests={enabling} access={access} error={requests.error} empty="No request waits on enablement" />

          <Card title="Admin handoff" subtitle="Runs waiting on access, landing or configuration" pad={false}>
            {waiting.length ? (
              <div className="os-divide">
                {waiting.map((r) => (
                  <Link key={r.run_id} href={`/runs/${r.run_id}/source`} className="flex items-center justify-between gap-2 px-5 py-2.5 os-hover">
                    <span className="min-w-0"><span className="block truncate text-[12px] font-bold">{r.run_name}</span>
                      <span className="os-faint text-[10.5px]">{r.source_system_name ?? "no source"} · {ago(r.updated_at ?? r.created_at)}</span></span>
                    <Tag tone="amber">{stateLabel(r.current_state)}</Tag>
                  </Link>
                ))}
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
            <div className="border-t border-[var(--os-line)] px-5 py-3"><Link href="/dashboard/sources" className="os-accent inline-flex items-center gap-1 text-[12px] font-bold">Open source store <ArrowRight className="h-3 w-3" /></Link></div>
          </Card>
        </div>
      </div>
    </>
  );
}
