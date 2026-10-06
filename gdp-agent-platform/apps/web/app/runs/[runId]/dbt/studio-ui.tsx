"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { Check, CheckCircle2, ChevronDown, Circle, Copy, Loader2, TriangleAlert, X, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

export type Tone = "done" | "active" | "warn" | "fail" | "idle";

const TONE: Record<Tone, string> = {
  done: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  active: "bg-blue-50 text-blue-700 ring-blue-200",
  warn: "bg-amber-50 text-amber-800 ring-amber-200",
  fail: "bg-red-50 text-red-700 ring-red-200",
  idle: "bg-slate-50 text-slate-600 ring-slate-200",
};

export function StatusPill({ tone, children, className }: { tone: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset", TONE[tone], className)}>
      <span className={cn("h-1.5 w-1.5 rounded-full", {
        "bg-emerald-500": tone === "done", "bg-blue-500": tone === "active", "bg-amber-500": tone === "warn",
        "bg-red-500": tone === "fail", "bg-slate-400": tone === "idle",
      })} />
      {children}
    </span>
  );
}

export function StepIcon({ tone, n, busy }: { tone: Tone; n: number; busy?: boolean }) {
  if (busy) return <Loader2 className="h-5 w-5 animate-spin text-blue-600" />;
  if (tone === "done") return <CheckCircle2 className="h-5 w-5 text-emerald-600" />;
  if (tone === "fail") return <XCircle className="h-5 w-5 text-red-600" />;
  if (tone === "warn") return <TriangleAlert className="h-5 w-5 text-amber-600" />;
  return (
    <span className={cn("flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-semibold",
      tone === "active" ? "bg-blue-600 text-white" : "bg-slate-200 text-slate-600")}>{n}</span>
  );
}

/** A numbered workflow step that collapses to a one-line summary once it is done. */
export function Section({
  id, n, title, subtitle, tone, summary, open, onToggle, actions, children,
}: {
  id: string; n: number; title: string; subtitle?: string; tone: Tone; summary?: ReactNode;
  open: boolean; onToggle: () => void; actions?: ReactNode; children: ReactNode;
}) {
  return (
    <section id={id} className={cn("scroll-mt-24 rounded-xl border bg-card shadow-sm transition-shadow",
      tone === "active" && "ring-1 ring-blue-200", open && "shadow-md")}>
      <header className="flex items-start gap-3 px-5 py-4">
        <button type="button" onClick={onToggle} className="flex min-w-0 flex-1 items-start gap-3 text-left">
          <StepIcon tone={tone} n={n} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-[15px] font-semibold leading-5">{title}</h3>
              {!open && summary && <span className="truncate text-xs text-muted-foreground">{summary}</span>}
            </div>
            {open && subtitle && <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>}
          </div>
          <ChevronDown className={cn("mt-0.5 h-4 w-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
        </button>
        {actions}
      </header>
      {open && <div className="border-t px-5 py-4">{children}</div>}
    </section>
  );
}

export function CopyButton({ text, label = "Copy", className }: { text: string; label?: string; className?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      onClick={() => { navigator.clipboard?.writeText(text); setDone(true); setTimeout(() => setDone(false), 1400); }}
      className={cn("inline-flex items-center gap-1 rounded-md border bg-white px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-50", className)}
    >
      {done ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <Copy className="h-3.5 w-3.5" />}
      {done ? "Copied" : label}
    </button>
  );
}

export function Stat({ label, value, hint, tone = "idle" }: { label: string; value: ReactNode; hint?: string; tone?: Tone }) {
  return (
    <div className={cn("rounded-lg border px-3 py-2", tone === "warn" && "border-amber-200 bg-amber-50/50",
      tone === "fail" && "border-red-200 bg-red-50/50", tone === "done" && "border-emerald-200 bg-emerald-50/40")}>
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-0.5 text-lg font-semibold leading-6">{value}</p>
      {hint && <p className="truncate text-[11px] text-muted-foreground" title={hint}>{hint}</p>}
    </div>
  );
}

export function Callout({ tone, title, children, action }: { tone: Tone; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className={cn("rounded-lg border p-3 text-sm", {
      "border-emerald-200 bg-emerald-50 text-emerald-900": tone === "done",
      "border-blue-200 bg-blue-50 text-blue-900": tone === "active",
      "border-amber-200 bg-amber-50 text-amber-900": tone === "warn",
      "border-red-200 bg-red-50 text-red-900": tone === "fail",
      "border-slate-200 bg-slate-50 text-slate-700": tone === "idle",
    })}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium">{title}</p>
          {children && <div className="mt-1 text-xs leading-relaxed opacity-90">{children}</div>}
        </div>
        {action}
      </div>
    </div>
  );
}

type Toast = { id: number; tone: Tone; text: string };
const ToastCtx = createContext<(tone: Tone, text: string) => void>(() => undefined);
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((tone: Tone, text: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, tone, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "fail" ? 9000 : 4500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-50 flex w-[380px] flex-col gap-2">
        {toasts.map((t) => (
          <div key={t.id} role="status" className={cn("pointer-events-auto flex items-start gap-2 rounded-lg border bg-white p-3 text-sm shadow-lg",
            t.tone === "fail" && "border-red-200", t.tone === "done" && "border-emerald-200")}>
            <StepIcon tone={t.tone === "active" ? "idle" : t.tone} n={0} />
            <p className="min-w-0 flex-1 break-words">{t.text}</p>
            <button type="button" aria-label="Dismiss" onClick={() => setToasts((all) => all.filter((x) => x.id !== t.id))}>
              <X className="h-4 w-4 text-muted-foreground" />
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export type DiffLine = { kind: "same" | "add" | "del"; text: string; a?: number; b?: number };

/** Line diff via LCS (fine for dbt-sized files); falls back to a plain replace view for very large inputs. */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.replace(/\r\n/g, "\n").split("\n");
  const b = after.replace(/\r\n/g, "\n").split("\n");
  if (a.length * b.length > 1_500_000) {
    return [...a.map((t, i) => ({ kind: "del" as const, text: t, a: i + 1 })), ...b.map((t, i) => ({ kind: "add" as const, text: t, b: i + 1 }))];
  }
  const dp: number[][] = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { out.push({ kind: "same", text: a[i], a: i + 1, b: j + 1 }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ kind: "del", text: a[i], a: i + 1 }); i++; }
    else { out.push({ kind: "add", text: b[j], b: j + 1 }); j++; }
  }
  while (i < a.length) { out.push({ kind: "del", text: a[i], a: i + 1 }); i++; }
  while (j < b.length) { out.push({ kind: "add", text: b[j], b: j + 1 }); j++; }
  return out;
}

export function DiffView({ before, after, className }: { before: string; after: string; className?: string }) {
  const lines = useMemo(() => diffLines(before, after), [before, after]);
  const added = lines.filter((l) => l.kind === "add").length;
  const removed = lines.filter((l) => l.kind === "del").length;
  return (
    <div className={cn("overflow-hidden rounded-lg border", className)}>
      <div className="flex items-center gap-3 border-b bg-slate-50 px-3 py-1.5 text-[11px]">
        <span className="font-medium text-emerald-700">+{added}</span>
        <span className="font-medium text-red-700">−{removed}</span>
        <span className="text-muted-foreground">vs. cut-from branch</span>
      </div>
      <pre className="max-h-[32rem] overflow-auto bg-white text-[12px] leading-5">
        {lines.map((l, k) => (
          <div key={k} className={cn("flex", l.kind === "add" && "bg-emerald-50", l.kind === "del" && "bg-red-50")}>
            <span className="w-10 shrink-0 select-none pr-2 text-right text-slate-400">{l.a ?? ""}</span>
            <span className="w-10 shrink-0 select-none pr-2 text-right text-slate-400">{l.b ?? ""}</span>
            <span className={cn("w-4 shrink-0 select-none", l.kind === "add" ? "text-emerald-700" : l.kind === "del" ? "text-red-700" : "text-slate-300")}>
              {l.kind === "add" ? "+" : l.kind === "del" ? "−" : " "}
            </span>
            <code className="whitespace-pre pr-4 font-mono">{l.text}</code>
          </div>
        ))}
      </pre>
    </div>
  );
}

export function CodeView({ text, className }: { text: string; className?: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  return (
    <pre className={cn("max-h-[32rem] overflow-auto rounded-lg border bg-slate-950 py-2 text-[12px] leading-5 text-slate-100", className)}>
      {lines.map((line, i) => (
        <div key={i} className="flex hover:bg-white/5">
          <span className="w-12 shrink-0 select-none pr-3 text-right text-slate-500">{i + 1}</span>
          <code className={cn("whitespace-pre pr-4 font-mono", /^\s*(--|#)/.test(line) && "text-slate-400",
            /TODO/.test(line) && "text-amber-300")}>{line || " "}</code>
        </div>
      ))}
    </pre>
  );
}

export function Empty({ icon: Icon = Circle, title, children }: { icon?: typeof Circle; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed px-6 py-10 text-center">
      <Icon className="h-6 w-6 text-slate-400" />
      <p className="mt-2 text-sm font-medium">{title}</p>
      {children && <div className="mt-1 max-w-md text-xs text-muted-foreground">{children}</div>}
    </div>
  );
}
