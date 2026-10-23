"use client";

import { useState, useTransition } from "react";
import Link from "next/link";
import { GitPullRequestArrow, Loader2 } from "lucide-react";
import type { MappingOverview } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { publishTableRuns } from "../pipeline-actions";

const CODE_READY = ["DBT_REVIEW", "DBT_APPROVED", "COMPLETED"];

/** A run mapped to a hub and its spokes continues in one table run per target table (hub first); once each has its
 * code, all of it is published as one pull request (files written at one fixed path get a path per table). */
export function TableRunsPanel({ runId, runs, canPublish }: {
  runId: string; runs: NonNullable<MappingOverview["table_runs"]>; canPublish: boolean;
}) {
  const [notes, setNotes] = useState<string[]>([]);
  const [pending, start] = useTransition();
  const ready = runs.filter((r) => CODE_READY.includes(r.current_state)).length;

  const publish = () => start(async () => {
    const r = await publishTableRuns(runId);
    if (!r.ok) { setNotes([r.error]); return; }
    const pr = r.data.pull_request?.url;
    setNotes([pr ? `Pull request opened: ${pr}` : `${r.data.status}: ${r.data.detail ?? "see the run's audit trail"}`,
      ...(r.data.conflicts?.length ? [`Kept once (differing copies): ${r.data.conflicts.join(", ")}`] : [])]);
  });

  return (
    <Card data-testid="table-runs">
      <CardHeader>
        <div className="flex flex-wrap items-start gap-2">
          <div className="min-w-0 flex-1">
            <CardTitle>Table runs</CardTitle>
            <CardDescription>
              The approved mapping continues in one run per target table, hub first: each generates its own transformations,
              checks and code from this mapping. {ready}/{runs.length} have their code.
            </CardDescription>
          </div>
          {canPublish && (
            <Button size="sm" onClick={publish} disabled={pending || ready < runs.length} data-testid="publish-table-runs"
              title={ready < runs.length ? "Every table run needs its code (code review or later) first" : undefined}>
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitPullRequestArrow className="h-4 w-4" />}
              Publish one pull request
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-1.5">
        {runs.map((r) => (
          <Link key={r.run_id} href={`/runs/${r.run_id}`}
            className="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm hover:bg-muted/50">
            <span className="min-w-0 flex-1 truncate font-medium">{r.model_entity ?? r.run_name}</span>
            <span className="hidden truncate font-mono text-[11px] text-muted-foreground sm:inline">{r.target_model}</span>
            <Badge variant={CODE_READY.includes(r.current_state) ? "success" : "outline"} className="text-[10px]">
              {r.current_state.replaceAll("_", " ").toLowerCase()}
            </Badge>
          </Link>
        ))}
        {notes.length > 0 && (
          <ul className="list-disc space-y-0.5 pl-4 pt-1 text-xs text-muted-foreground" role="status">
            {notes.map((n) => <li key={n} className="break-all">{n}</li>)}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
