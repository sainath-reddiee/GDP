"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState, useTransition } from "react";
import { Bookmark, BookmarkCheck, ChevronLeft, ChevronRight, KeyRound, LayoutGrid, Rows3, Search, ShieldAlert } from "lucide-react";
import type { ProfileFacets, ProfileRow } from "@/lib/hub-types";
import { Meter } from "@/components/hub/charts";
import { EmptyState } from "@/components/hub/states";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { ProfileDrawer } from "./profile-drawer";
import { toggleBookmark } from "./actions";

type Filters = { domain: string; table: string; run: string; tag: string; q: string; bookmarked: boolean; sort: string };

const ago = (iso: string | null) => {
  if (!iso) return "-";
  const s = (Date.now() - new Date(iso.replace(" ", "T")).getTime()) / 1000;
  if (!Number.isFinite(s)) return iso.slice(0, 16);
  return s < 3600 ? `${Math.max(1, Math.round(s / 60))} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
};

export function ProfileBrowser({ rows, total, facets, filters, view, page, pageSize }: {
  rows: ProfileRow[]; total: number; facets: ProfileFacets; filters: Filters; view: "cards" | "grid"; page: number; pageSize: number;
}) {
  const router = useRouter();
  const path = usePathname();
  const params = useSearchParams();
  const [q, setQ] = useState(filters.q);
  const [open, setOpen] = useState<ProfileRow | null>(null);
  const [marks, setMarks] = useState<Record<string, boolean>>({});
  const [, start] = useTransition();

  const push = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(patch)) { if (v) next.set(k, v); else next.delete(k); }
    if (!("page" in patch)) next.delete("page");
    router.push(`${path}?${next}`, { scroll: false });
  };
  const marked = (r: ProfileRow) => marks[r.table_fqn] ?? r.bookmarked;
  const flip = (r: ProfileRow) => {
    const on = !marked(r);
    setMarks((m) => ({ ...m, [r.table_fqn]: on }));
    start(async () => { const res = await toggleBookmark(r.table_fqn, on); if (!res.ok) setMarks((m) => ({ ...m, [r.table_fqn]: !on })); });
  };

  return (
    <div className="space-y-4">
      <div className="surface flex flex-wrap items-center gap-2 p-3">
        <form className="relative min-w-[14rem] flex-1" onSubmit={(e) => { e.preventDefault(); push({ q: q || null }); }}>
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input aria-label="Search tables" className="pl-9" placeholder="Search database, schema or table" value={q} onChange={(e) => setQ(e.target.value)} />
        </form>
        <Select aria-label="Domain" className="w-40" value={filters.domain} onChange={(e) => push({ domain: e.target.value || null })}>
          <option value="">All domains</option>
          {facets.domains.map((d) => <option key={d.domain_id} value={d.domain_id}>{d.domain_name}</option>)}
        </Select>
        <Select aria-label="Run" className="w-44" value={filters.run} onChange={(e) => push({ run: e.target.value || null })}>
          <option value="">All runs</option>
          {facets.runs.map((r) => <option key={r.run_id} value={r.run_id}>{r.run_name ?? r.run_id.slice(0, 8)}</option>)}
        </Select>
        <Select aria-label="Tag" className="w-36" value={filters.tag} onChange={(e) => push({ tag: e.target.value || null })}>
          <option value="">All tags</option>
          {facets.tags.map((t) => <option key={t} value={t}>#{t}</option>)}
        </Select>
        <Select aria-label="Sort" className="w-36" value={filters.sort} onChange={(e) => push({ sort: e.target.value })}>
          <option value="recent">Newest</option><option value="table">Name</option>
          <option value="nulls">Most nulls</option><option value="rows">Most rows</option>
          <option value="drift">Most drift</option>
        </Select>
        <Button variant={filters.bookmarked ? "default" : "outline"} size="sm" onClick={() => push({ bookmarked: filters.bookmarked ? null : "1" })}>
          <Bookmark className="h-3.5 w-3.5" /> Bookmarked
        </Button>
        <div className="ml-auto flex rounded-lg border border-border p-0.5" role="group" aria-label="View">
          {(["cards", "grid"] as const).map((v) => (
            <button key={v} aria-pressed={view === v} onClick={() => push({ view: v === "cards" ? null : v })}
                    className={cn("rounded-md px-2 py-1", view === v ? "bg-muted text-foreground" : "text-muted-foreground")}>
              {v === "cards" ? <LayoutGrid className="h-4 w-4" aria-label="Cards" /> : <Rows3 className="h-4 w-4" aria-label="Grid" />}
            </button>
          ))}
        </div>
      </div>

      {!rows.length ? (
        <EmptyState title="No profiles match">Profile tables from the Source Store or a run, or clear the filters.</EmptyState>
      ) : view === "cards" ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {rows.map((r) => (
            <article key={r.table_fqn} className="surface group flex flex-col p-4 transition hover:border-primary/25 hover:shadow-hover">
              <div className="flex items-start gap-2">
                <button className="min-w-0 flex-1 text-left" onClick={() => setOpen(r)}>
                  <p className="truncate font-mono text-[11px] text-muted-foreground">{r.database_name}.{r.schema_name}</p>
                  <h3 className="truncate text-sm font-semibold group-hover:text-primary">{r.table_name}</h3>
                </button>
                <button onClick={() => flip(r)} aria-label={marked(r) ? "Remove bookmark" : "Bookmark"} className="rounded-md p-1 text-muted-foreground hover:bg-muted">
                  {marked(r) ? <BookmarkCheck className="h-4 w-4 text-primary" /> : <Bookmark className="h-4 w-4" />}
                </button>
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2 text-center">
                <Stat label="rows" value={r.row_count == null ? "-" : Number(r.row_count).toLocaleString()} />
                <Stat label="columns" value={r.column_count ?? "-"} />
                <Stat label="passes" value={r.passes} />
              </div>
              <div className="mt-3">
                <p className="mb-1 text-[11px] text-muted-foreground">Average null rate</p>
                <Meter value={r.avg_null_pct == null ? null : Number(r.avg_null_pct)} label="Average null rate" warnAt={20} />
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[11px]">
                {r.domain_name && <span className="rounded-full bg-accent px-2 py-0.5 text-accent-foreground">{r.domain_name}</span>}
                {Number(r.key_candidates) > 0 && <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5"><KeyRound className="h-3 w-3" />{r.key_candidates} keys</span>}
                {Number(r.pii_columns) > 0 && <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-amber-800"><ShieldAlert className="h-3 w-3" />{r.pii_columns} PII</span>}
                {r.tags.map((t) => <span key={t} className="rounded-full border border-border px-2 py-0.5 text-muted-foreground">#{t}</span>)}
                {!r.catalog && <span className="rounded-full border border-dashed border-border px-2 py-0.5 text-muted-foreground" title="Profiled before the catalog existed; re-profile to add its history">no history yet</span>}
              </div>
              <p className="mt-auto pt-3 text-[11px] text-muted-foreground">Profiled {ago(r.profiled_at)}{r.profiled_by ? ` by ${r.profiled_by}` : ""}</p>
            </article>
          ))}
        </div>
      ) : (
        <div className="surface overflow-hidden">
          <Table>
            <THead><TR><TH /><TH>Table</TH><TH>Domain</TH><TH className="text-right">Rows</TH><TH className="text-right">Cols</TH><TH className="w-48">Null rate</TH><TH className="text-right">Keys</TH><TH className="text-right">PII</TH><TH>Run</TH><TH>Profiled</TH></TR></THead>
            <TBody>
              {rows.map((r) => (
                <TR key={r.table_fqn} className="cursor-pointer" onClick={() => setOpen(r)}>
                  <TD onClick={(e) => { e.stopPropagation(); flip(r); }}>{marked(r) ? <BookmarkCheck className="h-4 w-4 text-primary" aria-label="Bookmarked" /> : <Bookmark className="h-4 w-4 text-muted-foreground" aria-label="Bookmark" />}</TD>
                  <TD><div className="font-medium">{r.table_name}</div><div className="font-mono text-[11px] text-muted-foreground">{r.database_name}.{r.schema_name}</div></TD>
                  <TD className="text-xs">{r.domain_name ?? "-"}</TD>
                  <TD className="text-right tabular-nums">{r.row_count == null ? "-" : Number(r.row_count).toLocaleString()}</TD>
                  <TD className="text-right tabular-nums">{r.column_count ?? "-"}</TD>
                  <TD><Meter value={r.avg_null_pct == null ? null : Number(r.avg_null_pct)} label="Average null rate" warnAt={20} /></TD>
                  <TD className="text-right tabular-nums">{r.key_candidates ?? "-"}</TD>
                  <TD className="text-right tabular-nums">{r.pii_columns ?? "-"}</TD>
                  <TD className="text-xs">{r.run_id ? <Link onClick={(e) => e.stopPropagation()} href={`/runs/${r.run_id}`} className="text-primary hover:underline">{r.run_id.slice(0, 8)}</Link> : "-"}</TD>
                  <TD className="whitespace-nowrap text-xs text-muted-foreground">{ago(r.profiled_at)}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </div>
      )}

      {total > pageSize && (
        <div className="flex items-center justify-end gap-2 text-sm">
          <span className="text-muted-foreground">Page {page + 1} of {Math.ceil(total / pageSize)}</span>
          <Button variant="outline" size="sm" disabled={page === 0} onClick={() => push({ page: String(page - 1) })}><ChevronLeft className="h-4 w-4" /></Button>
          <Button variant="outline" size="sm" disabled={(page + 1) * pageSize >= total} onClick={() => push({ page: String(page + 1) })}><ChevronRight className="h-4 w-4" /></Button>
        </div>
      )}
      <ProfileDrawer row={open} onClose={() => setOpen(null)} bookmarked={open ? marked(open) : false} onBookmark={() => open && flip(open)} />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-lg bg-muted/50 px-2 py-1.5">
      <p className="text-sm font-semibold tabular-nums">{value}</p>
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
    </div>
  );
}
