"use client";

import { useEffect, useState } from "react";
import {
  CheckCircle2, ClipboardCopy, FileJson, GitCompare, KeyRound, Loader2, RefreshCw, ShieldAlert, X,
} from "lucide-react";
import type { ProfileColumn, Scorecard, TableInsights } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useCopilot } from "@/components/copilot/copilot-provider";
import { loadCatalogProfile } from "./actions";

export type DrawerTab = "overview" | "columns" | "checks";

function Meter({ value, tone = "primary" }: { value: number; tone?: "primary" | "warning" | "destructive" | "success" }) {
  const pct = Math.max(0, Math.min(100, value));
  const color = { primary: "bg-primary", warning: "bg-warning", destructive: "bg-destructive", success: "bg-success" }[tone];
  return (
    <div className="h-1.5 w-full rounded-full bg-muted">
      <div className={cn("h-1.5 rounded-full", color)} style={{ width: `${pct}%` }} />
    </div>
  );
}

function toneFor(score: number | null | undefined): "success" | "primary" | "warning" | "destructive" {
  if (score == null) return "primary";
  if (score >= 90) return "success";
  if (score >= 75) return "primary";
  if (score >= 60) return "warning";
  return "destructive";
}

export function GradeChip({ card, size = "sm" }: { card: Scorecard | null | undefined; size?: "sm" | "lg" }) {
  if (!card || card.overall == null) return <span className="text-xs text-muted-foreground">—</span>;
  const tone = toneFor(card.overall);
  const color = {
    success: "bg-success/10 text-success border-success/30",
    primary: "bg-primary/10 text-primary border-primary/30",
    warning: "bg-warning/10 text-warning border-warning/30",
    destructive: "bg-destructive/10 text-destructive border-destructive/30",
  }[tone];
  const d = card.dimensions;
  const title = `Completeness ${d.completeness ?? "—"} · Uniqueness ${d.uniqueness ?? "—"} · Validity ${d.validity ?? "—"} · Freshness ${d.freshness ?? "—"}`;
  return (
    <span title={title} className={cn("inline-flex items-center gap-1 rounded-md border font-semibold tabular-nums",
      size === "lg" ? "px-3 py-1.5 text-2xl" : "px-1.5 py-0.5 text-xs", color)}>
      {card.grade}<span className={cn("font-normal opacity-80", size === "lg" ? "text-base" : "")}>{Math.round(card.overall)}</span>
    </span>
  );
}

function MiniBars({ values }: { values: number[] }) {
  const max = Math.max(...values, 1);
  return (
    <div className="flex h-8 items-end gap-0.5" aria-hidden>
      {values.map((v, i) => (
        <div key={i} className="w-2 rounded-sm bg-primary/70" style={{ height: `${Math.max(4, (v / max) * 100)}%` }} />
      ))}
    </div>
  );
}

function ColumnCard({ c }: { c: ProfileColumn }) {
  const s = c.statistics;
  const nullPct = Number(s.null_percentage ?? 0);
  const top = (s.frequency_distribution ?? c.sample_values ?? []).slice(0, 5);
  const topMax = Math.max(...top.map((t) => t.count), 1);
  return (
    <div className="rounded-xl border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm font-semibold">{c.column_name}</span>
        <span className="font-mono text-[11px] text-muted-foreground">{c.data_type}</span>
        <Badge variant="outline">{c.semantic_type}</Badge>
        {c.potential_key && <Badge variant="success"><KeyRound className="mr-1 h-3 w-3" />key candidate</Badge>}
        {c.pii_classification !== "NONE" && (
          <Badge variant="destructive"><ShieldAlert className="mr-1 h-3 w-3" />{c.pii_classification}</Badge>
        )}
      </div>
      {c.description && <p className="mt-1 text-xs text-muted-foreground">{c.description}</p>}
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <div>
          <p className="text-[11px] text-muted-foreground">Nulls {nullPct.toFixed(1)}%</p>
          <Meter value={nullPct} tone={nullPct > 50 ? "destructive" : nullPct > 10 ? "warning" : "primary"} />
        </div>
        <div>
          <p className="text-[11px] text-muted-foreground">
            Distinct {s.distinct_count ?? "—"}{s.distinct_percentage != null ? ` (${Number(s.distinct_percentage).toFixed(1)}%)` : ""}
          </p>
          <Meter value={Number(s.distinct_percentage ?? 0)} />
        </div>
        <div className="text-[11px] text-muted-foreground">
          <p>Cardinality {c.cardinality ?? "—"}</p>
          {(s.min != null || s.max != null) && <p className="truncate font-mono">{s.min ?? "…"} → {s.max ?? "…"}</p>}
        </div>
      </div>
      {s.histogram && s.histogram.length > 0 && (
        <div className="mt-3">
          <p className="mb-1 text-[11px] text-muted-foreground">Distribution</p>
          <MiniBars values={s.histogram.map((h) => h.count)} />
        </div>
      )}
      {top.length > 0 && !s.histogram && (
        <div className="mt-3 space-y-1">
          <p className="text-[11px] text-muted-foreground">Top values</p>
          {top.map((t, i) => (
            <div key={i} className="flex items-center gap-2 text-xs">
              <span className="w-32 truncate font-mono">{t.value ?? "∅"}</span>
              <div className="h-1.5 flex-1 rounded-full bg-muted">
                <div className="h-1.5 rounded-full bg-primary/60" style={{ width: `${(t.count / topMax) * 100}%` }} />
              </div>
              <span className="w-12 text-right tabular-nums text-muted-foreground">{t.count}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Overview({ data, onReprofile }: { data: TableInsights; onReprofile?: () => void }) {
  const p = data.profile;
  const card = data.scorecard;
  const drift = data.drift;
  const dims: [string, number | null, string][] = [
    ["Completeness", card.dimensions.completeness, "share of non-null values"],
    ["Uniqueness", card.dimensions.uniqueness, "a key candidate exists"],
    ["Validity", card.dimensions.validity, "text values follow their dominant format"],
    ["Freshness", card.dimensions.freshness, card.age_days != null ? `source changed ${card.age_days} days ago` : "unknown for views"],
  ];
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-5 rounded-xl border bg-card p-4">
        <div className="text-center">
          <GradeChip card={card} size="lg" />
          <p className="mt-1 text-[11px] uppercase tracking-wide text-muted-foreground">Quality score</p>
        </div>
        <div className="grid flex-1 gap-3 sm:grid-cols-2">
          {dims.map(([label, value, hint]) => (
            <div key={label}>
              <div className="flex justify-between text-xs">
                <span className="font-medium">{label}</span>
                <span className="tabular-nums text-muted-foreground">{value == null ? "—" : Math.round(value)}</span>
              </div>
              <Meter value={value ?? 0} tone={toneFor(value)} />
              <p className="mt-0.5 text-[11px] text-muted-foreground">{hint}</p>
            </div>
          ))}
        </div>
      </div>

      <div className={cn("rounded-xl border p-4", drift?.changed ? "border-warning/40 bg-warning/5" : "")}>
        <div className="flex items-center gap-2">
          <GitCompare className="h-4 w-4 text-primary" />
          <p className="text-sm font-semibold">What changed since profiling</p>
          {drift?.changed && onReprofile && (
            <Button size="sm" variant="outline" className="ml-auto" onClick={onReprofile}>
              <RefreshCw className="h-3.5 w-3.5" /> Re-profile now
            </Button>
          )}
        </div>
        {!drift && <p className="mt-2 text-sm text-muted-foreground">The live table could not be read with your current role.</p>}
        {drift && !drift.changed && (
          <p className="mt-2 flex items-center gap-1.5 text-sm text-success">
            <CheckCircle2 className="h-4 w-4" /> Columns, types and row count match the stored profile.
          </p>
        )}
        {drift?.changed && (
          <ul className="mt-3 space-y-1.5 text-sm">
            {drift.added.map((c) => <li key={`a-${c}`}><Badge variant="success">added</Badge> <span className="font-mono">{c}</span></li>)}
            {drift.removed.map((c) => <li key={`r-${c}`}><Badge variant="destructive">removed</Badge> <span className="font-mono">{c}</span></li>)}
            {drift.retyped.map((c) => (
              <li key={`t-${c.column}`}>
                <Badge variant="warning">retyped</Badge> <span className="font-mono">{c.column}</span>{" "}
                <span className="font-mono text-xs text-muted-foreground">{c.from} → {c.to}</span>
              </li>
            ))}
            {drift.row_count.delta ? (
              <li>
                <Badge variant="outline">rows</Badge>{" "}
                <span className="tabular-nums">{drift.row_count.before?.toLocaleString()} → {drift.row_count.after?.toLocaleString()}</span>{" "}
                <span className={cn("text-xs", drift.row_count.delta > 0 ? "text-success" : "text-destructive")}>
                  ({drift.row_count.delta > 0 ? "+" : ""}{drift.row_count.delta.toLocaleString()}
                  {drift.row_count.pct != null ? `, ${drift.row_count.pct}%` : ""})
                </span>
              </li>
            ) : null}
            {!drift.schema_changed && !drift.row_count.delta && (
              <li className="text-muted-foreground">The source was modified after profiling; values may differ even though the shape is the same.</li>
            )}
          </ul>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          ["Rows", p.row_count.toLocaleString()],
          ["Columns", p.column_count],
          ["Key candidates", p.columns.filter((c) => c.potential_key).length],
          ["PII columns", p.columns.filter((c) => c.pii_classification !== "NONE").length],
        ].map(([label, value]) => (
          <div key={label as string} className="rounded-lg border bg-card px-3 py-2">
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
            <p className="text-sm font-semibold tabular-nums">{value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function Checks({ data }: { data: TableInsights }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Checks that hold on today&apos;s data, derived from the profile. Copy them into the run&apos;s data quality
        stage or a Soda checks file.
      </p>
      <ul className="space-y-2">
        {data.checks.map((c, i) => (
          <li key={i} className="rounded-lg border px-3 py-2">
            <p className="font-mono text-xs font-semibold">{c.check}</p>
            {c.valid_values && <p className="font-mono text-[11px] text-muted-foreground">valid values: {c.valid_values.join(", ")}</p>}
            {c.valid_regex && <p className="font-mono text-[11px] text-muted-foreground">valid regex: {c.valid_regex}</p>}
            <p className="text-[11px] text-muted-foreground">{c.reason}</p>
          </li>
        ))}
      </ul>
      <div className="relative">
        <Button size="sm" variant="outline" className="absolute right-2 top-2"
                onClick={() => navigator.clipboard.writeText(data.checks_yaml).then(() => setCopied(true))}>
          <ClipboardCopy className="h-3.5 w-3.5" /> {copied ? "Copied" : "Copy SodaCL"}
        </Button>
        <pre className="max-h-80 overflow-auto rounded-lg bg-muted p-3 font-mono text-[11px]">{data.checks_yaml}</pre>
      </div>
    </div>
  );
}

/** The full profile of one stored table (overview, columns, suggested checks), embeddable anywhere: the Sources
 *  drawer, a run's profile page. `onClose` adds a close button to the header. */
export function TableProfileView({ database, schema, table, initialTab = "overview", onClose, onReprofile, className }: {
  database: string; schema: string; table: string; initialTab?: DrawerTab; onClose?: () => void;
  onReprofile?: (table: string) => void; className?: string;
}) {
  const [data, setData] = useState<TableInsights | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<DrawerTab>(initialTab);

  useEffect(() => {
    let live = true;
    setData(null);
    setError("");
    loadCatalogProfile(database, schema, table).then((r) => {
      if (!live) return;
      if (r.ok) setData(r.data);
      else setError(r.error);
    });
    return () => { live = false; };
  }, [database, schema, table]);

  const { setFocus } = useCopilot();
  useEffect(() => {
    setFocus({ database, schema, table });
    return () => setFocus(null);
  }, [database, schema, table, setFocus]);

  const p = data?.profile;
  const columns = (p?.columns ?? []).filter((c) => c.column_name.toLowerCase().includes(query.toLowerCase()));

  return (
    <div className={cn("flex min-h-0 flex-col", className)}>
      <header className="flex items-start gap-3 border-b px-5 pt-4">
        <FileJson className="mt-0.5 h-5 w-5 text-primary" />
        <div className="min-w-0 pb-3">
          <h3 className="truncate text-base font-semibold">{table}</h3>
          <p className="truncate font-mono text-[11px] text-muted-foreground">{database}.{schema}</p>
          <p className="truncate font-mono text-[11px] text-muted-foreground">
            @METADATA.PROFILES_STAGE/{data?.entry.profile_stage_path ?? "…"}
          </p>
        </div>
        {onClose && (
          <button type="button" onClick={onClose} className="ml-auto rounded-md p-1 hover:bg-muted" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        )}
      </header>
      <div className="flex gap-1 border-b px-5" role="tablist">
        {(["overview", "columns", "checks"] as const).map((t) => (
          <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                  className={cn("-mb-px border-b-2 px-3 py-2 text-sm capitalize",
                    tab === t ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground")}>
            {t === "checks" ? `Suggested checks${data ? ` (${data.checks.length})` : ""}` : t}
          </button>
        ))}
      </div>
      <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
        {!data && !error && (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Reading the staged profile…
          </p>
        )}
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        {data && p && tab === "overview" && (
          <>
            <Overview data={data} onReprofile={onReprofile ? () => onReprofile(table) : undefined} />
            <p className="text-xs text-muted-foreground">
              Profiled {p.profiled_at.slice(0, 16).replace("T", " ")} UTC{data.entry.profiled_by ? ` by ${data.entry.profiled_by}` : ""}
              {p.approximate ? " · sampled (table over 10M rows)" : " · exact"}
              {p.model_version ? ` · descriptions by ${p.model_version}` : ""}
            </p>
          </>
        )}
        {data && p && tab === "columns" && (
          <>
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter columns"
                   className="h-8 w-full rounded-md border bg-card px-2 text-sm" />
            <div className="space-y-3">{columns.map((c) => <ColumnCard key={c.column_name} c={c} />)}</div>
          </>
        )}
        {data && tab === "checks" && <Checks data={data} />}
      </div>
    </div>
  );
}

export function ProfileDrawer({ database, schema, table, initialTab = "overview", onClose, onReprofile }: {
  database: string; schema: string; table: string; initialTab?: DrawerTab; onClose: () => void;
  onReprofile?: (table: string) => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button type="button" aria-label="Close profile" className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside role="dialog" aria-label={`Profile of ${table}`}
             className="relative flex h-full w-[760px] max-w-full flex-col overflow-hidden border-l bg-background shadow-2xl">
        <TableProfileView database={database} schema={schema} table={table} initialTab={initialTab}
                          onClose={onClose} onReprofile={onReprofile} className="h-full" />
      </aside>
    </div>
  );
}
