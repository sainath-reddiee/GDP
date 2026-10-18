"use client";

import { Fragment, useEffect, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Bot, UserCheck } from "lucide-react";
import { cn } from "@/lib/utils";

// What the platform does once something is onboarded (docs/onboarding-inputs.md, "What happens after onboarding"):
// the agent drafts each artifact and a person approves it; nothing is promoted without an approval.
const STAGES: { name: string; review: boolean }[] = [
  { name: "Land", review: false },
  { name: "Profile", review: false },
  { name: "Map", review: true },
  { name: "STTM", review: true },
  { name: "Checks", review: true },
  { name: "Tests", review: true },
  { name: "dbt", review: true },
  { name: "Promote", review: true },
];

/** A small live run of the pipeline every onboarding feeds: a cursor walks the stages the way a run does. */
export function PipelinePreview() {
  const still = useReducedMotion();
  const [at, setAt] = useState(still ? STAGES.length - 1 : 0);
  useEffect(() => {
    if (still) return;
    const id = window.setInterval(() => setAt((i) => (i + 1) % (STAGES.length + 2)), 900);
    return () => window.clearInterval(id);
  }, [still]);

  return (
    <motion.section initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.25, type: "spring", stiffness: 220, damping: 28 }}
                    className="surface overflow-hidden" aria-label="What happens after onboarding" data-testid="pipeline-preview">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b px-5 py-3">
        <h2 className="text-sm font-semibold">After onboarding</h2>
        <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"><Bot className="h-3.5 w-3.5 text-agent" /> the agent drafts</span>
        <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground"><UserCheck className="h-3.5 w-3.5 text-human" /> a person approves</span>
      </header>
      <div className="overflow-x-auto px-5 py-5">
        <ol className="flex min-w-[720px] items-center">
          {STAGES.map((s, i) => {
            const done = i < at;
            const active = i === at;
            return (
              <Fragment key={s.name}>
                {i > 0 && (
                  <li aria-hidden className="relative h-px flex-1 bg-border">
                    <motion.span className="absolute inset-0 origin-left bg-primary" initial={false}
                                 animate={{ scaleX: done || active ? 1 : 0 }} transition={{ duration: still ? 0 : 0.35, ease: [0.2, 0.8, 0.2, 1] }} />
                  </li>
                )}
                <li className="relative flex flex-col items-center gap-1.5">
                  <motion.span animate={{ scale: active ? 1.08 : 1 }} transition={{ type: "spring", stiffness: 500, damping: 26 }}
                               className={cn("grid h-9 min-w-[64px] place-items-center rounded-md border px-2.5 font-mono text-xs transition-colors duration-300",
                                 active ? "border-primary bg-primary text-primary-foreground shadow-card"
                                   : done ? "border-primary/40 bg-primary/[0.06] text-primary" : "bg-card text-muted-foreground")}>
                    {s.name}
                  </motion.span>
                  <span className={cn("flex h-4 items-center gap-1 text-[10px] font-medium transition-colors duration-300",
                    s.review ? (done ? "text-human" : "text-muted-foreground/70") : "text-transparent")}>
                    {s.review && <><UserCheck className="h-3 w-3" /> review</>}
                  </span>
                </li>
              </Fragment>
            );
          })}
        </ol>
      </div>
    </motion.section>
  );
}
