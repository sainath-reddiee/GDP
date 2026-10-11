"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";

/** A 0-100 meter: a thin bar on a muted track, value in ink beside it. Higher-is-worse meters pass `warnAt`. */
export function Meter({ value, label, warnAt, className }: { value: number | null | undefined; label?: string; warnAt?: number; className?: string }) {
  const v = value === null || value === undefined || Number.isNaN(value) ? null : Math.max(0, Math.min(100, value));
  const warn = v !== null && warnAt !== undefined && v >= warnAt;
  return (
    <div className={cn("flex items-center gap-2", className)} title={label ? `${label}: ${v === null ? "unknown" : `${v.toFixed(1)}%`}` : undefined}>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
        {v !== null && <div className={cn("h-full rounded-full", warn ? "bg-amber-500" : "bg-primary")} style={{ width: `${Math.max(v, v > 0 ? 2 : 0)}%` }} />}
      </div>
      <span className="w-12 text-right text-xs tabular-nums text-muted-foreground">{v === null ? "-" : `${v.toFixed(v < 10 ? 1 : 0)}%`}</span>
    </div>
  );
}

type Bar = { label: string; value: number };

/** Single-series column chart (histogram or top values): one hue, 2px gaps, rounded data ends on the baseline, a
 *  tooltip per bar. The values also show as a table under `showTable`, so nothing depends on reading the bars. */
export function Columns({ bars, height = 96, unit = "rows", showTable }: { bars: Bar[]; height?: number; unit?: string; showTable?: boolean }) {
  const [hover, setHover] = useState<number | null>(null);
  if (!bars.length) return <p className="text-xs text-muted-foreground">No distribution recorded.</p>;
  const max = Math.max(...bars.map((b) => b.value), 1);
  const w = 100 / bars.length;
  return (
    <div>
      <div className="relative" style={{ height }}>
        <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" className="h-full w-full" role="img"
             aria-label={`Distribution over ${bars.length} buckets`}>
          <line x1="0" x2="100" y1={height - 0.5} y2={height - 0.5} className="stroke-border" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          {bars.map((b, i) => {
            const h = Math.max((b.value / max) * (height - 4), b.value > 0 ? 2 : 0);
            return (
              <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                <rect x={i * w} y={0} width={w} height={height} fill="transparent" />
                <rect x={i * w + Math.min(0.6, w * 0.1)} y={height - h} width={Math.max(w - Math.min(1.2, w * 0.2), 0.4)} height={h} rx="0.8"
                      className={cn(hover === i ? "fill-primary" : "fill-primary/70")} />
              </g>
            );
          })}
        </svg>
        {hover !== null && (
          <div className="pointer-events-none absolute -top-2 z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-lg border border-border bg-card px-2 py-1 text-[11px] shadow-card"
               style={{ left: `${(hover + 0.5) * w}%` }}>
            <span className="font-medium">{bars[hover].label}</span>
            <span className="ml-1.5 tabular-nums text-muted-foreground">{bars[hover].value.toLocaleString()} {unit}</span>
          </div>
        )}
      </div>
      {showTable && (
        <table className="mt-2 w-full text-[11px]">
          <tbody>
            {bars.map((b, i) => (
              <tr key={i} className="border-b border-border/50 last:border-0">
                <td className="py-0.5 pr-2 text-muted-foreground">{b.label}</td>
                <td className="py-0.5 text-right tabular-nums">{b.value.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
