"use client";

import { useEffect, useState } from "react";
import { FileJson, KeyRound, Loader2, ShieldAlert, X } from "lucide-react";
import type { ProfileColumn, TableProfileDoc } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { loadCatalogProfile } from "./actions";

function Meter({ value, tone = "primary" }: { value: number; tone?: "primary" | "warning" | "destructive" }) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div className="h-1.5 w-full rounded-full bg-muted">
      <div className={cn("h-1.5 rounded-full", tone === "primary" ? "bg-primary" : tone === "warning" ? "bg-warning" : "bg-destructive")}
           style={{ width: `${pct}%` }} />
    </div>
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

export function ProfileDrawer({ database, schema, table, onClose }: {
  database: string; schema: string; table: string; onClose: () => void;
}) {
  const [doc, setDoc] = useState<TableProfileDoc | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");

  useEffect(() => {
    let live = true;
    setDoc(null);
    setError("");
    loadCatalogProfile(database, schema, table).then((r) => {
      if (!live) return;
      if (r.ok) setDoc(r.data);
      else setError(r.error);
    });
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => { live = false; window.removeEventListener("keydown", onKey); };
  }, [database, schema, table, onClose]);

  const p = doc?.profile;
  const columns = (p?.columns ?? []).filter((c) => c.column_name.toLowerCase().includes(query.toLowerCase()));
  const avgNull = p?.columns.length
    ? p.columns.reduce((n, c) => n + Number(c.statistics.null_percentage ?? 0), 0) / p.columns.length : 0;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button type="button" aria-label="Close profile" className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside role="dialog" aria-label={`Profile of ${table}`}
             className="relative flex h-full w-[720px] max-w-full flex-col overflow-hidden border-l bg-background shadow-2xl">
        <header className="flex items-start gap-3 border-b px-5 py-4">
          <FileJson className="mt-0.5 h-5 w-5 text-primary" />
          <div className="min-w-0">
            <h3 className="truncate text-base font-semibold">{table}</h3>
            <p className="truncate font-mono text-[11px] text-muted-foreground">{database}.{schema}</p>
            <p className="truncate font-mono text-[11px] text-muted-foreground">
              @METADATA.PROFILES_STAGE/{doc?.entry.profile_stage_path ?? "…"}
            </p>
          </div>
          <button type="button" onClick={onClose} className="ml-auto rounded-md p-1 hover:bg-muted" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {!doc && !error && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Reading the staged profile…
            </p>
          )}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          {p && (
            <>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
                {[
                  ["Rows", p.row_count.toLocaleString()],
                  ["Columns", p.column_count],
                  ["Avg nulls", `${avgNull.toFixed(1)}%`],
                  ["Key candidates", p.columns.filter((c) => c.potential_key).length],
                  ["PII columns", p.columns.filter((c) => c.pii_classification !== "NONE").length],
                ].map(([label, value]) => (
                  <div key={label as string} className="rounded-lg border bg-card px-3 py-2">
                    <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
                    <p className="text-sm font-semibold tabular-nums">{value}</p>
                  </div>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                Profiled {p.profiled_at.slice(0, 16).replace("T", " ")} UTC{doc?.entry.profiled_by ? ` by ${doc.entry.profiled_by}` : ""}
                {p.approximate ? " · sampled (table over 10M rows)" : " · exact"}
                {p.model_version ? ` · descriptions by ${p.model_version}` : ""}
              </p>
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter columns"
                     className="h-8 w-full rounded-md border bg-card px-2 text-sm" />
              <div className="space-y-3">{columns.map((c) => <ColumnCard key={c.column_name} c={c} />)}</div>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
