import Link from "next/link";
import { ArrowRight, BookCheck, Bot, CircleCheck, ShieldCheck, Ticket, UserCheck } from "lucide-react";
import { api, ApiError, whoami } from "@/lib/api";
import { can } from "@/lib/types";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/page-header";
import { FixDecision } from "@/components/ops/fix-decision";
import { FailureStatusPill } from "@/components/ops/status-pill";
import { FAILURE_STATUS, flowLabel, score } from "@/lib/resolve-labels";
import type { FailureRow, FixApproval, Preference, ResolveSummary, Route, Scope } from "../resolve-actions";
import { Guardrails } from "./guardrails";

type View = "queue" | "approvals" | "guardrails";
const ORDER = ["AWAITING_APPROVAL", "IN_PROGRESS", "PENDING", "TICKETED", "RESOLVED", "REJECTED", "OUT_OF_SCOPE"];

async function load<T>(fn: () => Promise<T>): Promise<{ data: T; error: null } | { data: null; error: string }> {
  try {
    return { data: await fn(), error: null };
  } catch (e) {
    if (e instanceof ApiError) return { data: null, error: e.status === 404 ? "Automatic resolution needs the latest deploy (migration V062)." : e.message };
    throw e;
  }
}

function Tabs({ view, approvals }: { view: View; approvals: number }) {
  const tab = (id: View, label: string, extra?: number) => (
    <Link href={id === "queue" ? "/ops/resolutions" : `/ops/resolutions?view=${id}`} aria-current={view === id ? "page" : undefined}
          className={cn("-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium",
            view === id ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}>
      {label}{extra ? <span className="rounded-full bg-human px-1.5 text-[10px] font-semibold text-white">{extra}</span> : null}
    </Link>
  );
  return (
    <nav className="flex gap-1 border-b" aria-label="Resolution views">
      {tab("queue", "Failures")}
      {tab("approvals", "Approvals", approvals)}
      {tab("guardrails", "Guardrails")}
    </nav>
  );
}

function Stat({ label, value, icon: Icon, tone }: { label: string; value: number; icon: typeof Bot; tone: string }) {
  return (
    <div className="surface flex items-center gap-3 px-4 py-3">
      <span className={cn("grid h-9 w-9 place-items-center rounded-lg", tone)}><Icon className="h-4 w-4" /></span>
      <div><p className="text-xl font-semibold tabular-nums">{value}</p><p className="text-xs text-muted-foreground">{label}</p></div>
    </div>
  );
}

export default async function ResolutionsPage({ searchParams }: { searchParams?: { view?: string; status?: string; dag?: string } }) {
  const me = await whoami();
  const header = (
    <PageHeader eyebrow="Operations" title="Resolutions"
                description="Every failed Airflow task, triaged in Snowflake: the guardrails are read first, then a published fix runs (or waits for human approval), or the cause is analysed and a Jira ticket opened."
                actions={<Link href="/ops/fixes" className="inline-flex items-center gap-1.5 rounded-lg border bg-card px-3 py-1.5 text-sm font-medium shadow-xs hover:bg-muted"><BookCheck className="h-4 w-4" />Known fixes</Link>} />
  );
  if (!can(me, "OPS.VIEW")) {
    return <div className="space-y-5">{header}<p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">Resolutions need the OPS.VIEW privilege.</p></div>;
  }
  const sp = searchParams ?? {};
  const view: View = sp.view === "approvals" || sp.view === "guardrails" ? sp.view : "queue";
  const summary = await load(() => api<ResolveSummary>("/api/ops/resolve/summary"));
  if (summary.error !== null) {
    return <div className="space-y-5">{header}<p role="alert" className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{summary.error}</p></div>;
  }
  const s = summary.data;
  const st = (k: string) => s.statuses[k as keyof typeof s.statuses] ?? 0;
  const banner = !s.enabled && (
    <p className="flex flex-wrap items-center gap-2 rounded-xl border border-dashed px-4 py-3 text-sm text-muted-foreground">
      <ShieldCheck className="h-4 w-4" />Automatic resolution is off: failures are queued but nothing is claimed.
      {can(me, "OPS.RESOLVE_ADMIN") && <Link href="/ops/resolutions?view=guardrails" className="font-medium text-primary hover:underline">Turn it on in Guardrails</Link>}
    </p>
  );

  let body: React.ReactNode;
  if (view === "approvals") {
    const list = await load(() => api<FixApproval[]>("/api/ops/resolve/approvals?scope=open"));
    const canApprove = can(me, "OPS.FIX_APPROVE");
    body = list.error !== null ? <p role="alert" className="text-sm text-destructive">{list.error}</p> : list.data.length === 0
      ? <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">No fix waits for approval.</p>
      : (
        <ul className="space-y-3" data-testid="fix-approvals">
          {list.data.map((a) => (
            <li key={a.approval_id} className="surface space-y-2 p-4">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="rounded-full bg-human/10 px-2 py-0.5 text-[11px] font-semibold text-human">Fix approval</span>
                <Link href={`/ops/resolutions/${encodeURIComponent(a.failure_id)}`} className="font-medium hover:text-primary">{a.dag_id} · {a.task_id}</Link>
                <span className="text-xs text-muted-foreground">match {score(a.search_score)} · {a.fix_type} {a.fix_name}</span>
              </div>
              {canApprove ? <FixDecision approvalId={a.approval_id} fixTitle={a.issue_title} approver={a.approver} compact />
                : <p className="text-xs text-muted-foreground">Waiting for {a.approver ?? "a fix approver"} (OPS.FIX_APPROVE).</p>}
            </li>
          ))}
        </ul>
      );
  } else if (view === "guardrails") {
    const [pref, scopes, routes] = await Promise.all([
      load(() => api<Preference>("/api/ops/resolve/preference")),
      load(() => api<Scope[]>("/api/ops/resolve/scopes")),
      load(() => api<Route[]>("/api/ops/resolve/routes")),
    ]);
    body = pref.error !== null ? <p role="alert" className="text-sm text-destructive">{pref.error}</p>
      : <Guardrails preference={pref.data} scopes={scopes.data ?? []} routes={routes.data ?? []} canEdit={can(me, "OPS.RESOLVE_ADMIN")} />;
  } else {
    const status = sp.status && FAILURE_STATUS[sp.status] ? sp.status : "";
    const q = new URLSearchParams({ limit: "100", ...(status ? { status } : {}), ...(sp.dag ? { dag: sp.dag } : {}) });
    const list = await load(() => api<FailureRow[]>(`/api/ops/resolve/failures?${q}`));
    body = (
      <div className="space-y-3">
        <div className="flex flex-wrap gap-1.5" aria-label="Filter by status">
          <Link href="/ops/resolutions" className={cn("rounded-full border px-2.5 py-1 text-xs font-medium", !status ? "border-primary bg-primary/10 text-primary" : "hover:bg-muted")}>All</Link>
          {ORDER.map((k) => (
            <Link key={k} href={`/ops/resolutions?status=${k}`}
                  className={cn("rounded-full border px-2.5 py-1 text-xs font-medium", status === k ? "border-primary bg-primary/10 text-primary" : "hover:bg-muted")}>
              {FAILURE_STATUS[k].label} <span className="tabular-nums text-muted-foreground">{st(k)}</span>
            </Link>
          ))}
        </div>
        {list.error !== null ? <p role="alert" className="text-sm text-destructive">{list.error}</p> : list.data.length === 0
          ? <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">No failures{status ? " in this state" : " yet"}. Failed Airflow tasks appear here as soon as incident detection sees them.</p>
          : (
            <div className="surface overflow-x-auto">
              <table className="w-full text-sm" data-testid="failure-table">
                <thead className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
                  <tr><th className="px-4 py-2 font-medium">Failure</th><th className="px-3 py-2 font-medium">Status</th><th className="px-3 py-2 font-medium">Scope</th>
                    <th className="px-3 py-2 font-medium">Known fix</th><th className="px-3 py-2 font-medium">Ticket</th><th className="px-3 py-2 font-medium">Seen</th><th /></tr>
                </thead>
                <tbody className="divide-y">
                  {list.data.map((f) => (
                    <tr key={f.failure_id} className="align-top hover:bg-muted/30">
                      <td className="max-w-[28rem] px-4 py-2.5">
                        <p className="font-medium">{f.dag_id} <span className="text-muted-foreground">·</span> {f.task_id}</p>
                        <p className="truncate text-xs text-muted-foreground" title={f.exception_text ?? ""}>{f.exception_class ?? "Error"}: {f.exception_text ?? "no error text"}</p>
                      </td>
                      <td className="px-3 py-2.5"><FailureStatusPill status={f.status} /></td>
                      <td className="px-3 py-2.5 text-xs">{f.flow_mode ? flowLabel(f.flow_mode) : "-"}</td>
                      <td className="px-3 py-2.5 text-xs">{f.issue_title ? <>{f.issue_title} <span className="text-muted-foreground">({score(f.search_score)})</span></> : <span className="text-muted-foreground">-</span>}</td>
                      <td className="px-3 py-2.5 text-xs">{f.ticket_id ?? f.jira_key ?? <span className="text-muted-foreground">-</span>}</td>
                      <td className="whitespace-nowrap px-3 py-2.5 text-xs text-muted-foreground">{f.created_at?.slice(0, 16).replace("T", " ")}</td>
                      <td className="px-3 py-2.5"><Link href={`/ops/resolutions/${encodeURIComponent(f.failure_id)}`} aria-label={`Open ${f.dag_id} ${f.task_id}`} className="text-primary"><ArrowRight className="h-4 w-4" /></Link></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {header}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Fixed by a known fix (30 days)" value={st("RESOLVED")} icon={CircleCheck} tone="bg-success/10 text-success" />
        <Stat label="Waiting for approval" value={s.approvals_open} icon={UserCheck} tone="bg-human/10 text-human" />
        <Stat label="Analysed and ticketed (30 days)" value={st("TICKETED")} icon={Ticket} tone="bg-amber-100 text-amber-800" />
        <Stat label="Published known fixes" value={s.fixes.PUBLISHED ?? 0} icon={BookCheck} tone="bg-agent/10 text-agent" />
      </div>
      {banner}
      <Tabs view={view} approvals={s.approvals_open} />
      {body}
    </div>
  );
}
