"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, BookCheck, Loader2 } from "lucide-react";
import { FixDecision } from "@/components/ops/fix-decision";
import { FailureStatusPill } from "@/components/ops/status-pill";
import { flowLabel, score } from "@/lib/resolve-labels";
import { failureDetail, listFailures, type FailureDetail } from "../../ops/resolve-actions";

/** What automatic resolution did with this incident's latest failure: the known fix it matched, a fix waiting for
 *  human approval (decided here), or the root cause it found before opening the ticket. */
export function ResolutionPanel({ incidentId, status, canApprove, canWrite }: {
  incidentId: string; status: string; canApprove: boolean; canWrite: boolean;
}) {
  const [data, setData] = useState<FailureDetail | null | undefined>(undefined);
  const [count, setCount] = useState(0);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    (async () => {
      const list = await listFailures({ incident_id: incidentId, limit: 20 });
      if (!live) return;
      if (!list.ok) { setError(list.error); setData(null); return; }
      setCount(list.data.length);
      if (!list.data.length) { setData(null); return; }
      const detail = await failureDetail(list.data[0].failure_id);
      if (live) setData(detail.ok ? detail.data : null);
    })();
    return () => { live = false; };
  }, [incidentId]);

  if (data === undefined) return <p className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading</p>;
  const write = canWrite && (status === "RESOLVED" || data?.failure.status === "TICKETED") && (
    <Link href={`/ops/fixes/new?incident=${encodeURIComponent(incidentId)}`} className="inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline">
      <BookCheck className="h-3.5 w-3.5" />Write a known fix from this incident
    </Link>
  );
  if (!data) {
    return (
      <div className="space-y-2 text-xs text-muted-foreground" data-testid="resolution-panel">
        <p>{error ? `Not available: ${error}` : "No failed task of this incident was queued for automatic resolution."}</p>
        {write}
      </div>
    );
  }
  const f = data.failure;
  const open = data.approvals.find((a) => a.status === "OPEN");
  return (
    <div className="space-y-3 text-sm" data-testid="resolution-panel">
      <p className="flex flex-wrap items-center gap-2">
        <FailureStatusPill status={f.status} />
        <span className="text-xs text-muted-foreground">{f.task_id} · try {f.try_number ?? "-"} · scope {flowLabel(f.flow_mode)}</span>
        {count > 1 && <span className="text-xs text-muted-foreground">· {count} failures</span>}
      </p>
      {data.issue && <p className="text-xs">Known fix: <Link href={`/ops/fixes/${encodeURIComponent(data.issue.issue_id)}`} className="font-medium text-primary hover:underline">{data.issue.title}</Link> ({score(f.search_score)} match)</p>}
      {open && (canApprove
        ? <FixDecision approvalId={open.approval_id} fixTitle={data.issue?.title} approver={open.approver} compact />
        : <p className="text-xs text-muted-foreground">Waiting for {open.approver ?? "a fix approver"} to approve the fix.</p>)}
      {f.rca?.cause && <p className="text-xs"><span className="font-medium">Root cause ({f.rca.mode}):</span> {f.rca.cause}</p>}
      {f.status === "RESOLVED" && <p className="text-xs text-success">Fixed automatically{f.clear_state === "DONE" ? "; the Airflow task was cleared to run again" : ""}.</p>}
      <div className="flex flex-wrap items-center gap-4">
        <Link href={`/ops/resolutions/${encodeURIComponent(f.failure_id)}`} className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">
          Full path <ArrowRight className="h-3 w-3" />
        </Link>
        {write}
      </div>
    </div>
  );
}
