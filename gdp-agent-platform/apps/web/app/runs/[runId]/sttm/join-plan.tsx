"use client";

import { useState, useTransition } from "react";
import { GitBranch, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { JoinPlanEditor, draftsFrom, joinPayload, type JoinGraph } from "@/components/sttm/join-plan-editor";
import { saveJoinPlan } from "./join-actions";

export type { JoinGraph, PlannedJoin } from "@/components/sttm/join-plan-editor";

export function JoinPlan({ runId, graph, tables, previewSql, canEdit }: {
  runId: string; graph: JoinGraph; tables: string[]; previewSql: string | null; canEdit: boolean;
}) {
  const [driving, setDriving] = useState(graph.driving_table ?? "");
  const [drafts, setDrafts] = useState(() => draftsFrom(graph));
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [pending, start] = useTransition();
  const save = () => start(async () => {
    setError("");
    const r = await saveJoinPlan(runId, joinPayload(driving, drafts));
    if (!r.ok) setError(r.error);
    else setSaved(true);
  });
  return (
    <Card>
      <CardHeader className="space-y-1">
        <CardTitle className="flex items-center gap-2"><GitBranch className="h-4 w-4 text-primary" /> Join plan</CardTitle>
        <CardDescription>How the source tables combine into the target, inferred from profiled keys, value ranges and overlap.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <JoinPlanEditor graph={graph} tables={tables} canEdit={canEdit} driving={driving} drafts={drafts}
                        onDriving={(t) => { setDriving(t); setSaved(false); }} onDrafts={(d) => { setDrafts(d); setSaved(false); }} />
        {canEdit && (
          <div className="flex items-center gap-3">
            <Button disabled={pending} onClick={save}>{pending && <Loader2 className="h-4 w-4 animate-spin" />} Confirm join plan</Button>
            {saved && <span className="text-sm text-success">Saved. The preview below reflects it.</span>}
            {error && <span role="alert" className="text-sm text-destructive">{error}</span>}
          </div>
        )}
        {previewSql && (
          <div>
            <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Model preview</p>
            <pre className="max-h-96 overflow-auto rounded-lg bg-muted p-3 font-mono text-[11px]">{previewSql}</pre>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
