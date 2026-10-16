import Link from "next/link";
import type { ReactNode } from "react";
import { BookCheck, Bot, CircleCheck, CircleDashed, CircleX, Search, ShieldCheck, Ticket, UserCheck } from "lucide-react";
import { api, ApiError, whoami } from "@/lib/api";
import { can } from "@/lib/types";
import { cn } from "@/lib/utils";
import { FixDecision } from "@/components/ops/fix-decision";
import { FailureStatusPill } from "@/components/ops/status-pill";
import { flowLabel, score } from "@/lib/resolve-labels";
import type { FailureDetail } from "../../resolve-actions";

function Crumb() {
  return <p className="eyebrow"><Link href="/ops/resolutions" className="hover:underline">Resolutions</Link></p>;
}

type StepTone = "done" | "human" | "fail" | "skip";
function Step({ icon: Icon, title, tone, children }: { icon: typeof Bot; title: string; tone: StepTone; children: ReactNode }) {
  const color = { done: "bg-agent/10 text-agent", human: "bg-human/10 text-human", fail: "bg-rose-100 text-rose-700", skip: "bg-muted text-muted-foreground" }[tone];
  return (
    <li className="relative flex gap-3 pb-5 last:pb-0">
      <span className={cn("z-10 grid h-8 w-8 shrink-0 place-items-center rounded-full", color)}><Icon className="h-4 w-4" /></span>
      <div className="min-w-0 pt-1"><p className="text-sm font-semibold">{title}</p><div className="mt-0.5 text-xs text-muted-foreground">{children}</div></div>
    </li>
  );
}

/** One failure, the way the engine handled it (slide 3): guardrails, search, then fix, approval or ticket. */
export default async function FailurePage({ params }: { params: { id: string } }) {
  const me = await whoami();
  if (!can(me, "OPS.VIEW")) return <div className="space-y-3"><Crumb /><p className="text-sm text-muted-foreground">Resolutions need the OPS.VIEW privilege.</p></div>;
  let data: FailureDetail;
  try {
    data = await api<FailureDetail>(`/api/ops/resolve/failures/${encodeURIComponent(params.id)}`);
  } catch (e) {
    if (e instanceof ApiError) return <div className="space-y-3"><Crumb /><p role="alert" className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{e.message}</p></div>;
    throw e;
  }
  const { failure: f, issue, runs, steps, approvals } = data;
  const open = approvals.find((a) => a.status === "OPEN");
  const rca = f.rca;
  const claimed = !["PENDING"].includes(f.status);
  const inScope = f.flow_mode && f.flow_mode !== "DISABLED";

  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <Crumb />
        <h1 className="flex flex-wrap items-center gap-2 text-xl font-semibold">{f.dag_id} · {f.task_id} <FailureStatusPill status={f.status} /></h1>
        <p className="text-sm text-muted-foreground">
          Run {f.run_id}{f.try_number ? `, try ${f.try_number}` : ""} · {f.env_id}
          {f.incident_id && <> · <Link href={`/incidents/${encodeURIComponent(f.incident_id)}`} className="text-primary hover:underline">incident</Link></>}
          {f.ticket_id && <> · ticket {f.ticket_id}</>}
        </p>
      </div>

      {open && can(me, "OPS.FIX_APPROVE") && <FixDecision approvalId={open.approval_id} fixTitle={issue?.title} approver={open.approver} />}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <section className="surface p-4">
          <h2 className="mb-4 text-sm font-semibold">What the engine did</h2>
          <ol className="relative before:absolute before:left-4 before:top-2 before:h-[calc(100%-1rem)] before:w-px before:bg-border">
            <Step icon={Bot} title="Failure queued" tone="done">
              {f.exception_class ?? "Error"}: {f.exception_text ?? "no error text"}
              {f.query_id && <> · query {f.query_id}</>}{f.dbt_node_id && <> · dbt {f.dbt_node_id}</>}
            </Step>
            <Step icon={ShieldCheck} title="Guardrails read" tone={!claimed ? "skip" : inScope ? "done" : "skip"}>
              {!claimed ? "Not claimed yet: automatic resolution is off or the worker has not run." :
                f.status === "OUT_OF_SCOPE" ? (f.detail ?? "Out of scope.") : `Scope: ${flowLabel(f.flow_mode)}.`}
            </Step>
            {claimed && inScope && (
              <Step icon={Search} title="Known fix search" tone={f.flow_mode === "TICKET_ONLY" ? "skip" : f.issue_id ? "done" : "skip"}>
                {f.flow_mode === "TICKET_ONLY" ? "Skipped: the pipeline is set to ticket only."
                  : issue ? <>Matched <Link href={`/ops/fixes/${encodeURIComponent(issue.issue_id)}`} className="text-primary hover:underline">{issue.title}</Link> ({score(f.search_score)}).</>
                    : `No published fix scored high enough (best ${score(f.search_score)}). A miss never invents a fix.`}
              </Step>
            )}
            {approvals.map((a) => (
              <Step key={a.approval_id} icon={UserCheck} title={a.status === "OPEN" ? "Waiting for human approval" : `Approval ${a.status.toLowerCase()}`}
                    tone={a.status === "REJECTED" ? "fail" : "human"}>
                {a.status === "OPEN" ? `Routed to ${a.approver ?? "any fix approver"}.` : `${a.decided_by ?? ""}${a.decision_note ? `: ${a.decision_note}` : ""}`}
              </Step>
            ))}
            {runs.map((r) => (
              <Step key={r.resolution_id} icon={r.outcome === "SUCCESS" ? CircleCheck : r.outcome === "TICKETED" ? Ticket : CircleX}
                    title={r.outcome === "SUCCESS" ? `Fix ran: ${r.fix_name ?? ""}` : r.outcome === "TICKETED" ? "Root cause analysed, ticket opened" : `Fix did not run to the end${r.fix_name ? `: ${r.fix_name}` : ""}`}
                    tone={r.outcome === "SUCCESS" ? "done" : r.outcome === "TICKETED" ? "human" : "fail"}>
                {r.actor} · {r.started_at?.slice(0, 16).replace("T", " ")}
                {typeof r.detail?.error === "string" && <span className="block text-destructive">{r.detail.error}</span>}
                {typeof r.detail?.reason === "string" && <span className="block">{r.detail.reason}</span>}
                {steps.filter((s) => s.resolution_id === r.resolution_id).map((s) => (
                  <span key={s.step_no} className="block">Step {s.step_no}: {s.action} · {s.status.toLowerCase()}</span>
                ))}
              </Step>
            ))}
            {f.status === "RESOLVED" && (
              <Step icon={f.clear_state === "DONE" ? CircleCheck : CircleDashed} title="Airflow task" tone={f.clear_state === "FAILED" ? "fail" : "done"}>
                {f.clear_state === "DONE" ? f.clear_detail : f.clear_state === "SKIPPED" ? "Not cleared (switched off in the guardrails)."
                  : f.clear_state === "FAILED" ? `Clearing failed: ${f.clear_detail}` : "Being cleared so Airflow runs it again."}
              </Step>
            )}
          </ol>
        </section>

        <div className="space-y-4">
          {rca && (
            <section className="surface p-4" data-testid="rca">
              <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">Root cause <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold uppercase text-muted-foreground">{rca.mode}</span></h2>
              <p className="text-sm">{rca.cause}</p>
              {!!rca.evidence?.length && <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-muted-foreground">{rca.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>}
              {!!rca.path?.length && <p className="mt-2 text-xs"><span className="font-medium">Upstream:</span> {rca.path.join(" ← ")}</p>}
              {!!rca.fix_steps?.length && <ol className="mt-2 list-decimal space-y-1 pl-4 text-xs">{rca.fix_steps.map((s, i) => <li key={i}>{s}</li>)}</ol>}
              {rca.agent_note && <p className="mt-2 text-[11px] text-muted-foreground">RCA agent not used: {rca.agent_note}</p>}
            </section>
          )}
          {f.status === "TICKETED" && f.incident_id && can(me, "OPS.OPERATE") && (
            <section className="surface space-y-2 p-4">
              <h2 className="text-sm font-semibold">Close the loop</h2>
              <p className="text-xs text-muted-foreground">When the ticket is solved, write the fix down. Once another person publishes it, the same failure is fixed automatically next time.</p>
              <Link href={`/ops/fixes/new?incident=${encodeURIComponent(f.incident_id)}`} className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground hover:bg-primary/90">
                <BookCheck className="h-3.5 w-3.5" />Write a known fix
              </Link>
            </section>
          )}
          <section className="surface p-4 text-xs">
            <h2 className="mb-2 text-sm font-semibold">Details</h2>
            <dl className="grid grid-cols-[8rem_minmax(0,1fr)] gap-x-3 gap-y-1">
              <dt className="text-muted-foreground">Signature</dt><dd className="break-all font-mono">{f.error_signature}</dd>
              <dt className="text-muted-foreground">Claimed by</dt><dd>{f.claimed_by ?? "-"}</dd>
              <dt className="text-muted-foreground">Last change</dt><dd>{f.detail ?? "-"}</dd>
              <dt className="text-muted-foreground">Queued</dt><dd>{f.created_at?.slice(0, 19).replace("T", " ")}</dd>
            </dl>
          </section>
        </div>
      </div>
    </div>
  );
}
