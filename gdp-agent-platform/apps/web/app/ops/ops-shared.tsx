"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

/** Airflow states and how they read: badge tone and the bar color in the runs strip. */
const STATE_TONE: Record<string, { badge: string; bar: string }> = {
  success: { badge: "bg-emerald-50 text-emerald-700 ring-emerald-100", bar: "bg-emerald-500" },
  failed: { badge: "bg-rose-50 text-rose-700 ring-rose-100", bar: "bg-rose-500" },
  upstream_failed: { badge: "bg-orange-50 text-orange-700 ring-orange-100", bar: "bg-orange-400" },
  up_for_retry: { badge: "bg-amber-50 text-amber-700 ring-amber-100", bar: "bg-amber-400" },
  up_for_reschedule: { badge: "bg-amber-50 text-amber-700 ring-amber-100", bar: "bg-amber-300" },
  running: { badge: "bg-sky-50 text-sky-700 ring-sky-100", bar: "bg-sky-500" },
  restarting: { badge: "bg-sky-50 text-sky-700 ring-sky-100", bar: "bg-sky-400" },
  queued: { badge: "bg-slate-100 text-slate-600 ring-slate-200", bar: "bg-slate-400" },
  scheduled: { badge: "bg-slate-100 text-slate-600 ring-slate-200", bar: "bg-slate-300" },
  deferred: { badge: "bg-violet-50 text-violet-700 ring-violet-100", bar: "bg-violet-400" },
  skipped: { badge: "bg-slate-100 text-slate-500 ring-slate-200", bar: "bg-slate-300" },
  removed: { badge: "bg-slate-100 text-slate-500 ring-slate-200", bar: "bg-slate-200" },
};
const IDLE = { badge: "bg-slate-100 text-slate-600 ring-slate-200", bar: "bg-slate-300" };

export const stateTone = (state: string | null | undefined) => STATE_TONE[(state ?? "").toLowerCase()] ?? IDLE;

export function StateBadge({ state, className }: { state: string | null | undefined; className?: string }) {
  return (
    <span className={cn("inline-flex shrink-0 whitespace-nowrap rounded-full px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset",
      stateTone(state).badge, className)}>{state ? state.toLowerCase().replace(/_/g, " ") : "no runs"}</span>
  );
}

/** "42 s", "12 min", "1 h 5 min". */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "-";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min${s % 60 && s < 600 ? ` ${s % 60} s` : ""}`;
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  return `${h} h${m ? ` ${m} min` : ""}`;
}

/** A 0..1 fraction as a whole percent. */
export const pct = (v: number | null | undefined) => (v === null || v === undefined || !Number.isFinite(v) ? "-" : `${Math.round(v * 100)}%`);

export const pctTone = (v: number | null | undefined) =>
  v === null || v === undefined ? "text-muted-foreground" : v >= 0.98 ? "text-emerald-700" : v >= 0.9 ? "text-amber-700" : "text-rose-700";

/** ISO strings and Snowflake VARCHAR timestamps ("2026-10-09 10:20:30.123 -0700") as a Date. */
export function parseTs(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?$/);
  const tz = m?.[4] ? (m[4] === "Z" ? "Z" : `${m[4].slice(0, 3)}:${m[4].slice(-2)}`) : m ? "Z" : "";
  const d = new Date(m ? `${m[1]}T${m[2]}${m[3] ?? ""}${tz}` : iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

function useMounted() {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted;
}

function relative(d: Date): string {
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 0) return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.round(s / 86400)} d ago`;
  return d.toLocaleDateString(undefined, { dateStyle: "medium" });
}

/** A time in the viewer's locale. The server and the first client render show UTC so hydration matches; the local
 *  form replaces it once mounted. `rel` shows "5 min ago" with the full time on hover. */
export function When({ iso, rel = false, empty = "never" }: { iso: string | null | undefined; rel?: boolean; empty?: string }) {
  const mounted = useMounted();
  const d = parseTs(iso);
  if (!iso) return <span className="text-muted-foreground">{empty}</span>;
  if (!d) return <span>{iso}</span>;
  if (!mounted) return <time dateTime={d.toISOString()}>{d.toISOString().slice(0, 16).replace("T", " ")} UTC</time>;
  const full = d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  return <time dateTime={d.toISOString()} title={full}>{rel ? relative(d) : full}</time>;
}

/** The two API errors people can act on, said plainly. Everything else is shown as the API wrote it. */
export function explain(error: string): string {
  if (/AWS credentials are not available/i.test(error)) {
    return `${error}. The API host needs an IAM role (or AWS profile) that can call Amazon MWAA; no keys are entered in the platform.`;
  }
  return error;
}
