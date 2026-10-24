"use client";

import Link from "next/link";
import { useEffect, useState, useTransition } from "react";
import { GraduationCap, Loader2 } from "lucide-react";
import { useAccess } from "@/components/access";
import { QuestionsPanel } from "@/components/learning/questions-panel";
import { ReplayStrip } from "@/components/learning/replay-strip";
import { domainLearning, learnFromDecisions, type LearnResult, type Learning } from "../../learning/actions";

const HABIT: Record<string, string> = { TRANSFORMATION: "Transformation", OVERRIDE: "Corrected pick", RENAME: "Rename" };

/** How this layer learns from use: rules learned from reviewer decisions (each replayed on past runs before it is
 *  proposed), and the questions the engine asked the domain owner. */
export function LearningPanel({ domainId, layer }: { domainId: string; layer: "SILVER" | "GOLD" }) {
  const { canAct } = useAccess();
  const [data, setData] = useState<Learning | null>(null);
  const [result, setResult] = useState<LearnResult | null>(null);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const load = () => void domainLearning(domainId, layer).then((r) => (r.ok ? setData(r.data) : setError(r.error)));
  useEffect(load, [domainId, layer]);   // eslint-disable-line react-hooks/exhaustive-deps

  const learn = () => start(async () => {
    setError("");
    const r = await learnFromDecisions(domainId, layer);
    if (!r.ok) { setError(r.error); return; }
    setResult(r.data);
    load();
  });

  if (!data) return error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null;
  const shown = result ? [...result.proposed, ...result.held_back.map((h) => ({ ...h, held: true }))] : [];
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
      <section className="surface space-y-3 p-4" data-testid="learning">
        <div className="flex flex-wrap items-center gap-2">
          <GraduationCap className="h-4 w-4 text-primary" />
          <h3 className="text-sm font-semibold">Learned from reviewer decisions</h3>
          {canAct("KNOWLEDGE.EDIT") && data.deployed && (
            <button type="button" onClick={learn} disabled={pending || !data.decisions}
                    className="ml-auto inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-medium text-primary-foreground disabled:opacity-50">
              {pending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}{pending ? "Learning and replaying…" : "Learn from decisions"}</button>
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          {data.decisions} reviewer decision(s) in this layer. Habits that repeat across tables and runs (a transformation shape, a corrected pick,
          a rename) become proposed rules. Each is replayed on past runs first: one that makes mapping worse is held back, the rest wait in the{" "}
          <Link href="/os/knowledge?view=inbox" className="text-primary hover:underline">knowledge inbox</Link> for a steward.</p>
        {!data.deployed && <p className="text-xs text-muted-foreground">Needs migration V058 (Admin, Deploy).</p>}
        {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
        {result && (
          <p role="status" className="rounded-lg bg-muted px-3 py-2 text-xs">
            {result.found ? `${result.found} habit(s) found: ${result.proposed.length} proposed, ${result.held_back.length} held back (worse on replay)`
              + (result.already_decided ? `, ${result.already_decided} already decided by a steward` : "") : "No repeated habit yet: rules need the same decision in several tables or runs."}
            {result.runs.length ? ` Replayed on ${result.runs.length} run(s).` : ""}</p>
        )}
        {shown.map((c) => (
          <article key={c.key} className="space-y-1 rounded-lg border p-3">
            <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
              <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-semibold uppercase">{HABIT[c.habit] ?? c.habit}</span>{c.title}
              {"held" in c && <span className="text-[11px] font-normal text-destructive">held back</span>}</p>
            <p className="text-[11px] text-muted-foreground">{c.evidence.decisions} decisions in {c.evidence.runs} run(s) and {c.evidence.tables} table(s);
              explains {Math.round(c.evidence.share * 100)}% of them; corrected the engine {c.evidence.corrected_engine} time(s). E.g. {c.evidence.examples.slice(0, 2).join("; ")}</p>
            <ReplayStrip replay={c.replay} />
          </article>
        ))}
        {!result && !!data.learned.length && (
          <ul className="space-y-1.5 text-xs">
            {data.learned.slice(0, 8).map((l) => (
              <li key={l.knowledge_id} className="flex flex-wrap items-center gap-2">
                <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-semibold">{l.status.toLowerCase()}</span>
                <span className="font-medium">{l.title}</span>
                {l.replay && <ReplayStrip replay={l.replay} compact />}
              </li>
            ))}
          </ul>
        )}
        {!!data.evals.length && (
          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground">Recent replays ({data.evals.length})</summary>
            <ul className="mt-1 space-y-0.5">
              {data.evals.map((e, i) => (
                <li key={i} className="text-muted-foreground">{e.created_at} · {e.subject_kind.toLowerCase()} · {e.verdict.toLowerCase().replace("_", " ")}
                  {e.delta?.target_pct ? ` (${e.delta.target_pct > 0 ? "+" : ""}${e.delta.target_pct} targets)` : ""} · {e.created_by}</li>
              ))}
            </ul>
          </details>
        )}
      </section>
      <QuestionsPanel domainId={domainId} layer={layer} />
    </div>
  );
}
