import Link from "next/link";
import { ArrowRight, BookCheck, Bot, Hand, Inbox, Search, ShieldCheck, Ticket } from "lucide-react";
import { api, ApiError, whoami } from "@/lib/api";
import { can } from "@/lib/types";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/page-header";
import { FixDecision } from "@/components/ops/fix-decision";
import { FailureStatusPill } from "@/components/ops/status-pill";
import { RecurringFixes } from "@/components/ops/recurring-fixes";
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

type FlowStep = { n: number; title: string; what: string; icon: typeof Bot; counts: { label: string; value: number; href: string; tone?: string }[] };

/** How every failed Airflow task is handled, in order, with how many failures sit at each step: what a developer
 *  needs to know first on this page. Each count opens the failures at that step. */
function HandlingFlow({ s, enabled }: { s: ResolveSummary; enabled: boolean }) {
  const st = (k: string) => s.statuses[k as keyof typeof s.statuses] ?? 0;
  const at = (status: string) => `/ops/resolutions?status=${status}`;
  const steps: FlowStep[] = [
    { n: 1, title: "Detected", what: enabled ? "A failed task is queued as soon as detection sees it." : "Queued. Automatic resolution is off, so nothing is picked up.",
      icon: Inbox, counts: [{ label: "queued", value: st("PENDING"), href: at("PENDING") }] },
    { n: 2, title: "Checked", what: "Guardrails and scope first. Old, already fixed or out-of-scope failures are left to a person.",
      icon: ShieldCheck, counts: [{ label: "working", value: st("IN_PROGRESS"), href: at("IN_PROGRESS") },
                                   { label: "left to a person", value: st("OUT_OF_SCOPE"), href: at("OUT_OF_SCOPE"), tone: "text-human" }] },
    { n: 3, title: "Known fix", what: "Searched in Enterprise Memory. A match runs, or waits for approval, then Airflow reruns the task.",
      icon: Search, counts: [{ label: "need approval", value: s.approvals_open, href: "/ops/resolutions?view=approvals", tone: "text-human" },
                              { label: "fixed (30 days)", value: st("RESOLVED"), href: at("RESOLVED"), tone: "text-success" }] },
    { n: 4, title: "No known fix", what: "The root cause is analysed and a Jira ticket opened. Fixed by hand, it becomes a known fix.",
      icon: Ticket, counts: [{ label: "ticketed (30 days)", value: st("TICKETED"), href: at("TICKETED") },
                              { label: "published fixes", value: s.fixes.PUBLISHED ?? 0, href: "/ops/fixes" }] },
  ];
  const waiting = s.approvals_open + st("OUT_OF_SCOPE");
  return (
    <section className="space-y-3" aria-label="How a failed task is handled">
      {waiting > 0 && (
        <p className="flex flex-wrap items-center gap-2 rounded-xl border border-human/30 bg-human/5 px-4 py-2.5 text-sm">
          <Hand className="h-4 w-4 text-human" /><span className="font-medium">Needs a person:</span>
          {s.approvals_open > 0 && <Link href="/ops/resolutions?view=approvals" className="text-primary hover:underline">{s.approvals_open} fix{s.approvals_open === 1 ? "" : "es"} to approve</Link>}
          {s.approvals_open > 0 && st("OUT_OF_SCOPE") > 0 && <span className="text-muted-foreground">·</span>}
          {st("OUT_OF_SCOPE") > 0 && <Link href={at("OUT_OF_SCOPE")} className="text-primary hover:underline">{st("OUT_OF_SCOPE")} failure{st("OUT_OF_SCOPE") === 1 ? "" : "s"} left to a person</Link>}
        </p>
      )}
      <ol className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {steps.map((step) => (
          <li key={step.n} className="surface flex flex-col gap-2 p-4">
            <div className="flex items-center gap-2">
              <span className="grid h-7 w-7 place-items-center rounded-lg bg-primary/10 text-primary"><step.icon className="h-3.5 w-3.5" /></span>
              <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{step.n}. {step.title}</span>
            </div>
            <p className="flex-1 text-xs leading-snug text-muted-foreground">{step.what}</p>
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {step.counts.map((c) => (
                <Link key={c.label} href={c.href} className="group">
                  <span className={cn("text-lg font-semibold tabular-nums group-hover:underline", c.tone)}>{c.value}</span>
                  <span className="ml-1 text-xs text-muted-foreground">{c.label}</span>
                </Link>
              ))}
            </div>
          </li>
        ))}
      </ol>
    </section>
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
                <span className="rounded-full bg-human/10 px-2 py-0.5 text-[11px] font-semibold text-human">{a.kind === "PROPOSAL" ? "Agent proposal" : "Fix approval"}</span>
                <Link href={`/ops/resolutions/${encodeURIComponent(a.failure_id)}`} className="font-medium hover:text-primary">{a.dag_id} · {a.task_id}</Link>
                <span className="text-xs text-muted-foreground">match {score(a.search_score)} · {a.fix_type} {a.fix_name}</span>
              </div>
              {canApprove ? <FixDecision approvalId={a.approval_id} fixTitle={a.issue_title} approver={a.approver} compact proposal={a.kind === "PROPOSAL" ? { cause: a.cause, rationale: a.rationale, steps: a.steps } : null} />
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
      : <Guardrails preference={pref.data} scopes={scopes.data ?? []} routes={routes.data ?? []} canEdit={can(me, "OPS.RESOLVE_ADMIN")}
                    queued={st("PENDING")} />;
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
                        {f.status === "OUT_OF_SCOPE" && f.detail && <p className="mt-0.5 text-xs text-human">{f.detail}</p>}
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
      {banner}
      <HandlingFlow s={s} enabled={s.enabled} />
      {view === "queue" && (s.recurring?.length ?? 0) > 0 && <RecurringFixes items={s.recurring ?? []} />}
      <Tabs view={view} approvals={s.approvals_open} />
      {body}
    </div>
  );
}
