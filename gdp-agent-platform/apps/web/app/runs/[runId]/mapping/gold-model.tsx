"use client";

import { useEffect, useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Check, Crown, Loader2, Sparkles } from "lucide-react";
import { useAccess } from "@/components/access";
import { cn } from "@/lib/utils";
import { chooseGoldTarget, proposeGold, runGold, type RunGold } from "../../../gold/actions";

const KIND: Record<string, string> = {
  FACT: "bg-sky-500/10 text-sky-700 dark:text-sky-300", DIMENSION: "bg-amber-500/10 text-amber-700 dark:text-amber-300",
  BRIDGE: "bg-violet-500/10 text-violet-700 dark:text-violet-300",
};

/** A Gold run's model step: the domain's gold model (proposed from this run's profile, reviewed and approved in the
 *  Model Workbench), then the gold table this run builds. Mapping starts once the table is chosen. */
export function GoldModelPanel({ runId }: { runId: string }) {
  const { canAct } = useAccess();
  const router = useRouter();
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
    setNotes(r.data.notes ?? []);
    load();
  });
  const choose = (entity: string) => start(async () => {
    setError("");
    const r = await chooseGoldTarget(runId, entity);
    if (!r.ok) { setError(r.error); return; }
    load();
    router.refresh();
  });

  return (
    <section className="surface space-y-4 p-4" data-testid="gold-model">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="flex items-center gap-2 text-sm font-semibold"><Crown className="h-4 w-4 text-amber-500" />Gold model of {data.domain_name}</h3>
          <p className="text-xs text-muted-foreground">Facts at the grain of each Silver hub, conformed Company and Property dimensions resolved through
            Reltio&apos;s cross-reference, new dimensions for low-cardinality descriptors (from this run&apos;s profile) and bridges for many-to-many.
            It is the domain&apos;s GOLD model, reviewed and approved in the Model Workbench by a second person.</p>
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
        <li className={cn("rounded-xl border p-3", !approved && "opacity-60")}>
          <p className="text-xs font-semibold">b. The gold table this run builds {data.target && <span className="ml-1 font-mono text-emerald-600">{data.target.entity}</span>}</p>
          <p className="mt-1 text-xs text-muted-foreground">{approved ? "One run builds one fact or bridge (with the dimensions it adds), like a Silver run builds one hub. Start another Gold run for another table."
            : "Available once the gold model is approved."}</p>
          {!!approved && (
            <div className="mt-2 space-y-1.5">
              {data.tables.map((t) => {
                const on = data.target?.entity === t.entity;
                return (
                  <button key={t.entity} type="button" disabled={pending || on || !data.can_choose_target || !canAct("MODEL.EDIT")} onClick={() => choose(t.entity)}
                          className={cn("flex w-full items-start gap-2 rounded-lg border px-2.5 py-1.5 text-left text-xs transition disabled:cursor-default",
                            on ? "border-emerald-500/50 bg-emerald-500/5" : "hover:bg-muted")}>
                    <span className={cn("mt-0.5 rounded px-1.5 py-0.5 text-[10px] font-semibold", KIND[t.table_type] ?? "bg-muted")}>{t.table_type}</span>
                    <span className="min-w-0 flex-1"><span className="font-mono font-semibold">{t.entity}</span>
                      <span className="block truncate text-muted-foreground">{t.grain ?? t.description ?? ""} · {t.columns} columns</span></span>
                    {on && <Check className="h-4 w-4 text-emerald-600" />}
                  </button>
                );
              })}
              {!data.can_choose_target && <p className="text-[11px] text-muted-foreground">The table can change before the mapping is approved; reopen mapping to change it later.</p>}
            </div>
          )}
        </li>
      </ol>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </section>
  );
}
