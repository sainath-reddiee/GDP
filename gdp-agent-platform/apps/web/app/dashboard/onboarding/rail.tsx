"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, LayoutGroup, MotionConfig, motion, type Transition } from "framer-motion";
import { ArrowRight, Check, X, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

// The onboarding page reads like a pipeline run: each step is a node on a rail, a finished step fills its edge down
// to the next one, and the path chosen so far is shown as a route in the header.

const SPRING: Transition = { type: "spring", stiffness: 420, damping: 36, mass: 0.8 };
const SOFT: Transition = { type: "spring", stiffness: 260, damping: 30 };

/* ------------------------------------------------------------------ route trail */

type TrailCtx = { segments: (string | null)[]; set: (depth: number, segment: string | null) => void };
const Trail = createContext<TrailCtx>({ segments: [], set: () => {} });

export function OnboardingShell({ children }: { children: ReactNode }) {
  const [segments, setSegments] = useState<(string | null)[]>([]);
  const set = useCallback((depth: number, segment: string | null) => setSegments((s) => {
    if (s[depth] === segment && (segment !== null || s.length <= depth)) return s;
    const next = s.slice(0, depth);
    if (segment) next[depth] = segment;
    return next;
  }), []);
  const value = useMemo(() => ({ segments, set }), [segments, set]);
  return (
    <MotionConfig reducedMotion="user">
      <Trail.Provider value={value}>{children}</Trail.Provider>
    </MotionConfig>
  );
}

/** Publish this level of the route (null clears it and everything after it). */
export function useTrailSegment(depth: number, segment: string | null) {
  const { set } = useContext(Trail);
  useEffect(() => { set(depth, segment); }, [depth, segment, set]);
  useEffect(() => () => set(depth, null), [depth, set]);
}

/** ~/onboard/source/snowflake-share, built segment by segment as choices are made. */
export function RouteTrail() {
  const { segments } = useContext(Trail);
  const parts = segments.filter((s): s is string => !!s);
  return (
    <div className="mt-3 inline-flex h-8 max-w-full items-center overflow-hidden rounded-md border bg-card px-2.5 font-mono text-xs shadow-xs"
         aria-label={`Onboarding path: ${["onboard", ...parts].join(" / ")}`} data-testid="onboard-route">
      <span className="text-muted-foreground">~/</span>
      <span className="text-foreground/80">onboard</span>
      <AnimatePresence initial={false}>
        {parts.map((p, i) => (
          <motion.span key={`${i}-${p}`} layout className="inline-flex items-center whitespace-nowrap"
                       initial={{ opacity: 0, x: -6, filter: "blur(3px)" }} animate={{ opacity: 1, x: 0, filter: "blur(0px)" }}
                       exit={{ opacity: 0, x: -4, filter: "blur(3px)", transition: { duration: 0.12 } }} transition={SOFT}>
            <span className="px-0.5 text-muted-foreground/60">/</span>
            <span className={i === parts.length - 1 ? "font-medium text-primary" : "text-foreground/80"}>{p}</span>
          </motion.span>
        ))}
      </AnimatePresence>
      <motion.span layout aria-hidden className="ml-0.5 inline-block h-3.5 w-[7px] rounded-[1px] bg-primary/70"
                   animate={{ opacity: [1, 1, 0, 0] }} transition={{ duration: 1.1, repeat: Infinity, times: [0, 0.5, 0.5, 1] }} />
    </div>
  );
}

/* ------------------------------------------------------------------ rail */

/** One node on the rail. A finished step fills its edge down to the next; the last step on screen has no edge. */
export function RailStep({ n, title, done = false, last = false, aside, children }: {
  n: number; title: string; done?: boolean; last?: boolean; aside?: ReactNode; children: ReactNode;
}) {
  return (
    <div className="grid grid-cols-[28px_minmax(0,1fr)] gap-x-4">
      <div className="relative flex justify-center">
        <motion.span layout="position"
                     className={cn("relative z-10 grid h-7 w-7 shrink-0 place-items-center rounded-full border text-[11px] font-semibold transition-colors duration-300",
                       done ? "border-primary bg-primary text-primary-foreground" : "border-primary/40 bg-card text-primary ring-4 ring-primary/10")}>
          <AnimatePresence mode="wait" initial={false}>
            {done ? (
              <motion.span key="done" initial={{ scale: 0.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.4, opacity: 0 }} transition={SPRING}>
                <Check className="h-3.5 w-3.5" strokeWidth={3} />
              </motion.span>
            ) : (
              <motion.span key="n" initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.6, opacity: 0 }} transition={SPRING}>{n}</motion.span>
            )}
          </AnimatePresence>
        </motion.span>
        {!last && (
          <span aria-hidden className="absolute bottom-0 top-7 w-px overflow-hidden bg-border">
            <motion.span className="absolute inset-0 origin-top bg-primary" initial={false}
                         animate={{ scaleY: done ? 1 : 0 }} transition={{ duration: 0.45, ease: [0.2, 0.8, 0.2, 1] }} />
            {/* a packet travels down the edge when the step completes: the choice flowing on to the next step */}
            {done && (
              <motion.span key={`packet-${n}`} className="absolute left-1/2 h-6 w-[3px] -translate-x-1/2 rounded-full bg-gradient-to-b from-transparent to-primary"
                           initial={{ top: "-24px", opacity: 1 }} animate={{ top: "100%", opacity: 0 }} transition={{ duration: 0.7, ease: "easeIn", delay: 0.1 }} />
            )}
          </span>
        )}
      </div>
      <div className={cn("min-w-0", !last && "pb-9")}>
        <div className="flex h-7 items-center gap-3">
          <span className="text-xs font-semibold uppercase tracking-[0.12em] text-foreground/80">{title}</span>
          {aside}
        </div>
        <div className="mt-3">{children}</div>
      </div>
    </div>
  );
}

/** Content arriving under a step: rises in and comes into focus. The filter and transform are cleared afterwards,
 *  because even blur(0px) makes this box the containing block of any fixed dialog inside it. */
export function Reveal({ children, className }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  return (
    <motion.div ref={ref} className={className} initial={{ opacity: 0, y: 10, filter: "blur(6px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                transition={{ ...SOFT, opacity: { duration: 0.25 }, filter: { duration: 0.3 } }}
                onAnimationComplete={() => { if (ref.current) { ref.current.style.filter = ""; ref.current.style.transform = ""; } }}>
      {children}
    </motion.div>
  );
}

/* ------------------------------------------------------------------ choice step */

export type Choice<K extends string> = { key: K; label: string; icon: LucideIcon; soon?: boolean; slug?: string };

const editable = (el: EventTarget | null) => el instanceof HTMLElement && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));

/** One decision on the rail. Every option shows as a tile (number keys pick one); once chosen, the other tiles step
 *  away, the chosen one settles into a single row, and Change brings the rest back. */
export function ChoiceStep<K extends string>({ n, title, choices, value, onChange, testId, depth }: {
  n: number; title: string; choices: Choice<K>[]; value: K | null; onChange: (key: K | null) => void; testId: string; depth: number;
}) {
  const chosen = choices.find((c) => c.key === value) ?? null;
  useTrailSegment(depth, chosen ? chosen.slug ?? chosen.key : null);

  useEffect(() => {
    if (chosen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || editable(e.target)) return;
      const i = Number(e.key) - 1;
      if (Number.isInteger(i) && i >= 0 && i < choices.length) { e.preventDefault(); onChange(choices[i].key); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [chosen, choices, onChange]);

  const shown = chosen ? [chosen] : choices;
  return (
    <RailStep n={n} title={title} done={!!chosen} last={!chosen}
              aside={!chosen && (
                <span className="hidden items-center gap-1 text-[11px] text-muted-foreground sm:inline-flex">
                  press <kbd className="kbd">1</kbd>{choices.length > 1 && <>to <kbd className="kbd">{choices.length}</kbd></>}
                </span>
              )}>
      <LayoutGroup id={testId}>
        <motion.div layout transition={SPRING} role="group" aria-label={title} data-testid={testId}
                    className={cn("grid gap-2", chosen ? "grid-cols-1" : choices.length === 2 ? "sm:grid-cols-2" : "sm:grid-cols-2 lg:grid-cols-3")}>
          <AnimatePresence mode="popLayout">
            {shown.map((c) => {
              const i = choices.indexOf(c);
              const on = chosen?.key === c.key;
              return (
                <motion.div key={c.key} layout transition={SPRING}
                            initial={{ opacity: 0, y: 8, filter: "blur(4px)" }}
                            animate={{ opacity: 1, y: 0, filter: "blur(0px)", transition: { ...SPRING, delay: chosen ? 0 : i * 0.04 } }}
                            exit={{ opacity: 0, scale: 0.95, filter: "blur(4px)", transition: { duration: 0.16, ease: "easeOut" } }}
                            onMouseMove={(e) => {
                              const r = e.currentTarget.getBoundingClientRect();
                              e.currentTarget.style.setProperty("--mx", `${e.clientX - r.left}px`);
                              e.currentTarget.style.setProperty("--my", `${e.clientY - r.top}px`);
                            }}
                            data-testid={on ? `${testId}-chosen` : undefined}
                            className={cn("group relative flex min-w-0 items-center gap-3 overflow-hidden rounded-lg border transition-colors duration-200",
                              on ? "border-primary/35 bg-primary/[0.045] py-2 pl-2 pr-1.5"
                                : cn("bg-card px-3 py-3 hover:border-primary/40", c.soon && "border-dashed"))}>
                  {!on && (
                    <>
                      {/* a soft spotlight that follows the pointer */}
                      <span aria-hidden className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover:opacity-100"
                            style={{ background: "radial-gradient(240px circle at var(--mx, 50%) var(--my, 50%), hsl(var(--primary) / 0.09), transparent 70%)" }} />
                      <button type="button" onClick={() => onChange(c.key)} data-testid={`${testId}-${c.key}`} aria-label={c.label}
                              className="absolute inset-0 z-10 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
                    </>
                  )}
                  <motion.span layout="position"
                               className={cn("relative grid shrink-0 place-items-center rounded-md transition-colors duration-200",
                                 on ? "h-8 w-8 bg-primary text-primary-foreground"
                                   : c.soon ? "h-9 w-9 bg-muted text-muted-foreground"
                                     : "h-9 w-9 bg-muted text-foreground/70 group-hover:bg-primary group-hover:text-primary-foreground")}>
                    <c.icon className="h-[18px] w-[18px]" />
                  </motion.span>
                  <motion.span layout="position" className="relative flex min-w-0 flex-col">
                    <span className={cn("line-clamp-2 text-sm", on ? "font-semibold" : "font-medium", c.soon && !on && "text-muted-foreground")}>{c.label}</span>
                    {c.soon && <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">Coming soon</span>}
                  </motion.span>
                  {on ? (
                    <motion.button type="button" onClick={() => onChange(null)} data-testid={`${testId}-change`}
                                   initial={{ opacity: 0, x: 6 }} animate={{ opacity: 1, x: 0, transition: { delay: 0.12, ...SOFT } }}
                                   className="relative z-10 ml-auto inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium text-muted-foreground transition hover:bg-background hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                      <X className="h-3.5 w-3.5" /> Change
                    </motion.button>
                  ) : (
                    <span className="relative ml-auto grid h-5 w-5 shrink-0 place-items-center">
                      <kbd className="kbd absolute transition-all duration-200 group-hover:scale-75 group-hover:opacity-0">{i + 1}</kbd>
                      <ArrowRight className="absolute h-4 w-4 -translate-x-1 text-primary opacity-0 transition-all duration-200 group-hover:translate-x-0 group-hover:opacity-100" aria-hidden />
                    </span>
                  )}
                </motion.div>
              );
            })}
          </AnimatePresence>
        </motion.div>
      </LayoutGroup>
    </RailStep>
  );
}
