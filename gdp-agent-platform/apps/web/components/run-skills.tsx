"use client";

import Link from "next/link";
import { useEffect, useRef, useState, useTransition } from "react";
import { FlaskConical, Loader2, Sparkles, X } from "lucide-react";
import { useAccess } from "@/components/access";
import { cn } from "@/lib/utils";
import { runSkills, tryOnRun } from "@/app/skills/actions";
import { pretty, type RunSkills as Data } from "@/app/skills/types";

/** Run header chip: the skill versions this run loaded, and candidates it can try instead of production. */
export function RunSkills({ runId, disabled }: { runId: string; disabled?: boolean }) {
  const { can } = useAccess();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const load = () => start(async () => {
    const r = await runSkills(runId);
    if (r.ok) setData(r.data); else setError(r.error);
  });
  const set = (name: string, skillId: string | null) => start(async () => {
    const r = await tryOnRun(runId, name, skillId);
    if (r.ok) { setData(r.data); setError(""); } else setError(r.error);
  });

  const trials = data?.overrides ?? [];
  const tried = new Set(trials.map((o) => o.skill_name));
  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => { setOpen(!open); if (!data) load(); }} aria-expanded={open}
              className={cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium hover:border-primary hover:text-primary",
                            trials.length && "border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300")}>
        <Sparkles className="h-3 w-3" />Skills{trials.length ? `: trying ${trials.length}` : ""}
      </button>
      {open && (
        <div className="absolute left-0 top-8 z-40 w-[420px] max-w-[90vw] rounded-xl border bg-card shadow-xl">
          <div className="flex items-center justify-between border-b px-4 py-2.5">
            <p className="text-sm font-semibold">Skills for this run</p>
            <button type="button" aria-label="Close" onClick={() => setOpen(false)} className="rounded p-0.5 hover:bg-muted"><X className="h-3.5 w-3.5" /></button>
          </div>
          <div className="max-h-[60vh] space-y-4 overflow-y-auto overscroll-contain px-4 py-3 text-xs">
            {pending && !data && <p className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading…</p>}
            {error && <p role="alert" className="text-destructive">{error}</p>}
            {data && (
              <>
                <div>
                  <p className="mb-1.5 font-medium">Loaded so far</p>
                  {data.loaded.length ? (
                    <ul className="space-y-1">
                      {data.loaded.map((l) => (
                        <li key={`${l.skill_name}-${l.skill_id}`} className="flex items-center gap-2">
                          <Link href={`/skills/${encodeURIComponent(l.skill_name)}`} className="min-w-0 flex-1 truncate hover:text-primary">{pretty(l.skill_name)}</Link>
                          <span className="font-mono text-[10px] text-muted-foreground">v{(l.version ?? "").split("+")[0]}</span>
                          {l.picked_by === "override" && <FlaskConical className="h-3 w-3 text-amber-600" />}
                        </li>
                      ))}
                    </ul>
                  ) : <p className="text-muted-foreground">No stage has loaded a skill yet.</p>}
                </div>
                <div>
                  <p className="mb-1 font-medium">Try a candidate on this run</p>
                  <p className="mb-2 text-muted-foreground">Stages you run from now on load the chosen version instead of production. Other runs are not affected.</p>
                  {trials.map((o) => (
                    <div key={o.skill_name} className="mb-1 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50/60 px-2 py-1.5 dark:border-amber-900 dark:bg-amber-950/30">
                      <FlaskConical className="h-3 w-3 text-amber-600" />
                      <span className="min-w-0 flex-1 truncate">{pretty(o.skill_name)} <span className="font-mono text-[10px] text-muted-foreground">v{o.version.split("+")[0]}</span></span>
                      {can("SKILL.EDIT") && !disabled && <button type="button" disabled={pending} onClick={() => set(o.skill_name, null)} className="text-primary hover:underline">Back to production</button>}
                    </div>
                  ))}
                  {data.candidates.filter((c) => !tried.has(c.skill_name)).map((c) => (
                    <div key={c.skill_name} className="mb-1 flex items-center gap-2 rounded-lg border px-2 py-1.5">
                      <span className="min-w-0 flex-1 truncate">{pretty(c.skill_name)} <span className="font-mono text-[10px] text-muted-foreground">v{c.version.split("+")[0]}</span></span>
                      {can("SKILL.EDIT") && !disabled && <button type="button" disabled={pending} onClick={() => set(c.skill_name, c.skill_id)} className="text-primary hover:underline">Try it</button>}
                    </div>
                  ))}
                  {!trials.length && !data.candidates.length && <p className="text-muted-foreground">No candidate versions are waiting. Create one on <Link href="/skills" className="text-primary hover:underline">Skills</Link>.</p>}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
