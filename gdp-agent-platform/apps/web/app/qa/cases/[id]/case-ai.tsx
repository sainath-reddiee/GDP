"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import { GitMerge, Loader2, MessageCircleQuestion, RotateCw, Sparkles, Table2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { Alert, useLive, useSeq } from "../../qa-shared";
import { When } from "../../../ops/ops-shared";
import { ConfidenceBar } from "../../../incidents/[id]/incident-ai";
import { incidentHref } from "../../../incidents/incident-shared";
import {
  askCase, mergeCase, triageCase, updateCase,
  type CaseAi, type CaseAnswer, type CaseResult, type CaseTargetCandidate, type TriageResult,
} from "../actions";
import { caseHref, pill } from "../case-ui";
import { aiProblem, Chips, Info, JiraPost, KindTag, list, msgOf, Muted, pct, RefLink, words } from "./case-kit";

const REPRO: Record<string, { cls: string; label: string }> = {
  yes: { cls: "bg-emerald-50 text-emerald-700 ring-emerald-100", label: "reproducible" },
  no: { cls: "bg-slate-100 text-slate-600 ring-slate-200", label: "not reproducible" },
  unknown: { cls: "bg-amber-50 text-amber-700 ring-amber-100", label: "reproducibility unknown" },
};

/** Runs a case action through the page (busy state, errors, reload). */
export type Act = (key: string, fn: () => Promise<CaseResult<unknown>>, done: string | ((data: unknown) => string), after?: () => void) => void;

type Props = {
  caseId: string; caseNumber: string; ai: CaseAi | null; cached: boolean | null; fallbackSummary: string | null;
  canAI: boolean; canWork: boolean; canResolve: boolean; canJira: boolean; hasJira: boolean;
  targetTableId: string | null; tables: Record<string, string>; busy: string | null; pending: boolean; act: Act;
  onTriaged: (r: TriageResult) => void; onNotice: (text: string) => void;
};

/** The AI triage of a case: classification, hypotheses with evidence, blast radius, questions, targets, similar and duplicate
 *  cases. Triage and Re-run need AI.USE and CASE.WORK; nothing here is applied without a person. */
export function CaseAiPanel(p: Props) {
  const { ai, caseId } = p;
  const [pending, start] = useTransition();
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [rerun, setRerun] = useState(false);
  const [note, setNote] = useState("");
  const [mergeAsk, setMergeAsk] = useState("");
  const live = useLive();
  const seq = useSeq();
  const canTriage = p.canAI && p.canWork;

  const triage = (force: boolean) => start(async () => {
    const ticket = seq.next();
    setError(""); setInfo("");
    try {
      const r = await triageCase(caseId, { ...(force ? { force: true } : {}), ...(note.trim() ? { note: note.trim() } : {}) });
      if (!live.current || !seq.current(ticket)) return;
      if (!r.ok) { const pr = aiProblem(r); if (pr.info) setInfo(pr.text); else setError(pr.text); return; }
      setRerun(false); setNote("");
      p.onTriaged(r.data);
    } catch (e) {
      if (live.current && seq.current(ticket)) setError(msgOf(e, "The triage failed."));
    }
  });

  const hyps = list(ai?.hypotheses);
  const br = ai?.blast_radius;
  const questions = list(ai?.questions).filter(Boolean);
  const candidates = list(ai?.target_candidates);
  const similar = list(ai?.similar);
  const dups = list(ai?.duplicate_candidates).filter((d) => d.case_id !== caseId);
  const citations = list(ai?.citations);
  const parts = list(ai?.context_parts);
  const repro = REPRO[(ai?.reproducible ?? "").toLowerCase()];
  const summary = ai?.summary || p.fallbackSummary;

  return (
    <div className="space-y-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        {canTriage ? (
          ai ? (
            <Button size="sm" variant={rerun ? "secondary" : "outline"} disabled={pending} onClick={() => setRerun(!rerun)}>
              <RotateCw className="h-3.5 w-3.5" />Re-run</Button>
          ) : (
            <Button size="sm" disabled={pending} onClick={() => triage(false)}>
              {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}{pending ? "Triaging" : "Triage with AI"}</Button>
          )
        ) : <Muted>Triage needs the AI.USE and CASE.WORK privileges.</Muted>}
        {pending && <span className="text-muted-foreground">Reading the report, table, code, runs and past cases. This can take a minute.</span>}
      </div>
      {rerun && ai && (
        <form className="space-y-2 rounded-lg border p-3" onSubmit={(e) => { e.preventDefault(); triage(true); }}>
          <Textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={4000} className="text-xs" aria-label="New information"
                    placeholder="New information (optional): what changed, what you found, answers from the reporter" autoFocus />
          <div className="flex gap-2">
            <Button size="sm" type="submit" disabled={pending}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCw className="h-3.5 w-3.5" />}Re-run triage</Button>
            <Button size="sm" type="button" variant="ghost" disabled={pending} onClick={() => setRerun(false)}>Cancel</Button>
          </div>
        </form>
      )}
      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
      {info && <Info>{info}</Info>}

      {!ai || (!ai.classification && !hyps.length && !ai.confidence) ? (
        summary ? <p className="whitespace-pre-wrap break-words">{summary}</p> : <Muted>No AI analysis yet.</Muted>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-1.5">
            {ai.classification && <span className={cn(pill, "bg-violet-50 text-violet-700 ring-violet-100")}>{words(ai.classification)}</span>}
            {repro && <span className={cn(pill, repro.cls)}>{repro.label}</span>}
          </div>
          <dl className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-1.5">
            <dt className="text-muted-foreground">Confidence</dt><dd><ConfidenceBar value={ai.confidence} /></dd>
            {ai.target?.target_table_fqn && <><dt className="text-muted-foreground">Target</dt><dd className="break-all font-mono">{ai.target.target_table_fqn}</dd></>}
            {list(ai.target?.models).length > 0 && <><dt className="text-muted-foreground">Models</dt><dd className="break-all font-mono">{list(ai.target?.models).join(", ")}</dd></>}
          </dl>
          {summary && <p className="whitespace-pre-wrap break-words text-[13px] leading-relaxed">{summary}</p>}

          {hyps.length > 0 && (
            <div className="space-y-1.5">
              <p className="font-medium">Hypotheses</p>
              <ol className="space-y-2">
                {hyps.map((h, i) => (
                  <li key={i} className="rounded-lg border px-3 py-2">
                    <p className="flex items-start gap-2">
                      <span className="mt-px grid h-4 w-4 shrink-0 place-items-center rounded-full bg-muted text-[10px] font-semibold">{i + 1}</span>
                      <span className="min-w-0 flex-1 whitespace-pre-wrap break-words font-medium">{h.cause}</span>
                      {pct(h.confidence) && <span className="shrink-0 tabular-nums text-muted-foreground">{pct(h.confidence)}</span>}
                    </p>
                    {list(h.evidence).length > 0 && (
                      <ul className="mt-1.5 space-y-1 border-l pl-3">
                        {list(h.evidence).map((e, j) => (
                          <li key={j} className="min-w-0">
                            <p className="flex min-w-0 items-baseline gap-1.5 text-[11px]">
                              <KindTag kind={e.kind || "evidence"} />
                              {e.ref && <RefLink kind={e.kind} refText={e.ref} tables={p.tables} />}
                            </p>
                            {e.text && <p className="whitespace-pre-wrap break-words text-muted-foreground">{e.text}</p>}
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ol>
            </div>
          )}

          {br && (list(br.models).length + list(br.tables).length + list(br.domains).length > 0) && (
            <div className="space-y-1">
              <p className="font-medium">Blast radius</p>
              <Chips label="Models" items={list(br.models)} mono />
              <Chips label="Tables" items={list(br.tables)} mono />
              <Chips label="Domains" items={list(br.domains)} />
            </div>
          )}

          {questions.length > 0 && (
            <div className="space-y-1.5">
              <p className="flex items-center gap-1.5 font-medium"><MessageCircleQuestion className="h-3.5 w-3.5" />Questions for the reporter</p>
              <ul className="list-disc space-y-0.5 pl-4">{questions.map((q, i) => <li key={i} className="whitespace-pre-wrap break-words">{q}</li>)}</ul>
              {p.canJira && p.hasJira
                ? <JiraPost caseId={caseId} label="Post questions to Jira" initialText={`Questions to help triage ${p.caseNumber}:\n${questions.map((q) => `- ${q}`).join("\n")}`}
                            hint="Check the questions before posting." onPosted={p.onNotice} />
                : !p.hasJira ? <Muted>Link a Jira issue to post these to the reporter.</Muted> : null}
            </div>
          )}

          {candidates.length > 0 && (
            <TargetPicker candidates={candidates} current={p.targetTableId} canWork={p.canWork} busy={p.busy} pending={p.pending}
                          choose={(c) => p.act(`target:${c.target_table_id}`, () => updateCase(caseId, { target_table_id: c.target_table_id }), `Target table set to ${c.fqn}.`)} />
          )}

          {similar.length > 0 && (
            <div className="space-y-1.5">
              <p className="font-medium">Similar cases and incidents</p>
              <ul className="space-y-1.5">
                {similar.map((s) => (
                  <li key={`${s.kind}:${s.id}`} className="min-w-0">
                    <p className="flex min-w-0 items-baseline gap-2">
                      <KindTag kind={s.kind} />
                      <Link href={s.kind === "incident" ? incidentHref(s.id) : caseHref(s.id)} className="min-w-0 truncate text-primary hover:underline">{s.title || s.id}</Link>
                      {pct(s.score) && <span className="shrink-0 tabular-nums text-muted-foreground">{pct(s.score)} match</span>}
                    </p>
                    {s.resolution && <p className="line-clamp-2 whitespace-pre-wrap break-words text-muted-foreground" title={s.resolution}>{s.resolution}</p>}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {dups.length > 0 && (
            <div className="space-y-1.5 rounded-lg bg-amber-50 px-3 py-2 text-amber-900 dark:bg-amber-950/40 dark:text-amber-100">
              <p className="font-medium">Possible duplicates</p>
              <ul className="space-y-1.5">
                {dups.map((d) => (
                  <li key={d.case_id} className="flex flex-wrap items-center gap-2">
                    <Link href={caseHref(d.case_id)} className="font-mono text-primary hover:underline">{d.number}</Link>
                    <span className="min-w-0 flex-1 truncate" title={d.title ?? undefined}>{d.title}</span>
                    {pct(d.score) && <span className="tabular-nums">{pct(d.score)}</span>}
                    {p.canResolve && (mergeAsk === d.case_id ? (
                      <span className="flex w-full flex-wrap items-center gap-2">
                        <span>Merge {p.caseNumber} into {d.number}? This case becomes a duplicate of it.</span>
                        <Button size="sm" className="h-6 px-2 text-[11px]" disabled={p.pending}
                                onClick={() => p.act(`merge:${d.case_id}`, () => mergeCase(caseId, d.case_id), `Merged into ${d.number}.`, () => setMergeAsk(""))}>
                          {p.busy === `merge:${d.case_id}` ? <Loader2 className="h-3 w-3 animate-spin" /> : <GitMerge className="h-3 w-3" />}Yes, merge</Button>
                        <Button size="sm" variant="ghost" className="h-6 px-2 text-[11px]" disabled={p.pending} onClick={() => setMergeAsk("")}>Cancel</Button>
                      </span>
                    ) : (
                      <Button size="sm" variant="outline" className="h-6 bg-background px-2 text-[11px]" disabled={p.pending} onClick={() => setMergeAsk(d.case_id)}>
                        <GitMerge className="h-3 w-3" />Merge into</Button>
                    ))}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {citations.length > 0 && (
            <div className="space-y-1">
              <p className="font-medium">Citations</p>
              <ul className="space-y-0.5">
                {citations.map((c, i) => (
                  <li key={i} className="flex min-w-0 items-baseline gap-1.5">
                    <KindTag kind={c.kind} />
                    <RefLink kind={c.kind} refText={c.ref} extra={c} tables={p.tables}
                             label={c.path ? `${c.path}${c.line ? `:${c.line}` : ""}` : c.ref} />
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="border-t pt-2 text-[11px] text-muted-foreground">
            {ai.model ? <>By <span className="font-mono">{ai.model}</span></> : "AI generated"}
            {ai.generated_at && <> · <When iso={ai.generated_at} /></>}
            {parts.length > 0 && <> · context: {parts.map(words).join(", ")}</>}
            . Check it before acting.
            {p.cached && " Cached: nothing in the context changed since the last triage, so it was not run again."}
          </p>
        </div>
      )}

      {p.canAI && <AskCase caseId={caseId} tables={p.tables} />}
    </div>
  );
}

function TargetPicker({ candidates, current, canWork, busy, pending, choose }: {
  candidates: CaseTargetCandidate[]; current: string | null; canWork: boolean; busy: string | null; pending: boolean;
  choose: (c: CaseTargetCandidate) => void;
}) {
  return (
    <div className="space-y-1.5">
      <p className="flex items-center gap-1.5 font-medium"><Table2 className="h-3.5 w-3.5" />Which table is this about?</p>
      <ul className="space-y-1">
        {candidates.map((c) => {
          const reasons = Array.isArray(c.reasons) ? c.reasons.join("; ") : c.reasons ?? "";
          const isCurrent = c.target_table_id === current;
          return (
            <li key={c.target_table_id} className={cn("flex flex-wrap items-center gap-2 rounded-md border px-2.5 py-1.5", isCurrent && "border-primary/40 bg-primary/5")}>
              <span className="min-w-0 flex-1">
                <span className="block break-all font-mono">{c.fqn}</span>
                {reasons && <span className="block text-[11px] text-muted-foreground">{reasons}</span>}
              </span>
              {pct(c.score) && <span className="tabular-nums text-muted-foreground">{pct(c.score)}</span>}
              {isCurrent ? <span className={cn(pill, "bg-primary/10 text-primary ring-primary/20")}>current</span>
                : canWork && (
                  <Button size="sm" variant="outline" className="h-6 px-2 text-[11px]" disabled={pending} onClick={() => choose(c)}>
                    {busy === `target:${c.target_table_id}` && <Loader2 className="h-3 w-3 animate-spin" />}Use this table</Button>
                )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function AskCase({ caseId, tables }: { caseId: string; tables: Record<string, string> }) {
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState("");
  const [answer, setAnswer] = useState<CaseAnswer | null>(null);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const live = useLive();
  const seq = useSeq();
  const ask = () => start(async () => {
    const q = question.trim();
    if (!q) return;
    const ticket = seq.next();
    setError("");
    try {
      const r = await askCase(caseId, q);
      if (!live.current || !seq.current(ticket)) return;
      if (r.ok) { setAnswer(r.data); setAsked(q); setQuestion(""); } else setError(aiProblem(r).text);
    } catch (e) {
      if (live.current && seq.current(ticket)) setError(msgOf(e, "The question could not be answered."));
    }
  });
  return (
    <div className="space-y-2 border-t pt-3">
      <p className="flex items-center gap-1.5 font-medium"><MessageCircleQuestion className="h-3.5 w-3.5" />Ask about this case</p>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); ask(); }}>
        <Textarea value={question} onChange={(e) => setQuestion(e.target.value)} rows={2} maxLength={2000} className="min-w-0 flex-1 text-xs"
                  placeholder="Which model writes this column? When did the row counts change?" aria-label="Question about this case"
                  onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); ask(); } }} />
        <Button size="sm" type="submit" disabled={pending || !question.trim()} className="self-end">
          {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}Ask</Button>
      </form>
      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
      {answer && (
        <div className="space-y-1.5 rounded-lg bg-muted/40 px-3 py-2">
          <p className="text-[11px] text-muted-foreground">Q: {asked}</p>
          <p className="whitespace-pre-wrap break-words">{answer.answer}</p>
          {list(answer.citations).length > 0 && (
            <ul className="space-y-0.5 text-[11px]">
              {answer.citations.map((c, i) => (
                <li key={i} className="flex min-w-0 items-baseline gap-1.5">
                  <KindTag kind={c.kind} /><RefLink kind={c.kind} refText={c.ref} extra={{ url: c.url }} tables={tables} />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
