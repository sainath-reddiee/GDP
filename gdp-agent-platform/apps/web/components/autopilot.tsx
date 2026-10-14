"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, CircleAlert, FastForward, Flag, Loader2, X } from "lucide-react";
import { useAccess } from "@/components/access";
import { cn } from "@/lib/utils";
import { autopilotStatus, startAutopilot, type AutopilotJob } from "@/app/runs/[runId]/autopilot-actions";

/** "Run to next gate": every automatic step of the run, in order, until a person has to review something. Shows the
 *  steps as they run and where it stopped. Each step is checked against the person's own privileges first. */
export function Autopilot({ runId, disabled }: { runId: string; disabled?: boolean }) {
  const { canAct } = useAccess();
  const router = useRouter();
  const [job, setJob] = useState<AutopilotJob | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
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
    void autopilotStatus(runId).then((r) => { if (r.ok && r.data.status === "RUNNING") { setJob(r.data); setOpen(true); timer.current = setTimeout(poll, 3000); } });
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [runId]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (!canAct("RUN.OPERATE")) return null;
  const running = job?.status === "RUNNING";
  const start = async () => {
    setError(""); setOpen(true);
    const r = await startAutopilot(runId);
    if (!r.ok) { setError(r.error); return; }
    setJob(r.data);
    timer.current = setTimeout(poll, 2000);
  };
  const stop = job?.stop;

  return (
    <div className="relative">
      <button type="button" onClick={running ? () => setOpen(!open) : start} disabled={disabled} data-testid="autopilot"
              title="Run every automatic step until the next review"
              className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-semibold text-primary-foreground disabled:opacity-50">
        {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FastForward className="h-3.5 w-3.5" />}
        {running ? "Running to next gate…" : "Run to next gate"}
      </button>
      {open && (job || error) && (
        <div role="status" className="popover absolute right-0 top-10 z-40 w-[380px] p-3 shadow-pop">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-semibold">Autopilot</p>
            <button type="button" aria-label="Close" onClick={() => setOpen(false)} className="rounded p-0.5 hover:bg-muted"><X className="h-3.5 w-3.5" /></button>
          </div>
          {error && <p className="text-xs text-destructive">{error}</p>}
          {job && (
            <ol className="space-y-1">
              {job.steps.map((s, i) => (
                <li key={i} className="flex items-start gap-2 text-xs">
                  {s.error ? <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" /> : <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" />}
                  <span className="min-w-0 flex-1">{s.label}{s.error && <span className="block text-destructive">{s.error}</span>}</span>
                  {s.seconds != null && <span className="tabular-nums text-muted-foreground">{s.seconds}s</span>}
                </li>
              ))}
              {running && <li className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Working…</li>}
              {!job.steps.length && !running && job.next && <li className="text-xs text-muted-foreground">Next: {job.next.label}</li>}
              {job.children.map((c) => (
                <li key={c.run_id} className="rounded-md border px-2 py-1 text-xs">
                  <span className="font-mono font-semibold">{c.entity}</span>: {c.steps.length} step(s), then {c.stop.label.toLowerCase()}
                </li>
              ))}
            </ol>
          )}
          {stop && !running && (
            <p className={cn("mt-2 flex items-start gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium",
              stop.kind === "GATE" ? "bg-amber-500/10 text-amber-700 dark:text-amber-300" : stop.kind === "DONE" ? "bg-emerald-500/10 text-emerald-700" : "bg-rose-500/10 text-rose-700 dark:text-rose-300")}>
              <Flag className="mt-0.5 h-3.5 w-3.5 shrink-0" />{stop.kind === "GATE" ? `Your review: ${stop.label}` : stop.label}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
