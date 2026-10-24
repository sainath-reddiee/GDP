import Link from "next/link";
import { AlertTriangle, Bot, FileCode2, GitPullRequest } from "lucide-react";
import { api, ApiError, whoami } from "@/lib/api";
import { can } from "@/lib/types";
import { PermanentFixStatusPill } from "@/components/ops/status-pill";
import { UnifiedDiff } from "@/app/qa/cases/[id]/case-kit";
import type { PermanentFix } from "../../permanent-fix-actions";
import { PermanentFixReview } from "./review";

function Crumb() {
  return <p className="eyebrow"><Link href="/os/run/ops/permanent-fixes" className="hover:underline">Permanent fixes</Link></p>;
}

const when = (v: string | null | undefined) => (v ? String(v).slice(0, 16).replace("T", " ") : "");

/** One permanent fix: why it exists, the files and the diff, what could not be used, and the review. */
export default async function PermanentFixPage({ params }: { params: { id: string } }) {
  const me = await whoami();
  if (!can(me, "OPS.VIEW")) return <div className="space-y-3"><Crumb /><p className="text-sm text-muted-foreground">Permanent fixes need the OPS.VIEW privilege.</p></div>;
  let f: PermanentFix;
  try {
    f = await api<PermanentFix>(`/api/ops/permanent-fixes/${encodeURIComponent(params.id)}`);
  } catch (e) {
    if (e instanceof ApiError) return <div className="space-y-3"><Crumb /><p role="alert" className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{e.message}</p></div>;
    throw e;
  }
  const ctx = f.context ?? {};
  const stats = ctx.stats;
  return (
    <div className="space-y-4">
      <Crumb />
      <h1 className="flex flex-wrap items-center gap-2 text-xl font-semibold">{f.title ?? "Permanent fix"} <PermanentFixStatusPill status={f.status} /></h1>
      <p className="text-sm text-muted-foreground">
        For the known fix <Link href={`/os/run/ops/fixes/${encodeURIComponent(f.issue_id)}`} className="text-primary hover:underline">{f.issue.title ?? f.issue_id}</Link>
        {f.dag_id ? ` · ${f.dag_id}${f.task_id ? `.${f.task_id}` : ""}` : ""} · drafted {f.trigger_kind === "AUTO" ? "by the agent" : `by ${f.drafted_by}`} {when(f.created_at)}
        {f.decided_by ? ` · ${f.status === "REJECTED" ? "rejected" : "accepted"} by ${f.decided_by}` : ""}
      </p>
      {stats && (
        <p className="flex items-center gap-2 text-sm"><Bot className="h-4 w-4 text-agent" />
          The known fix ran {stats.runs} times in 30 days on {stats.dags} DAG{stats.dags === 1 ? "" : "s"}{ctx.note ? `: ${ctx.note}` : ""}.
        </p>
      )}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-4">
          {f.status === "UNAVAILABLE" ? (
            <section className="surface space-y-2 p-4" data-testid="permanent-fix-missing">
              <h2 className="flex items-center gap-2 text-sm font-semibold"><AlertTriangle className="h-4 w-4 text-amber-600" />What the platform needs</h2>
              <p className="text-sm">{f.unavailable_reason}</p>
              {(ctx.missing ?? []).length > 1 && <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">{ctx.missing!.map((m) => <li key={m}>{m}</li>)}</ul>}
            </section>
          ) : (
            <>
              {(f.summary || f.rationale) && (
                <section className="surface space-y-2 p-4">
                  <h2 className="text-sm font-semibold">The change</h2>
                  {f.summary && <p className="text-sm">{f.summary}</p>}
                  {f.rationale && <p className="text-xs text-muted-foreground">Why: {f.rationale}</p>}
                  {f.confidence != null && <p className="text-xs text-muted-foreground">Model confidence {Math.round(f.confidence * 100)}%</p>}
                  {f.instructions && <p className="text-xs text-muted-foreground">Asked for: {f.instructions}</p>}
                </section>
              )}
              {(ctx.warnings ?? []).length > 0 && (
                <ul className="space-y-1 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
                  {ctx.warnings!.map((w) => <li key={w}>{w}</li>)}
                </ul>
              )}
              <section className="surface space-y-3 p-4">
                <h2 className="flex items-center gap-2 text-sm font-semibold"><FileCode2 className="h-4 w-4" />Files</h2>
                <ul className="space-y-1 text-xs">
                  {(f.files ?? []).map((x) => <li key={x.path}><code className="rounded bg-muted px-1">{x.path}</code> <span className="text-muted-foreground">{x.change}</span></li>)}
                </ul>
                {f.diff ? <UnifiedDiff diff={f.diff} /> : null}
                {f.base_branch && <p className="text-xs text-muted-foreground">Drafted against {f.base_branch}{f.indexed_commit ? ` at ${f.indexed_commit.slice(0, 8)}` : ""}. Publishing checks the files have not changed since.</p>}
              </section>
            </>
          )}
          {(ctx.skipped ?? []).length > 0 && (
            <section className="surface p-4">
              <h2 className="mb-2 text-sm font-semibold">Files not used</h2>
              <ul className="space-y-1 text-xs">{ctx.skipped!.map((s) => <li key={s.path}><code className="rounded bg-muted px-1">{s.path}</code> <span className="text-muted-foreground">{s.reason}</span></li>)}</ul>
            </section>
          )}
        </div>
        <div className="space-y-4">
          <PermanentFixReview fix={f} canDecide={can(me, "OPS.FIX_PUBLISH")} canPublish={can(me, "DBT.EDIT")} />
          {f.pr_url && (
            <section className="surface space-y-1 p-4 text-xs">
              <h2 className="flex items-center gap-2 text-sm font-semibold"><GitPullRequest className="h-4 w-4" />Pull request</h2>
              <a href={f.pr_url} target="_blank" rel="noreferrer" className="text-primary hover:underline">#{f.pr_number ?? ""} on {f.branch}</a>
              <p className="text-muted-foreground">{f.pr_state ?? ""}{f.published_by ? ` · opened by ${f.published_by} ${when(f.published_at)}` : ""}{f.merged_sha ? ` · merged ${f.merged_sha.slice(0, 8)}` : ""}</p>
            </section>
          )}
          {f.decision_note && <p className="surface p-4 text-xs"><span className="font-semibold">Reviewer&apos;s note:</span> {f.decision_note}</p>}
        </div>
      </div>
    </div>
  );
}
