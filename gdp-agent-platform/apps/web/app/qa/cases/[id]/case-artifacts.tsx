"use client";

import Link from "next/link";
import { useEffect, useState, useTransition, type ReactNode } from "react";
import {
  AlertTriangle, BookOpen, Check, CheckCheck, ExternalLink, FileCode2, FlaskConical, GitPullRequest, Loader2, Play, ShieldCheck, X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useScrollLock } from "@/components/use-scroll-lock";
import { cn } from "@/lib/utils";
import { CopyButton } from "../../../runs/[runId]/dbt/studio-ui";
import { Alert, useLive, useSeq } from "../../qa-shared";
import { When } from "../../../ops/ops-shared";
import {
  decideArtifact, markApplied, previewPublish, publishArtifact, runArtifact, verifyCase,
  type CaseAi, type CaseArtifact, type PublishDone, type PublishPreview, type TestRun, type VerifyResult,
} from "../actions";
import { pill, up } from "../case-ui";
import type { Act } from "./case-ai";
import { Info, list, msgOf, Muted, safeUrl, SqlBlock, str, UnifiedDiff, words } from "./case-kit";

const TYPES: { id: string; label: string }[] = [
  { id: "REPRO_TEST", label: "Reproduction tests" },
  { id: "STTM_CHANGE", label: "Mapping changes" },
  { id: "CORRECTION_SQL", label: "Data corrections" },
  { id: "DBT_PATCH", label: "dbt patches" },
  { id: "KNOWLEDGE_DRAFT", label: "Knowledge drafts" },
];

const STATUS_TONE: Record<string, string> = {
  PROPOSED: "bg-amber-50 text-amber-700 ring-amber-100",
  ACCEPTED: "bg-sky-50 text-sky-700 ring-sky-100",
  APPLIED: "bg-emerald-50 text-emerald-700 ring-emerald-100",
  REJECTED: "bg-slate-100 text-slate-600 ring-slate-200",
};

export const isPass = (o: string | null | undefined) => /pass|success|^ok$/i.test(o ?? "");
const outcomeTone = (o: string | null | undefined) =>
  isPass(o) ? "bg-emerald-50 text-emerald-700 ring-emerald-100"
    : /fail|error/i.test(o ?? "") ? "bg-rose-50 text-rose-700 ring-rose-100"
      : /warn/i.test(o ?? "") ? "bg-amber-50 text-amber-700 ring-amber-100" : "bg-slate-100 text-slate-600 ring-slate-200";

export function OutcomePill({ outcome }: { outcome: string | null | undefined }) {
  return <span className={cn(pill, outcomeTone(outcome))}>{outcome ? words(outcome) : "not run"}</span>;
}

const payloadOf = (a: CaseArtifact) => (a.payload && typeof a.payload === "object" ? a.payload : {}) as Record<string, unknown>;
const show = (v: unknown) => (v === null || v === undefined || v === "" ? "" : typeof v === "string" ? v : typeof v === "number" || typeof v === "boolean" ? String(v) : JSON.stringify(v));
/** The last outcome known for a test: run in this session first, else what the API stored. */
export const lastOutcome = (a: CaseArtifact, outcomes: Record<string, string>) =>
  outcomes[a.artifact_id] ?? (str(payloadOf(a).last_outcome) || str(payloadOf(a).outcome) || null);
export const acceptedTests = (artifacts: CaseArtifact[]) =>
  artifacts.filter((a) => up(a.type) === "REPRO_TEST" && ["ACCEPTED", "APPLIED"].includes(up(a.status)));

type Common = {
  caseId: string; runId: string | null; canWork: boolean; canDbt: boolean; busy: string | null; pending: boolean; act: Act;
  outcomes: Record<string, string>; setOutcome: (id: string, outcome: string) => void;
};

/** Every proposal from triage, grouped by type, with the actions each type allows. Nothing is applied automatically. */
export function Proposals(p: Common & { artifacts: CaseArtifact[]; reload: () => Promise<void>; onNotice: (t: string) => void }) {
  const known = new Set(TYPES.map((t) => t.id));
  const groups = [
    ...TYPES.map((t) => ({ ...t, items: p.artifacts.filter((a) => up(a.type) === t.id) })),
    ...Array.from(new Set(p.artifacts.map((a) => up(a.type)).filter((t) => !known.has(t))))
      .map((t) => ({ id: t, label: words(t), items: p.artifacts.filter((a) => up(a.type) === t) })),
  ].filter((g) => g.items.length);
  const tests = acceptedTests(p.artifacts);
  if (!groups.length) return <Muted>No proposals yet. Triage with AI drafts reproduction tests and fixes here.</Muted>;
  return (
    <div className="space-y-4">
      {p.canWork && tests.length > 0 && <VerifyBar caseId={p.caseId} count={tests.length} setOutcome={p.setOutcome} reload={p.reload} onNotice={p.onNotice} />}
      {groups.map((g) => (
        <div key={g.id} className="space-y-2">
          <p className="text-xs font-semibold">{g.label} <span className="font-normal text-muted-foreground">{g.items.length}</span></p>
          <ul className="space-y-2">{g.items.map((a) => <ArtifactCard key={a.artifact_id} a={a} {...p} />)}</ul>
        </div>
      ))}
    </div>
  );
}

function ArtifactCard(p: Common & { a: CaseArtifact }) {
  const { a } = p;
  const type = up(a.type);
  const status = up(a.status);
  const pl = payloadOf(a);
  const [rejecting, setRejecting] = useState(false);
  const [applying, setApplying] = useState(false);
  const [note, setNote] = useState("");
  const [publish, setPublish] = useState(false);
  const k = (what: string) => `${what}:${a.artifact_id}`;
  const spin = (what: string, icon: ReactNode) => (p.busy === k(what) ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : icon);
  const accepted = status === "ACCEPTED";
  const canApply = accepted && ["STTM_CHANGE", "CORRECTION_SQL", "DBT_PATCH"].includes(type);
  const prUrl = safeUrl(str(pl.pr_url));
  const publishWhy = str(pl.publish_unavailable) || str(pl.unavailable_reason)
    || (pl.publishable === false ? str(pl.reason) || "Publishing is not available for this patch." : "")
    || (!a.diff ? "No diff to publish." : "") || (!accepted ? "Accept the patch first." : "");

  return (
    <li className="rounded-lg border">
      <p className="flex flex-wrap items-center gap-2 border-b px-3 py-2 text-xs">
        <TypeIcon type={type} />
        <span className="min-w-0 flex-1 truncate font-medium" title={a.title ?? undefined}>{a.title || words(a.type)}</span>
        {a.valid === true && <span className={cn(pill, "bg-emerald-50 text-emerald-700 ring-emerald-100")}>passes the guard</span>}
        {a.valid === false && <span className={cn(pill, "bg-rose-50 text-rose-700 ring-rose-100")}>guard problems</span>}
        <span className={cn(pill, STATUS_TONE[status] ?? STATUS_TONE.REJECTED)}>{words(a.status)}</span>
      </p>
      <div className="space-y-2 px-3 py-2 text-xs">
        {list(a.problems).length > 0 && (
          <ul role="alert" className="list-disc space-y-0.5 rounded-lg bg-destructive/10 py-2 pl-7 pr-3 text-destructive">
            {list(a.problems).map((x, i) => <li key={i} className="whitespace-pre-wrap break-words">{x}</li>)}</ul>
        )}

        {type === "REPRO_TEST" && <ReproBody {...p} pl={pl} />}
        {type === "STTM_CHANGE" && <SttmBody a={a} pl={pl} />}
        {type === "CORRECTION_SQL" && (
          <>
            <p className="flex items-start gap-1.5 rounded-lg bg-rose-50 px-3 py-2 font-medium text-rose-900 dark:bg-rose-950/40 dark:text-rose-100">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />The platform never runs this. Review it with the data owner and run it yourself.</p>
            {(str(pl.sql) || a.content) && (
              <div className="space-y-1">
                <div className="flex justify-end"><CopyButton text={str(pl.sql) || a.content || ""} label="Copy SQL" /></div>
                <SqlBlock sql={str(pl.sql) || a.content || ""} label="Correction SQL" />
              </div>
            )}
            {str(pl.rationale) && <p className="whitespace-pre-wrap break-words text-muted-foreground">{str(pl.rationale)}</p>}
          </>
        )}
        {type === "DBT_PATCH" && (
          <>
            {str(pl.path) && <p className="break-all font-mono text-[11px]">{str(pl.path)}</p>}
            {a.diff ? <UnifiedDiff diff={a.diff} /> : <Muted>No diff was produced.</Muted>}
            {(str(pl.rationale) || a.content) && <p className="whitespace-pre-wrap break-words">{str(pl.rationale) || a.content}</p>}
            {prUrl && <a href={prUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
              <GitPullRequest className="h-3.5 w-3.5" />Pull request<ExternalLink className="h-3 w-3" /></a>}
          </>
        )}
        {type === "KNOWLEDGE_DRAFT" && (
          <>
            <p className="flex flex-wrap items-center gap-1.5">
              {(str(pl.knowledge_type) || str(pl.type) || str(pl.kind)) && <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">{words(str(pl.knowledge_type) || str(pl.type) || str(pl.kind))}</span>}
              {str(pl.title) && <span className="font-medium">{str(pl.title)}</span>}
            </p>
            {(str(pl.content) || a.content) && <p className="whitespace-pre-wrap break-words">{str(pl.content) || a.content}</p>}
            <p className="text-[11px] text-muted-foreground">Accepted drafts are proposed to the domain stewards when the case is resolved.</p>
          </>
        )}
        {!TYPES.some((t) => t.id === type) && a.content && <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-2 font-mono text-[11px]">{a.content}</pre>}
        {!TYPES.some((t) => t.id === type) && a.diff && <UnifiedDiff diff={a.diff} />}

        {a.note && <p className="whitespace-pre-wrap break-words text-muted-foreground">Note: {a.note}</p>}
        <p className="text-[11px] text-muted-foreground">
          <When iso={a.created_at} rel empty="-" />
          {a.decided_by && <> · {words(a.status)} by {a.decided_by}{a.decided_at && <> <When iso={a.decided_at} rel /></>}</>}
        </p>

        {p.canWork && (
          <div className="flex flex-wrap items-center gap-2 border-t pt-2">
            {status === "PROPOSED" && !rejecting && (
              <>
                <Button size="sm" disabled={p.pending} onClick={() => p.act(k("accept"), () => decideArtifact(p.caseId, a.artifact_id, "accept"), `Accepted ${a.title || words(a.type)}.`)}>
                  {spin("accept", <Check className="h-3.5 w-3.5" />)}Accept</Button>
                <Button size="sm" variant="outline" disabled={p.pending} onClick={() => { setRejecting(true); setNote(""); }}><X className="h-3.5 w-3.5" />Reject</Button>
              </>
            )}
            {rejecting && (
              <form className="flex w-full flex-wrap items-center gap-2" onSubmit={(e) => {
                e.preventDefault();
                p.act(k("reject"), () => decideArtifact(p.caseId, a.artifact_id, "reject", note.trim() || undefined), `Rejected ${a.title || words(a.type)}.`, () => setRejecting(false));
              }}>
                <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why (optional)" className="h-8 min-w-[12rem] flex-1 text-xs" aria-label="Reason for rejecting" autoFocus />
                <Button size="sm" variant="destructive" type="submit" disabled={p.pending}>{spin("reject", <X className="h-3.5 w-3.5" />)}Reject</Button>
                <Button size="sm" variant="ghost" type="button" onClick={() => setRejecting(false)}>Cancel</Button>
              </form>
            )}
            {type === "STTM_CHANGE" && accepted && (p.runId
              ? <Link href={`/runs/${encodeURIComponent(p.runId)}/sttm`} className="inline-flex h-8 items-center gap-1 rounded-md border px-3 text-xs font-medium hover:bg-muted">
                  <ExternalLink className="h-3.5 w-3.5" />Open in STTM editor</Link>
              : <span className="text-[11px] text-muted-foreground">Link the run to open its STTM editor.</span>)}
            {type === "DBT_PATCH" && !prUrl && status !== "REJECTED" && status !== "APPLIED" && p.canDbt && (
              <span className="inline-flex flex-wrap items-center gap-2">
                <Button size="sm" variant="outline" disabled={p.pending || !!publishWhy} onClick={() => setPublish(true)} title={publishWhy || undefined}>
                  <GitPullRequest className="h-3.5 w-3.5" />Publish as PR</Button>
                {publishWhy && <span className="text-[11px] text-muted-foreground">{publishWhy}</span>}
              </span>
            )}
            {canApply && !applying && (
              <Button size="sm" variant="outline" disabled={p.pending} onClick={() => { setApplying(true); setNote(""); }}><CheckCheck className="h-3.5 w-3.5" />Mark applied</Button>
            )}
            {applying && (
              <form className="flex w-full flex-wrap items-center gap-2" onSubmit={(e) => {
                e.preventDefault();
                p.act(k("applied"), () => markApplied(p.caseId, a.artifact_id, note.trim() || undefined), (d) => {
                  const st = up((d as { case?: { status?: string } } | null)?.case?.status);
                  return `Marked applied.${st === "VERIFIED" ? " The tests pass; the case is verified." : ""}`;
                }, () => setApplying(false));
              }}>
                <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Where and how it was applied (optional)" className="h-8 min-w-[14rem] flex-1 text-xs"
                       aria-label="Applied note" autoFocus />
                <Button size="sm" type="submit" disabled={p.pending}>{spin("applied", <CheckCheck className="h-3.5 w-3.5" />)}Mark applied</Button>
                <Button size="sm" variant="ghost" type="button" onClick={() => setApplying(false)}>Cancel</Button>
              </form>
            )}
          </div>
        )}
      </div>
      {publish && <PublishDialog caseId={p.caseId} a={a} onClose={() => setPublish(false)} />}
    </li>
  );
}

function TypeIcon({ type }: { type: string }) {
  const Icon = type === "REPRO_TEST" ? FlaskConical : type === "KNOWLEDGE_DRAFT" ? BookOpen : type === "CORRECTION_SQL" ? AlertTriangle : FileCode2;
  return <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <div><p className="text-[11px] text-muted-foreground">{label}</p>{children}</div>;
}

function SttmBody({ a, pl }: { a: CaseArtifact; pl: Record<string, unknown> }) {
  const column = str(pl.target_column) || str(pl.column);
  const current = str(pl.current_transformation) || str(pl.current);
  const proposed = str(pl.proposed_transformation) || str(pl.proposed) || str(pl.transformation);
  const why = str(pl.rationale) || a.content || "";
  return (
    <div className="space-y-2">
      {column && <Field label="Target column"><p className="break-all font-mono">{column}</p></Field>}
      {(current || proposed) && (
        <div className="grid gap-2 md:grid-cols-2">
          <Field label="Current"><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-rose-50 p-2 font-mono text-[11px] text-rose-900 dark:bg-rose-950/40 dark:text-rose-100">{current || "not set"}</pre></Field>
          <Field label="Proposed"><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-emerald-50 p-2 font-mono text-[11px] text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-100">{proposed || "not set"}</pre></Field>
        </div>
      )}
      {a.diff && <UnifiedDiff diff={a.diff} />}
      {why && <Field label="Why"><p className="whitespace-pre-wrap break-words">{why}</p></Field>}
    </div>
  );
}

// ---------------------------------------------------------------- reproduction tests

function ReproBody(p: Common & { a: CaseArtifact; pl: Record<string, unknown> }) {
  const { a, pl } = p;
  const sql = str(pl.sql) || a.content || "";
  const [run, setRun] = useState<TestRun | null>(null);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const live = useLive();
  const seq = useSeq();
  const go = () => start(async () => {
    const ticket = seq.next();
    setError("");
    try {
      const r = await runArtifact(p.caseId, a.artifact_id);
      if (!live.current || !seq.current(ticket)) return;
      if (!r.ok) { setError(r.error); return; }
      setRun(r.data);
      p.setOutcome(a.artifact_id, r.data.outcome);
    } catch (e) {
      if (live.current && seq.current(ticket)) setError(msgOf(e, "The test did not run."));
    }
  });
  const last = lastOutcome(a, p.outcomes);
  return (
    <div className="space-y-2">
      {sql ? <SqlBlock sql={sql} label="Test SQL" /> : <Muted>No SQL.</Muted>}
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        {show(pl.expected) && <><dt className="text-muted-foreground">Expected</dt><dd className="whitespace-pre-wrap break-words">{show(pl.expected)}</dd></>}
        {show(pl.severity) && <><dt className="text-muted-foreground">Severity</dt><dd>{show(pl.severity)}</dd></>}
        {show(pl.category) && <><dt className="text-muted-foreground">Category</dt><dd>{words(show(pl.category))}</dd></>}
        {!run && last && <><dt className="text-muted-foreground">Last run</dt><dd><OutcomePill outcome={last} /></dd></>}
      </dl>
      {p.canWork && up(a.status) !== "REJECTED" && (
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" disabled={pending || !sql} onClick={go}>
            {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}{run ? "Run again" : "Run"}</Button>
          <span className="text-[11px] text-muted-foreground">Read-only; samples hide personal data.</span>
        </div>
      )}
      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
      {run && <RunResult run={run} />}
    </div>
  );
}

const MASK_TOKEN = /^(\*{3,}|<masked>|\[masked\]|masked)$/i;

function RunResult({ run }: { run: TestRun }) {
  const columns = list(run.columns);
  const maskedCols = new Set(Array.isArray(run.masked) ? run.masked.map((c) => c.toUpperCase()) : []);
  const rows = list(run.sample).map((r) => (Array.isArray(r) ? r : columns.map((c) => (r && typeof r === "object" ? (r as Record<string, unknown>)[c] : undefined))));
  const cell = (v: unknown, col: string) => {
    if (maskedCols.has(col.toUpperCase()) || (typeof v === "string" && MASK_TOKEN.test(v)) || (v && typeof v === "object" && (v as { masked?: unknown }).masked === true)) {
      return <span className="rounded bg-muted px-1 text-[10px] italic text-muted-foreground">masked</span>;
    }
    if (v === null || v === undefined) return <span className="text-muted-foreground">null</span>;
    return typeof v === "object" ? JSON.stringify(v) : String(v);
  };
  return (
    <div className="space-y-1.5 rounded-lg border bg-muted/20 px-3 py-2">
      <p className="flex flex-wrap items-center gap-2">
        <OutcomePill outcome={run.outcome} />
        {typeof run.rows_returned === "number" && <span className="text-muted-foreground">{run.rows_returned} row{run.rows_returned === 1 ? "" : "s"}</span>}
        {show(run.measured) && <span className="text-muted-foreground">measured <span className="font-mono text-foreground">{show(run.measured)}</span></span>}
        {run.masked === true && <span className="text-[11px] text-muted-foreground">Personal data masked.</span>}
      </p>
      {run.detail && <p className="whitespace-pre-wrap break-words text-muted-foreground">{run.detail}</p>}
      {columns.length > 0 && rows.length > 0 && (
        <div className="max-h-64 overflow-auto rounded border bg-background">
          <table className="w-full text-[11px]">
            <thead className="sticky top-0 bg-card text-left text-muted-foreground">
              <tr className="border-b">{columns.map((c) => <th key={c} className="whitespace-nowrap px-2 py-1 font-mono font-medium">{c}</th>)}</tr>
            </thead>
            <tbody className="divide-y">
              {rows.map((r, i) => <tr key={i}>{columns.map((c, j) => <td key={c} className="max-w-[16rem] truncate whitespace-nowrap px-2 py-1 font-mono">{cell(r[j], c)}</td>)}</tr>)}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- verify

function VerifyBar({ caseId, count, setOutcome, reload, onNotice }: {
  caseId: string; count: number; setOutcome: (id: string, o: string) => void; reload: () => Promise<void>; onNotice: (t: string) => void;
}) {
  const [result, setResult] = useState<VerifyResult | null>(null);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const live = useLive();
  const seq = useSeq();
  const verify = () => start(async () => {
    const ticket = seq.next();
    setError(""); setResult(null);
    try {
      const r = await verifyCase(caseId);
      if (!live.current || !seq.current(ticket)) return;
      if (!r.ok) { setError(r.error); return; }
      list(r.data.results).forEach((x) => setOutcome(x.artifact_id, x.outcome));
      if (r.data.verified) {
        onNotice("All accepted tests pass. The case is verified.");
        await reload();
      } else setResult(r.data);
    } catch (e) {
      if (live.current && seq.current(ticket)) setError(msgOf(e, "Verification failed to run."));
    }
  });
  const failed = list(result?.results).filter((x) => !isPass(x.outcome));
  return (
    <div className="space-y-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={pending} onClick={verify}>
          {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}Verify fix</Button>
        <span className="text-muted-foreground">Runs the {count} accepted reproduction test{count === 1 ? "" : "s"}. All must pass for the case to be verified.</span>
      </div>
      {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
      {result && (
        <div className="space-y-1">
          <p role="alert" className="font-medium text-destructive">{failed.length} of {result.results.length} test{result.results.length === 1 ? "" : "s"} did not pass. The case stays in progress.</p>
          <ul className="space-y-0.5">
            {result.results.map((x) => (
              <li key={x.artifact_id} className="flex items-center gap-2"><OutcomePill outcome={x.outcome} /><span className="min-w-0 truncate">{x.title || x.artifact_id}</span></li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- publish as PR

/** Publish a dbt patch as a draft PR: a dry run shows the branch and diff first; the confirm sends that preview's token. */
function PublishDialog({ caseId, a, onClose }: { caseId: string; a: CaseArtifact; onClose: () => void }) {
  useScrollLock();
  const [preview, setPreview] = useState<PublishPreview | null>(null);
  const [done, setDone] = useState<PublishDone | null>(null);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [pending, start] = useTransition();
  const live = useLive();
  const seq = useSeq();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !pending) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, pending]);

  const doPreview = () => start(async () => {
    const ticket = seq.next();
    setError(""); setInfo(""); setPreview(null);
    try {
      const r = await previewPublish(caseId, a.artifact_id);
      if (!live.current || !seq.current(ticket)) return;
      if (r.ok) setPreview(r.data);
      else if (r.status === 202) setInfo(r.error);
      else setError(r.error);
    } catch (e) {
      if (live.current && seq.current(ticket)) setError(msgOf(e, "The preview failed."));
    }
  });

  const confirm = () => start(async () => {
    if (!preview) return;
    const ticket = seq.next();
    setError(""); setInfo("");
    try {
      const r = await publishArtifact(caseId, a.artifact_id, preview.preview_token);
      if (!live.current || !seq.current(ticket)) return;
      if (r.ok) { setDone(r.data); setPreview(null); }
      else if (r.status === 202) { setInfo(r.error); setPreview(null); }
      else setError(r.error);
    } catch (e) {
      if (live.current && seq.current(ticket)) setError(msgOf(e, "Publishing failed."));
    }
  });

  const prUrl = safeUrl(done?.pr_url);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-label="Publish as pull request"
         onClick={(e) => { if (e.target === e.currentTarget && !pending) onClose(); }}>
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-2xl border bg-background shadow-2xl">
        <div className="flex items-start justify-between border-b px-5 py-3">
          <div>
            <h3 className="flex items-center gap-1.5 text-base font-semibold"><GitPullRequest className="h-4 w-4" />Publish as PR</h3>
            <p className="text-xs text-muted-foreground">Opens a draft pull request with this patch, linked to the case. Preview first; nothing is pushed until you confirm.</p>
          </div>
          <button type="button" onClick={onClose} disabled={pending} aria-label="Close" className="rounded-md p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
        </div>
        <div className="flex-1 space-y-3 overflow-y-auto overscroll-contain px-5 py-3 text-xs">
          {done ? (
            <div role="status" className="space-y-1 rounded-lg bg-success/10 px-3 py-2 text-success">
              <p>Draft pull request {words(done.status || "opened")} on <span className="font-mono">{done.branch}</span>.</p>
              {prUrl && <a href={prUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-medium underline">Open the pull request<ExternalLink className="h-3 w-3" /></a>}
            </div>
          ) : preview ? (
            <div className="space-y-2">
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                <dt className="text-muted-foreground">Repository</dt><dd className="break-all font-mono">{preview.repo}</dd>
                <dt className="text-muted-foreground">Base branch</dt><dd className="break-all font-mono">{preview.base_branch}</dd>
                <dt className="text-muted-foreground">New branch</dt><dd className="break-all font-mono">{preview.branch}</dd>
                <dt className="text-muted-foreground">File</dt><dd className="break-all font-mono">{preview.path}</dd>
              </dl>
              {preview.diff ? <UnifiedDiff diff={preview.diff} /> : <Muted>The diff is empty.</Muted>}
            </div>
          ) : (
            <Muted>Preview checks the file against the current index and shows exactly what the pull request will contain.</Muted>
          )}
          {error && <Alert onDismiss={() => setError("")}>{error}</Alert>}
          {info && <Info>{info}</Info>}
        </div>
        <div className="flex justify-end gap-2 border-t px-5 py-3">
          <Button variant="ghost" onClick={onClose} disabled={pending}>{done || info ? "Close" : "Cancel"}</Button>
          {!done && (
            <Button variant={preview ? "outline" : "default"} onClick={doPreview} disabled={pending}>
              {pending && !preview ? <Loader2 className="h-4 w-4 animate-spin" /> : null}{preview ? "Preview again" : "Preview"}</Button>
          )}
          {!done && preview && (
            <Button onClick={confirm} disabled={pending || !preview.diff}>
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitPullRequest className="h-4 w-4" />}Open draft PR</Button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- resolve

/** What resolving proposes as knowledge, for the domain stewards to approve. Mirrors what the API writes on resolve. */
export function KnowledgePreview({ ai, fallbackSummary, artifacts, outcomes }: {
  ai: CaseAi | null; fallbackSummary: string | null; artifacts: CaseArtifact[]; outcomes: Record<string, string>;
}) {
  const top = list(ai?.hypotheses)[0]?.cause ?? "";
  const cause = top || ai?.summary || fallbackSummary || "";
  const applied = artifacts.filter((a) => up(a.status) === "APPLIED" && up(a.type) !== "REPRO_TEST" && up(a.type) !== "KNOWLEDGE_DRAFT");
  const tests = acceptedTests(artifacts);
  const passing = tests.filter((a) => isPass(lastOutcome(a, outcomes)));
  const drafts = artifacts.filter((a) => up(a.type) === "KNOWLEDGE_DRAFT" && up(a.status) === "ACCEPTED");
  return (
    <div className="space-y-2 rounded-lg border bg-muted/20 px-3 py-2 text-xs">
      <p className="font-medium">Proposed as knowledge on resolve</p>
      <div className="space-y-1">
        <p><span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">case resolution</span></p>
        <p className="whitespace-pre-wrap break-words"><span className="text-muted-foreground">Cause: </span>{cause || "not known"}</p>
        <p className="break-words"><span className="text-muted-foreground">Fix: </span>
          {applied.length ? applied.map((a) => a.title || words(a.type)).join("; ") : "no fix marked applied"}</p>
      </div>
      {tests.length > 0 && (
        <div className="space-y-0.5">
          <p><span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">qa test</span> {passing.length} of {tests.length} accepted test{tests.length === 1 ? "" : "s"}</p>
          {passing.length > 0 && <ul className="list-disc pl-4">{passing.map((a) => <li key={a.artifact_id}>{a.title || "Reproduction test"}</li>)}</ul>}
          {passing.length < tests.length && <p className="text-muted-foreground">Tests without a passing run are left out. Verify the fix to run them.</p>}
        </div>
      )}
      {drafts.length > 0 && (
        <div className="space-y-0.5">
          <p><span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">knowledge drafts</span> {drafts.length} accepted</p>
          <ul className="list-disc pl-4">{drafts.map((a) => <li key={a.artifact_id}>{str(payloadOf(a).title) || a.title || "Draft"}</li>)}</ul>
        </div>
      )}
      <p className="border-t pt-1.5 text-muted-foreground">Domain stewards approve it; you cannot approve your own.</p>
    </div>
  );
}
