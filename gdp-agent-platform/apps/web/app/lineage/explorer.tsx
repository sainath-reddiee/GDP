"use client";

import { useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { ArrowRight, Loader2, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { lineageObjects, lineageTrace, type LineageEdge, type LineageLayer, type LineageNode, type LineageTrace } from "./actions";

const LAYER: Record<LineageLayer, { label: string; cls: string }> = {
  SOURCE: { label: "Source", cls: "border-slate-400/40 bg-slate-500/5" },
  BRONZE: { label: "Bronze (landed)", cls: "border-orange-400/40 bg-orange-500/5" },
  SILVER_PRE_MDM: { label: "Silver, pre-MDM", cls: "border-zinc-400/50 bg-zinc-500/5" },
  MDM: { label: "MDM cross-reference", cls: "border-violet-500/40 bg-violet-500/5" },
  SILVER_POST_MDM: { label: "Silver, post-MDM", cls: "border-sky-500/40 bg-sky-500/5" },
  GOLD: { label: "Gold", cls: "border-amber-500/50 bg-amber-500/10" },
};
const KIND: Record<LineageEdge["kind"], string> = {
  INGEST: "landed", MAP: "mapped (STTM)", MDM_MATCH: "MDM match", MDM_GOLDEN: "golden record", BUILD: "built", RESOLVE: "resolved key",
};
const short = (o: string) => o.split(".").slice(-1)[0];

/** Search an object, trace it, and show the path in layer swimlanes with every hop. */
export function LineageExplorer({ initial }: { initial: { q: string; object: string; column: string | null; direction: "up" | "down" | "both" } }) {
  const router = useRouter();
  const path = usePathname();
  const [q, setQ] = useState(initial.object || initial.q);
  const [options, setOptions] = useState<LineageNode[]>([]);
  const [object, setObject] = useState(initial.object);
  const [column, setColumn] = useState<string | null>(initial.column);
  const [direction, setDirection] = useState(initial.direction);
  const [data, setData] = useState<LineageTrace | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [hover, setHover] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => { void lineageObjects(q).then((r) => r.ok && setOptions(r.data.objects)); }, 250);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => {
    if (!object) { setData(null); return; }
    setBusy(true); setError("");
    void lineageTrace(object, column, direction).then((r) => { setBusy(false); if (r.ok) setData(r.data); else setError(r.error); });
    const qs = new URLSearchParams({ object, direction, ...(column ? { column } : {}) });
    router.replace(`${path}?${qs}`, { scroll: false });
  }, [object, column, direction]);   // eslint-disable-line react-hooks/exhaustive-deps

  const picked = options.find((o) => o.object === object) ?? data?.nodes.find((n) => n.object === data.start.object);
  const lanes = useMemo(() => (data?.layers ?? []).map((l) => ({ layer: l, nodes: (data?.nodes ?? []).filter((n) => n.layer === l) })), [data]);
  const touches = (e: LineageEdge, o: string) => e.from_object === o || e.to_object === o;

  return (
    <div className="space-y-4">
      <section className="surface space-y-2 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[18rem] flex-1">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a table (any part of DATABASE.SCHEMA.TABLE)" aria-label="Find a table"
                   className="h-9 w-full rounded-lg border bg-background pl-8 pr-3 text-sm" list="lineage-objects" />
            <datalist id="lineage-objects">{options.map((o) => <option key={o.object} value={o.object}>{LAYER[o.layer]?.label}</option>)}</datalist>
          </div>
          <button type="button" disabled={!q.trim()} onClick={() => { setObject(q.trim().toUpperCase()); setColumn(null); }}
                  className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground disabled:opacity-50">Trace</button>
          {picked && !!picked.columns.length && (
            <select aria-label="Column" value={column ?? ""} onChange={(e) => setColumn(e.target.value || null)} className="h-9 rounded-lg border bg-background px-2 text-sm">
              <option value="">Whole table</option>
              {picked.columns.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          )}
          <div className="flex rounded-lg border p-0.5 text-xs" role="group" aria-label="Direction">
            {(["up", "both", "down"] as const).map((d) => (
              <button key={d} type="button" aria-pressed={direction === d} onClick={() => setDirection(d)}
                      className={cn("rounded-md px-2 py-1", direction === d ? "bg-muted font-medium" : "text-muted-foreground")}>
                {d === "up" ? "Where it comes from" : d === "down" ? "Where it goes" : "Both"}</button>
            ))}
          </div>
        </div>
        {!object && <p className="text-xs text-muted-foreground">{options.length ? `${options.length} tables have lineage. Pick one.` : "No lineage yet: it appears as runs approve mappings and gold builds generate code."}</p>}
      </section>

      {busy && <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-4 w-4 animate-spin" />Tracing…</p>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {data && !busy && (
        !data.edges.length ? <p className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">No lineage found for {data.start.object}{data.start.column ? `.${data.start.column}` : ""}.</p> : (
          <>
            <div className="overflow-x-auto">
              <div className="flex min-w-max items-start gap-2" data-testid="lineage-lanes">
                {lanes.map((lane, i) => (
                  <div key={lane.layer} className="flex items-start gap-2">
                    {i > 0 && <ArrowRight className="mt-10 h-4 w-4 shrink-0 text-muted-foreground" />}
                    <div className="w-60 space-y-2">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{LAYER[lane.layer]?.label ?? lane.layer}</p>
                      {lane.nodes.map((n) => (
                        <button key={n.object} type="button" onMouseEnter={() => setHover(n.object)} onMouseLeave={() => setHover(null)}
                                onClick={() => { setObject(n.object); setQ(n.object); setColumn(null); }}
                                className={cn("w-full rounded-xl border p-2.5 text-left transition", LAYER[n.layer]?.cls,
                                  n.object === data.start.object && "ring-2 ring-primary", hover === n.object && "shadow-md")}>
                          <p className="truncate font-mono text-xs font-semibold" title={n.object}>{short(n.object)}</p>
                          <p className="truncate font-mono text-[10px] text-muted-foreground" title={n.object}>{n.object.split(".").slice(0, -1).join(".")}</p>
                          {!!n.columns.length && <p className="mt-1 line-clamp-3 font-mono text-[10px]">{n.columns.map((c) => (
                            <span key={c} className={cn("mr-1", n.object === data.start.object && c === data.start.column && "font-bold text-primary")}>{c}</span>))}</p>}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <section className="surface p-0">
              <table className="w-full text-xs">
                <thead className="bg-muted/50 text-left text-[10px] uppercase tracking-wide text-muted-foreground">
                  <tr><th className="px-3 py-2">From</th><th className="px-3 py-2">To</th><th className="px-3 py-2">How</th></tr>
                </thead>
                <tbody>
                  {data.edges.map((e, i) => (
                    <tr key={i} className={cn("border-t align-top", hover && touches(e, hover) && "bg-primary/5")}>
                      <td className="px-3 py-1.5 font-mono"><span className="text-muted-foreground">{LAYER[e.from_layer]?.label}: </span>{short(e.from_object)}{e.from_column ? `.${e.from_column}` : ""}</td>
                      <td className="px-3 py-1.5 font-mono"><span className="text-muted-foreground">{LAYER[e.to_layer]?.label}: </span>{short(e.to_object)}{e.to_column ? `.${e.to_column}` : ""}</td>
                      <td className="px-3 py-1.5"><span className="mr-1 rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium">{KIND[e.kind] ?? e.kind}</span>{e.expression}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          </>
        )
      )}
    </div>
  );
}
