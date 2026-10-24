"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Check, CircleAlert, FastForward, Flag, Loader2 } from "lucide-react";
import { autopilotStatus, startAutopilot, type AutopilotJob } from "@/app/runs/[runId]/autopilot-actions";
import { cn } from "@/lib/utils";

// Build end to end (Ready to build): autopilot (services/workflow/autopilot.py) runs every automatic step of a ready
// run in order, generating the code and the data quality checks and validating the code with the review and standards
// agents, until the code review gate a person owns. Each step is checked against the signed-in person's privileges
// first, so a preview of another role disables it (it would run as the admin).

function Steps({ job }: { job: AutopilotJob }) {
  const running = job.status === "RUNNING";
  return (
    <div className="mt-2 rounded-lg border border-[var(--os-line)] p-2.5 text-[11.5px]" role="status">
      <ol className="flex flex-col gap-1">
        {job.steps.map((s, i) => (
          <li key={i} className="flex items-start gap-1.5">
            {s.error ? <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--os-red-ink)]" /> : <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--os-emerald)]" />}
            <span className="min-w-0 flex-1">{s.label}{s.error && <span className="block text-[var(--os-red-ink)]">{s.error}</span>}</span>
            {s.seconds != null && <span className="os-faint tabular-nums">{s.seconds}s</span>}
          </li>
        ))}
        {running && <li className="os-faint flex items-center gap-1.5"><Loader2 className="h-3.5 w-3.5 os-spin" />Working…</li>}
      </ol>
      {job.stop && !running && (
        <p className={cn("mt-1.5 flex items-start gap-1.5 font-semibold",
          job.stop.kind === "GATE" || job.stop.kind === "DONE" ? "text-[var(--os-green-ink)]" : "text-[var(--os-red-ink)]")}>
          <Flag className="mt-0.5 h-3.5 w-3.5 shrink-0" />{job.stop.kind === "GATE" ? `Stopped at a person's gate: ${job.stop.label}` : job.stop.label}
        </p>
      )}
    </div>
  );
}

/** One run: start autopilot, follow it while it runs (it may already be running), refresh the board when it stops. */
export function BuildEndToEnd({ runId, disabled, previewing, label = "Build end to end" }: {
  runId: string; disabled?: boolean; previewing: boolean; label?: string;
}) {
  const router = useRouter();
  const [job, setJob] = useState<AutopilotJob | null>(null);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const poll = () => {
    void autopilotStatus(runId).then((r) => {
      if (!r.ok) { setError(r.error); return; }
      setJob(r.data);
      if (r.data.status === "RUNNING") timer.current = setTimeout(poll, 3000);
      else router.refresh();
    });
  };
  useEffect(() => {
    void autopilotStatus(runId).then((r) => { if (r.ok && r.data.status === "RUNNING") { setJob(r.data); timer.current = setTimeout(poll, 3000); } });
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [runId]);   // eslint-disable-line react-hooks/exhaustive-deps

  const running = job?.status === "RUNNING";
  const go = () => start(async () => {
    setError("");
    const r = await startAutopilot(runId);
    if (!r.ok) { setError(r.error); return; }
    setJob(r.data);
    timer.current = setTimeout(poll, 2000);
  });
  return (
    <div data-testid={`build-${runId.slice(0, 8)}`}>
      <button type="button" className="os-btn os-btn-primary" onClick={go} disabled={disabled || previewing || pending || running}
              data-testid="build-end-to-end" title="Generate the code and its checks, then validate with the review and standards agents, up to code review">
        {running || pending ? <Loader2 className="h-3.5 w-3.5 os-spin" /> : <FastForward className="h-3.5 w-3.5" />}
        {running ? "Building…" : label}
      </button>
      {previewing && <p className="os-faint mt-1 text-[11px]">Off while you preview another role: it would run as you.</p>}
      {error && <p role="alert" className="mt-1 text-[11px] text-[var(--os-red-ink)]">{error}</p>}
      {job && (job.steps.length > 0 || running || job.stop) && <Steps job={job} />}
    </div>
  );
}

/** Every ready run of a domain, one after another: each starts its own autopilot (they run in the API in parallel). */
export function BuildAllReady({ runIds, previewing }: { runIds: string[]; previewing: boolean }) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [note, setNote] = useState("");
  const go = () => start(async () => {
    const failed: string[] = [];
    for (const id of runIds) {
      const r = await startAutopilot(id);
      if (!r.ok) failed.push(r.error);
    }
    setNote(failed.length ? `${runIds.length - failed.length} started; ${failed.length} could not start: ${failed[0]}` : `${runIds.length} build${runIds.length === 1 ? "" : "s"} started`);
    router.refresh();
  });
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <button type="button" className="os-btn os-btn-mint" onClick={go} disabled={previewing || pending || !runIds.length} data-testid="build-all-ready">
        {pending ? <Loader2 className="h-3.5 w-3.5 os-spin" /> : <FastForward className="h-3.5 w-3.5" />}Build all ready ({runIds.length})
      </button>
      {note && <span role="status" className="os-faint text-[11px]">{note}</span>}
    </span>
  );
}
