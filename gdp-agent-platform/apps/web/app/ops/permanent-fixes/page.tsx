import Link from "next/link";
import { Bot, GitPullRequest } from "lucide-react";
import { whoami } from "@/lib/api";
import { can } from "@/lib/types";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/page-header";
import { PermanentFixStatusPill } from "@/components/ops/status-pill";
import { listPermanentFixes } from "../permanent-fix-actions";

export const metadata = { title: "Permanent fixes" };

const FILTERS = ["", "DRAFTED", "ACCEPTED", "PUBLISHED", "MERGED", "UNAVAILABLE", "REJECTED", "CLOSED"];
const LABEL: Record<string, string> = { DRAFTED: "Waiting for review", ACCEPTED: "Accepted", PUBLISHED: "Pull request open",
  MERGED: "Merged", UNAVAILABLE: "Needs mapping", REJECTED: "Rejected", CLOSED: "Closed" };

/** Permanent fixes: code changes that remove the cause of a known fix that keeps firing. The agent drafts one when a
 *  fix runs often; a second person accepts it before a draft pull request opens. */
export default async function PermanentFixesPage({ searchParams }: { searchParams?: { status?: string } }) {
  const me = await whoami();
  const header = (
    <PageHeader eyebrow="Operations" title="Permanent fixes"
                description="When a known fix keeps repairing the same failure, the agent drafts the code change that removes its cause. A second person accepts it; only then does a draft pull request open." />
  );
  if (!can(me, "OPS.VIEW")) return <div className="space-y-5">{header}<p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">Permanent fixes need the OPS.VIEW privilege.</p></div>;
  const status = FILTERS.includes(searchParams?.status ?? "") ? searchParams?.status ?? "" : "";
  const r = await listPermanentFixes({ status, limit: 200 });
  const items = r.ok ? r.data.items : [];
  return (
    <div className="space-y-5">
      {header}
      {!r.ok ? <p role="alert" className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{r.error}</p>
        : !r.data.available ? <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">Permanent fixes need the latest deploy (migration V066).</p> : (
        <>
          <div className="flex flex-wrap gap-1.5" aria-label="Filter by status">
            {FILTERS.map((f) => (
              <Link key={f || "all"} href={f ? `/ops/permanent-fixes?status=${f}` : "/ops/permanent-fixes"}
                    className={cn("rounded-full border px-2.5 py-1 text-xs font-medium", status === f ? "border-primary bg-primary/10 text-primary" : "hover:bg-muted")}>
                {f ? LABEL[f] : "All"}
              </Link>
            ))}
          </div>
          {items.length === 0 ? (
            <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
              No permanent fixes{status ? " with this status" : " yet"}. The agent drafts one when a published known fix runs three or more times in 30 days,
              and anyone allowed can ask for one from the known fix&apos;s page.
            </p>
          ) : (
            <div className="surface overflow-x-auto">
              <table className="w-full text-sm" data-testid="permanent-fix-table">
                <thead className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
                  <tr><th className="px-4 py-2 font-medium">Change</th><th className="px-3 py-2 font-medium">Known fix</th>
                    <th className="px-3 py-2 font-medium">Pipeline</th><th className="px-3 py-2 font-medium">Drafted</th>
                    <th className="px-3 py-2 font-medium">Status</th></tr>
                </thead>
                <tbody className="divide-y">
                  {items.map((f) => (
                    <tr key={f.fix_id} className="align-top hover:bg-muted/30">
                      <td className="px-4 py-2.5">
                        <Link href={`/ops/permanent-fixes/${encodeURIComponent(f.fix_id)}`} className="font-medium hover:text-primary">{f.title ?? "Permanent fix"}</Link>
                        <p className="line-clamp-2 text-xs text-muted-foreground">{f.status === "UNAVAILABLE" ? f.unavailable_reason : f.summary}</p>
                      </td>
                      <td className="px-3 py-2.5 text-xs">
                        <Link href={`/ops/fixes/${encodeURIComponent(f.issue_id)}`} className="hover:text-primary">{f.issue_title ?? f.issue_id}</Link>
                      </td>
                      <td className="px-3 py-2.5 text-xs">{f.dag_id ?? "-"}{f.task_id ? ` · ${f.task_id}` : ""}</td>
                      <td className="px-3 py-2.5 text-xs">
                        <span className="inline-flex items-center gap-1">{f.trigger_kind === "AUTO" && <Bot className="h-3 w-3 text-agent" />}{f.trigger_kind === "AUTO" ? "by the agent" : f.drafted_by}</span>
                        <p className="text-muted-foreground">{String(f.created_at).slice(0, 10)}</p>
                      </td>
                      <td className="px-3 py-2.5 text-xs">
                        <PermanentFixStatusPill status={f.status} />
                        {f.pr_url && <a href={f.pr_url} target="_blank" rel="noreferrer" className="mt-1 flex items-center gap-1 text-primary hover:underline"><GitPullRequest className="h-3 w-3" />Pull request</a>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
