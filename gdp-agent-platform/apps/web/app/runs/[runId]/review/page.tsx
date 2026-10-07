import Link from "next/link";
import { CheckCircle2, CircleDashed, ExternalLink, XCircle } from "lucide-react";
import { api, getRun } from "@/lib/api";
import { StageGate } from "@/components/stage-gate";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { RunConsole } from "../console";
import type { DbtGeneration } from "../dbt/dbt-types";

type Row = { label: string; href: string; done: boolean; failed?: boolean; detail: string };

/** Code review is the human sign-off after the three parallel lanes. It summarises them and links to each one;
 *  the dbt code itself is reviewed on the dbt page (not repeated here). */
export default async function ReviewPage({ params }: { params: { runId: string } }) {
  const [state, dbt, validation] = await Promise.all([
    getRun(params.runId),
    api<{ generation: DbtGeneration | null; artifacts: unknown[];
          branch: { base_branch?: string; cut_branch?: string; repo?: string } | null;
          publication?: { pull_request?: { url?: string | null } | null } | null;
        }>(`/api/runs/${params.runId}/dbt`).catch(() => null),
    api<{ runs: { validation_id: string; validation_type: string; status: string; error_count: number; warning_count?: number }[] }>(
      `/api/runs/${params.runId}/validation`,
    ).catch(() => ({ runs: [] })),
  ]);
  const lanes = state.lanes;
  const checks = validation.runs.filter((r) => r.validation_type !== "SUMMARY");
  const failedChecks = checks.filter((r) => r.status === "FAILED");
  const prUrl = dbt?.publication?.pull_request?.url;
  const rows: Row[] = [
    { label: "QA tests", href: `/runs/${params.runId}/qa`, done: !!lanes?.QA.done,
      failed: lanes?.QA.status === "BLOCKED", detail: lanes?.QA.note ?? "sign off on the QA page" },
    { label: "Data Quality", href: `/runs/${params.runId}/soda`, done: !!lanes?.SODA.done,
      detail: lanes?.SODA.note ?? "confirm the checks" },
    { label: "dbt project", href: `/runs/${params.runId}/dbt`, done: !!dbt?.generation,
      detail: dbt?.generation
        ? `${dbt.artifacts.length} files${dbt.branch?.cut_branch ? ` · branch ${dbt.branch.cut_branch}` : ""}${prUrl ? " · PR opened" : ""}`
        : "not generated" },
    { label: "Validation", href: `/runs/${params.runId}/validation`, done: !!lanes?.VALIDATION.done,
      failed: failedChecks.length > 0,
      detail: checks.length
        ? `${checks.length - failedChecks.length}/${checks.length} checks passed${failedChecks.length ? ` · failed: ${failedChecks.map((c) => c.validation_type).join(", ")}` : ""}`
        : "not run" },
  ];
  const ready = lanes?.gate.ready ?? false;

  return (
    <StageGate state={state} stage="REVIEW">
      <Card>
        <CardHeader>
          <CardTitle>Code review</CardTitle>
          <CardDescription>
            The final human sign-off once QA, Data Quality and the validated dbt project are all done. Approval records
            the reviewer; it does not deploy, merge a PR or enable a pipeline.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <ul className="divide-y rounded-xl border">
            {rows.map((r) => (
              <li key={r.label} className="flex items-center gap-3 px-3 py-2.5">
                {r.done ? <CheckCircle2 className="h-4 w-4 text-success" />
                  : r.failed ? <XCircle className="h-4 w-4 text-destructive" />
                  : <CircleDashed className="h-4 w-4 text-warning" />}
                <span className="w-32 font-medium">{r.label}</span>
                <span className={cn("flex-1 text-muted-foreground", r.failed && "text-destructive")}>{r.detail}</span>
                <Link href={r.href} className="text-xs font-medium text-primary hover:underline">Open</Link>
              </li>
            ))}
          </ul>
          {prUrl && (
            <a href={prUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
              Review the pull request <ExternalLink className="h-3.5 w-3.5" />
            </a>
          )}
          {!ready && lanes && (
            <p className="rounded-lg border border-warning/30 bg-warning/10 p-3 text-warning">
              Approval is blocked until: {lanes.gate.waiting_on.join("; ")}.
            </p>
          )}
        </CardContent>
      </Card>
      <RunConsole state={state} />
    </StageGate>
  );
}
