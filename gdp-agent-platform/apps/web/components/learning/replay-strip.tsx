import { cn } from "@/lib/utils";
import type { Replay } from "@/app/learning/actions";

const TONE: Record<Replay["verdict"], string> = {
  BETTER: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  SAME: "bg-muted text-muted-foreground",
  WORSE: "bg-destructive/10 text-destructive",
  NO_EVIDENCE: "bg-muted text-muted-foreground",
};
const WORDS: Record<Replay["verdict"], string> = {
  BETTER: "Better on past runs", SAME: "No change on past runs", WORSE: "Worse on past runs", NO_EVIDENCE: "No past runs to replay on",
};

const pct = (v: number | null | undefined) => (v == null ? "n/a" : `${v}%`);
const signed = (v: number | null | undefined) => (v == null ? "" : `${v > 0 ? "+" : ""}${v}`);

/** What replaying a knowledge change on the domain's past runs showed: first-choice accuracy (and transformations)
 *  before and after, against what reviewers approved, and the columns whose pick changed. */
export function ReplayStrip({ replay, compact = false }: { replay: Replay; compact?: boolean }) {
  const b = replay.baseline, c = replay.candidate;
  return (
    <div className="space-y-1 text-xs" data-testid="replay">
      <p className="flex flex-wrap items-center gap-2">
        <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-semibold", TONE[replay.verdict])}>{WORDS[replay.verdict]}</span>
        {b && c && replay.verdict !== "NO_EVIDENCE" && (
          <span className="text-muted-foreground">
            targets {pct(b.target_pct)} to <span className="font-medium text-foreground">{pct(c.target_pct)}</span>
            {replay.delta?.target_pct ? ` (${signed(replay.delta.target_pct)})` : ""}
            {b.transforms ? <> · transformations {pct(b.transform_pct)} to <span className="font-medium text-foreground">{pct(c.transform_pct)}</span></> : null}
            {` · ${b.columns} approved column(s) in ${replay.runs} run(s)`}
          </span>
        )}
      </p>
      {!compact && !!replay.flipped?.length && (
        <ul className="space-y-0.5 pl-1 text-[11px] text-muted-foreground">
          {replay.flipped.slice(0, 6).map((f, i) => (
            <li key={i} className={f.better ? "text-emerald-700 dark:text-emerald-300" : "text-destructive"}>
              {f.run}: {f.column} {f.before ?? "nothing"} to {f.after ?? "nothing"} (approved {f.approved})</li>
          ))}
        </ul>
      )}
    </div>
  );
}
