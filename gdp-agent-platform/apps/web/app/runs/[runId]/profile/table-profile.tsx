"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Download, KeyRound, Loader2, Lock, RefreshCw, Search, TriangleAlert } from "lucide-react";
import type { ProfileColumn, TableInsights } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { GradeChip } from "@/app/sources/profile-drawer";
import { loadCatalogProfile } from "@/app/sources/actions";

const n = (v: number | null | undefined, digits = 0) => v == null || Number.isNaN(Number(v)) ? "—"
  : Number(v).toLocaleString(undefined, { maximumFractionDigits: digits });
const pct = (part: number | null | undefined, whole: number | null | undefined) =>
  part == null || !whole ? null : (100 * Number(part)) / Number(whole);
const p1 = (v: number | null) => v == null ? "—" : v === 0 ? "0%" : v < 0.1 ? "<0.1%" : `${v.toFixed(v < 10 ? 1 : 0)}%`;
const short = (v: string | null | undefined, max = 28) => v == null ? "—" : v.length > max ? `${v.slice(0, max - 1)}…` : v;

/** Things a developer should look at in a column, read from its profile alone. */
export function columnIssues(c: ProfileColumn): string[] {
  const s = c.statistics;
  const rows = s.row_count || 0;
  const out: string[] = [];
  if (s.null_percentage >= 50) out.push(`${p1(s.null_percentage)} empty`);
  if ((s.placeholder_null_count ?? 0) > 0) out.push(`${n(s.placeholder_null_count)} placeholder nulls (such as 'N/A')`);
  if (c.cardinality === "CONSTANT" && rows > 1) out.push("one value only");
  if ((s.blank_count ?? 0) > 0) out.push(`${n(s.blank_count)} blank`);
  if ((s.padded_count ?? 0) > 0) out.push(`${n(s.padded_count)} with leading or trailing spaces`);
  if (c.inferred_type) out.push(`text holding ${c.inferred_type === "NUMBER" ? "numbers" : "dates"}`);
  if (c.semantic_type === "IDENTIFIER" && (s.duplicate_count ?? 0) > 0) out.push(`identifier with ${n(s.duplicate_count)} repeats`);
  return out;
}

function Tile({ label, value, hint, tone }: { label: string; value: React.ReactNode; hint?: React.ReactNode; tone?: "warn" | "ok" }) {
  return (
    <div className="rounded-xl border bg-card px-3 py-2">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={cn("text-lg font-semibold tabular-nums", tone === "warn" && "text-amber-600", tone === "ok" && "text-emerald-600")}>{value}</p>
      {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

function Bar({ value, tone = "bg-primary" }: { value: number | null; tone?: string }) {
  return (
    <span className="block h-1.5 w-full overflow-hidden rounded-full bg-muted">
      <span className={cn("block h-full rounded-full", tone)} style={{ width: `${Math.max(value ? 2 : 0, Math.min(100, value ?? 0))}%` }} />
    </span>
  );
}

function Dimension({ label, value, hint }: { label: string; value: number | null; hint: string }) {
  return (
    <div title={hint}>
      <p className="flex justify-between text-[11px]"><span className="text-muted-foreground">{label}</span><span className="tabular-nums">{value == null ? "—" : Math.round(value)}</span></p>
      <Bar value={value} tone={value == null ? "bg-muted" : value >= 90 ? "bg-emerald-500" : value >= 75 ? "bg-primary" : value >= 60 ? "bg-amber-500" : "bg-rose-500"} />
    </div>
  );
}

function Kv({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-3 py-0.5"><dt className="text-muted-foreground">{k}</dt><dd className="text-right font-mono tabular-nums">{v}</dd></div>;
}

/** Everything the profiler measured for one column. */
function ColumnDetail({ c }: { c: ProfileColumn }) {
  const s = c.statistics;
  const rows = s.row_count || 0;
  const nonNull = rows - (s.physical_null_count ?? s.null_count ?? 0);
  const freq = s.frequency_distribution ?? [];
  const top = Math.max(1, ...freq.map((f) => f.count));
  const hist = s.histogram ?? [];
  const hmax = Math.max(1, ...hist.map((h) => h.count));
  const pii = c.pii_classification !== "NONE";
  const issues = columnIssues(c);
  return (
    <div className="grid gap-4 bg-muted/20 px-4 py-3 text-xs md:grid-cols-3">
      <dl>
        <p className="mb-1 font-semibold">Completeness</p>
        <Kv k="Rows" v={n(rows)} />
        <Kv k="Values present" v={`${n(nonNull)} (${p1(pct(nonNull, rows))})`} />
        <Kv k="Nulls" v={`${n(s.physical_null_count ?? s.null_count)} (${p1(pct(s.physical_null_count ?? s.null_count, rows))})`} />
        {s.placeholder_null_count != null && <Kv k="Placeholder nulls" v={n(s.placeholder_null_count)} />}
        {s.blank_count != null && <Kv k="Blank values" v={n(s.blank_count)} />}
        {s.padded_count != null && <Kv k="Leading or trailing spaces" v={n(s.padded_count)} />}
        <p className="mb-1 mt-3 font-semibold">Uniqueness</p>
        <Kv k="Distinct values" v={`${n(s.distinct_count)}${s.distinct_percentage != null ? ` (${p1(s.distinct_percentage)})` : ""}`} />
        {s.duplicate_count != null && <Kv k="Repeated values" v={n(s.duplicate_count)} />}
        <Kv k="Cardinality" v={c.cardinality?.toLowerCase() ?? "—"} />
        <Kv k="Key candidate" v={c.potential_key ? "yes (unique, never empty)" : "no"} />
        {s.approximate && <p className="mt-1 text-[11px] text-muted-foreground">Large table: distinct counts are estimates and top values come from a sample.</p>}
      </dl>
      <dl>
        <p className="mb-1 font-semibold">Values</p>
        {pii ? <p className="text-muted-foreground">Personal data ({c.pii_classification}): values, ranges and quartiles are not shown.</p> : (
          <>
            <Kv k="Minimum" v={short(s.min, 32)} />
            {s.p25 != null && <Kv k="25th percentile" v={n(s.p25, 4)} />}
            {s.median != null && <Kv k="Median" v={n(s.median, 4)} />}
            {s.p75 != null && <Kv k="75th percentile" v={n(s.p75, 4)} />}
            <Kv k="Maximum" v={short(s.max, 32)} />
            {s.average != null && <Kv k="Mean" v={n(s.average, 4)} />}
            {s.stddev != null && <Kv k="Standard deviation" v={n(s.stddev, 4)} />}
            {s.zero_count != null && <Kv k="Zeros" v={n(s.zero_count)} />}
            {s.negative_count != null && <Kv k="Negative values" v={n(s.negative_count)} />}
          </>
        )}
        {s.min_length != null && (
          <>
            <p className="mb-1 mt-3 font-semibold">Text</p>
            <Kv k="Length min / avg / max" v={`${n(s.min_length)} / ${n(s.avg_length, 1)} / ${n(s.max_length)}`} />
            {s.numeric_like_count != null && <Kv k="Values that are numbers" v={`${n(s.numeric_like_count)} (${p1(pct(s.numeric_like_count, nonNull))})`} />}
            {s.date_like_count != null && <Kv k="Values that are dates" v={`${n(s.date_like_count)} (${p1(pct(s.date_like_count, nonNull))})`} />}
            {s.date_format && <Kv k="Date format" v={s.date_format} />}
          </>
        )}
        {s.date_range && !pii && <Kv k="Date range" v={`${short(s.date_range.min, 19)} to ${short(s.date_range.max, 19)}`} />}
        {!!c.patterns?.length && (
          <>
            <p className="mb-1 mt-3 font-semibold">Value shapes</p>
            <p className="mb-1 text-[11px] text-muted-foreground">9 is a digit, A a letter.</p>
            {c.patterns.map((p) => <Kv key={p.pattern} k={p.pattern} v={`${n(p.count)} (${p1(pct(p.count, nonNull))})`} />)}
          </>
        )}
      </dl>
      <div className="space-y-3">
        {!!freq.length && (
          <div>
            <p className="mb-1 font-semibold">Most frequent values{pii ? " (masked)" : ""}</p>
            <ul className="space-y-1">
              {freq.slice(0, 10).map((f, i) => (
                <li key={i} className="grid grid-cols-[minmax(0,1fr)_4rem] items-center gap-2">
                  <span className="min-w-0">
                    <span className="block truncate font-mono" title={f.value ?? "null"}>{f.value ?? <em className="text-muted-foreground">null</em>}</span>
                    <Bar value={(100 * f.count) / top} tone="bg-primary/70" />
                  </span>
                  <span className="text-right tabular-nums text-muted-foreground">{p1(pct(f.count, rows))}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {!!hist.length && (
          <div>
            <p className="mb-1 font-semibold">Distribution</p>
            <div className="flex h-16 items-end gap-0.5" aria-label="Histogram">
              {hist.map((h, i) => <div key={i} title={`${n(h.lower, 2)} to ${n(h.upper, 2)}: ${n(h.count)}`} className="flex-1 rounded-sm bg-primary/70" style={{ height: `${Math.max(3, (100 * h.count) / hmax)}%` }} />)}
            </div>
            <p className="flex justify-between text-[10px] text-muted-foreground"><span>{n(hist[0].lower, 2)}</span><span>{n(hist[hist.length - 1].upper, 2)}</span></p>
          </div>
        )}
        {!!s.enum_values?.length && <p><span className="font-semibold">Allowed values seen: </span><span className="font-mono">{s.enum_values.slice(0, 20).join(", ")}</span></p>}
        {c.description && <p><span className="font-semibold">Description: </span>{c.description}</p>}
        {!!issues.length && <ul className="space-y-0.5 text-amber-700 dark:text-amber-300">{issues.map((x) => <li key={x} className="flex gap-1"><TriangleAlert className="mt-0.5 h-3 w-3 shrink-0" />{x}</li>)}</ul>}
      </div>
    </div>
  );
}

/** One table's profile, full width: whole-table facts, the quality dimensions, the column mix, and every column with
 *  its statistics (expand a column for all of them). Only what profiling measured: no checks, no models. */
export function TableProfile({ database, schema, table, onReprofile, reprofiling }: {
  database: string; schema: string; table: string; onReprofile?: () => void; reprofiling?: boolean;
}) {
  const [data, setData] = useState<TableInsights | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [family, setFamily] = useState("ALL");
  const [onlyIssues, setOnlyIssues] = useState(false);
  useEffect(() => {
    let live = true;
    setData(null); setError(""); setOpen(null);
    void loadCatalogProfile(database, schema, table).then((r) => { if (live) { if (r.ok) setData(r.data); else setError(r.error); } });
    return () => { live = false; };
  }, [database, schema, table]);

  const cols = useMemo(() => data?.profile.columns ?? [], [data]);
  const families = useMemo(() => Array.from(new Set(cols.map((c) => c.family))).sort(), [cols]);
  const semantics = useMemo(() => {
    const m = new Map<string, number>();
    cols.forEach((c) => m.set(c.semantic_type, (m.get(c.semantic_type) ?? 0) + 1));
    return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
  }, [cols]);
  if (error) return <p role="alert" className="rounded-xl border p-4 text-sm text-destructive">The profile of {table} could not be read: {error}</p>;
  if (!data) return <p className="rounded-xl border p-4 text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-4 w-4 animate-spin" />Reading the profile of {table}…</p>;

  const p = data.profile;
  const rows = p.row_count;
  const cells = rows * Math.max(cols.length, 1);
  const empty = cols.reduce((sum, c) => sum + (c.statistics.null_count ?? 0), 0);
  const keys = cols.filter((c) => c.potential_key);
  const pii = cols.filter((c) => c.pii_classification !== "NONE");
  const withIssues = cols.filter((c) => columnIssues(c).length);
  const t = p.table;
  const old = Number(p.profiler_version || 0) < 3;
  const q = query.trim().toLowerCase();
  const shown = cols.filter((c) => (!q || `${c.column_name} ${c.description ?? ""} ${c.semantic_type}`.toLowerCase().includes(q))
    && (family === "ALL" || c.family === family) && (!onlyIssues || columnIssues(c).length));
  const d = data.scorecard?.dimensions;
  const json = `data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(p, null, 2))}`;

  return (
    <section className="space-y-4 rounded-2xl border bg-card p-4" data-testid="table-profile">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="flex items-center gap-2 text-base font-semibold">{table}<GradeChip card={data.scorecard} /></h3>
          <p className="text-xs text-muted-foreground">
            <span className="font-mono">{p.source.database}.{p.source.schema}.{p.source.table}</span> · profiled {p.profiled_at?.slice(0, 16).replace("T", " ")}
            {data.entry?.profiled_by ? ` by ${data.entry.profiled_by}` : ""} · {p.approximate ? "large table: estimates and samples" : "exact counts"} · profiler v{p.profiler_version}
          </p>
        </div>
        <a href={json} download={`${table}_profile.json`} className="inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-xs hover:bg-muted"><Download className="h-3.5 w-3.5" />Profile JSON</a>
        {onReprofile && <Button size="sm" variant="outline" disabled={reprofiling} onClick={onReprofile}>{reprofiling ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}Re-profile</Button>}
      </div>
      {old && <p className="rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">This profile was made by an older profiler: re-profile the table for duplicate rows, spread and quartiles, zeros and negatives, blanks, padded values and text that holds numbers or dates.</p>}
      {data.drift?.changed && (
        <p className="rounded-lg bg-sky-500/10 px-3 py-2 text-xs text-sky-900 dark:text-sky-100">
          Changed since the previous profile{data.drift.profiled_at ? ` (${data.drift.profiled_at.slice(0, 10)})` : ""}:
          {data.drift.added.length ? ` ${data.drift.added.length} column(s) added (${data.drift.added.slice(0, 4).join(", ")});` : ""}
          {data.drift.removed.length ? ` ${data.drift.removed.length} removed (${data.drift.removed.slice(0, 4).join(", ")});` : ""}
          {data.drift.retyped.length ? ` ${data.drift.retyped.length} changed type;` : ""}
          {data.drift.row_count.delta != null ? ` rows ${data.drift.row_count.delta >= 0 ? "+" : ""}${n(data.drift.row_count.delta)}${data.drift.row_count.pct != null ? ` (${data.drift.row_count.pct.toFixed(1)}%)` : ""}` : ""}
        </p>
      )}
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Tile label="Rows" value={n(rows)} hint={p.approximate ? "estimates above 10M rows" : "exact"} />
        <Tile label="Columns" value={cols.length} hint={families.map((f) => `${cols.filter((c) => c.family === f).length} ${f.toLowerCase()}`).join(" · ")} />
        <Tile label="Duplicate rows" value={t?.duplicate_rows != null ? n(t.duplicate_rows) : "—"} tone={t?.duplicate_rows ? "warn" : t?.duplicate_rows === 0 ? "ok" : undefined}
              hint={t?.duplicate_rows != null ? `${p1(pct(t.duplicate_rows, rows))} of rows${t.duplicates_estimated ? ", estimated" : ""}` : "re-profile to measure"} />
        <Tile label="Filled cells" value={p1(cells ? 100 - (100 * empty) / cells : null)} hint={`${n(empty)} empty of ${n(cells)}`} />
        <Tile label="Key candidates" value={keys.length} hint={keys.slice(0, 3).map((k) => k.column_name).join(", ") || "none unique and never empty"} />
        <Tile label="Personal data" value={pii.length} hint={pii.slice(0, 3).map((k) => `${k.column_name} (${k.pii_classification.toLowerCase()})`).join(", ") || "none detected"} tone={pii.length ? "warn" : undefined} />
      </div>
      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
        <div className="space-y-2 rounded-xl border p-3">
          <p className="text-xs font-semibold">Quality from the profile</p>
          <Dimension label="Completeness" value={d?.completeness ?? null} hint="Share of values present across columns" />
          <Dimension label="Uniqueness" value={d?.uniqueness ?? null} hint="Key-like columns without repeats" />
          <Dimension label="Validity" value={d?.validity ?? null} hint="Values that match their column's dominant shape" />
          <Dimension label="Freshness" value={d?.freshness ?? null} hint={data.scorecard?.age_days != null ? `Last changed ${Math.round(data.scorecard.age_days)} days ago` : "When the table last changed"} />
        </div>
        <div className="space-y-2 rounded-xl border p-3 text-xs">
          <p className="font-semibold">What the columns hold</p>
          <div className="flex flex-wrap gap-1">{semantics.map(([s_, k]) => <Badge key={s_} variant="outline" className="text-[10px]">{s_.toLowerCase().replace(/_/g, " ")} · {k}</Badge>)}</div>
          <p className="pt-1 font-semibold">Needs a look ({withIssues.length})</p>
          {withIssues.length ? (
            <ul className="space-y-0.5">{withIssues.slice(0, 6).map((c) => (
              <li key={c.column_name}><button type="button" className="font-mono hover:text-primary" onClick={() => { setOpen(c.column_name); setOnlyIssues(false); }}>{c.column_name}</button>
                <span className="text-muted-foreground">: {columnIssues(c).join("; ")}</span></li>
            ))}{withIssues.length > 6 && <li className="text-muted-foreground">+{withIssues.length - 6} more (filter: needs a look)</li>}</ul>
          ) : <p className="text-muted-foreground">Nothing stands out.</p>}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a column" aria-label="Find a column" className="h-9 w-60 pl-8" />
        </div>
        <select value={family} onChange={(e) => setFamily(e.target.value)} aria-label="Type" className="h-9 rounded-lg border bg-card px-2 text-sm">
          <option value="ALL">All types</option>{families.map((f) => <option key={f} value={f}>{f.toLowerCase()}</option>)}
        </select>
        <label className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={onlyIssues} onChange={(e) => setOnlyIssues(e.target.checked)} />Needs a look</label>
        <span className="ml-auto text-xs text-muted-foreground">{shown.length} of {cols.length} columns · click a column for every statistic</span>
      </div>
      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full min-w-[1100px] text-xs">
          <thead className="bg-muted/50 text-left text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="w-6 px-2 py-2" /><th className="px-2 py-2">Column</th><th className="px-2 py-2">Type</th><th className="w-28 px-2 py-2">Empty</th>
              <th className="w-28 px-2 py-2">Distinct</th><th className="px-2 py-2">Min</th><th className="px-2 py-2">Max</th>
              <th className="px-2 py-2">Mean or length</th><th className="px-2 py-2">Most frequent</th><th className="px-2 py-2">Flags</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((c) => {
              const s = c.statistics;
              const isOpen = open === c.column_name;
              const piiCol = c.pii_classification !== "NONE";
              const topv = s.frequency_distribution?.[0];
              const issues = columnIssues(c);
              return (
                <Fragment key={c.column_name}>
                  <tr className={cn("cursor-pointer border-t align-top hover:bg-muted/30", isOpen && "bg-muted/30")} onClick={() => setOpen(isOpen ? null : c.column_name)} data-testid="profile-column">
                    <td className="px-2 py-2 text-muted-foreground">{isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}</td>
                    <td className="px-2 py-2"><span className="font-medium">{c.column_name}</span>
                      <span className="block text-[10px] text-muted-foreground">{c.semantic_type.toLowerCase().replace(/_/g, " ")}{c.cardinality ? ` · ${c.cardinality.toLowerCase()}` : ""}</span></td>
                    <td className="px-2 py-2 font-mono text-[11px]">{c.data_type}{c.inferred_type && <span className="block text-amber-600">holds {c.inferred_type.toLowerCase()}s</span>}</td>
                    <td className="px-2 py-2"><Bar value={s.null_percentage} tone={s.null_percentage > 20 ? "bg-amber-500" : "bg-emerald-500"} /><span className="tabular-nums">{p1(s.null_percentage)}</span></td>
                    <td className="px-2 py-2"><Bar value={s.distinct_percentage ?? null} /><span className="tabular-nums">{n(s.distinct_count)}{s.distinct_percentage != null ? ` · ${p1(s.distinct_percentage)}` : ""}</span></td>
                    <td className="max-w-[9rem] truncate px-2 py-2 font-mono" title={s.min ?? undefined}>{piiCol ? "masked" : short(s.min, 18)}</td>
                    <td className="max-w-[9rem] truncate px-2 py-2 font-mono" title={s.max ?? undefined}>{piiCol ? "masked" : short(s.max, 18)}</td>
                    <td className="px-2 py-2 font-mono tabular-nums">{s.average != null ? `${n(s.average, 2)}${s.stddev != null ? ` ± ${n(s.stddev, 2)}` : ""}` : s.min_length != null ? `${n(s.min_length)} to ${n(s.max_length)} chars` : "—"}</td>
                    <td className="max-w-[12rem] px-2 py-2">{topv ? <span className="block truncate font-mono" title={topv.value ?? "null"}>{topv.value ?? "null"} <span className="text-muted-foreground">{p1(pct(topv.count, s.row_count))}</span></span> : "—"}</td>
                    <td className="space-x-1 whitespace-nowrap px-2 py-2">
                      {c.potential_key && <Badge variant="secondary" className="gap-1 text-[10px]"><KeyRound className="h-3 w-3" />key</Badge>}
                      {piiCol && <Badge variant="destructive" className="gap-1 text-[10px]"><Lock className="h-3 w-3" />{c.pii_classification.toLowerCase()}</Badge>}
                      {issues.length > 0 && <span title={issues.join("; ")} className="inline-flex items-center gap-0.5 text-amber-600"><TriangleAlert className="h-3 w-3" />{issues.length}</span>}
                    </td>
                  </tr>
                  {isOpen && <tr><td colSpan={10} className="p-0"><ColumnDetail c={c} /></td></tr>}
                </Fragment>
              );
            })}
            {!shown.length && <tr><td colSpan={10} className="px-3 py-8 text-center text-muted-foreground">No columns match.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
