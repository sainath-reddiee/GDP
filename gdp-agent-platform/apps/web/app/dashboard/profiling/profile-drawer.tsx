"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, useTransition } from "react";
import { AlertTriangle, Bookmark, BookmarkCheck, ChevronDown, ChevronRight, FileCode2, GitCompareArrows, KeyRound, Loader2, Network, RefreshCw, ShieldAlert } from "lucide-react";
import type { ColumnDetail, ColumnProfile, Expectations, ProfileRow } from "@/lib/hub-types";
import type { TableInsights } from "@/lib/types";
import { Columns, Meter } from "@/components/hub/charts";
import { Drawer, Modal } from "@/components/hub/overlay";
import { ErrorNote } from "@/components/hub/states";
import { Button, buttonVariants } from "@/components/ui/button";
import { Select } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { expectations, loadColumns, loadInsights, reprofile } from "./actions";

/** Staged profile document columns in the catalog's shape (tables profiled before the catalog existed). */
function fromDoc(doc: TableInsights | null): ColumnProfile[] {
  return (doc?.profile.columns ?? []).map((c, i) => {
    const s = c.statistics;
    const nonNull = (s.row_count ?? 0) - (s.null_count ?? 0);
    return {
      column_name: c.column_name, ordinal: i + 1, data_type: c.data_type, family: c.family, semantic_type: c.semantic_type,
      pii_classification: c.pii_classification, row_count: s.row_count, null_count: s.null_count, null_pct: s.null_percentage,
      distinct_count: s.distinct_count, cardinality: c.cardinality,
      cardinality_ratio: s.distinct_count != null && nonNull > 0 ? s.distinct_count / nonNull : null,
      min_value: s.min ?? null, max_value: s.max ?? null, potential_key: c.potential_key,
      top_values: (s.frequency_distribution ?? c.sample_values ?? []).slice(0, 10),
      histogram: s.histogram ?? null, description: c.description ?? null,
    };
  });
}

function bars(c: ColumnProfile) {
  if (c.histogram?.length) {
    return c.histogram.map((h) => {
      const lo = h.low ?? h.lower, hi = h.high ?? h.upper;
      return { label: lo != null && hi != null ? `${Number(lo).toLocaleString()} to ${Number(hi).toLocaleString()}` : "bucket", value: Number(h.count) };
    });
  }
  return (c.top_values ?? []).map((t) => ({ label: t.value == null ? "(null)" : String(t.value), value: Number(t.count) }));
}

export function ProfileDrawer({ row, onClose, bookmarked, onBookmark }: {
  row: ProfileRow | null; onClose: () => void; bookmarked: boolean; onBookmark: () => void;
}) {
  const [detail, setDetail] = useState<ColumnDetail | null>(null);
  const [insights, setInsights] = useState<TableInsights | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [exp, setExp] = useState<Expectations | null>(null);
  const [expTab, setExpTab] = useState<"soda" | "gx">("soda");
  const [pending, start] = useTransition();

  useEffect(() => {
    if (!row) return;
    let live = true;
    setLoading(true); setError(null); setNotice(null); setDetail(null); setInsights(null); setExpanded(null);
    Promise.all([loadColumns(row.table_fqn), loadInsights(row.database_name, row.schema_name, row.table_name)]).then(([cols, ins]) => {
      if (!live) return;
      if (cols.ok) setDetail(cols.data);
      if (ins.ok) setInsights(ins.data);
      if (!cols.ok && !ins.ok) setError(ins.error);
      setLoading(false);
    });
    return () => { live = false; };
  }, [row]);

  const columns = useMemo(() => (detail?.catalog && detail.columns.length ? detail.columns : fromDoc(insights)), [detail, insights]);
  if (!row) return null;

  const switchPass = (profileId: string) => start(async () => {
    const r = await loadColumns(row.table_fqn, profileId);
    if (r.ok) setDetail(r.data); else setError(r.error);
  });
  const rerun = () => start(async () => {
    const r = await reprofile(row.database_name, row.schema_name, [row.table_name]);
    setNotice(r.ok ? "Re-profiling started. The new pass appears here when it finishes." : null);
    if (!r.ok) setError(r.error);
  });
  const openExpectations = () => start(async () => {
    const r = await expectations(row.database_name, row.schema_name, row.table_name);
    if (r.ok) setExp(r.data); else setError(r.error);
  });

  const score = insights?.scorecard;
  const drifted = detail?.drift?.columns ?? {};
  const live = insights?.drift;
  return (
    <Drawer open onClose={onClose} title={row.table_name}
            subtitle={<span className="font-mono text-xs">{row.database_name}.{row.schema_name}{row.domain_name ? ` · ${row.domain_name}` : ""}</span>}
            actions={<button onClick={onBookmark} aria-label={bookmarked ? "Remove bookmark" : "Bookmark"} className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted">
              {bookmarked ? <BookmarkCheck className="h-4 w-4 text-primary" /> : <Bookmark className="h-4 w-4" />}</button>}>
      <div className="mb-4 flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={rerun} disabled={pending}><RefreshCw className="h-3.5 w-3.5" /> Re-profile</Button>
        <Button size="sm" variant="outline" onClick={openExpectations} disabled={pending}><FileCode2 className="h-3.5 w-3.5" /> Soda and GX checks</Button>
        <Link href={`/dashboard/sttm?source=${encodeURIComponent(row.table_name)}`} className={buttonVariants({ size: "sm", variant: "outline" })}><GitCompareArrows className="h-3.5 w-3.5" /> Mappings</Link>
        <Link href={`/dashboard/modeling?tables=${encodeURIComponent(row.table_fqn)}`} className={buttonVariants({ size: "sm" })}><Network className="h-3.5 w-3.5" /> Model this table</Link>
      </div>
      {error && <div className="mb-3"><ErrorNote>{error}</ErrorNote></div>}
      {notice && <p className="mb-3 rounded-lg bg-accent px-3 py-2 text-sm text-accent-foreground">{notice}</p>}
      {loading ? <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Loading profile</div> : (
        <div className="space-y-5">
          {score && (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
              <Score label="Overall" value={score.overall} grade={score.grade} />
              {(["completeness", "uniqueness", "validity", "freshness"] as const).map((k) => <Score key={k} label={k} value={score.dimensions[k]} />)}
            </div>
          )}
          {(live?.changed || detail?.drift?.changed) && (
            <div className="rounded-xl border border-amber-200 bg-amber-50/70 p-3 text-sm text-amber-900">
              <p className="flex items-center gap-1.5 font-medium"><AlertTriangle className="h-4 w-4" /> Drift detected</p>
              {live?.changed && (
                <p className="mt-1 text-xs">Live table since this profile: {[
                  live.added.length && `${live.added.length} columns added`, live.removed.length && `${live.removed.length} removed`,
                  live.retyped.length && `${live.retyped.length} retyped`, live.row_count.delta && `rows ${live.row_count.delta > 0 ? "+" : ""}${live.row_count.delta.toLocaleString()}`,
                ].filter(Boolean).join(", ")}</p>
              )}
              {detail?.drift?.changed && <p className="mt-1 text-xs">Against the previous pass: {detail.drift.drifted_columns} columns changed (marked below).</p>}
            </div>
          )}
          {detail?.catalog && detail.passes.length > 1 && (
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">Pass</span>
              <Select aria-label="Profiling pass" className="h-8 w-auto text-xs" value={detail.profile_id} onChange={(e) => switchPass(e.target.value)}>
                {detail.passes.map((p) => <option key={p.profile_id} value={p.profile_id}>{p.profiled_at.slice(0, 16)}{p.run_id ? ` · run ${p.run_id.slice(0, 8)}` : ""}</option>)}
              </Select>
              <span className="text-xs text-muted-foreground">{detail.passes.length} passes on record</span>
            </div>
          )}
          <div className="overflow-hidden rounded-xl border border-border/80">
            <div className="grid grid-cols-[1.4fr_0.9fr_1fr_1fr] gap-3 bg-muted/60 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              <span>Column</span><span>Semantic type</span><span>Null rate</span><span>Distinct ratio</span>
            </div>
            {columns.map((c) => {
              const open = expanded === c.column_name;
              const flags = drifted[c.column_name];
              return (
                <div key={c.column_name} className="border-t border-border/60">
                  <button onClick={() => setExpanded(open ? null : c.column_name)} aria-expanded={open}
                          className="grid w-full grid-cols-[1.4fr_0.9fr_1fr_1fr] items-center gap-3 px-3 py-2 text-left text-sm hover:bg-accent/40">
                    <span className="flex min-w-0 items-center gap-1.5">
                      {open ? <ChevronDown className="h-3.5 w-3.5 shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0" />}
                      <span className="truncate font-mono text-xs">{c.column_name}</span>
                      {c.potential_key && <KeyRound className="h-3 w-3 shrink-0 text-primary" aria-label="key candidate" />}
                      {c.pii_classification && c.pii_classification !== "NONE" && <ShieldAlert className="h-3 w-3 shrink-0 text-amber-600" aria-label="PII" />}
                      {flags && <span className="shrink-0 rounded bg-amber-100 px-1 text-[10px] text-amber-800" title={flags.join("; ")}>drift</span>}
                    </span>
                    <span className="truncate text-xs">{(c.semantic_type ?? "-").toLowerCase().replace(/_/g, " ")}<span className="block text-[10px] text-muted-foreground">{c.data_type}</span></span>
                    <Meter value={c.null_pct == null ? null : Number(c.null_pct)} label="Null rate" warnAt={20} />
                    <Meter value={c.cardinality_ratio == null ? null : Number(c.cardinality_ratio) * 100} label="Distinct values per non-null row" />
                  </button>
                  {open && (
                    <div className={cn("grid gap-4 bg-muted/20 px-4 py-3 md:grid-cols-[1fr_14rem]")}>
                      <div>
                        <p className="mb-2 text-xs font-medium">{c.histogram?.length ? "Value distribution" : "Most frequent values"}</p>
                        <Columns bars={bars(c)} unit="rows" showTable />
                      </div>
                      <dl className="space-y-1 text-xs">
                        <Item k="Rows" v={c.row_count} /><Item k="Nulls" v={c.null_count} /><Item k="Distinct" v={c.distinct_count} />
                        <Item k="Cardinality" v={c.cardinality?.toLowerCase()} /><Item k="Min" v={c.min_value ?? (c.pii_classification !== "NONE" ? "masked" : null)} />
                        <Item k="Max" v={c.max_value ?? (c.pii_classification !== "NONE" ? "masked" : null)} />
                        {flags && <Item k="Drift" v={flags.join("; ")} />}
                        {c.description && <p className="pt-1 text-muted-foreground">{c.description}</p>}
                      </dl>
                    </div>
                  )}
                </div>
              );
            })}
            {!columns.length && <p className="px-3 py-4 text-sm text-muted-foreground">No column profile on record. Re-profile the table.</p>}
          </div>
        </div>
      )}
      <Modal open={!!exp} onClose={() => setExp(null)} wide title="Checks from this profile"
             description="What the profile shows to be true today, as SodaCL and as a Great Expectations suite. Adopt them in a run's data quality stage.">
        <div className="mb-3 flex gap-1">
          {(["soda", "gx"] as const).map((t) => (
            <Button key={t} size="sm" variant={expTab === t ? "default" : "outline"} onClick={() => setExpTab(t)}>{t === "soda" ? "SodaCL" : "Great Expectations"}</Button>
          ))}
        </div>
        <pre className="max-h-[55vh] overflow-auto rounded-lg bg-slate-950 p-4 text-xs text-slate-100">
          {expTab === "soda" ? exp?.soda_yaml : JSON.stringify(exp?.gx_suite, null, 2)}
        </pre>
      </Modal>
    </Drawer>
  );
}

function Score({ label, value, grade }: { label: string; value: number | null; grade?: string }) {
  return (
    <div className="rounded-xl border border-border/70 px-3 py-2">
      <p className="text-lg font-semibold tabular-nums">{value == null ? "-" : Math.round(value)}{grade ? <span className="ml-1 text-xs font-medium text-muted-foreground">{grade}</span> : null}</p>
      <p className="text-[11px] capitalize text-muted-foreground">{label}</p>
    </div>
  );
}

function Item({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-3"><dt className="text-muted-foreground">{k}</dt><dd className="truncate text-right tabular-nums">{v == null || v === "" ? "-" : typeof v === "number" ? v.toLocaleString() : v}</dd></div>;
}
