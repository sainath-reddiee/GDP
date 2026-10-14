"use client";

import { useEffect, useState, useTransition } from "react";
import Link from "next/link";
import { Crown, Loader2, Sparkles } from "lucide-react";
import { useAccess } from "@/components/access";
import { proposeGold, runGold, type RunGold } from "../../../gold/actions";
import { StarSchema } from "./star-schema";

/** A Gold run's model step: the domain's gold model (proposed from this run's profile, reviewed and approved in the
 *  Model Workbench), then its star schema: a table run per fact and bridge. A table run shows which table it builds. */
export function GoldModelPanel({ runId }: { runId: string }) {
  const { canAct } = useAccess();
  const [data, setData] = useState<RunGold | null>(null);
  const [error, setError] = useState("");
  const [notes, setNotes] = useState<string[]>([]);
  const [pending, start] = useTransition();
  const load = () => void runGold(runId).then((r) => (r.ok ? setData(r.data) : setError(r.error)));
  useEffect(load, [runId]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (error && !data) return <section className="surface p-4 text-sm text-destructive" role="alert">{error}</section>;
  if (!data) return <section className="surface p-4 text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-4 w-4 animate-spin" />Reading the gold model…</section>;
  const model = data.model;
  const approved = model?.approved_version ?? null;
  const open = model?.open_version ?? null;
  const propose = () => start(async () => {
    setError("");
    const r = await proposeGold(runId);
    if (!r.ok) { setError(r.error); return; }
    const aligned = r.data.alignment?.filter((a) => a.proposed && a.reference) ?? [];
    setNotes([...(r.data.reference ? [`Aligned to your reference model ${r.data.reference.title ?? ""}: ${aligned.length} table(s) take its names, columns and types`] : []),
              ...(r.data.score ? [`Before alignment the platform's own proposal had ${r.data.score.matched} of the reference's ${r.data.score.total} columns (${r.data.score.pct}%)`] : []),
              ...(r.data.notes ?? [])]);
    load();
  });
  if (data.parent_run_id) {
    return (
      <section className="surface flex flex-wrap items-center gap-3 p-4" data-testid="gold-table-run">
        <Crown className="h-4 w-4 text-amber-500" />
        <p className="min-w-0 flex-1 text-sm">A table run of the <Link href={`/runs/${data.parent_run_id}/modeling`} className="font-medium text-primary hover:underline">Gold run</Link>:
          it builds <span className="font-mono font-semibold">{data.target?.entity ?? "its gold table"}</span>
          {" "}(and the dimensions the table adds) from the approved gold model v{data.target?.version}.</p>
        {model && <Link href={`/dashboard/modeling/${model.model_id}`} className="text-xs font-medium text-primary hover:underline">Gold model</Link>}
      </section>
    );
  }

  return (
    <section className="surface space-y-4 p-4" data-testid="gold-model">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="flex items-center gap-2 text-sm font-semibold"><Crown className="h-4 w-4 text-amber-500" />Gold model of {data.domain_name}</h3>
          <p className="text-xs text-muted-foreground">Facts at the grain of each Silver hub, conformed Company and Property dimensions resolved through
            Reltio&apos;s cross-reference, new dimensions for low-cardinality descriptors (from this run&apos;s profile) and bridges for many-to-many.
            It is the domain&apos;s GOLD model, reviewed and approved in the Model Workbench by a second person, then built as a star schema.</p>
        </div>
        {model && <Link href={`/dashboard/modeling/${model.model_id}`} className="text-xs font-medium text-primary hover:underline">Open in the Model Workbench</Link>}
      </div>

      <ol className="grid gap-3 lg:grid-cols-2">
        <li className="rounded-xl border p-3">
          <p className="text-xs font-semibold">a. The gold model {approved ? <span className="ml-1 text-emerald-600">v{approved} approved</span> : null}
            {open ? <span className="ml-1 text-amber-600">v{open} in review</span> : null}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {!model ? "No gold model yet: propose one from this run's profiled Silver tables."
              : open ? "A draft is open: review the diagram and the changes in the Model Workbench, submit it, and another model approver approves it."
                : "Approved. Propose again to fold in tables or columns this run adds (a new draft, reviewed the same way)."}</p>
          {canAct("MODEL.EDIT") && (
            <button type="button" onClick={propose} disabled={pending || !data.inputs.length}
                    className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-medium hover:bg-muted disabled:opacity-50">
              {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
              {model ? "Propose an update from this run" : "Propose the gold model"}</button>
          )}
          {!!notes.length && <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[11px] text-muted-foreground">{notes.map((n) => <li key={n}>{n}</li>)}</ul>}
          <p className="mt-2 text-[11px] text-muted-foreground">Inputs: {data.inputs.map((i) => `${i.table} (${i.columns} columns${i.row_count != null ? `, ${i.row_count} rows` : ""})`).join(", ") || "not profiled yet"}</p>
        </li>
        <li className="lg:col-span-1"><StarSchema runId={runId} approved={Boolean(approved)} items={data.children ?? []} /></li>
      </ol>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </section>
  );
}
