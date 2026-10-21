import Link from "next/link";
import { Check, Eye, Lock } from "lucide-react";
import { getRun } from "@/lib/api";
import type { StageStatusValue } from "@/lib/types";
import { cn } from "@/lib/utils";
import { MappingStep } from "../mapping/mapping-step";
import { SttmStep } from "../sttm/sttm-step";

type Tab = "mapping" | "transformations";

const STEPS: { tab: Tab; stage: "MAPPING" | "STTM"; n: number; title: string; hint: string }[] = [
  { tab: "mapping", stage: "MAPPING", n: 1, title: "Field mapping",
    hint: "Which target column each source column feeds, scored and approved by a modeler." },
  { tab: "transformations", stage: "STTM", n: 2, title: "Transformations and joins",
    hint: "How each target column is computed, and how the source tables join (the STTM)." },
];

const STATUS: Record<StageStatusValue, { label: string; cls: string }> = {
  COMPLETE: { label: "approved", cls: "text-emerald-600" },
  ACTIVE: { label: "in progress", cls: "text-blue-600" },
  REVIEW_REQUIRED: { label: "needs review", cls: "text-amber-600" },
  LOCKED: { label: "locked", cls: "text-muted-foreground" },
  FAILED: { label: "failed", cls: "text-rose-600" },
  BLOCKED: { label: "blocked", cls: "text-rose-600" },
  CANCELLED: { label: "cancelled", cls: "text-muted-foreground" },
};

/** Mapping (Modeling on a Gold run, which designs its model here first): the field mapping and the transformations
 *  and joins of one run, as two steps of one screen. Each keeps
 *  its own approval (MAPPING.APPROVE, then STTM.APPROVE); the run's states are unchanged. */
export default async function ModelingPage({ params, searchParams }: {
  params: { runId: string }; searchParams: { tab?: string };
}) {
  const state = await getRun(params.runId);
  const status = (stage: string): StageStatusValue => state.stages.find((s) => s.stage === stage)?.status ?? "LOCKED";
  const asked = searchParams.tab === "mapping" || searchParams.tab === "transformations" ? searchParams.tab : null;
  // open where the work is: transformations once the mapping is approved, else the mapping
  const tab: Tab = asked ?? (status("MAPPING") === "COMPLETE" ? "transformations" : "mapping");
  return (
    <div className="space-y-4">
      <section className="surface p-4" aria-label={`${state.run.pipeline === "GOLD" ? "Modeling" : "Mapping"} steps`}>
        <div className="mb-3">
          <h2 className="text-base font-semibold">{state.run.pipeline === "GOLD" ? "Modeling" : "Mapping"}</h2>
          <p className="text-xs text-muted-foreground">{state.run.pipeline === "GOLD"
            ? "Design the gold model and pick the gold table this run builds, map the profiled Silver columns to it, then define how every gold column is computed. Gold code generation, QA tests and Data Quality are built from the approved result."
            : "Map the profiled source to the target model, then define how every target column is computed. Silver code generation, QA tests and Data Quality are built from the approved result."}</p>
        </div>
        <ol className="grid gap-2 sm:grid-cols-2" role="tablist">
          {STEPS.map((s) => {
            const st = status(s.stage);
            const on = s.tab === tab;
            const Icon = st === "COMPLETE" ? Check : st === "LOCKED" ? Lock : st === "REVIEW_REQUIRED" ? Eye : null;
            return (
              <li key={s.tab}>
                <Link href={`/runs/${params.runId}/modeling?tab=${s.tab}`} role="tab" aria-selected={on} scroll={false}
                      className={cn("flex items-start gap-3 rounded-xl border px-3 py-2.5 transition",
                        on ? "border-primary/50 bg-primary/5 ring-1 ring-primary/20" : "hover:bg-muted")}>
                  <span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-full text-xs font-semibold",
                    st === "COMPLETE" ? "bg-emerald-500 text-white" : on ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>
                    {Icon ? <Icon className="h-3.5 w-3.5" /> : s.n}
                  </span>
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-baseline gap-2 text-sm font-semibold">{s.n}. {s.title}
                      <span className={cn("text-[10px] font-medium uppercase tracking-wide", STATUS[st].cls)}>{STATUS[st].label}</span></span>
                    <span className="block text-xs text-muted-foreground">{s.hint}</span>
                  </span>
                </Link>
              </li>
            );
          })}
        </ol>
      </section>
      {tab === "mapping" ? <MappingStep runId={params.runId} /> : <SttmStep runId={params.runId} />}
    </div>
  );
}
