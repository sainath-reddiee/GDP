"use client";

import Link from "next/link";
import { BookOpen, Lightbulb, Sparkles } from "lucide-react";

/** The runbook-learning contract (services/ops/runbook_learning.py learn()). */
export type RunbookSolution = {
  key: string; title: string; status: string; meaning: string | null; usual_cause: string | null;
  action: string | null; human_steps: string[]; seen: number; fixed: number; last_fix: string | null;
};
export type LearnedRunbooks = { matches: RunbookSolution[]; unmatched: string[]; proposed: string[] };

const ACTION_TEXT: Record<string, string> = {
  RERUN_ONLY: "Safe to rerun: the agent can propose clearing the task",
  ADD_COLUMN: "The agent can propose adding the column",
  CREATE_TABLE_FROM: "The agent can propose recreating the table",
  CREATE_STAGE: "The agent can propose creating the stage",
  DEDUP_ON_KEY: "The agent can propose removing duplicates (needs approval)",
};

/** The solutions the Airflow knowledge pack has for the errors in a pulled log, and what it learned from it.
 *  Renders nothing when the log matched no runbook and nothing new was proposed. */
export function RunbookSolutions({ data }: { data?: LearnedRunbooks | null }) {
  if (!data || (!data.matches.length && !data.proposed.length)) return null;
  return (
    <div className="space-y-2 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 text-xs">
      <p className="flex items-center gap-1.5 font-semibold"><Lightbulb className="h-3.5 w-3.5 text-emerald-600" />Known solutions for these errors</p>
      {data.matches.map((m) => (
        <div key={m.key} className="space-y-1 rounded-md bg-background/60 p-2">
          <p className="flex flex-wrap items-center gap-2 font-medium">
            <BookOpen className="h-3.5 w-3.5 text-muted-foreground" />{m.title}
            {m.status === "PROPOSED" && <span className="rounded bg-amber-500/15 px-1.5 text-[10px] text-amber-700">awaiting review</span>}
            {m.seen > 0 && <span className="text-[10px] text-muted-foreground">seen {m.seen}x{m.fixed ? `, fixed ${m.fixed}x` : ""}</span>}
          </p>
          {m.meaning && <p><span className="text-muted-foreground">Meaning: </span>{m.meaning}</p>}
          {m.usual_cause && <p><span className="text-muted-foreground">Usual cause: </span>{m.usual_cause}</p>}
          {m.last_fix && <p><span className="text-muted-foreground">Last fix that worked: </span>{m.last_fix}</p>}
          {m.action && <p className="text-emerald-700">{ACTION_TEXT[m.action] ?? m.action}</p>}
          {m.human_steps.length > 0 && (
            <ol className="ml-4 list-decimal space-y-0.5">{m.human_steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
          )}
        </div>
      ))}
      {data.proposed.length > 0 && (
        <p className="flex items-center gap-1.5 text-muted-foreground">
          <Sparkles className="h-3.5 w-3.5 text-violet-600" />
          {data.proposed.length} new error{data.proposed.length === 1 ? "" : "s"} no runbook knows yet: proposed in the{" "}
          <Link href="/knowledge?tab=inbox" className="text-primary hover:underline">Knowledge inbox</Link>. Add the fix and approve, and it is suggested next time.
        </p>
      )}
    </div>
  );
}
