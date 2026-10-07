import { ArrowRight, User } from "lucide-react";
import { cn } from "@/lib/utils";

export type GraphState = { state: string; stage: string | null; kind: string; ordinal: number; phase: number; enabled: boolean; graph_version: string };
export type GraphTransition = { from_state: string; to_state: string; actor: string; enabled: boolean };

const KIND_TONE: Record<string, string> = {
  START: "border-slate-300 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200",
  WORK: "border-primary/30 bg-primary/5",
  REVIEW: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100",
  GATE: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100",
  TERMINAL: "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-100",
  FAILED: "border-red-300 bg-red-50 text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-100",
};

const pretty = (s: string) => s.toLowerCase().replace(/_/g, " ");

/** The workflow graph as lanes: one column per stage in run order, each state a card listing where it can go. */
export function WorkflowLanes({ states, transitions }: { states: GraphState[]; transitions: GraphTransition[] }) {
  const lanes = new Map<string, GraphState[]>();
  for (const s of [...states].sort((a, b) => a.ordinal - b.ordinal)) {
    const lane = s.stage ?? (s.kind === "TERMINAL" || s.kind === "FAILED" ? "END" : "START");
    lanes.set(lane, [...(lanes.get(lane) ?? []), s]);
  }
  const ordered = Array.from(lanes.entries()).sort(([a, x], [b, y]) =>
    (a === "END" ? 1 : 0) - (b === "END" ? 1 : 0) || x[0].ordinal - y[0].ordinal);
  const out = (s: string) => transitions.filter((t) => t.from_state === s && t.enabled);

  return (
    <div className="overflow-x-auto pb-2">
      <div className="flex min-w-max gap-3">
        {ordered.map(([lane, items], i) => (
          <div key={lane} className="flex items-stretch gap-3">
            <div className="w-56 shrink-0 rounded-xl border bg-muted/30 p-2">
              <p className="mb-2 flex items-center justify-between px-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {pretty(lane)}<span className="font-normal normal-case">phase {items[0].phase}</span>
              </p>
              <div className="space-y-2">
                {items.map((s) => {
                  const next = out(s.state);
                  return (
                    <div key={s.state} className={cn("rounded-lg border p-2 text-xs", KIND_TONE[s.kind] ?? "bg-card", !s.enabled && "opacity-40")}
                         title={s.enabled ? undefined : "Disabled: belongs to a later phase"}>
                      <p className="font-mono text-[11px] font-semibold">{s.state}</p>
                      <p className="text-[10px] opacity-75">{pretty(s.kind)}{!s.enabled && " · disabled"}</p>
                      {next.length > 0 && (
                        <ul className="mt-1.5 space-y-0.5 border-t border-current/10 pt-1.5">
                          {next.map((t) => (
                            <li key={t.to_state} className="flex items-center gap-1 text-[10px] opacity-90">
                              <ArrowRight className="h-3 w-3 shrink-0" />
                              <span className="truncate font-mono">{t.to_state}</span>
                              {t.actor === "HUMAN" && <User className="ml-auto h-3 w-3 shrink-0" aria-label="needs a person" />}
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
            {i < ordered.length - 1 && <ArrowRight className="mt-6 h-4 w-4 shrink-0 self-start text-muted-foreground" />}
          </div>
        ))}
      </div>
    </div>
  );
}
