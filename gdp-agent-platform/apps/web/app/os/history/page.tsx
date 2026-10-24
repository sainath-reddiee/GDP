import Link from "next/link";
import { CheckCircle2, History, ShieldAlert, ShieldCheck, XCircle } from "lucide-react";
import { api } from "@/lib/api";
import { stateLabel } from "@/lib/run-labels";
import { STATUS_FILTERS, parseStatusFilter } from "@/lib/run-filters";
import type { RunSummary } from "@/lib/types";
import { loadDomains, loadRuns } from "@/lib/os/data";
import { guard } from "@/lib/os/session";
import { canSee } from "@/lib/os/access";
import { AutoForm } from "@/components/os/client";
import { Btn, Card, Empty, Kpi, PageHeader, SourceError, Tag, ago, pretty, type Tone } from "@/components/os/ui";

export const dynamic = "force-dynamic";
export const metadata = { title: "Run history" };

const PAGE = 50;
type RunHistory = { attempts: number; first_score: number | null; last_score: number | null; last_status: string | null;
  last_at: string | null; reasons: string[]; failed_before?: boolean; decisions: { stage: string; decision: string; reviewer: string; at: string }[] };

const OUTCOME: Record<string, { tone: Tone; label: string }> = {
  COMPLETED: { tone: "green", label: "Completed" }, FAILED: { tone: "red", label: "Failed" }, CANCELLED: { tone: "neutral", label: "Cancelled" },
};
const outcomeOf = (r: RunSummary) => r.is_archived ? { tone: "neutral" as Tone, label: "Archived" }
  : OUTCOME[r.current_state] ?? (r.current_state === "DBT_APPROVED" ? { tone: "green" as Tone, label: "Code approved" } : { tone: "blue" as Tone, label: "In flight" });

function Score({ h }: { h: RunHistory | undefined }) {
  if (!h || !h.attempts) return <span className="os-faint">not validated</span>;
  const moved = h.first_score !== null && h.last_score !== null && h.attempts > 1 && h.first_score !== h.last_score;
  return (
    <span className="whitespace-nowrap tabular-nums" title={h.reasons.join("; ") || undefined}>
      <b className={h.last_status === "PASSED" ? "text-[var(--os-emerald)]" : "text-[var(--os-red-ink)]"}>{h.last_score === null ? "–" : `${Math.round(h.last_score)}%`}</b>
      <span className="os-faint text-[10.5px]"> · {h.attempts} attempt{h.attempts === 1 ? "" : "s"}{moved ? ` (from ${Math.round(h.first_score as number)}%)` : ""}</span>
    </span>
  );
}

/** Every run of both pipelines, finished or not: its outcome, its validation history and who decided its gates.
 *  Read only and open to everyone signed in; the run itself opens in the Workbench for those who may. */
export default async function RunHistoryPage({ searchParams }: { searchParams: { status?: string; pipeline?: string; domain?: string; q?: string; page?: string } }) {
  const access = await guard("history");
  const status = parseStatusFilter(searchParams.status ?? "all");
  const pipeline = ["SILVER", "GOLD"].includes(searchParams.pipeline ?? "") ? (searchParams.pipeline as string) : "ALL";
  const page = Math.max(0, Number.parseInt(searchParams.page ?? "0", 10) || 0);
  const query = new URLSearchParams({ status, pipeline, sort: "updated", limit: String(PAGE), offset: String(page * PAGE) });
  if (searchParams.domain) query.set("domain_id", searchParams.domain);
  if (searchParams.q?.trim()) query.set("q", searchParams.q.trim());
  const [runs, domains] = await Promise.all([loadRuns(query.toString()), loadDomains()]);
  const list = runs.data.runs;
  const history = list.length
    ? await api<{ runs: Record<string, RunHistory> }>(`/api/run-history?ids=${list.map((r) => r.run_id).join(",")}`)
        .then((r) => ({ data: r.runs, error: null as string | null })).catch((e) => ({ data: {} as Record<string, RunHistory>, error: e instanceof Error ? e.message : String(e) }))
    : { data: {} as Record<string, RunHistory>, error: null };
  const openRun = canSee(access, "build");
  const validated = list.filter((r) => history.data[r.run_id]?.attempts);
  const passed = validated.filter((r) => history.data[r.run_id]?.last_status === "PASSED").length;
  const recovered = validated.filter((r) => { const h = history.data[r.run_id]; return h?.failed_before && h.last_status === "PASSED"; }).length;
  const more = (page + 1) * PAGE < runs.data.total;
  const href = (p: number) => `/os/history?${new URLSearchParams({ ...Object.fromEntries(Object.entries(searchParams).filter(([, v]) => v)), page: String(p) })}`;

  return (
    <>
      <PageHeader eyebrow={`${access.persona.label} / Build`} title="Run history"
                  subtitle="Every run of the Silver and Gold pipelines, finished or not: how it ended, every validation attempt and who decided its gates." />
      <SourceError what="Runs" error={runs.error} />
      <SourceError what="Validation history" error={history.error} />

      <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Kpi label="Runs matching" value={runs.data.total} hint={`showing ${list.length}`} icon={History} tone="blue" />
        <Kpi label="Validated on this page" value={validated.length} hint={`${passed} passed the gate`} icon={ShieldCheck} tone="green" />
        <Kpi label="Recovered" value={recovered} hint="failed, then passed after a fix" icon={CheckCircle2} tone={recovered ? "green" : "neutral"} />
        <Kpi label="Failed or flagged" value={validated.length - passed} hint="latest attempt below the gate" icon={ShieldAlert} tone={validated.length - passed ? "red" : "green"} />
      </div>

      <Card className="mb-4" title="Filter">
        <AutoForm action="/os/history" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <div><label className="os-label" htmlFor="h-status">Outcome</label>
            <select id="h-status" name="status" defaultValue={status} className="os-input">
              {STATUS_FILTERS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
            </select></div>
          <div><label className="os-label" htmlFor="h-pipeline">Pipeline</label>
            <select id="h-pipeline" name="pipeline" defaultValue={pipeline} className="os-input">
              <option value="ALL">Silver and Gold</option><option value="SILVER">Silver</option><option value="GOLD">Gold</option>
            </select></div>
          <div><label className="os-label" htmlFor="h-domain">Domain</label>
            <select id="h-domain" name="domain" defaultValue={searchParams.domain ?? ""} className="os-input">
              <option value="">All domains</option>
              {domains.data.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
            </select></div>
          <div><label className="os-label" htmlFor="h-q">Search</label>
            <input id="h-q" name="q" defaultValue={searchParams.q ?? ""} placeholder="Run, source or table" className="os-input" /></div>
        </AutoForm>
      </Card>

      <Card title="Runs" subtitle="Newest activity first" pad={false}>
        {list.length ? (
          <div className="overflow-x-auto" data-testid="run-history">
            <table className="os-table">
              <thead><tr><th>Run</th><th>Domain</th><th>Pipeline</th><th>Outcome</th><th>Validation</th><th>Gates decided</th><th>Last activity</th></tr></thead>
              <tbody>
                {list.map((r) => {
                  const h = history.data[r.run_id];
                  const o = outcomeOf(r);
                  return (
                    <tr key={r.run_id}>
                      <td className="max-w-[280px]">
                        {openRun ? <Link href={`/os/build?run=${r.run_id}`} className="font-semibold underline-offset-2 hover:underline">{r.run_name}</Link> : <span className="font-semibold">{r.run_name}</span>}
                        <div className="os-faint truncate text-[10.5px]">{r.source_system_name ?? "no source"}{r.table_count > 1 ? ` · ${r.table_count} tables` : ""} · by {r.created_by}</div>
                      </td>
                      <td>{r.domain_name ?? <span className="os-faint">none</span>}</td>
                      <td>{pretty(r.pipeline ?? "SILVER")}</td>
                      <td><Tag tone={o.tone}>{o.label}</Tag>{o.label === "In flight" && <div className="os-faint mt-0.5 text-[10.5px]">{stateLabel(r.current_state)}</div>}</td>
                      <td><Score h={h} /></td>
                      <td className="max-w-[240px] text-[11px]">
                        {h?.decisions.length
                          ? h.decisions.slice(-3).map((d, i) => <div key={i} className="truncate">{pretty(d.stage)}: {pretty(d.decision).toLowerCase()} by {d.reviewer}</div>)
                          : <span className="os-faint">none yet</span>}
                        {h && h.decisions.length > 3 && <div className="os-faint">+{h.decisions.length - 3} earlier</div>}
                      </td>
                      <td className="os-faint whitespace-nowrap text-[11px]">{ago(r.updated_at ?? r.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : <div className="p-5"><Empty icon={XCircle} title="No run matches">Change the filter above.</Empty></div>}
        {(page > 0 || more) && (
          <div className="flex items-center justify-between gap-2 border-t border-[var(--os-line)] px-5 py-3">
            {page > 0 ? <Btn href={href(page - 1)}>Newer</Btn> : <span />}
            <span className="os-faint text-[11px]">Page {page + 1} of {Math.max(1, Math.ceil(runs.data.total / PAGE))}</span>
            {more ? <Btn href={href(page + 1)}>Older</Btn> : <span />}
          </div>
        )}
      </Card>
    </>
  );
}
