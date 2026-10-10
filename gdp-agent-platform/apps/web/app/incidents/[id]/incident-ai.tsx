"use client";

import Link from "next/link";
import { useEffect, useState, useTransition, type ReactNode } from "react";
import { AlertTriangle, Download, FileText, Loader2, MessageCircleQuestion, RotateCw, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { Markdown } from "@/components/copilot/markdown";
import { useScrollLock } from "@/components/use-scroll-lock";
import { cn } from "@/lib/utils";
import { CopyButton } from "../../runs/[runId]/dbt/studio-ui";
import { Alert, useLive, useSeq } from "../../qa/qa-shared";
import { When } from "../../ops/ops-shared";
import {
  askIncident, diagnoseIncident, incidentImpact, postmortemIncident, previewRetry, retryIncident,
  type AiCitation, type AskAnswer, type Impact, type IncidentAi, type RetryDone, type RetryPreview, type RetryTask,
} from "../actions";
import { dagRunHref, incidentHref } from "../incident-shared";

const OVERRIDE_MIN = 15;
const pill = "inline-flex shrink-0 whitespace-nowrap rounded-full px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset";
const isInfo = (e: string) => /approval|request/i.test(e);
const msgOf = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);
const Muted = ({ children }: { children: ReactNode }) => <p className="text-xs text-muted-foreground">{children}</p>;
const Info = ({ children }: { children: ReactNode }) => <p role="status" className="rounded-lg bg-sky-50 px-3 py-2 text-xs text-sky-800">{children}</p>;
const words = (s: string) => s.toLowerCase().replace(/_/g, " ");

export const codeHref = (c: { repo_id?: string | null; path?: string | null; line?: number | null }) =>
  c.repo_id && c.path
    ? `/code?${new URLSearchParams({ repo: c.repo_id, path: c.path, ...(c.line ? { line: String(c.line) } : {}) })}`
    : null;

/** Only app paths and http(s) links are followed; anything else from the model shows as text. */
const safeUrl = (u: string | null | undefined) => (u && (/^\/(?!\/)/.test(u) || /^https?:\/\//i.test(u)) ? u : null);

// ---------------------------------------------------------------- safe to retry

const RETRY_TONE: Record<string, { cls: string; label: string }> = {
  yes: { cls: "bg-emerald-50 text-emerald-700 ring-emerald-100", label: "safe to retry" },
  after_fix: { cls: "bg-amber-50 text-amber-700 ring-amber-100", label: "retry after the fix" },
  no: { cls: "bg-rose-50 text-rose-700 ring-rose-100", label: "not safe to retry" },
};

export function RetryBadge({ value }: { value: string | null | undefined }) {
  const t = RETRY_TONE[(value ?? "").toLowerCase()];
  if (!t) return null;
  return <span className={cn(pill, t.cls)}>{t.label}</span>;
}

export function ConfidenceBar({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined || !Number.isFinite(value)) return <span className="text-muted-foreground">not given</span>;
  const v = Math.min(1, Math.max(0, value));
  const tone = v >= 0.75 ? "bg-emerald-500" : v >= 0.45 ? "bg-amber-500" : "bg-rose-500";
  return (
    <span className="flex items-center gap-2">
      <span className="h-1.5 w-28 overflow-hidden rounded-full bg-muted" role="meter" aria-valuemin={0} aria-valuemax={100}
            aria-valuenow={Math.round(v * 100)} aria-label="Confidence">
        <span className={cn("block h-full rounded-full", tone)} style={{ width: `${Math.round(v * 100)}%` }} />
      </span>
      <span className="tabular-nums">{Math.round(v * 100)}%</span>
    </span>
  );
}

function Citation({ c }: { c: AiCitation }) {
  const href = codeHref(c);
  const label = c.path ? `${c.path}${c.line ? `:${c.line}` : ""}` : c.ref;
  return (
    <li className="flex min-w-0 items-baseline gap-1.5">
      <span className="shrink-0 rounded bg-muted px-1 text-[10px] text-muted-foreground">{words(c.kind || "ref")}</span>
      {href
        ? <Link href={href} className="min-w-0 truncate font-mono text-primary hover:underline" title={c.ref}>{label}</Link>
        : <span className="min-w-0 truncate font-mono" title={c.ref}>{label}</span>}
    </li>
  );
}

// ---------------------------------------------------------------- diagnosis

/** The AI diagnosis with Diagnose or Regenerate, the question box and the postmortem draft. */
export function AiPanel({ incidentId, ai, summary, canAI, onAi }: {
  incidentId: string; ai: IncidentAi | null; summary: string | null; canAI: boolean; onAi: (ai: IncidentAi) => void;
}) {
  const [pending, start] = useTransition();
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const live = useLive();
  const seq = useSeq();

  const diagnose = () => start(async () => {
    const ticket = seq.next();
    setError(""); setInfo("");
    try {
      const r = await diagnoseIncident(incidentId, !!ai);
      if (!live.current || !seq.current(ticket)) return;
      if (r.ok) onAi(r.data.ai);
      else if (isInfo(r.error)) setInfo(r.error);
      else setError(r.error);
    } catch (e) {
      if (live.current && seq.current(ticket)) setError(msgOf(e, "The diagnosis failed."));
    }
  });

  const steps = ai?.fix_steps?.filter(Boolean) ?? [];
  const evidence = ai?.evidence ?? [];
  const citations = ai?.citations ?? [];
  const similar = ai?.similar ?? [];
  const parts = ai?.context_parts ?? [];
  const br = ai?.blast_radius;
  const brText = br ? [
    br.models?.length ? `${br.models.length} model${br.models.length === 1 ? "" : "s"}` : "",
    br.tables?.length ? `${br.tables.length} table${br.tables.length === 1 ? "" : "s"}` : "",
    br.domains?.length ? `${br.domains.length} domain${br.domains.length === 1 ? "" : "s"}` : "",
  ].filter(Boolean).join(", ") : "";

  return (
    <div className="space-y-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        {canAI ? (
          <Button size="sm" variant={ai ? "outline" : "default"} disabled={pending} onClick={diagnose}>
            {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : ai ? <RotateCw className="h-3.5 w-3.5" /> : <Sparkles className="h-3.5 w-3.5" />}
            {pending ? (ai ? "Regenerating" : "Diagnosing") : ai ? "Regenerate" : "Diagnose"}
          </Button>
        ) : <Muted>Running a diagnosis needs the AI.USE privilege.</Muted>}
        {pending && <span className="text-muted-foreground">Reading the log, code, runs and past incidents. This can take a minute.</span>}
      </div>
      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
      {info && <Info>{info}</Info>}

      {!ai ? (
        summary ? <p className="whitespace-pre-wrap break-words">{summary}</p> : <Muted>No diagnosis yet.</Muted>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-1.5">
            {ai.category && <span className={cn(pill, "bg-violet-50 text-violet-700 ring-violet-100")}>{words(ai.category)}</span>}
            <RetryBadge value={ai.safe_to_retry} />
          </div>
          {ai.probable_cause && (
            <div><p className="font-medium">Probable cause</p><p className="whitespace-pre-wrap break-words">{ai.probable_cause}</p></div>
          )}
          <dl className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-1.5">
            <dt className="text-muted-foreground">Confidence</dt><dd><ConfidenceBar value={ai.confidence} /></dd>
            {ai.retry_reason && <><dt className="text-muted-foreground">Retry</dt><dd className="whitespace-pre-wrap break-words">{ai.retry_reason}</dd></>}
            {ai.owner_hint && <><dt className="text-muted-foreground">Owner hint</dt><dd className="whitespace-pre-wrap break-words">{ai.owner_hint}</dd></>}
            {brText && <><dt className="text-muted-foreground">Blast radius</dt><dd>{brText}</dd></>}
          </dl>
          {steps.length > 0 && (
            <div><p className="font-medium">Fix steps</p>
              <ol className="list-decimal space-y-0.5 pl-4">{steps.map((s, i) => <li key={i} className="whitespace-pre-wrap break-words">{s}</li>)}</ol></div>
          )}
          {evidence.length > 0 && (
            <div><p className="font-medium">Evidence</p>
              <ul className="space-y-1.5">
                {evidence.map((e, i) => (
                  <li key={i} className="rounded-lg border px-2.5 py-1.5">
                    <p className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                      <span className="rounded bg-muted px-1 text-[10px]">{words(e.kind || "evidence")}</span>
                      {e.ref && <span className="break-all font-mono">{e.ref}</span>}
                    </p>
                    {e.text && <p className="mt-0.5 whitespace-pre-wrap break-words">{e.text}</p>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {citations.length > 0 && (
            <div><p className="font-medium">Citations</p><ul className="space-y-0.5">{citations.map((c, i) => <Citation key={i} c={c} />)}</ul></div>
          )}
          {similar.length > 0 && (
            <div><p className="font-medium">Similar past incidents</p>
              <ul className="space-y-1.5">
                {similar.map((s) => (
                  <li key={s.incident_id} className="min-w-0">
                    <p className="flex items-baseline gap-2">
                      <Link href={incidentHref(s.incident_id)} className="min-w-0 truncate text-primary hover:underline">{s.title || s.incident_id}</Link>
                      {typeof s.score === "number" && Number.isFinite(s.score) && <span className="shrink-0 tabular-nums text-muted-foreground">{Math.round(s.score * 100)}% match</span>}
                      {s.resolved_at && <span className="shrink-0 text-muted-foreground"><When iso={s.resolved_at} rel /></span>}
                    </p>
                    {s.resolution && <p className="line-clamp-2 whitespace-pre-wrap break-words text-muted-foreground" title={s.resolution}>{s.resolution}</p>}
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
          </p>
        </div>
      )}

      {canAI && <AskBox incidentId={incidentId} />}
      {canAI && <Postmortem incidentId={incidentId} />}
    </div>
  );
}

function AskBox({ incidentId }: { incidentId: string }) {
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState("");
  const [answer, setAnswer] = useState<AskAnswer | null>(null);
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
      const r = await askIncident(incidentId, q);
      if (!live.current || !seq.current(ticket)) return;
      if (r.ok) { setAnswer(r.data); setAsked(q); setQuestion(""); } else setError(r.error);
    } catch (e) {
      if (live.current && seq.current(ticket)) setError(msgOf(e, "The question could not be answered."));
    }
  });
  return (
    <div className="space-y-2 border-t pt-3">
      <p className="flex items-center gap-1.5 font-medium"><MessageCircleQuestion className="h-3.5 w-3.5" />Ask about this incident</p>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); ask(); }}>
        <Textarea value={question} onChange={(e) => setQuestion(e.target.value)} rows={2} maxLength={2000} className="min-w-0 flex-1 text-xs"
                  placeholder="Which upstream table was late? Did a commit change this model?" aria-label="Question about this incident"
                  onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); ask(); } }} />
        <Button size="sm" type="submit" disabled={pending || !question.trim()} className="self-end">
          {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}Ask</Button>
      </form>
      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
      {answer && (
        <div className="space-y-1.5 rounded-lg bg-muted/40 px-3 py-2">
          <p className="text-[11px] text-muted-foreground">Q: {asked}</p>
          <p className="whitespace-pre-wrap break-words">{answer.answer}</p>
          {answer.citations?.length > 0 && (
            <ul className="space-y-0.5 text-[11px]">
              {answer.citations.map((c, i) => {
                const href = safeUrl(c.url);
                return (
                  <li key={i} className="flex min-w-0 items-baseline gap-1.5">
                    <span className="shrink-0 rounded bg-muted px-1 text-[10px] text-muted-foreground">{words(c.kind || "ref")}</span>
                    {href
                      ? (href.startsWith("/")
                        ? <Link href={href} className="min-w-0 truncate font-mono text-primary hover:underline">{c.ref}</Link>
                        : <a href={href} target="_blank" rel="noreferrer" className="min-w-0 truncate font-mono text-primary hover:underline">{c.ref}</a>)
                      : <span className="min-w-0 truncate font-mono">{c.ref}</span>}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function Postmortem({ incidentId }: { incidentId: string }) {
  const [markdown, setMarkdown] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const live = useLive();
  const seq = useSeq();
  const draft = () => start(async () => {
    const ticket = seq.next();
    setError("");
    try {
      const r = await postmortemIncident(incidentId);
      if (!live.current || !seq.current(ticket)) return;
      if (r.ok) setMarkdown(r.data.markdown ?? ""); else setError(r.error);
    } catch (e) {
      if (live.current && seq.current(ticket)) setError(msgOf(e, "The postmortem draft failed."));
    }
  });
  const download = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }));
    a.download = `postmortem-${incidentId.replace(/[^\w.-]+/g, "_")}.md`;
    a.click();
    { const done = a.href; setTimeout(() => URL.revokeObjectURL(done), 1000); }
  };
  return (
    <div className="space-y-2 border-t pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" disabled={pending} onClick={draft}>
          {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileText className="h-3.5 w-3.5" />}{markdown ? "Redraft postmortem" : "Draft postmortem"}</Button>
        {markdown && <>
          <CopyButton text={markdown} label="Copy" />
          <button type="button" onClick={download}
                  className="inline-flex items-center gap-1 rounded-md border bg-white px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-50">
            <Download className="h-3.5 w-3.5" />Download .md</button>
          <button type="button" onClick={() => setMarkdown("")} aria-label="Close the draft" className="ml-auto rounded-md p-1 hover:bg-muted"><X className="h-3.5 w-3.5" /></button>
        </>}
      </div>
      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
      {markdown && (
        <div className="max-h-[28rem] overflow-auto rounded-lg border px-3 py-2 text-xs">
          <Markdown text={markdown} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- impact

const outcomeTone = (o: string | null) =>
  /pass|success|ok/i.test(o ?? "") ? "bg-emerald-50 text-emerald-700 ring-emerald-100"
    : /fail|error/i.test(o ?? "") ? "bg-rose-50 text-rose-700 ring-rose-100"
      : /warn/i.test(o ?? "") ? "bg-amber-50 text-amber-700 ring-amber-100" : "bg-slate-100 text-slate-600 ring-slate-200";

function Chips({ label, items, mono }: { label: string; items: string[]; mono?: boolean }) {
  if (!items.length) return null;
  return (
    <div>
      <p className="font-medium">{label} <span className="font-normal text-muted-foreground">{items.length}</span></p>
      <ul className="mt-1 flex flex-wrap gap-1">
        {items.map((m) => <li key={m} className={cn("rounded bg-muted px-1.5 py-0.5 text-[11px]", mono && "font-mono")}>{m}</li>)}
      </ul>
    </div>
  );
}

/** What this failure affects downstream, from the code graph of the DAG's repository. */
export function ImpactPanel({ incidentId, envId, dagId }: { incidentId: string; envId: string; dagId: string }) {
  const [impact, setImpact] = useState<Impact | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let live = true;
    setImpact(null); setError("");
    incidentImpact(incidentId).then((r) => {
      if (!live) return;
      if (r.ok) setImpact(r.data); else setError(r.error);
    }, (e: unknown) => { if (live) setError(msgOf(e, "Could not load the impact.")); });
    return () => { live = false; };
  }, [incidentId, reload]);

  if (error) {
    return <Alert>{/not found|404/i.test(error) ? "Impact needs the latest API deploy." : `Impact did not load: ${error}`}
      <button type="button" className="ml-2 underline" onClick={() => setReload((n) => n + 1)}>Try again</button></Alert>;
  }
  if (!impact) return <p className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Working out the impact</p>;
  const empty = !impact.models?.length && !impact.tables?.length && !impact.domains?.length && !impact.sttm?.length && !impact.qa?.length;
  if (impact.source !== "code_graph" || empty) {
    return (
      <div className="space-y-1 text-xs text-muted-foreground">
        <p>{impact.source !== "code_graph"
          ? "No impact is known: this DAG is not mapped to a repository, so its models cannot be traced."
          : "The code graph shows nothing downstream of this DAG."}</p>
        {impact.detail && <p className="whitespace-pre-wrap break-words">{impact.detail}</p>}
        {impact.source !== "code_graph" && (
          <p>Set the repository and path in the <Link href={dagRunHref(envId, dagId)} className="text-primary hover:underline">DAG settings</Link> on Pipelines.</p>
        )}
      </div>
    );
  }
  return (
    <div className="space-y-3 text-xs">
      <Chips label="Models" items={impact.models ?? []} mono />
      <Chips label="Tables" items={impact.tables ?? []} mono />
      <Chips label="Domains" items={impact.domains ?? []} />
      {impact.sttm?.length > 0 && (
        <div><p className="font-medium">Mappings (STTM)</p>
          <ul className="mt-1 space-y-0.5">
            {impact.sttm.map((s) => (
              <li key={`${s.run_id}:${s.target}`}>
                <Link href={`/runs/${encodeURIComponent(s.run_id)}/sttm`} className="font-mono text-primary hover:underline">{s.target || s.run_id}</Link>
              </li>
            ))}
          </ul>
        </div>
      )}
      {impact.qa?.length > 0 && (
        <div><p className="font-medium">Latest QA result</p>
          <ul className="mt-1 space-y-0.5">
            {impact.qa.map((q) => (
              <li key={q.target_table_id} className="flex min-w-0 items-center gap-2">
                <Link href={`/qa?${new URLSearchParams({ tab: "results", table: q.target_table_id })}`}
                      className="min-w-0 truncate font-mono text-primary hover:underline" title={q.fqn}>{q.fqn || q.target_table_id}</Link>
                <span className={cn(pill, outcomeTone(q.last_outcome))}>{q.last_outcome ? words(q.last_outcome) : "not tested"}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="border-t pt-2 text-[11px] text-muted-foreground">Source: code graph{impact.detail ? `. ${impact.detail}` : ""}</p>
    </div>
  );
}

// ---------------------------------------------------------------- retry

function TaskList({ tasks }: { tasks: RetryTask[] }) {
  return (
    <div className="max-h-56 overflow-auto rounded-lg border">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-card text-left text-[11px] uppercase tracking-wide text-muted-foreground">
          <tr className="border-b"><th className="px-3 py-1.5 font-medium">Task</th><th className="px-3 py-1.5 font-medium">Run</th><th className="px-3 py-1.5 font-medium">State</th></tr>
        </thead>
        <tbody className="divide-y">
          {tasks.map((t) => (
            <tr key={`${t.dag_id}:${t.run_id}:${t.task_id}:${t.map_index}`}>
              <td className="max-w-[14rem] px-3 py-1.5 font-mono"><span className="block truncate" title={`${t.dag_id}.${t.task_id}`}>
                {t.task_id}{t.map_index >= 0 ? ` [${t.map_index}]` : ""}</span></td>
              <td className="max-w-[12rem] px-3 py-1.5 font-mono"><span className="block truncate" title={t.run_id}>{t.run_id}</span></td>
              <td className="px-3 py-1.5">{t.state ? words(t.state) : "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Clear and retry: a dry run lists the task instances first; the confirm sends that preview's token. */
export function RetryDialog({ incidentId, safe, onClose, onDone }: {
  incidentId: string; safe: string | null | undefined; onClose: () => void; onDone: (text: string) => void;
}) {
  useScrollLock();
  const [downstream, setDownstream] = useState(false);
  const [preview, setPreview] = useState<RetryPreview | null>(null);
  const [result, setResult] = useState<RetryDone | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [pending, start] = useTransition();
  const live = useLive();
  const seq = useSeq();
  const unsafe = (safe ?? "").toLowerCase() === "no";
  const reasonShort = unsafe && reason.trim().length < OVERRIDE_MIN;
  const n = preview?.tasks.length ?? 0;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !pending) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, pending]);

  const doPreview = () => start(async () => {
    const ticket = seq.next();
    setError(""); setInfo(""); setPreview(null); setResult(null);
    try {
      const r = await previewRetry(incidentId, downstream);
      if (!live.current || !seq.current(ticket)) return;
      if (r.ok) setPreview(r.data);
      else if (r.pending || isInfo(r.error)) setInfo(r.error);
      else setError(r.error);
    } catch (e) {
      if (live.current && seq.current(ticket)) setError(msgOf(e, "The preview failed."));
    }
  });

  const confirm = () => start(async () => {
    if (!preview || reasonShort) return;
    const ticket = seq.next();
    setError(""); setInfo("");
    try {
      const r = await retryIncident(incidentId, preview.preview_token, unsafe ? reason.trim() : undefined);
      if (!live.current || !seq.current(ticket)) return;
      if (r.ok) {
        setResult(r.data); setPreview(null);
        onDone(`Cleared ${r.data.cleared} task${r.data.cleared === 1 ? "" : "s"}. Airflow reruns them.`);
      } else if (r.pending) {
        setInfo(r.error || "The retry is waiting for approval."); setPreview(null);
      } else setError(r.error);
    } catch (e) {
      if (live.current && seq.current(ticket)) setError(msgOf(e, "The retry failed."));
    }
  });

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-label="Retry task"
         onClick={(e) => { if (e.target === e.currentTarget && !pending) onClose(); }}>
      <div className="flex max-h-[85vh] w-full max-w-xl flex-col rounded-2xl border bg-background shadow-2xl">
        <div className="flex items-start justify-between border-b px-5 py-3">
          <div>
            <h3 className="flex items-center gap-1.5 text-base font-semibold"><RotateCw className="h-4 w-4" />Retry task</h3>
            <p className="text-xs text-muted-foreground">Clears the failed task instances in Airflow so they run again. Preview first; nothing changes until you confirm.</p>
          </div>
          <button type="button" onClick={onClose} disabled={pending} aria-label="Close" className="rounded-md p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
        </div>
        <div className="flex-1 space-y-3 overflow-y-auto overscroll-contain px-5 py-3 text-xs">
          {result ? (
            <div className="space-y-2">
              <p role="status" className="rounded-lg bg-success/10 px-3 py-2 text-success">
                Cleared {result.cleared} task{result.cleared === 1 ? "" : "s"}. Airflow schedules them again.</p>
              {result.detail && <p className="whitespace-pre-wrap break-words text-muted-foreground">{result.detail}</p>}
              {result.tasks?.length > 0 && <TaskList tasks={result.tasks} />}
            </div>
          ) : (
            <>
              <label className="flex items-start gap-2">
                <input type="checkbox" className="mt-0.5" checked={downstream} disabled={pending}
                       onChange={(e) => { setDownstream(e.target.checked); setPreview(null); }} />
                <span>Also clear downstream tasks <span className="block text-muted-foreground">Tasks that depend on the failed one in the same run.</span></span>
              </label>
              {(safe ?? "").toLowerCase() === "after_fix" && (
                <p className="flex items-start gap-1.5 rounded-lg bg-amber-50 px-3 py-2 text-amber-900">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />The diagnosis says to retry after the fix. Make sure it is in place.</p>
              )}
              {unsafe && (
                <div className="space-y-1.5 rounded-lg bg-rose-50 px-3 py-2 text-rose-900">
                  <p className="flex items-start gap-1.5"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    The diagnosis says this failure is not safe to retry. A rerun may repeat the damage or load bad data. Give a reason to override.</p>
                  <Textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} maxLength={1000} className="bg-background text-xs"
                            placeholder="Why a retry is safe now" aria-label="Override reason" aria-invalid={!!preview && reasonShort} />
                  {reasonShort && <p className="text-[11px]">At least {OVERRIDE_MIN} characters ({reason.trim().length} so far). Saved in the audit trail.</p>}
                </div>
              )}
              {preview && (
                <div className="space-y-1.5">
                  <p className="font-medium">{n ? `${n} task${n === 1 ? "" : "s"} will be cleared` : "Nothing to clear"}</p>
                  {preview.detail && <p className="whitespace-pre-wrap break-words text-muted-foreground">{preview.detail}</p>}
                  {n > 0 && <TaskList tasks={preview.tasks} />}
                </div>
              )}
            </>
          )}
          {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
          {info && <Info>{info}</Info>}
        </div>
        <div className="flex justify-end gap-2 border-t px-5 py-3">
          <Button variant="ghost" onClick={onClose} disabled={pending}>{result || info ? "Close" : "Cancel"}</Button>
          {!result && (
            <Button variant={preview ? "outline" : "default"} onClick={doPreview} disabled={pending}>
              {pending && !preview ? <Loader2 className="h-4 w-4 animate-spin" /> : null}{preview ? "Preview again" : "Preview"}</Button>
          )}
          {!result && preview && (
            <Button variant={unsafe ? "destructive" : "default"} onClick={confirm} disabled={pending || !n || reasonShort}>
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCw className="h-4 w-4" />}Clear and retry {n} task{n === 1 ? "" : "s"}</Button>
          )}
        </div>
      </div>
    </div>
  );
}
