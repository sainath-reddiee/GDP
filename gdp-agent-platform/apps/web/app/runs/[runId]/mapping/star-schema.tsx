"use client";

import { useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Check, Eye, Loader2, Network } from "lucide-react";
import { useAccess } from "@/components/access";
import { cn } from "@/lib/utils";
import { buildStarSchema, publishStarSchema, type GoldChild } from "../../../gold/actions";
import { approveStage } from "../actions";
import { startAutopilot } from "../autopilot-actions";

const COLUMNS = ["Mapping", "STTM", "QA and DQ", "Gold code", "Validation", "Review"] as const;
const SLUG = ["modeling?tab=mapping", "modeling?tab=transformations", "soda", "dbt", "validation", "review"];
// the column a table run is at, from its state (before mapping: -1; complete: past the last column)
const AT: Record<string, number> = {
  MAPPING_PENDING: 0, MAPPING_REVIEW: 0, MAPPING_APPROVED: 1, STTM_PENDING: 1, STTM_REVIEW: 1, STTM_APPROVED: 2,
  SODA_PENDING: 2, SODA_REVIEW: 2, SODA_APPROVED: 3, DBT_PENDING: 3, DBT_GENERATING: 3, VALIDATION_PENDING: 4,
  VALIDATION_RUNNING: 4, VALIDATION_FAILED: 4, VALIDATION_PASSED: 5, DBT_REVIEW: 5, DBT_APPROVED: 6, COMPLETED: 6,
};
const REVIEW = new Set(["MAPPING_REVIEW", "STTM_REVIEW", "SODA_REVIEW", "DBT_REVIEW"]);
const KIND: Record<string, string> = {
  FACT: "bg-sky-500/10 text-sky-700 dark:text-sky-300", BRIDGE: "bg-blue-500/10 text-blue-700 dark:text-blue-300",
};

/** A Gold run's star schema: one table run per fact and bridge, their progress through the same steps, and the
 *  actions that move them together (each still goes through its own gate and the approver's privileges). */
export function StarSchema({ runId, approved, items }: { runId: string; approved: boolean; items: GoldChild[] }) {
  const { canAct } = useAccess();
  const router = useRouter();
  const [pending, start] = useTransition();
  const [notes, setNotes] = useState<string[]>([]);

  const build = () => start(async () => {
    const r = await buildStarSchema(runId);
    if (!r.ok) { setNotes([r.error]); return; }
    setNotes([
      ...r.data.errors.map((e) => `${e.entity}: ${e.error}`),
      ...(r.data.unsourced.length ? [`No silver source among this run's tables for ${r.data.unsourced.join(", ")}: start a Gold run on that table.`] : []),
    ]);
    router.refresh();
  });
  const approveAll = (from: string, to: string, what: string) => start(async () => {
    const out: string[] = [];
    for (const c of items.filter((x) => x.current_state === from)) {
      const r = await approveStage(c.run_id, to, `Approved with the Gold run's star schema (${what})`);
      out.push(`${c.entity}: ${r.ok ? "approved" : r.error}`);
    }
    setNotes(out);
    router.refresh();
  });
  const publish = () => start(async () => {
    const r = await publishStarSchema(runId);
    if (!r.ok) { setNotes([r.error]); return; }
    const pr = r.data.pull_request?.url;
    setNotes([pr ? `Pull request opened: ${pr}` : `${r.data.status}: ${r.data.detail ?? "see the run's audit trail"}`,
      ...(r.data.conflicts?.length ? [`Kept once (differing copies): ${r.data.conflicts.join(", ")}`] : [])]);
  });
  const runAll = () => start(async () => {
    const r = await startAutopilot(runId);
    setNotes([r.ok ? "Autopilot is taking every table run to its next review." : r.error]);
  });

  if (!items.length) {
    return (
      <div className="rounded-xl border p-3">
        <p className="text-xs font-semibold">b. The star schema</p>
        <p className="mt-1 text-xs text-muted-foreground">{approved
          ? "Build it: each fact and bridge of the approved gold model becomes a table run on its silver table (read in place), and maps, transforms, tests and generates its code through the same steps. The dimensions a table adds are built with it."
          : "Available once the gold model is approved."}</p>
        {approved && canAct("RUN.CREATE") && (
          <button type="button" onClick={build} disabled={pending}
                  className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-lg bg-amber-500 px-3 text-xs font-semibold text-white disabled:opacity-50">
            {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Network className="h-3.5 w-3.5" />}Build the star schema</button>
        )}
        {!!notes.length && <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[11px] text-muted-foreground">{notes.map((n) => <li key={n}>{n}</li>)}</ul>}
      </div>
    );
  }
  const waiting = (s: string) => items.filter((c) => c.current_state === s).length;
  return (
    <div className="rounded-xl border p-3" data-testid="star-schema">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <p className="mr-auto text-xs font-semibold">b. The star schema: {items.length} table run{items.length === 1 ? "" : "s"}</p>
        {canAct("RUN.OPERATE") && <button type="button" onClick={runAll} disabled={pending} className="inline-flex h-7 items-center gap-1 rounded-md bg-primary px-2.5 text-[11px] font-semibold text-primary-foreground disabled:opacity-50">Run all to next gate</button>}
        {waiting("MAPPING_REVIEW") > 0 && canAct("MAPPING.APPROVE") && (
          <button type="button" onClick={() => approveAll("MAPPING_REVIEW", "MAPPING_APPROVED", "mapping")} disabled={pending}
                  className="h-7 rounded-md border px-2.5 text-[11px] font-medium hover:bg-muted">Approve {waiting("MAPPING_REVIEW")} mapping(s)</button>
        )}
        {waiting("STTM_REVIEW") > 0 && canAct("STTM.APPROVE") && (
          <button type="button" onClick={() => approveAll("STTM_REVIEW", "STTM_APPROVED", "STTM")} disabled={pending}
                  className="h-7 rounded-md border px-2.5 text-[11px] font-medium hover:bg-muted">Approve {waiting("STTM_REVIEW")} Data Mapping(s)</button>
        )}
        {items.every((c) => (AT[c.current_state] ?? -1) >= 4) && canAct("DBT.EDIT") && (
          <button type="button" onClick={publish} disabled={pending}
                  className="h-7 rounded-md bg-amber-500 px-2.5 text-[11px] font-semibold text-white disabled:opacity-50">Publish one pull request</button>
        )}
        {pending && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-[10px] uppercase tracking-wide text-muted-foreground">
            <tr><th className="py-1 pr-2">Gold table</th>{COLUMNS.map((c) => <th key={c} className="px-1 py-1 text-center">{c}</th>)}</tr>
          </thead>
          <tbody>
            {items.map((c) => {
              const at = AT[c.current_state] ?? -1;
              return (
                <tr key={c.run_id} className="border-t">
                  <td className="py-1.5 pr-2">
                    <Link href={`/os/build/runs/${c.run_id}`} className="font-mono font-semibold text-primary hover:underline">{c.entity}</Link>
                    {c.table_type && <span className={cn("ml-1.5 rounded px-1 py-0.5 text-[9px] font-semibold", KIND[c.table_type] ?? "bg-muted")}>{c.table_type}</span>}
                  </td>
                  {COLUMNS.map((col, i) => {
                    const done = i < at;
                    const here = i === at;
                    const review = here && REVIEW.has(c.current_state);
                    const failed = here && c.current_state === "VALIDATION_FAILED";
                    return (
                      <td key={col} className="px-1 py-1.5 text-center">
                        <Link href={`/os/build/runs/${c.run_id}/${SLUG[i]}`} title={`${col}: ${c.current_state.replace(/_/g, " ").toLowerCase()}`}
                              className={cn("inline-grid h-5 w-5 place-items-center rounded-full",
                                done ? "bg-emerald-500 text-white" : failed ? "bg-rose-500 text-white" : review ? "bg-amber-500 text-white"
                                  : here ? "bg-blue-500 text-white" : "bg-muted")}>
                          {done ? <Check className="h-3 w-3" /> : review ? <Eye className="h-3 w-3" /> : null}
                        </Link>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!!notes.length && <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[11px] text-muted-foreground">{notes.map((n) => <li key={n}>{n}</li>)}</ul>}
    </div>
  );
}
