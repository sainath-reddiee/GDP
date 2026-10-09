"use client";

import { Badge } from "@/components/ui/badge";

export type Outcome = "PASS" | "WARN" | "FAIL" | "NOT_EVALUATED" | "ERROR";

export type CheckResult = {
  expectation_id: string;
  target_table: string | null;
  target_column: string | null;
  check_type: string | null;
  kind: string | null;
  dimension: string | null;
  severity: string | null;
  outcome: Outcome;
  measured: number | null;
  threshold: string | null;
  failed_rows: number | null;
  detail: string | null;
  sample: Record<string, unknown>[] | null;
  sql_text: string | null;
  duration_ms: number | null;
};

export type HistoryPoint = { outcome: Outcome; measured: number | null; at: string };

export type ScanRow = {
  scan_id: string; target: string; mode: "MODEL" | "SOURCE"; started_at: string; duration_ms: number | null;
  checks: number; passed: number; warned: number; failed: number; not_evaluated: number; errors: number;
  health: number | null; rows_scanned: number | null; triggered_by: string | null; created_by: string | null;
};

export type ScansPayload = {
  scans: ScanRow[];
  latest: CheckResult[];
  history: Record<string, HistoryPoint[]>;
  ready: boolean;
};

export const OUTCOME_LABEL: Record<Outcome, string> = {
  PASS: "pass", WARN: "warn", FAIL: "fail", NOT_EVALUATED: "not evaluated", ERROR: "error",
};

const OUTCOME_COLOR: Record<Outcome, string> = {
  PASS: "hsl(var(--success))",
  WARN: "hsl(var(--warning))",
  FAIL: "hsl(var(--destructive))",
  ERROR: "hsl(var(--destructive))",
  NOT_EVALUATED: "hsl(var(--muted-foreground))",
};

export function OutcomeBadge({ outcome }: { outcome?: Outcome | null }) {
  if (!outcome) return <span className="text-xs text-muted-foreground">not scanned</span>;
  const variant = outcome === "PASS" ? "success" : outcome === "WARN" ? "warning"
    : outcome === "FAIL" || outcome === "ERROR" ? "destructive" : "outline";
  return <Badge variant={variant}>{OUTCOME_LABEL[outcome]}</Badge>;
}

/** One bar per past scan, newest on the right, coloured by outcome. */
export function Spark({ points }: { points: HistoryPoint[] }) {
  if (points.length < 2) return null;
  const last = points.slice(-12);
  return (
    <svg width={last.length * 6} height={14} aria-label={`Last ${last.length} scans`} role="img">
      {last.map((p, i) => (
        <rect key={i} x={i * 6} y={p.outcome === "PASS" ? 4 : 0} width={4} height={p.outcome === "PASS" ? 10 : 14}
              rx={1} fill={OUTCOME_COLOR[p.outcome]}>
          <title>{`${p.at.slice(0, 16)} · ${OUTCOME_LABEL[p.outcome]}${p.measured != null ? ` · ${p.measured}` : ""}`}</title>
        </rect>
      ))}
    </svg>
  );
}

/** Health score ring with the pass / warn / fail split as segments around it. */
export function HealthRing({ health, passed, warned, failed, size = 132 }: {
  health: number | null; passed: number; warned: number; failed: number; size?: number;
}) {
  const r = size / 2 - 10;
  const c = 2 * Math.PI * r;
  const total = passed + warned + failed;
  const parts: [number, string][] = total
    ? [[passed / total, OUTCOME_COLOR.PASS], [warned / total, OUTCOME_COLOR.WARN], [failed / total, OUTCOME_COLOR.FAIL]]
    : [];
  let offset = 0;
  const tone = health == null ? "text-muted-foreground" : health >= 90 ? "text-success" : health >= 70 ? "text-warning" : "text-destructive";
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="hsl(var(--muted))" strokeWidth={12} />
        {parts.map(([share, color], i) => {
          const len = share * c;
          const el = share > 0 ? (
            <circle key={i} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={12}
                    strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-offset} />
          ) : null;
          offset += len;
          return el;
        })}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className={`text-3xl font-semibold tabular-nums ${tone}`}>{health ?? "–"}</span>
        <span className="text-[11px] uppercase tracking-wide text-muted-foreground">health</span>
      </div>
    </div>
  );
}

export function fmtNumber(v: number | null | undefined) {
  if (v == null) return "–";
  if (Math.abs(v) >= 1000) return Math.round(v).toLocaleString();
  return Number.isInteger(v) ? String(v) : v.toFixed(2);
}

export function fmtDuration(ms: number | null | undefined) {
  if (ms == null) return "–";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}
