"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { CircleDashed, KeyRound, Layers, Loader2, Lock, RefreshCw, Rows3, Search, Table2 } from "lucide-react";
import type { ProfileCacheTable } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { GradeChip, TableProfileView } from "@/app/sources/profile-drawer";
import { refreshTableProfile } from "../pipeline-actions";

export type ProfileColumn = {
  profile_id: string; table_name: string; column_name: string; data_type: string; semantic_type: string;
  pii_classification: string; row_count: number; null_percentage: number; distinct_percentage: number;
  cardinality: string | null; potential_key_flag: boolean; generated_description: string | null;
};

function fmt(n: number | null | undefined) {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}K`;
  return n.toLocaleString();
}

function Stat({ icon: Icon, label, value, hint }: { icon: typeof Table2; label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-xl border bg-card px-4 py-3">
      <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground"><Icon className="h-3.5 w-3.5" />{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** The run's profiles: headline numbers, every table with its grade and the full stored profile beside it, and a
 *  searchable list of all columns. */
export function ProfileWorkspace({ runId, tables, columns, canRefresh }: {
  runId: string; tables: ProfileCacheTable[]; columns: ProfileColumn[]; canRefresh: boolean;
}) {
  const router = useRouter();
  const profiled = tables.filter((t) => t.status === "CACHED");
  const [tab, setTab] = useState<"tables" | "columns">("tables");
  const [selected, setSelected] = useState<string | null>(profiled[0]?.table_name ?? null);
  const [refreshing, setRefreshing] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [, start] = useTransition();
  const [query, setQuery] = useState("");
  const [semantic, setSemantic] = useState("ALL");
  const [onlyPii, setOnlyPii] = useState(false);
  const [onlyKeys, setOnlyKeys] = useState(false);

  const graded = profiled.filter((t) => t.quality?.overall != null);
  const avg = graded.length ? Math.round(graded.reduce((n, t) => n + (t.quality!.overall as number), 0) / graded.length) : null;
  const rows = profiled.reduce((n, t) => n + (t.row_count ?? 0), 0);
  const pii = profiled.reduce((n, t) => n + (t.pii_columns ?? 0), 0);
  const keys = profiled.reduce((n, t) => n + (t.key_candidates ?? 0), 0);
  const current = tables.find((t) => t.table_name === selected);

  const semantics = useMemo(() => Array.from(new Set(columns.map((c) => c.semantic_type))).sort(), [columns]);
  const q = query.trim().toLowerCase();
  const shownColumns = columns.filter((c) => (!q || `${c.table_name}.${c.column_name} ${c.generated_description ?? ""}`.toLowerCase().includes(q))
    && (semantic === "ALL" || c.semantic_type === semantic) && (!onlyPii || c.pii_classification !== "NONE") && (!onlyKeys || c.potential_key_flag));

  const reprofile = (table: string) => start(async () => {
    setError(""); setRefreshing(table);
    const r = await refreshTableProfile(runId, table);
    setRefreshing(null);
    if (!r.ok) setError(r.error); else router.refresh();
  });

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Stat icon={Table2} label="Tables profiled" value={<>{profiled.length}<span className="text-sm text-muted-foreground"> / {tables.length}</span></>}
              hint={tables.length - profiled.length ? `${tables.length - profiled.length} not profiled yet` : "all landed tables"} />
        <Stat icon={Rows3} label="Rows" value={fmt(rows)} hint="across profiled tables" />
        <Stat icon={Layers} label="Average quality" value={avg ?? "—"} hint={`${graded.length} graded table${graded.length === 1 ? "" : "s"}`} />
        <Stat icon={KeyRound} label="Key candidates" value={keys} hint="unique, non-null columns" />
        <Stat icon={Lock} label="PII columns" value={pii} hint={pii ? "masked in samples" : "none detected"} />
      </div>

      <div className="flex items-center gap-1 border-b">
        {([["tables", `Tables · ${tables.length}`], ["columns", `All columns · ${columns.length}`]] as const).map(([id, label]) => (
          <button key={id} type="button" onClick={() => setTab(id)} aria-current={tab === id ? "page" : undefined}
                  className={cn("-mb-px border-b-2 px-3 py-2 text-sm", tab === id ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>
            {label}
          </button>
        ))}
      </div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}

      {tab === "tables" && (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <div className="overflow-hidden rounded-xl border">
            <ul className="divide-y">
              {tables.map((t) => {
                const active = t.table_name === selected;
                const ready = t.status === "CACHED";
                return (
                  <li key={t.table_name}>
                    <div className={cn("flex items-center gap-3 px-4 py-3 transition", active ? "bg-primary/[0.06]" : "hover:bg-muted/40")}>
                      <button type="button" disabled={!ready} onClick={() => setSelected(t.table_name)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
                        <span className={cn("grid h-8 w-8 shrink-0 place-items-center rounded-lg", ready ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground")}>
                          {ready ? <Table2 className="h-4 w-4" /> : <CircleDashed className="h-4 w-4" />}
                        </span>
                        <span className="min-w-0">
                          <span className={cn("block truncate font-medium", active && "text-primary")}>{t.table_name}</span>
                          <span className="block text-[11px] text-muted-foreground">
                            {ready ? `${fmt(t.row_count)} rows · ${t.column_count ?? "—"} columns · ${t.is_approximate ? "sampled" : "exact"}` : "Not profiled yet"}
                          </span>
                        </span>
                      </button>
                      {ready && <GradeChip card={t.quality} />}
                      {(t.pii_columns ?? 0) > 0 && <Badge variant="destructive" className="gap-1 text-[10px]"><Lock className="h-3 w-3" />{t.pii_columns}</Badge>}
                      {canRefresh && ready && (
                        <Button size="sm" variant="ghost" disabled={refreshing !== null} onClick={() => reprofile(t.table_name)}
                                aria-label={`Re-profile ${t.table_name}`} title="Re-profile this table">
                          {refreshing === t.table_name ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                        </Button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
          <div className="min-h-[420px] overflow-hidden rounded-xl border bg-card">
            {current?.status === "CACHED" && current.database_name && current.schema_name ? (
              <TableProfileView key={current.table_name} database={current.database_name} schema={current.schema_name}
                                table={current.table_name} className="h-[640px]"
                                onReprofile={canRefresh ? (table) => reprofile(table) : undefined} />
            ) : (
              <div className="grid h-full place-items-center p-8 text-center text-sm text-muted-foreground">
                {profiled.length ? "Choose a table to see its profile." : "Profile the run to see each table's quality, columns and suggested checks."}
              </div>
            )}
          </div>
        </div>
      )}

      {tab === "columns" && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search columns or descriptions" aria-label="Search columns" className="w-72 pl-8" />
            </div>
            <select value={semantic} onChange={(e) => setSemantic(e.target.value)} aria-label="Semantic type" className="h-9 rounded-lg border bg-card px-2 text-sm">
              <option value="ALL">All semantic types</option>
              {semantics.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <label className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={onlyPii} onChange={(e) => setOnlyPii(e.target.checked)} /> PII only</label>
            <label className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={onlyKeys} onChange={(e) => setOnlyKeys(e.target.checked)} /> Keys only</label>
            <span className="ml-auto text-xs text-muted-foreground">{shownColumns.length} of {columns.length}</span>
          </div>
          <div className="overflow-x-auto rounded-xl border">
            <table className="w-full min-w-[1000px] text-sm">
              <thead className="bg-muted/50 text-left text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-3 py-2.5">Table</th><th className="px-3 py-2.5">Column</th><th className="px-3 py-2.5">Type</th>
                  <th className="px-3 py-2.5">Semantic</th><th className="w-36 px-3 py-2.5">Nulls</th><th className="w-36 px-3 py-2.5">Distinct</th>
                  <th className="px-3 py-2.5">Flags</th><th className="px-3 py-2.5">Description</th>
                </tr>
              </thead>
              <tbody>
                {shownColumns.map((c) => (
                  <tr key={c.profile_id} className="border-t align-top hover:bg-muted/30">
                    <td className="px-3 py-2 text-xs text-muted-foreground">{c.table_name}</td>
                    <td className="px-3 py-2 font-medium">{c.column_name}</td>
                    <td className="px-3 py-2 font-mono text-[11px]">{c.data_type}</td>
                    <td className="px-3 py-2"><Badge variant="outline" className="text-[10px]">{c.semantic_type}</Badge></td>
                    {[c.null_percentage, c.distinct_percentage].map((v, i) => (
                      <td key={i} className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <span className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
                            <span className={cn("block h-full rounded-full", i === 0 ? (Number(v) > 20 ? "bg-warning" : "bg-success") : "bg-primary")}
                                  style={{ width: `${Math.max(2, Math.min(100, Number(v) || 0))}%` }} />
                          </span>
                          <span className="text-xs tabular-nums">{Number(v ?? 0).toFixed(1)}%</span>
                        </div>
                      </td>
                    ))}
                    <td className="space-x-1 px-3 py-2">
                      {c.potential_key_flag && <Badge variant="secondary" className="gap-1 text-[10px]"><KeyRound className="h-3 w-3" />key</Badge>}
                      {c.pii_classification !== "NONE" && <Badge variant="destructive" className="text-[10px]">{c.pii_classification}</Badge>}
                    </td>
                    <td className="max-w-md px-3 py-2 text-xs text-muted-foreground">{c.generated_description}</td>
                  </tr>
                ))}
                {shownColumns.length === 0 && <tr><td colSpan={8} className="px-3 py-10 text-center text-sm text-muted-foreground">No columns match.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
