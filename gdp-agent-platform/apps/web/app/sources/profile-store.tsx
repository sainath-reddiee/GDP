"use client";

import { TagChip, TagEditor } from "@/components/tag-editor";
import { useMemo, useState } from "react";
import { ArrowRight, Eye, HardDrive, KeyRound, Lock, Search, Snowflake, UploadCloud } from "lucide-react";
import type { ProfileStoreRow, SourcesOverview } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { Segmented } from "./oracle-ui";
import type { Target } from "./catalog-browser";

type Origin = "snowflake" | "external" | "oracle";
type Sort = "recent" | "name" | "rows" | "nulls";

function formatCount(n: number | null | undefined) {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}K`;
  return n.toLocaleString();
}

export function ago(value: string | null | undefined) {
  if (!value) return "—";
  const iso = value.trim().replace(/^(\d{4}-\d{2}-\d{2})[ T]/, "$1T").replace(/\s*([+-])(\d{2}):?(\d{2})$/, "$1$2:$3");
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (Number.isNaN(mins)) return value.slice(0, 16);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`;
  if (mins < 60 * 24 * 30) return `${Math.round(mins / 1440)}d ago`;
  return value.slice(0, 10);
}

const ORIGIN: Record<Origin, { label: string; icon: typeof Snowflake; tone: string }> = {
  snowflake: { label: "Snowflake", icon: Snowflake, tone: "bg-sky-500/10 text-sky-700 dark:text-sky-300" },
  external: { label: "External", icon: UploadCloud, tone: "bg-violet-500/10 text-violet-700 dark:text-violet-300" },
  oracle: { label: "Oracle in place", icon: HardDrive, tone: "bg-red-500/10 text-red-700 dark:text-red-300" },
};

/** Every stored profile, from any database or system: filter by where it came from, sort, open it, or jump to its
 *  schema to profile more or model. */
export function ProfileStore({ store, overview, onView, onOpenSchema }: {
  store: ProfileStoreRow[]; overview: SourcesOverview | null;
  onView: (row: ProfileStoreRow) => void; onOpenSchema: (target: Target) => void;
}) {
  const [query, setQuery] = useState("");
  const [origin, setOrigin] = useState<"all" | Origin>("all");
  const [sort, setSort] = useState<Sort>("recent");
  const [tag, setTag] = useState("");
  const allTags = useMemo(() => Array.from(new Set(store.flatMap((r) => r.tags ?? []))).sort(), [store]);

  const externalSchemas = useMemo(() => new Set((overview?.sources ?? [])
    .filter((s) => s.source_type.startsWith("EXTERNAL_")).map((s) => `${s.database_name}.${s.schema_name}`)), [overview]);
  const originOf = (r: ProfileStoreRow): Origin => r.source_name.startsWith("ORACLE_") ? "oracle"
    : externalSchemas.has(`${r.database_name}.${r.schema_name}`) ? "external" : "snowflake";

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: store.length, snowflake: 0, external: 0, oracle: 0 };
    for (const r of store) c[originOf(r)] += 1;
    return c;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store, externalSchemas]);

  const q = query.trim().toLowerCase();
  const rows = store
    .filter((r) => (origin === "all" || originOf(r) === origin)
      && (!tag || (r.tags ?? []).includes(tag))
      && (!q || `${r.database_name}.${r.schema_name}.${r.table_name} ${(r.tags ?? []).map((t) => `#${t}`).join(" ")}`.toLowerCase().includes(q)))
    .sort((a, b) => sort === "name" ? a.table_name.localeCompare(b.table_name)
      : sort === "rows" ? (b.row_count ?? -1) - (a.row_count ?? -1)
      : sort === "nulls" ? (b.avg_null_percentage ?? -1) - (a.avg_null_percentage ?? -1)
      : String(b.profiled_at).localeCompare(String(a.profiled_at)));
  const schemas = new Set(rows.map((r) => `${r.database_name}.${r.schema_name}`)).size;
  const pii = rows.reduce((n, r) => n + (r.pii_columns ?? 0), 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search database, schema or table"
                 aria-label="Search profiles" className="w-72 rounded-xl pl-8" />
        </div>
        <Segmented label="Where the data lives" value={origin} onChange={setOrigin}
                   options={[["all", `All · ${counts.all}`], ["snowflake", `Snowflake · ${counts.snowflake}`],
                             ["external", `External · ${counts.external}`], ["oracle", `Oracle in place · ${counts.oracle}`]] as const} />
        {allTags.length > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            {allTags.slice(0, 12).map((t) => <TagChip key={t} tag={t} active={tag === t} onClick={() => setTag(tag === t ? "" : t)} />)}
          </div>
        )}
        <label className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
          Sort
          <select value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="Sort profiles"
                  className="h-8 rounded-lg border bg-card px-2 text-xs text-foreground">
            <option value="recent">Most recent</option><option value="name">Name</option>
            <option value="rows">Most rows</option><option value="nulls">Most nulls</option>
          </select>
        </label>
      </div>
      <p className="text-xs text-muted-foreground">
        {rows.length} profile{rows.length === 1 ? "" : "s"} across {schemas} schema{schemas === 1 ? "" : "s"}
        {pii > 0 && <> · <span className="text-destructive">{pii} PII columns</span></>} · stored on{" "}
        <span className="font-mono">@METADATA.PROFILES_STAGE</span> and reused by every run.
      </p>

      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full min-w-[860px] text-sm">
          <thead className="bg-muted/50 text-left text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-4 py-2.5">Table</th>
              <th className="px-4 py-2.5">Location</th>
              <th className="w-24 px-4 py-2.5 text-right">Rows</th>
              <th className="w-24 px-4 py-2.5 text-right">Columns</th>
              <th className="px-4 py-2.5">Health</th>
              <th className="w-28 px-4 py-2.5">Profiled</th>
              <th className="w-24 px-4 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const o = ORIGIN[originOf(r)];
              const busy = r.status === "PROFILING";
              const failed = r.status === "FAILED";
              return (
                <tr key={`${r.source_name}.${r.database_name}.${r.schema_name}.${r.table_name}`} className="group border-t hover:bg-muted/30">
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2.5">
                      <span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-lg", o.tone)} title={o.label}><o.icon className="h-3.5 w-3.5" /></span>
                      <div className="min-w-0">
                        <button type="button" disabled={busy} onClick={() => onView(r)}
                                className="block truncate text-left font-medium hover:text-primary hover:underline disabled:no-underline">
                          {r.table_name}
                        </button>
                        <span className="text-[11px] text-muted-foreground">{o.label}{r.is_approximate ? " · sampled" : ""}</span>
                        <div className="mt-0.5"><TagEditor entityType="PROFILE" entityKey={`${r.database_name}.${r.schema_name}.${r.table_name}`} initial={r.tags ?? []} compact /></div>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-2.5">
                    {originOf(r) === "oracle" ? (
                      <span className="font-mono text-xs text-muted-foreground">{r.database_name}.{r.schema_name}</span>
                    ) : (
                      <button type="button" onClick={() => onOpenSchema({ database: r.database_name, schema: r.schema_name })}
                              className="inline-flex items-center gap-1 font-mono text-xs text-primary hover:underline">
                        {r.database_name}.{r.schema_name} <ArrowRight className="h-3 w-3 opacity-0 transition group-hover:opacity-100" />
                      </button>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{formatCount(r.row_count)}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{r.column_count ?? "—"}</td>
                  <td className="px-4 py-2.5">
                    {busy ? <span className="text-xs text-primary">Profiling…</span>
                      : failed ? <span className="text-xs text-destructive" title={r.error_message ?? ""}>Failed</span> : (
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                        <span className="flex items-center gap-1.5" title="Average null percentage across columns">
                          <span className="h-1.5 w-14 overflow-hidden rounded-full bg-muted">
                            <span className={cn("block h-full rounded-full", (r.avg_null_percentage ?? 0) > 20 ? "bg-warning" : "bg-success")}
                                  style={{ width: `${Math.max(3, 100 - (r.avg_null_percentage ?? 0))}%` }} />
                          </span>
                          <span className="tabular-nums text-muted-foreground">{r.avg_null_percentage != null ? `${Number(r.avg_null_percentage).toFixed(1)}% null` : "—"}</span>
                        </span>
                        {(r.key_candidates ?? 0) > 0 && <span className="flex items-center gap-1 text-muted-foreground"><KeyRound className="h-3 w-3 text-primary" />{r.key_candidates}</span>}
                        {(r.pii_columns ?? 0) > 0 && <span className="flex items-center gap-1 text-destructive"><Lock className="h-3 w-3" />{r.pii_columns} PII</span>}
                      </div>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-4 py-2.5 text-xs text-muted-foreground" title={r.profiled_at}>
                    {ago(r.profiled_at)}
                    {r.profiled_by && <span className="block text-[11px]">by {r.profiled_by}</span>}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    {!busy && (
                      <Button size="sm" variant="ghost" onClick={() => onView(r)} aria-label={`View profile of ${r.table_name}`}>
                        <Eye className="h-3.5 w-3.5" /> View
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
            {rows.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-12 text-center text-sm text-muted-foreground">
                {store.length ? "Nothing matches these filters." : "Nothing profiled yet. Open the Snowflake catalog and profile some tables."}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
