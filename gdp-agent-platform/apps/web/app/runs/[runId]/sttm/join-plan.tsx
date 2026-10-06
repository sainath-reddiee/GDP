"use client";

import { useState, useTransition } from "react";
import { AlertTriangle, ArrowRight, CheckCircle2, GitBranch, Loader2, Plus, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { saveJoinPlan } from "./join-actions";

export type PlannedJoin = {
  left_table: string; right_table: string; join_type: "LEFT" | "INNER"; keys: string[]; condition: string;
  cardinality: string; confidence: number | null; reasoning: string; fan_out?: boolean; source: string;
  status: "SUGGESTED" | "CONFIRMED";
  alternatives?: { left_table: string; keys: string[]; condition: string; cardinality: string; confidence: number; source: string }[];
};

export type JoinGraph = {
  driving_table: string | null; joins: PlannedJoin[]; unreachable: string[];
  ambiguities: { table: string; options: number; message: string }[]; edited?: boolean;
};

type Draft = { left_table: string; right_table: string; keys: string; join_type: "LEFT" | "INNER"; cardinality?: string; remove?: boolean };

export function JoinPlan({ runId, graph, tables, previewSql, canEdit }: {
  runId: string; graph: JoinGraph; tables: string[]; previewSql: string | null; canEdit: boolean;
}) {
  const [driving, setDriving] = useState(graph.driving_table ?? "");
  const [drafts, setDrafts] = useState<Draft[]>(() => graph.joins.map((j) => ({
    left_table: j.left_table, right_table: j.right_table, keys: j.keys.join(", "), join_type: j.join_type, cardinality: j.cardinality,
  })));
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [pending, start] = useTransition();
  const ambiguous = new Set(graph.ambiguities.map((a) => a.table));
  const original = new Map(graph.joins.map((j) => [j.right_table, j]));

  const update = (i: number, patch: Partial<Draft>) => {
    setSaved(false);
    setDrafts((d) => d.map((x, k) => (k === i ? { ...x, ...patch } : x)));
  };
  const save = () => start(async () => {
    setError("");
    const r = await saveJoinPlan(runId, {
      driving_table: driving || null,
      joins: drafts.map((d) => ({
        left_table: d.left_table, right_table: d.right_table, join_type: d.join_type, cardinality: d.cardinality ?? null,
        keys: d.keys.split(",").map((k) => k.trim()).filter(Boolean), remove: Boolean(d.remove),
      })),
    });
    if (!r.ok) setError(r.error);
    else setSaved(true);
  });

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-end gap-3 space-y-0">
        <div className="mr-auto">
          <CardTitle className="flex items-center gap-2"><GitBranch className="h-4 w-4 text-primary" /> Join plan</CardTitle>
          <CardDescription>
            How the source tables combine into the target, inferred from profiled keys, value ranges and overlap.
            dbt generation follows this plan exactly.
          </CardDescription>
        </div>
        <div>
          <label htmlFor="driving_table" className="mb-1 block text-xs font-medium">Driving table</label>
          <select id="driving_table" value={driving} disabled={!canEdit}
                  onChange={(e) => { setDriving(e.target.value); setSaved(false); }}
                  className="h-9 rounded-md border bg-card px-2 font-mono text-sm">
            {tables.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {graph.ambiguities.map((a) => (
          <p key={a.table} className="flex items-center gap-2 rounded-lg border border-warning/40 bg-warning/5 px-3 py-2 text-sm">
            <AlertTriangle className="h-4 w-4 text-warning" /> {a.message}
          </p>
        ))}
        {drafts.length === 0 && (
          <p className="text-sm text-muted-foreground">Single source table: no joins needed.</p>
        )}
        {drafts.map((d, i) => {
          const j = original.get(d.right_table);
          return (
            <div key={`${d.right_table}-${i}`}
                 className={cn("rounded-xl border p-3", d.remove && "opacity-50", ambiguous.has(d.right_table) && "border-warning/50")}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm">{d.left_table}</span>
                <select aria-label={`join type for ${d.right_table}`} value={d.join_type} disabled={!canEdit}
                        onChange={(e) => update(i, { join_type: e.target.value as "LEFT" | "INNER" })}
                        className="h-7 rounded-md border bg-card px-1 text-xs font-semibold">
                  <option value="LEFT">LEFT JOIN</option>
                  <option value="INNER">INNER JOIN</option>
                </select>
                <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="font-mono text-sm font-semibold">{d.right_table}</span>
                {d.cardinality && <Badge variant="outline">{d.cardinality}</Badge>}
                {j?.status === "CONFIRMED"
                  ? <Badge variant="success"><CheckCircle2 className="mr-1 h-3 w-3" />confirmed</Badge>
                  : j ? <Badge variant="secondary">suggested</Badge> : <Badge variant="secondary">new</Badge>}
                {j?.confidence != null && (
                  <span className={cn("text-xs tabular-nums", j.confidence >= 0.85 ? "text-success" : "text-warning")}>
                    {Math.round(j.confidence * 100)}% confidence
                  </span>
                )}
                {canEdit && (
                  <button type="button" aria-label={`remove join to ${d.right_table}`} onClick={() => update(i, { remove: !d.remove })}
                          className="ml-auto rounded p-1 text-muted-foreground hover:bg-muted">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <span className="text-[11px] uppercase tracking-wide text-muted-foreground">ON</span>
                <Input aria-label={`join keys for ${d.right_table}`} value={d.keys} disabled={!canEdit}
                       onChange={(e) => update(i, { keys: e.target.value.toUpperCase() })}
                       className="h-8 max-w-md font-mono text-xs" placeholder={`${d.left_table}_COL=${d.right_table}_COL`} />
              </div>
              {j && <p className="mt-1.5 text-[11px] text-muted-foreground">{j.reasoning}</p>}
              {j?.fan_out && (
                <p className="mt-1 text-[11px] text-warning">
                  Fan-out: one output row per {d.right_table} row. Aggregate it first or make it the driving table.
                </p>
              )}
              {canEdit && j?.alternatives && j.alternatives.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <span className="text-[11px] text-muted-foreground">Alternatives:</span>
                  {j.alternatives.map((a, k) => (
                    <button key={k} type="button"
                            onClick={() => update(i, { left_table: a.left_table, keys: a.keys.join(", "), cardinality: a.cardinality })}
                            className="rounded-full border px-2 py-0.5 font-mono text-[11px] hover:bg-muted">
                      {a.condition} · {Math.round(a.confidence * 100)}%
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        })}
        {graph.unreachable.length > 0 && (
          <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm">
            <p className="font-medium">No join path found for {graph.unreachable.join(", ")}</p>
            <p className="text-xs text-muted-foreground">Their mapped columns load as null until you add a join.</p>
            {canEdit && (
              <div className="mt-2 flex flex-wrap gap-2">
                {graph.unreachable.filter((t) => !drafts.some((d) => d.right_table === t)).map((t) => (
                  <Button key={t} size="sm" variant="outline"
                          onClick={() => setDrafts((d) => [...d, { left_table: driving, right_table: t, keys: "", join_type: "LEFT" }])}>
                    <Plus className="h-3.5 w-3.5" /> Add join for {t}
                  </Button>
                ))}
              </div>
            )}
          </div>
        )}
        {canEdit && (
          <div className="flex items-center gap-3">
            <Button disabled={pending} onClick={save}>
              {pending && <Loader2 className="h-4 w-4 animate-spin" />} Confirm join plan
            </Button>
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
