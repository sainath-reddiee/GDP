"use client";

import { useEffect, useState, useTransition } from "react";
import { HelpCircle, Loader2 } from "lucide-react";
import { useAccess } from "@/components/access";
import { cn } from "@/lib/utils";
import { answerQuestion, dismissQuestion, domainQuestions, runQuestions, type Question } from "@/app/learning/actions";

const NOT_LOADED = "NOT_LOADED";

/** The questions the engine asked where it was unsure (a run's, or a domain's open ones for a layer). An answer
 *  becomes knowledge at once: the next mapping ranks the chosen target first, and stage prompts read written answers. */
export function QuestionsPanel({ runId, domainId, layer, title = "Questions for the domain owner" }: {
  runId?: string; domainId?: string; layer?: string; title?: string;
}) {
  const { canAct } = useAccess();
  const may = canAct("KNOWLEDGE.EDIT");
  const [items, setItems] = useState<Question[] | null>(null);
  const [deployed, setDeployed] = useState(true);
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [text, setText] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<Record<string, string>>({});
  const [pending, start] = useTransition();
  const load = () => void (runId ? runQuestions(runId) : domainQuestions(domainId ?? "", layer ?? "SILVER")).then((r) => {
    if (!r.ok) { setItems([]); return; }
    setDeployed(r.data.deployed);
    setItems(r.data.questions);
  });
  useEffect(load, [runId, domainId, layer]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (!items || !deployed) return null;
  const open = items.filter((q) => q.status === "OPEN");
  if (!items.length) return null;
  const answer = (q: Question) => start(async () => {
    const r = await answerQuestion(q.question_id, { choice: choice[q.question_id] ?? null, text: text[q.question_id] ?? null });
    setMsg({ ...msg, [q.question_id]: r.ok ? "Answered: the next run uses it" : r.error });
    if (r.ok) load();
  });
  const dismiss = (q: Question) => start(async () => {
    const r = await dismissQuestion(q.question_id);
    if (r.ok) load(); else setMsg({ ...msg, [q.question_id]: r.error });
  });
  return (
    <section className="surface space-y-3 p-4" data-testid="questions">
      <div className="flex items-center gap-2">
        <HelpCircle className="h-4 w-4 text-primary" />
        <h3 className="text-sm font-semibold">{title}</h3>
        <span className="text-xs text-muted-foreground">{open.length} open{items.length > open.length ? `, ${items.length - open.length} answered` : ""}</span>
        {pending && <Loader2 className="ml-auto h-4 w-4 animate-spin" />}
      </div>
      <p className="text-xs text-muted-foreground">Where the engine was unsure, it asks instead of guessing. Each answer is kept as knowledge for every later run of this layer.</p>
      <ul className="space-y-2">
        {items.map((q) => (
          <li key={q.question_id} className={cn("rounded-lg border p-3", q.status !== "OPEN" && "opacity-70")}>
            <p className="text-sm font-medium">{q.question}</p>
            {q.kind === "MAPPING" && q.context && (
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                {q.context.data_type}{q.context.description ? ` · ${q.context.description}` : ""}
                {q.context.values?.length ? ` · values ${q.context.values.slice(0, 5).join(", ")}` : ""}</p>
            )}
            {q.status === "OPEN" ? (
              <div className="mt-2 space-y-2">
                {q.kind === "MAPPING" && (
                  <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={q.question}>
                    {(q.options ?? []).map((o) => {
                      const c = q.context?.candidates?.find((x) => x.target === o);
                      return (
                        <button key={o} type="button" role="radio" aria-checked={choice[q.question_id] === o} disabled={!may}
                                onClick={() => setChoice({ ...choice, [q.question_id]: o })} title={c?.definition ?? undefined}
                                className={cn("rounded-md border px-2 py-1 font-mono text-[11px]",
                                              choice[q.question_id] === o ? "border-primary bg-primary/10 text-primary" : "hover:bg-muted")}>
                          {o === NOT_LOADED ? "Not loaded" : o}{c ? <span className="ml-1 font-sans text-muted-foreground">{Math.round(c.score * 100)}</span> : null}</button>
                      );
                    })}
                  </div>
                )}
                <div className="flex flex-wrap items-center gap-2">
                  <input value={text[q.question_id] ?? ""} onChange={(e) => setText({ ...text, [q.question_id]: e.target.value })} disabled={!may}
                         placeholder={q.kind === "MAPPING" ? "Why (optional)" : "Your answer"} aria-label="Answer"
                         className="h-8 min-w-[220px] flex-1 rounded-lg border bg-background px-2 text-xs" />
                  <button type="button" onClick={() => answer(q)} disabled={!may || pending || (q.kind === "MAPPING" ? !choice[q.question_id] : !(text[q.question_id] ?? "").trim())}
                          className="h-8 rounded-lg bg-primary px-3 text-xs font-medium text-primary-foreground disabled:opacity-50">Answer</button>
                  <button type="button" onClick={() => dismiss(q)} disabled={!may || pending} className="h-8 rounded-lg px-2 text-xs text-muted-foreground hover:bg-muted">Dismiss</button>
                </div>
              </div>
            ) : (
              <p className="mt-1 text-xs text-muted-foreground">
                {q.status === "ANSWERED" ? `Answered by ${q.answered_by ?? "?"}: ${q.answer?.choice === NOT_LOADED ? "not loaded" : q.answer?.choice ?? ""}${q.answer?.text ? ` ${q.answer.text}` : ""}` : "Dismissed"}</p>
            )}
            {msg[q.question_id] && <p role="status" className="mt-1 text-xs text-muted-foreground">{msg[q.question_id]}</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}
