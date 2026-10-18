"use client";

import { useRef } from "react";
import { cn } from "@/lib/utils";

/** Monospace SQL textarea with a line-number gutter that scrolls with it. Tab inserts two spaces. */
export function SqlEditor({ value, onChange, readOnly, rows = 12, label = "SQL", invalid }: {
  value: string; onChange?: (v: string) => void; readOnly?: boolean; rows?: number; label?: string; invalid?: boolean;
}) {
  const gutter = useRef<HTMLDivElement>(null);
  const lines = Math.max(rows, value.split("\n").length);
  return (
    <div className={cn("relative flex overflow-hidden rounded-xl border bg-slate-950 font-mono text-[12.5px] leading-[1.6] shadow-inner",
      invalid ? "border-destructive/60" : "border-slate-800 focus-within:border-primary/60 focus-within:ring-4 focus-within:ring-primary/10")}>
      <div ref={gutter} aria-hidden className="select-none overflow-hidden border-r border-slate-800 bg-slate-900/70 px-2 py-3 text-right text-slate-500">
        {Array.from({ length: lines }, (_, i) => <div key={i}>{i + 1}</div>)}
      </div>
      <textarea aria-label={label} spellCheck={false} readOnly={readOnly} value={value} rows={rows}
                onChange={(e) => onChange?.(e.target.value)}
                onScroll={(e) => { if (gutter.current) gutter.current.scrollTop = e.currentTarget.scrollTop; }}
                onKeyDown={(e) => {
                  if (e.key !== "Tab" || readOnly) return;
                  e.preventDefault();
                  const t = e.currentTarget;
                  const { selectionStart: s, selectionEnd: end } = t;
                  onChange?.(`${value.slice(0, s)}  ${value.slice(end)}`);
                  requestAnimationFrame(() => { t.selectionStart = t.selectionEnd = s + 2; });
                }}
                className="min-h-0 flex-1 resize-y whitespace-pre bg-transparent px-3 py-3 text-slate-100 caret-blue-300 outline-none placeholder:text-slate-600" />
    </div>
  );
}

/** "#reconciliation" chips; the tone is stable per tag. */
const TAG_TONES = [
  "bg-blue-50 text-blue-700 ring-blue-100", "bg-emerald-50 text-emerald-700 ring-emerald-100",
  "bg-amber-50 text-amber-700 ring-amber-100", "bg-sky-50 text-sky-700 ring-sky-100",
  "bg-blue-50 text-blue-700 ring-blue-100", "bg-rose-50 text-rose-700 ring-rose-100",
];

export function TagChip({ tag, onClick, active }: { tag: string; onClick?: () => void; active?: boolean }) {
  const tone = TAG_TONES[[...tag].reduce((n, c) => n + c.charCodeAt(0), 0) % TAG_TONES.length];
  const cls = cn("inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset", tone,
    onClick && "transition hover:brightness-95", active && "ring-2 ring-primary/50");
  return onClick
    ? <button type="button" onClick={onClick} className={cls}>#{tag}</button>
    : <span className={cls}>#{tag}</span>;
}
