"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Crosshair, Download, KeyRound, Maximize2, Minimize2, Minus, Plus, Search, X } from "lucide-react";
import type { ModelGraph } from "@/app/onboarding/intent-types";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

function download(name: string, body: string | Blob, type: string) {
  const blob = body instanceof Blob ? body : new Blob([body], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const NODE_W = 230;
const TARGET_W = 250;
const HEADER = 36;
const ROW = 16;
const GAP_X = 110;
const GAP_Y = 30;
const PAD = 40;
const MIN_K = 0.15;
const MAX_K = 3;

type Box = { key: string; id: string; kind: "s" | "t"; x: number; y: number; w: number; h: number };
type View = { x: number; y: number; k: number };
type Detail = "keys" | "all";

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** Source tables (with their joins) on the left in as many columns as they need, candidate and chosen targets on the
 *  right, mappings between them. Pan, zoom, fullscreen, minimap, search, pin a table for its details, export. */
export function ModelEr({ graph, runName }: { graph: ModelGraph; runName?: string }) {
  const uid = useId().replace(/:/g, "");
  const wrapRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const dragRef = useRef<{ px: number; py: number; vx: number; vy: number } | null>(null);
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  const [size, setSize] = useState({ w: 800, h: 520 });
  const [full, setFull] = useState(false);
  const [detail, setDetail] = useState<Detail>("keys");
  const [hover, setHover] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [dragging, setDragging] = useState(false);
  const slug = (runName || "model-graph").replace(/\s+/g, "-");
  const joins = useMemo(() => graph.joins || [], [graph.joins]);
  const isolated = useMemo(() => new Set(graph.isolated || []), [graph.isolated]);

  const joinKeys = useMemo(() => {
    const out: Record<string, Set<string>> = {};
    for (const j of joins) {
      for (const side of [j.left, j.right]) {
        out[side] = out[side] ?? new Set();
        j.keys.flatMap((k) => k.split("=")).forEach((k) => out[side].add(k.trim()));
      }
    }
    return out;
  }, [joins]);

  const shownColumns = useCallback((name: string, cols: { name: string; type?: string | null; pk?: boolean }[]) => {
    if (detail === "all") return cols.slice(0, 40);
    const keys = joinKeys[name] ?? new Set<string>();
    const important = cols.filter((c) => c.pk || keys.has(c.name));
    return (important.length ? important : cols).slice(0, 8);
  }, [detail, joinKeys]);

  const layout = useMemo(() => {
    const sources = graph.sources.length ? graph.sources : [{ object_name: "(no source tables yet)", columns: [] }];
    const targets = graph.targets.length ? graph.targets : [{ target_table: "(no target selected)", fqn: "", columns: [] as string[] }];
    const srcCols = clamp(Math.ceil(Math.sqrt(sources.length / 2)), 1, 4);
    const colY = Array.from({ length: srcCols }, () => PAD);
    const boxes: Record<string, Box> = {};
    sources.forEach((s, i) => {
      const col = i % srcCols;
      const shown = shownColumns(s.object_name, s.columns || []);
      const more = (s.columns?.length || 0) > shown.length ? 1 : 0;
      const h = HEADER + (shown.length + more) * ROW + 12;
      boxes[`s:${s.object_name}`] = { key: `s:${s.object_name}`, id: s.object_name, kind: "s",
                                       x: PAD + 70 + col * (NODE_W + GAP_X), y: colY[col], w: NODE_W, h };
      colY[col] += h + GAP_Y;
    });
    // targets wrap into more columns once a column is as tall as the sources (or 900px), so the model stays wide
    // rather than one very tall strip
    const tx0 = PAD + 70 + srcCols * (NODE_W + GAP_X) + 40;
    const limit = Math.max(Math.max(...colY), 900);
    let tcol = 0;
    let ty = PAD;
    let tallest = PAD;
    targets.forEach((t) => {
      const cols = t.columns || [];
      const shown = detail === "all" ? Math.min(cols.length, 40) : Math.min(cols.length, 8);
      const h = HEADER + (shown + (cols.length > shown ? 1 : 0)) * ROW + 12;
      if (ty > PAD && ty + h > limit) { tcol += 1; ty = PAD; }
      boxes[`t:${t.target_table}`] = { key: `t:${t.target_table}`, id: t.target_table, kind: "t",
                                       x: tx0 + tcol * (TARGET_W + 40), y: ty, w: TARGET_W, h };
      ty += h + GAP_Y;
      tallest = Math.max(tallest, ty);
    });
    return { sources, targets, boxes, width: tx0 + (tcol + 1) * (TARGET_W + 40) + PAD, height: Math.max(...colY, tallest) + PAD };
  }, [graph.sources, graph.targets, shownColumns, detail]);

  const fit = useCallback(() => {
    const whole = Math.min(size.w / layout.width, size.h / layout.height) * 0.96;
    if (whole >= 0.55) {
      const k = Math.min(whole, 1.2);
      setView({ k, x: (size.w - layout.width * k) / 2, y: Math.max(8, (size.h - layout.height * k) / 2) });
    } else {
      // too big to read when fitted whole: fit the width at a readable size and start at the top (the minimap shows the rest)
      const k = clamp(size.w / layout.width * 0.96, 0.55, 1);
      setView({ k, x: Math.max(8, (size.w - layout.width * k) / 2), y: 8 });
    }
  }, [size, layout.width, layout.height]);

  // measure the viewport; refit whenever its size or the layout changes
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, [full]);
  useEffect(() => { fit(); }, [fit]);

  const zoomAt = useCallback((factor: number, cx?: number, cy?: number) => {
    setView((v) => {
      const k = clamp(v.k * factor, MIN_K, MAX_K);
      const px = cx ?? size.w / 2;
      const py = cy ?? size.h / 2;
      return { k, x: px - ((px - v.x) * k) / v.k, y: py - ((py - v.y) * k) / v.k };
    });
  }, [size]);

  // native, non-passive wheel: Ctrl/Cmd + wheel zooms the diagram (never the page); in fullscreen the wheel pans
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      const rect = el.getBoundingClientRect();
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - rect.left, e.clientY - rect.top);
      } else if (full) {
        e.preventDefault();
        setView((v) => ({ ...v, x: v.x - e.deltaX, y: v.y - e.deltaY }));
      }
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt, full]);

  // fullscreen: the browser's own when available, otherwise a fixed overlay; Esc leaves either
  useEffect(() => {
    if (!full) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFull(false); };
    const onChange = () => { if (!document.fullscreenElement) setFull(false); };
    window.addEventListener("keydown", onKey);
    document.addEventListener("fullscreenchange", onChange);
    wrapRef.current?.requestFullscreen?.().catch(() => undefined);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("fullscreenchange", onChange);
      if (document.fullscreenElement) document.exitFullscreen().catch(() => undefined);
    };
  }, [full]);

  const onDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if ((e.target as Element).closest("[data-node]") || e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    dragRef.current = { px: e.clientX, py: e.clientY, vx: view.x, vy: view.y };
    setDragging(true);
  };
  const onMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (d) setView((v) => ({ ...v, x: d.vx + e.clientX - d.px, y: d.vy + e.clientY - d.py }));
  };
  const onUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (dragRef.current) e.currentTarget.releasePointerCapture(e.pointerId);
    dragRef.current = null;
    setDragging(false);
  };

  const focus = useCallback((key: string) => {
    const b = layout.boxes[key];
    if (!b) return;
    const k = Math.max(view.k, 0.8);
    setView({ k, x: size.w / 2 - (b.x + b.w / 2) * k, y: size.h / 2 - (b.y + Math.min(b.h, 200) / 2) * k });
    setPinned(b.id);
  }, [layout.boxes, view.k, size]);

  const active = pinned ?? hover;
  const related = (id: string) => {
    if (!active || active === id) return true;
    return graph.edges.some((e) => (e.from === active && e.to === id) || (e.to === active && e.from === id))
      || joins.some((j) => (j.left === active && j.right === id) || (j.right === active && j.left === id));
  };

  const matches = query.trim()
    ? Object.values(layout.boxes).filter((b) => b.id.toLowerCase().includes(query.trim().toLowerCase())).slice(0, 8) : [];

  const exportSvg = () => {
    const svg = svgRef.current;
    if (!svg) return "";
    const clone = svg.cloneNode(true) as SVGSVGElement;
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone.setAttribute("width", String(layout.width));
    clone.setAttribute("height", String(layout.height));
    clone.setAttribute("viewBox", `0 0 ${layout.width} ${layout.height}`);
    clone.querySelector("[data-viewport]")?.setAttribute("transform", "");
    const root = getComputedStyle(document.documentElement);
    return new XMLSerializer().serializeToString(clone)
      .replace(/hsl\(var\((--[\w-]+)\)\)/g, (_, name: string) => `hsl(${root.getPropertyValue(name).trim() || "0 0% 40%"})`);
  };
  const exportPng = () => {
    const body = exportSvg();
    if (!body) return;
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(2, 8000 / Math.max(layout.width, layout.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(layout.width * scale);
      canvas.height = Math.round(layout.height * scale);
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((b) => b && download(`${slug}-er.png`, b, "image/png"));
    };
    img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(body)}`;
  };

  const { boxes } = layout;
  const pinnedBox = pinned ? Object.values(boxes).find((b) => b.id === pinned) : null;
  const pinnedSource = pinnedBox?.kind === "s" ? layout.sources.find((s) => s.object_name === pinned) : null;
  const pinnedTarget = pinnedBox?.kind === "t" ? layout.targets.find((t) => t.target_table === pinned) : null;
  const MINI_W = 160;
  const miniK = MINI_W / layout.width;

  const toolbar = (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span className="flex items-center gap-1.5"><span className="h-0.5 w-5 bg-warning" /> source join</span>
      <span className="flex items-center gap-1.5"><span className="h-0.5 w-5 bg-primary" /> mapped</span>
      <span className="flex items-center gap-1.5"><span className="h-0.5 w-5 border-t border-dashed border-primary" /> planned</span>
      <span className="flex items-center gap-1.5"><span className="h-0.5 w-5 border-t border-dashed border-muted-foreground" /> candidate</span>
      <div className="relative ml-auto">
        <Search className="absolute left-2 top-2 h-3.5 w-3.5" />
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a table" aria-label="Find a table"
               onKeyDown={(e) => { if (e.key === "Enter" && matches[0]) { focus(matches[0].key); setQuery(""); } }}
               className="h-8 w-44 rounded-md border bg-card pl-7 pr-2 text-xs text-foreground" />
        {matches.length > 0 && (
          <ul className="absolute right-0 z-20 mt-1 w-64 rounded-lg border bg-card p-1 shadow-lg">
            {matches.map((m) => (
              <li key={m.key}>
                <button type="button" onClick={() => { focus(m.key); setQuery(""); }}
                        className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs hover:bg-muted">
                  <span className={cn("h-2 w-2 rounded-full", m.kind === "s" ? "bg-primary" : "bg-success")} />
                  <span className="truncate font-mono">{m.id}</span>
                  <span className="ml-auto text-[10px]">{m.kind === "s" ? "source" : "target"}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="inline-flex rounded-md border p-0.5" role="radiogroup" aria-label="Columns shown">
        {([["keys", "Keys"], ["all", "All columns"]] as const).map(([v, l]) => (
          <button key={v} type="button" role="radio" aria-checked={detail === v} onClick={() => setDetail(v)}
                  className={cn("rounded px-2 py-1", detail === v ? "bg-muted font-medium text-foreground" : "")}>{l}</button>
        ))}
      </div>
      <Button type="button" variant="outline" size="sm" aria-label="Zoom out" onClick={() => zoomAt(1 / 1.2)}><Minus className="h-3.5 w-3.5" /></Button>
      <span className="w-10 text-center tabular-nums">{Math.round(view.k * 100)}%</span>
      <Button type="button" variant="outline" size="sm" aria-label="Zoom in" onClick={() => zoomAt(1.2)}><Plus className="h-3.5 w-3.5" /></Button>
      <Button type="button" variant="outline" size="sm" aria-label="Fit to view" title="Fit to view" onClick={fit}><Crosshair className="h-3.5 w-3.5" /></Button>
      <Button type="button" variant="outline" size="sm" aria-label={full ? "Exit fullscreen" : "Fullscreen"} title={full ? "Exit fullscreen (Esc)" : "Fullscreen"}
              onClick={() => setFull((f) => !f)}>
        {full ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
      </Button>
    </div>
  );

  return (
    <div ref={wrapRef} className={cn("space-y-3", full && "fixed inset-0 z-[80] !mt-0 flex flex-col bg-background p-4")}>
      {full && <p className="text-sm font-semibold">{runName ? `${runName}: ` : ""}source to target model</p>}
      {toolbar}
      <div className={cn("relative flex gap-3", full ? "min-h-0 flex-1" : "")}>
        <div ref={viewportRef} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
             onDoubleClick={(e) => { if (!(e.target as Element).closest("[data-node]")) fit(); }}
             className={cn("relative min-w-0 flex-1 overflow-hidden rounded-lg border bg-[radial-gradient(hsl(var(--border))_1px,transparent_1px)] [background-size:16px_16px]",
               full ? "h-full" : "h-[560px]", dragging ? "cursor-grabbing" : "cursor-grab")}
             style={{ touchAction: "none" }}>
          <svg ref={svgRef} width="100%" height="100%" role="img" aria-label="Source to target ER diagram" className="select-none">
            <defs>
              <marker id={`er-arrow-${uid}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M0,0 L10,5 L0,10 z" fill="hsl(var(--primary))" />
              </marker>
            </defs>
            <g data-viewport transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
              {joins.map((j, i) => {
                const a = boxes[`s:${j.left}`];
                const b = boxes[`s:${j.right}`];
                if (!a || !b) return null;
                const sameCol = a.x === b.x;
                const x1 = sameCol ? a.x : a.x < b.x ? a.x + a.w : a.x;
                const x2 = sameCol ? b.x : a.x < b.x ? b.x : b.x + b.w;
                const y1 = a.y + HEADER / 2;
                const y2 = b.y + HEADER / 2;
                const bend = sameCol ? -50 - (i % 4) * 8 : (x2 - x1) / 2;
                const d = sameCol
                  ? `M${x1},${y1} C${x1 + bend},${y1} ${x2 + bend},${y2} ${x2},${y2}`
                  : `M${x1},${y1} C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}`;
                const on = related(j.left) && related(j.right);
                const lx = sameCol ? x1 + bend * 0.75 : (x1 + x2) / 2;
                const ly = (y1 + y2) / 2;
                const label = `${j.cardinality} · ${j.keys[0]?.slice(0, 16) ?? ""}${j.keys.length > 1 ? ` +${j.keys.length - 1}` : ""}`;
                return (
                  <g key={`j-${i}`} opacity={on ? 1 : 0.12}>
                    <path d={d} fill="none" stroke="hsl(var(--warning))" strokeWidth={1.8} />
                    <g transform={`translate(${lx} ${ly})`}>
                      <rect x={-label.length * 2.9 - 8} y={-9} width={label.length * 5.8 + 16} height={18} rx={9}
                            fill="hsl(var(--background))" stroke="hsl(var(--warning))" />
                      <text textAnchor="middle" y={4} fontSize={9.5} fill="hsl(var(--warning))">{label}</text>
                    </g>
                    <title>{`${j.left} ⋈ ${j.right} on ${j.keys.join(", ")} (${j.cardinality}, confidence ${Math.round(j.confidence * 100)}%)`}</title>
                  </g>
                );
              })}

              {graph.edges.map((e, i) => {
                const a = boxes[`s:${e.from}`];
                const b = boxes[`t:${e.to}`];
                if (!a || !b) return null;
                const x1 = a.x + a.w;
                const y1 = a.y + HEADER / 2;
                const x2 = b.x;
                const y2 = b.y + HEADER / 2;
                const mid = (x1 + x2) / 2;
                const on = related(e.from) && related(e.to);
                return (
                  <g key={`e-${i}`} opacity={on ? 0.9 : 0.08}>
                    <path d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`} fill="none"
                          stroke={e.kind === "candidate" ? "hsl(var(--muted-foreground))" : "hsl(var(--primary))"}
                          strokeWidth={Math.min(4, 1.2 + (e.weight || 0) * 0.25)}
                          strokeDasharray={e.kind === "mapped" ? undefined : "6 4"}
                          markerEnd={e.kind === "candidate" ? undefined : `url(#er-arrow-${uid})`} />
                    <title>{`${e.from} → ${e.to} (${e.kind})${e.columns?.length ? `\nshared: ${e.columns.join(", ")}` : ""}`}</title>
                  </g>
                );
              })}

              {layout.sources.map((s) => {
                const box = boxes[`s:${s.object_name}`];
                const cols = s.columns || [];
                const shown = shownColumns(s.object_name, cols);
                const keys = joinKeys[s.object_name] ?? new Set<string>();
                const lonely = isolated.has(s.object_name);
                const isPinned = pinned === s.object_name;
                return (
                  <g key={`s-${s.object_name}`} data-node transform={`translate(${box.x} ${box.y})`} className="cursor-pointer"
                     opacity={related(s.object_name) ? 1 : 0.22}
                     onPointerEnter={() => setHover(s.object_name)} onPointerLeave={() => setHover(null)}
                     onClick={() => setPinned(isPinned ? null : s.object_name)}>
                    <rect width={box.w} height={box.h} rx={8} fill="hsl(var(--card))"
                          stroke={isPinned ? "hsl(var(--primary))" : lonely ? "hsl(var(--warning))" : "hsl(var(--border))"}
                          strokeWidth={isPinned ? 2 : 1} strokeDasharray={lonely && !isPinned ? "4 3" : undefined} />
                    <path d={`M0,8 a8,8 0 0 1 8,-8 h${box.w - 16} a8,8 0 0 1 8,8 v${HEADER - 8} h-${box.w} z`} fill="hsl(var(--primary))" opacity={0.1} />
                    <text x={10} y={16} fontSize={11.5} fontWeight={600} fill="hsl(var(--foreground))">{s.object_name.slice(0, 30)}</text>
                    <text x={10} y={29} fontSize={9.5} fill="hsl(var(--muted-foreground))">
                      {(s.object_type || "source").toLowerCase()}{s.row_count_estimate != null ? ` · ${Number(s.row_count_estimate).toLocaleString()} rows` : ""}{lonely ? " · no join found" : ""}
                    </text>
                    {shown.map((c, i) => (
                      <g key={c.name} transform={`translate(0 ${HEADER + 4 + i * ROW})`}>
                        <text x={10} y={10} fontSize={9.5} fontFamily="ui-monospace, monospace" fontWeight={c.pk ? 700 : 400}
                              fill={c.pk || keys.has(c.name) ? "hsl(var(--warning))" : "hsl(var(--foreground))"}>
                          {c.pk ? "PK " : keys.has(c.name) ? "FK " : "   "}{c.name.slice(0, 24)}
                        </text>
                        <text x={box.w - 10} y={10} fontSize={8.5} textAnchor="end" fill="hsl(var(--muted-foreground))">{(c.type || "").toLowerCase().slice(0, 14)}</text>
                      </g>
                    ))}
                    {cols.length > shown.length && (
                      <text x={10} y={HEADER + 4 + shown.length * ROW + 10} fontSize={9} fill="hsl(var(--muted-foreground))">+{cols.length - shown.length} more columns</text>
                    )}
                  </g>
                );
              })}

              {layout.targets.map((t) => {
                const box = boxes[`t:${t.target_table}`];
                const cols = t.columns || [];
                const shown = cols.slice(0, detail === "all" ? 40 : 8);
                const incoming = new Set(graph.edges.filter((e) => e.to === t.target_table).flatMap((e) => e.columns || []));
                const isPinned = pinned === t.target_table;
                return (
                  <g key={`t-${t.fqn || t.target_table}`} data-node transform={`translate(${box.x} ${box.y})`} className="cursor-pointer"
                     opacity={related(t.target_table) ? 1 : 0.22}
                     onPointerEnter={() => setHover(t.target_table)} onPointerLeave={() => setHover(null)}
                     onClick={() => setPinned(isPinned ? null : t.target_table)}>
                    <rect width={box.w} height={box.h} rx={8} fill="hsl(var(--card))"
                          stroke={isPinned || t.selected ? "hsl(var(--primary))" : "hsl(var(--border))"} strokeWidth={isPinned ? 2 : t.selected ? 1.6 : 1} />
                    <path d={`M0,8 a8,8 0 0 1 8,-8 h${box.w - 16} a8,8 0 0 1 8,8 v${HEADER - 8} h-${box.w} z`} fill="hsl(var(--success))" opacity={0.12} />
                    <text x={10} y={16} fontSize={11.5} fontWeight={600} fill="hsl(var(--foreground))">{t.target_table.slice(0, 32)}</text>
                    <text x={10} y={29} fontSize={9.5} fill="hsl(var(--muted-foreground))">
                      {t.domain_name ? `${t.domain_name} · ` : ""}{t.selected ? "target" : "candidate"}{t.grain ? ` · ${String(t.grain).slice(0, 30)}` : ""}
                    </text>
                    {shown.map((c, i) => {
                      const hit = incoming.has(String(c).toUpperCase());
                      return (
                        <text key={c} x={10} y={HEADER + 14 + i * ROW} fontSize={9.5} fontFamily="ui-monospace, monospace"
                              fill={hit ? "hsl(var(--primary))" : "hsl(var(--foreground))"} fontWeight={hit ? 700 : 400}>
                          {hit ? "← " : "   "}{String(c).slice(0, 28)}
                        </text>
                      );
                    })}
                    {cols.length > shown.length && (
                      <text x={10} y={HEADER + 14 + shown.length * ROW} fontSize={9} fill="hsl(var(--muted-foreground))">+{cols.length - shown.length} more columns</text>
                    )}
                  </g>
                );
              })}
            </g>
          </svg>

          {/* minimap: the whole model and the visible window; click to move there */}
          <svg width={MINI_W} height={Math.max(40, layout.height * miniK)} aria-label="Minimap"
               className="absolute bottom-2 right-2 cursor-pointer rounded-md border bg-card/90 shadow-sm"
               onPointerDown={(e) => e.stopPropagation()}
               onClick={(e) => {
                 const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
                 const gx = (e.clientX - r.left) / miniK;
                 const gy = (e.clientY - r.top) / miniK;
                 setView((v) => ({ ...v, x: size.w / 2 - gx * v.k, y: size.h / 2 - gy * v.k }));
               }}>
            {Object.values(boxes).map((b) => (
              <rect key={b.key} x={b.x * miniK} y={b.y * miniK} width={b.w * miniK} height={Math.max(2, b.h * miniK)} rx={1}
                    fill={b.kind === "s" ? "hsl(var(--primary))" : "hsl(var(--success))"} opacity={b.id === pinned ? 0.9 : 0.35} />
            ))}
            <rect x={(-view.x / view.k) * miniK} y={(-view.y / view.k) * miniK} width={(size.w / view.k) * miniK} height={(size.h / view.k) * miniK}
                  fill="none" stroke="hsl(var(--foreground))" strokeWidth={1} />
          </svg>
        </div>

        {pinnedBox && (
          <aside className={cn("w-72 shrink-0 overflow-y-auto rounded-lg border bg-card p-3 text-xs", full ? "h-full" : "h-[560px]")}>
            <div className="mb-2 flex items-start gap-2">
              <span className={cn("mt-1 h-2 w-2 shrink-0 rounded-full", pinnedBox.kind === "s" ? "bg-primary" : "bg-success")} />
              <div className="min-w-0">
                <p className="break-all font-mono text-sm font-semibold">{pinnedBox.id}</p>
                <p className="text-muted-foreground">{pinnedBox.kind === "s" ? "source table" : pinnedTarget?.selected ? "target" : "candidate target"}</p>
              </div>
              <button type="button" aria-label="Unpin" onClick={() => setPinned(null)} className="ml-auto rounded p-0.5 hover:bg-muted"><X className="h-3.5 w-3.5" /></button>
            </div>
            {joins.filter((j) => j.left === pinned || j.right === pinned).length > 0 && (
              <div className="mb-3">
                <p className="mb-1 font-medium">Joins</p>
                {joins.filter((j) => j.left === pinned || j.right === pinned).map((j, i) => (
                  <p key={i} className="text-muted-foreground"><span className="font-mono text-foreground">{j.left === pinned ? j.right : j.left}</span> on {j.keys.join(", ")} · {j.cardinality} · {Math.round(j.confidence * 100)}%</p>
                ))}
              </div>
            )}
            {graph.edges.filter((e) => e.from === pinned || e.to === pinned).length > 0 && (
              <div className="mb-3">
                <p className="mb-1 font-medium">{pinnedBox.kind === "s" ? "Feeds" : "Fed by"}</p>
                {graph.edges.filter((e) => e.from === pinned || e.to === pinned).map((e, i) => (
                  <p key={i} className="text-muted-foreground"><span className="font-mono text-foreground">{pinnedBox.kind === "s" ? e.to : e.from}</span> · {e.kind}{e.columns?.length ? ` · ${e.columns.length} columns` : ""}</p>
                ))}
              </div>
            )}
            <p className="mb-1 font-medium">Columns</p>
            <ul className="space-y-0.5 font-mono">
              {(pinnedSource?.columns ?? []).map((c) => (
                <li key={c.name} className="flex gap-2"><span className={cn(c.pk && "font-bold text-warning")}>{c.pk && <KeyRound className="mr-1 inline h-3 w-3" />}{c.name}</span><span className="ml-auto text-muted-foreground">{(c.type || "").toLowerCase()}</span></li>
              ))}
              {(pinnedTarget?.columns ?? []).map((c) => <li key={c}>{c}</li>)}
            </ul>
          </aside>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        Drag to pan · Ctrl/Cmd + scroll to zoom{full ? " · scroll to pan · Esc to exit" : ""} · double-click empty space to fit · click a table to pin it and see its details
      </p>
      {!full && (
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" size="sm" onClick={exportPng}><Download className="h-3.5 w-3.5" /> PNG</Button>
          <Button type="button" variant="outline" size="sm" onClick={() => download(`${slug}-er.svg`, exportSvg(), "image/svg+xml")}><Download className="h-3.5 w-3.5" /> SVG</Button>
          <Button type="button" variant="outline" size="sm" onClick={() => download(`${slug}-model-graph.json`, JSON.stringify(graph, null, 2), "application/json")}>
            <Download className="h-3.5 w-3.5" /> Model graph (JSON)
          </Button>
        </div>
      )}
    </div>
  );
}
