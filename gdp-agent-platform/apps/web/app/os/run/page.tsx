import Link from "next/link";
import { Activity, BadgeCheck, BookOpenCheck, Bot, CheckCircle2, FlaskConical, Siren, Sparkles, TriangleAlert } from "lucide-react";
import { loadDags, loadExecutions, loadIncidents, loadKnowledge, loadOps, loadQuality } from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import { has } from "@/lib/os/access";
import { Action } from "@/components/os/action";
import { ActivityRow, Banner, Bar, Btn, Card, Empty, Kpi, PageHeader, SourceError, Tag, ago, pretty, type Tone } from "@/components/os/ui";

export const dynamic = "force-dynamic";
export const metadata = { title: "Run and quality" };

const DAG_STATE: Record<string, { tone: Tone; label: string }> = {
  success: { tone: "green", label: "Healthy" }, failed: { tone: "red", label: "Failed" }, running: { tone: "blue", label: "Running" },
  queued: { tone: "blue", label: "Queued" }, up_for_retry: { tone: "amber", label: "Retrying" },
};
const pct = (v: number | null | undefined) => (v === null || v === undefined ? "–" : `${Math.round(v * 100)}%`);

export default async function RunAndQuality() {
  const access = await guard("run");
  const persona = access.persona;
  const [ops, quality, dags, executions, incidents, learned] = await Promise.all([
    loadOps(), loadQuality(), loadDags(), loadExecutions(), loadIncidents(), loadKnowledge({ q: "" }),
  ]);
  const q = quality.data;
  const open = incidents.data.filter((i) => i.status === "OPEN" || i.status === "ACKNOWLEDGED" || i.status === "INVESTIGATING");
  const opsLearning = learned.data.items.filter((k) => k.origin === "OPS" || k.knowledge_type === "AIRFLOW_RUNBOOK" || k.knowledge_type === "EXCEPTION")
    .sort((a, b) => (b.updated_at ?? b.created_at).localeCompare(a.updated_at ?? a.created_at));
  const failingDags = dags.data.filter((d) => d.last_state === "failed");
  const soda = executions.data.filter((e) => e.kind === "SODA");
  const qa = executions.data.filter((e) => e.kind === "QA");
  const worst = open.sort((a, b) => a.severity.localeCompare(b.severity))[0];

  return (
    <>
      <PageHeader eyebrow={`${persona.label} / Run and quality`} title="Production operations"
                  subtitle="Runtime health, data quality and incidents, connected back to the code, mapping and knowledge that produced them."
                  actions={<><Btn href="/os/run/quality">Quality center</Btn><Action access={access} id="viewOps" href="/os/run/ops" variant="primary" icon={<Activity className="h-3.5 w-3.5" />} /></>} />
      <SourceError what="Operations" error={ops.error} />
      <SourceError what="Quality" error={quality.error} />
      {open.length
        ? <Banner tone="red" icon={Siren} action={<Action access={access} id="viewOps" label="Open incidents" href="/os/run/incidents" variant="red" />}><b>{open.length} open incident{open.length === 1 ? "" : "s"}</b> across {new Set(open.map((i) => i.dag_id)).size} pipeline{new Set(open.map((i) => i.dag_id)).size === 1 ? "" : "s"}. The RCA agent proposes a cause; a person approves any fix.</Banner>
        : <Banner icon={CheckCircle2}>No open incident. Pipelines and quality checks are within policy.</Banner>}

      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi label="Monitored pipelines" value={ops.data?.dags ?? dags.data.length} hint={`${ops.data?.envs ?? 0} Airflow environment${ops.data?.envs === 1 ? "" : "s"} · polled ${ago(ops.data?.last_poll_at) || "never"}`} icon={Activity} tone="green" href="/os/run/ops" />
        <Kpi label="Open incidents" value={open.length} hint={`${failingDags.length} pipelines last failed`} icon={Siren} tone={open.length ? "red" : "green"} href="/os/run/incidents" />
        <Kpi label="Data quality health" value={q?.data_quality.avg_health === null || q?.data_quality.avg_health === undefined ? "–" : `${Math.round(q.data_quality.avg_health)}%`} hint={q ? `${q.data_quality.checks} checks · ${q.data_quality.failed_checks} failing` : ""} icon={BadgeCheck} tone="blue" href="/os/design/dq-store" />
        <Kpi label="QA pass rate (30d)" value={q?.qa.pass_rate === null || q?.qa.pass_rate === undefined ? "–" : `${Math.round(q.qa.pass_rate)}%`} hint={q ? `${q.qa.tests} tests · ${q.qa.failed} failed · ${q.qa.runs} runs` : ""} icon={FlaskConical} tone="purple" href="/os/release/qa" />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          {/* Airflow pipelines and their incidents are for people who hold OPS.VIEW */}
          {has(access, "OPS.VIEW") && (
          <Card title="Operational pipeline health" subtitle="Every monitored DAG: last state, reliability and latency" action={<Action access={access} id="viewOps" label="All pipelines" href="/os/run/ops" />} pad={false}>
            {dags.data.length ? (
              <div className="max-h-[440px] overflow-auto">
                <table className="os-table">
                  <thead className="sticky top-0"><tr><th>Pipeline</th><th>Last run</th><th className="text-right">Success 7d</th><th className="text-right">p95</th><th>Health</th><th className="text-right">Incidents</th></tr></thead>
                  <tbody>
                    {dags.data.map((d) => {
                      const s = DAG_STATE[d.last_state ?? ""] ?? { tone: "neutral" as Tone, label: pretty(d.last_state) || "No runs" };
                      return (
                        <tr key={`${d.env_id}:${d.dag_id}`}>
                          <td><Link href={`/os/run/ops/dag?${new URLSearchParams({ env: d.env_id, dag: d.dag_id })}`} className="font-bold hover:underline">{d.dag_id}</Link>
                            <div className="os-faint text-[10.5px]">{d.env_id} · {d.schedule ?? "no schedule"}</div></td>
                          <td className="os-muted whitespace-nowrap">{ago(d.last_run_at) || "–"}</td>
                          <td className="w-28 text-right"><span className="tabular-nums">{pct(d.success_7d)}</span>
                            {d.success_7d !== null && <Bar className="mt-1 h-1.5" value={d.success_7d * 100} tone={d.success_7d >= 0.9 ? "green" : d.success_7d >= 0.5 ? "amber" : "red"} />}</td>
                          <td className="text-right tabular-nums">{d.p95_s === null ? "–" : `${d.p95_s.toFixed(1)}s`}</td>
                          <td><Tag tone={s.tone}>{s.label}</Tag></td>
                          <td className="text-right tabular-nums">{d.open_incidents || "–"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : <div className="p-5"><Empty icon={Activity} title="No pipeline is monitored yet">Connect an Airflow environment in Operations.</Empty></div>}
          </Card>
          )}

          <Card title="Live data quality" subtitle="The latest Soda scans and QA runs, by target" action={<Btn href="/os/run/quality">Quality center</Btn>} pad={false}>
            {executions.data.length ? (
              <table className="os-table">
                <thead><tr><th>Target</th><th>Kind</th><th className="text-right">Passed</th><th className="text-right">Failed</th><th>Result</th><th>When</th></tr></thead>
                <tbody>
                  {[...soda, ...qa].slice(0, 10).map((e) => {
                    const failed = (e.failed ?? 0) + (e.errors ?? 0);
                    const notRun = e.total !== null && e.passed !== null && e.passed + failed + (e.warned ?? 0) < e.total;
                    return (
                      <tr key={e.id}>
                        <td className="max-w-[280px]"><div className="truncate font-mono text-[11px]" title={e.target ?? ""}>{e.target?.split(".").slice(-2).join(".") ?? "–"}</div>
                          {e.run_id && <Link href={`/os/build?run=${e.run_id}`} className="os-faint block truncate text-[10.5px] hover:underline">{e.run_name}</Link>}</td>
                        <td><Tag tone={e.kind === "SODA" ? "blue" : "purple"}>{e.kind === "SODA" ? "DQ scan" : "QA"}</Tag></td>
                        <td className="text-right tabular-nums">{e.passed ?? "–"}{e.total ? <span className="os-faint">/{e.total}</span> : null}</td>
                        <td className="text-right tabular-nums">{failed}</td>
                        <td><Tag tone={failed ? "red" : notRun ? "neutral" : "green"}>{failed ? "Failing" : notRun ? "Not run" : "Pass"}</Tag></td>
                        <td className="os-muted whitespace-nowrap">{ago(e.started_at)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : <div className="p-5"><Empty icon={BadgeCheck} title="No quality execution yet" /></div>}
          </Card>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          <Card title="RCA assistant" subtitle={worst ? `${worst.dag_id}${worst.task_id ? ` · ${worst.task_id}` : ""}` : "Root cause analysis for runtime incidents"}
                action={worst ? <Tag tone="red">{worst.severity}</Tag> : undefined}>
            {worst ? (
              <>
                <div className="os-subtle mb-3 rounded-lg p-3 text-[12px] leading-relaxed">
                  <b>{pretty(worst.kind)}</b> first seen {ago(worst.first_seen)}{worst.assignee ? `, assigned to ${worst.assignee}` : ", unassigned"}. The RCA agent reads the task log, the DAG and the code that produced it, and drafts a resolution for approval.
                </div>
                <div className="flex flex-wrap gap-2">
                  <Action access={access} id="operateIncident" href={`/os/run/incidents/${worst.incident_id}`} variant="primary" icon={<Bot className="h-3.5 w-3.5" />} />
                  <Action access={access} id="viewOps" label="Resolutions" href="/os/run/ops/resolutions" />
                </div>
                <div className="os-divide mt-3">
                  {open.slice(0, 5).map((i) => (
                    <Link key={i.incident_id} href={`/os/run/incidents/${i.incident_id}`} className="flex items-center justify-between gap-2 py-2 hover:underline">
                      <span className="min-w-0"><span className="block truncate text-[12px] font-semibold">{i.dag_id}{i.task_id ? `.${i.task_id}` : ""}</span>
                        <span className="os-faint text-[10.5px]">{pretty(i.kind)} · {ago(i.first_seen)}</span></span>
                      <Tag tone={i.severity <= "P2" ? "red" : "amber"}>{i.severity}</Tag>
                    </Link>
                  ))}
                </div>
              </>
            ) : <Empty icon={CheckCircle2} title="Nothing to investigate" />}
          </Card>

          <Card title="Learning from operations" subtitle="Runbooks, exceptions and fixes captured from runtime" action={<Action access={access} id="viewOps" label="Known fixes" href="/os/run/ops/fixes" />}>
            {opsLearning.length ? (
              <div className="os-divide">
                {opsLearning.slice(0, 6).map((k) => (
                  <ActivityRow key={k.knowledge_id} tone={k.origin === "OPS" ? "purple" : "blue"} icon={k.knowledge_type === "AIRFLOW_RUNBOOK" ? BookOpenCheck : Sparkles}
                               title={<Link href={`/os/knowledge?id=${k.knowledge_id}`} className="hover:underline">{k.title}</Link>} sub={`${pretty(k.knowledge_type)} · ${k.domain_name}`} />
                ))}
              </div>
            ) : <Empty icon={Sparkles} title="Nothing learned from operations yet" />}
          </Card>

          <Card title="QA and UAT feedback" subtitle="Last 30 days">
            {q ? (
              <div className="grid grid-cols-3 gap-2 text-center">
                {[["QA runs", q.qa.runs], ["Tests", q.qa.tests], ["Failed", q.qa.failed]].map(([l, v]) => (
                  <div key={String(l)} className="os-subtle rounded-lg py-2"><div className="text-[18px] font-extrabold tabular-nums">{v}</div><div className="os-faint text-[10.5px]">{l}</div></div>
                ))}
              </div>
            ) : <Empty icon={TriangleAlert} title="Quality summary unavailable" />}
            <div className="os-faint mt-3 text-[11px]">Accepted fixes become versioned knowledge only after a steward approves them.</div>
          </Card>
        </div>
      </div>
    </>
  );
}
