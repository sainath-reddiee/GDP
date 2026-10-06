"use client";

import { useMemo, useRef, useState, type PointerEvent, type WheelEvent } from "react";
import { Maximize2, Minus, Plus } from "lucide-react";
import type { ModelGraph } from "@/app/onboarding/intent-types";
import { Button } from "@/components/ui/button";

function download(name: string, body: string | Blob, type: string) {
  const blob = body instanceof Blob ? body : new Blob([body], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

const NODE_W = 220;
const TARGET_W = 240;
const HEADER = 34;
const ROW = 16;
const MAX_COLS = 10;
const GAP_Y = 28;
// left margin leaves room for join arcs between tables stacked in the same column
const SRC_COL_X = [90, 370];
const TGT_X = 690;

type Box = { id: string; x: number; y: number; w: number; h: number };

function nodeHeight(count: number) {
  return HEADER + Math.min(count, MAX_COLS) * ROW + (count > MAX_COLS ? ROW : 0) + 10;
}

export function ModelEr({ graph, runName }: { graph: ModelGraph; runName?: string }) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [drag, setDrag] = useState<{ x: number; y: number } | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const slug = (runName || "model-graph").replace(/\s+/g, "-");
  const joins = graph.joins || [];
  const isolated = new Set(graph.isolated || []);

  const layout = useMemo(() => {
    const sources = graph.sources.length ? graph.sources : [{ object_name: "(no source tables yet)", columns: [] }];
    const targets = graph.targets.length ? graph.targets : [{ target_table: "(no target selected)", fqn: "", columns: [] as string[] }];
    const twoCols = sources.length > 3;
    const colY = [24, 24];
    const boxes: Record<string, Box> = {};
    sources.forEach((s, i) => {
      const col = twoCols ? i % 2 : 0;
      const h = nodeHeight(s.columns?.length || 0);
      boxes[`s:${s.object_name}`] = { id: s.object_name, x: SRC_COL_X[col], y: colY[col], w: NODE_W, h };
      colY[col] += h + GAP_Y;
    });
    const tx = twoCols ? TGT_X : SRC_COL_X[1] + 120;
    let ty = 24;
    targets.forEach((t) => {
      const h = nodeHeight(t.columns?.length || 0);
      boxes[`t:${t.target_table}`] = { id: t.target_table, x: tx, y: ty, w: TARGET_W, h };
      ty += h + GAP_Y;
    });
    return {
      sources, targets, boxes,
      width: tx + TARGET_W + 24,
      height: Math.max(colY[0], colY[1], ty) + 8,
    };
  }, [graph.sources, graph.targets]);

  const related = (id: string) => {
    if (!hover) return true;
    if (hover === id) return true;
    return graph.edges.some((e) => (e.from === hover && e.to === id) || (e.to === hover && e.from === id))
      || joins.some((j) => (j.left === hover && j.right === id) || (j.right === hover && j.left === id));
  };

  const onWheel = (e: WheelEvent<SVGSVGElement>) => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    setZoom((z) => Math.min(2.5, Math.max(0.4, z * (e.deltaY < 0 ? 1.1 : 0.9))));
  };
  const onDown = (e: PointerEvent<SVGSVGElement>) => {
    if ((e.target as Element).closest("[data-node]")) return;
    setDrag({ x: e.clientX - pan.x, y: e.clientY - pan.y });
  };
  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    if (drag) setPan({ x: e.clientX - drag.x, y: e.clientY - drag.y });
  };

  const exportSvg = () => {
    const svg = svgRef.current;
    if (!svg) return "";
    const clone = svg.cloneNode(true) as SVGSVGElement;
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone.querySelector("[data-viewport]")?.setAttribute("transform", "");
    // standalone files have no stylesheet, so inline the theme variables
    const root = getComputedStyle(document.documentElement);
    return new XMLSerializer().serializeToString(clone)
      .replace(/hsl\(var\((--[\w-]+)\)\)/g, (_, name: string) => `hsl(${root.getPropertyValue(name).trim() || "0 0% 40%"})`);
  };
  const exportPng = () => {
    const body = exportSvg();
    if (!body) return;
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = layout.width * 2;
      canvas.height = layout.height * 2;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.scale(2, 2);
      ctx.drawImage(img, 0, 0);
      canvas.toBlob((b) => b && download(`${slug}-er.png`, b, "image/png"));
    };
    img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(body)}`;
  };

  const { boxes } = layout;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5"><span className="h-0.5 w-5 bg-amber-500" /> source join</span>
        <span className="flex items-center gap-1.5"><span className="h-0.5 w-5 bg-primary" /> mapped</span>
        <span className="flex items-center gap-1.5"><span className="h-0.5 w-5 border-t border-dashed border-primary" /> planned</span>
        <span className="flex items-center gap-1.5"><span className="h-0.5 w-5 border-t border-dashed border-muted-foreground" /> candidate</span>
        <span className="ml-auto flex items-center gap-1">
          <Button type="button" variant="outline" size="sm" aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(0.4, z * 0.85))}><Minus className="h-3.5 w-3.5" /></Button>
          <span className="w-10 text-center">{Math.round(zoom * 100)}%</span>
          <Button type="button" variant="outline" size="sm" aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(2.5, z * 1.15))}><Plus className="h-3.5 w-3.5" /></Button>
          <Button type="button" variant="outline" size="sm" aria-label="Reset view" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}><Maximize2 className="h-3.5 w-3.5" /></Button>
        </span>
      </div>

      <div className="max-h-[560px] overflow-hidden rounded-lg border bg-[radial-gradient(hsl(var(--border))_1px,transparent_1px)] [background-size:16px_16px]">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          width="100%"
          style={{ minHeight: Math.min(layout.height, 560), cursor: drag ? "grabbing" : "grab", touchAction: "none" }}
          role="img"
          aria-label="Source to target ER diagram"
          onWheel={onWheel}
          onPointerDown={onDown}
          onPointerMove={onMove}
          onPointerUp={() => setDrag(null)}
          onPointerLeave={() => setDrag(null)}
        >
          <defs>
            <marker id="er-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill="hsl(var(--primary))" />
            </marker>
          </defs>
          <g data-viewport transform={`translate(${pan.x} ${pan.y}) scale(${zoom})`}>
            {/* source <-> source joins */}
            {joins.map((j, i) => {
              const a = boxes[`s:${j.left}`];
              const b = boxes[`s:${j.right}`];
              if (!a || !b) return null;
              const sameCol = a.x === b.x;
              const x1 = sameCol ? a.x : a.x < b.x ? a.x + a.w : a.x;
              const x2 = sameCol ? b.x : a.x < b.x ? b.x : b.x + b.w;
              const y1 = a.y + HEADER / 2;
              const y2 = b.y + HEADER / 2;
              const bend = sameCol ? -60 - i * 6 : (x2 - x1) / 2;
              const d = sameCol
                ? `M${x1},${y1} C${x1 + bend},${y1} ${x2 + bend},${y2} ${x2},${y2}`
                : `M${x1},${y1} C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}`;
              const on = related(j.left) && related(j.right);
              const lx = sameCol ? x1 + bend * 0.75 : (x1 + x2) / 2;
              const ly = (y1 + y2) / 2;
              return (
                <g key={`j-${i}`} opacity={on ? 1 : 0.15}>
                  <path d={d} fill="none" stroke="#f59e0b" strokeWidth={1.8} />
                  <g transform={`translate(${lx} ${ly})`}>
                    <rect x={-46} y={-9} width={92} height={18} rx={9} fill="#fffbeb" stroke="#f59e0b" />
                    <text textAnchor="middle" y={4} fontSize={9.5} fill="#92400e">
                      {j.cardinality} · {j.keys[0]?.slice(0, 14)}{j.keys.length > 1 ? ` +${j.keys.length - 1}` : ""}
                    </text>
                  </g>
                  <title>{`${j.left} ⋈ ${j.right} on ${j.keys.join(", ")} (${j.cardinality}, confidence ${Math.round(j.confidence * 100)}%)`}</title>
                </g>
              );
            })}

            {/* source -> target edges */}
            {graph.edges.map((e, i) => {
              const a = boxes[`s:${e.from}`];
              const b = boxes[`t:${e.to}`] || Object.values(boxes).find((x) => x.id === e.to && x.w === TARGET_W);
              if (!a || !b) return null;
              const x1 = a.x + a.w;
              const y1 = a.y + HEADER / 2;
              const x2 = b.x;
              const y2 = b.y + HEADER / 2;
              const mid = (x1 + x2) / 2;
              const color = e.kind === "candidate" ? "hsl(var(--muted-foreground))" : "hsl(var(--primary))";
              const on = related(e.from) && related(e.to);
              return (
                <g key={`e-${i}`} opacity={on ? 0.9 : 0.1}>
                  <path
                    d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`}
                    fill="none"
                    stroke={color}
                    strokeWidth={Math.min(4, 1.2 + (e.weight || 0) * 0.25)}
                    strokeDasharray={e.kind === "mapped" ? undefined : "6 4"}
                    markerEnd={e.kind === "candidate" ? undefined : "url(#er-arrow)"}
                  />
                  <title>{`${e.from} → ${e.to} (${e.kind})${e.columns?.length ? `\nshared: ${e.columns.join(", ")}` : ""}`}</title>
                </g>
              );
            })}

            {/* source nodes */}
            {layout.sources.map((s) => {
              const box = boxes[`s:${s.object_name}`];
              const cols = s.columns || [];
              const joinKeys = new Set(joins.filter((j) => j.left === s.object_name || j.right === s.object_name)
                .flatMap((j) => j.keys.flatMap((k) => k.split("="))));
              const lonely = isolated.has(s.object_name);
              return (
                <g
                  key={`s-${s.object_name}`}
                  data-node
                  transform={`translate(${box.x} ${box.y})`}
                  opacity={related(s.object_name) ? 1 : 0.25}
                  onPointerEnter={() => setHover(s.object_name)}
                  onPointerLeave={() => setHover(null)}
                >
                  <rect width={box.w} height={box.h} rx={8} fill="hsl(var(--background))" stroke={lonely ? "#f97316" : "hsl(var(--border))"} strokeDasharray={lonely ? "4 3" : undefined} />
                  <path d={`M0,8 a8,8 0 0 1 8,-8 h${box.w - 16} a8,8 0 0 1 8,8 v${HEADER - 8} h-${box.w} z`} fill="#0ea5e9" opacity={0.12} />
                  <text x={10} y={15} fontSize={11.5} fontWeight={600} fill="hsl(var(--foreground))">{s.object_name.slice(0, 28)}</text>
                  <text x={10} y={28} fontSize={9.5} fill="hsl(var(--muted-foreground))">
                    {(s.object_type || "source").toLowerCase()}{s.row_count_estimate != null ? ` · ${Number(s.row_count_estimate).toLocaleString()} rows` : ""}{lonely ? " · no join found" : ""}
                  </text>
                  {cols.slice(0, MAX_COLS).map((c, i) => (
                    <g key={c.name} transform={`translate(0 ${HEADER + 4 + i * ROW})`}>
                      <text x={10} y={10} fontSize={9.5} fill={c.pk ? "#b45309" : joinKeys.has(c.name) ? "#d97706" : "hsl(var(--foreground))"} fontFamily="ui-monospace, monospace" fontWeight={c.pk ? 700 : 400}>
                        {c.pk ? "PK " : joinKeys.has(c.name) ? "FK " : "   "}{c.name.slice(0, 22)}
                      </text>
                      <text x={box.w - 10} y={10} fontSize={8.5} textAnchor="end" fill="hsl(var(--muted-foreground))">{(c.type || "").toLowerCase()}</text>
                    </g>
                  ))}
                  {cols.length > MAX_COLS && (
                    <text x={10} y={HEADER + 4 + MAX_COLS * ROW + 10} fontSize={9} fill="hsl(var(--muted-foreground))">+{cols.length - MAX_COLS} more columns</text>
                  )}
                </g>
              );
            })}

            {/* target nodes */}
            {layout.targets.map((t) => {
              const box = boxes[`t:${t.target_table}`];
              const cols = t.columns || [];
              const incoming = new Set(graph.edges.filter((e) => e.to === t.target_table).flatMap((e) => e.columns || []));
              return (
                <g
                  key={`t-${t.fqn || t.target_table}`}
                  data-node
                  transform={`translate(${box.x} ${box.y})`}
                  opacity={related(t.target_table) ? 1 : 0.25}
                  onPointerEnter={() => setHover(t.target_table)}
                  onPointerLeave={() => setHover(null)}
                >
                  <rect width={box.w} height={box.h} rx={8} fill="hsl(var(--background))" stroke={t.selected ? "hsl(var(--primary))" : "hsl(var(--border))"} strokeWidth={t.selected ? 1.6 : 1} />
                  <path d={`M0,8 a8,8 0 0 1 8,-8 h${box.w - 16} a8,8 0 0 1 8,8 v${HEADER - 8} h-${box.w} z`} fill="#8b5cf6" opacity={0.12} />
                  <text x={10} y={15} fontSize={11.5} fontWeight={600} fill="hsl(var(--foreground))">{t.target_table.slice(0, 30)}</text>
                  <text x={10} y={28} fontSize={9.5} fill="hsl(var(--muted-foreground))">
                    {t.domain_name ? `${t.domain_name} · ` : ""}{t.selected ? "target" : "candidate"}{"grain" in t && t.grain ? ` · ${t.grain}` : ""}
                  </text>
                  {cols.slice(0, MAX_COLS).map((c, i) => (
                    <text key={c} x={10} y={HEADER + 14 + i * ROW} fontSize={9.5} fontFamily="ui-monospace, monospace"
                      fill={incoming.has(String(c).toUpperCase()) ? "hsl(var(--primary))" : "hsl(var(--foreground))"}
                      fontWeight={incoming.has(String(c).toUpperCase()) ? 700 : 400}>
                      {incoming.has(String(c).toUpperCase()) ? "← " : "   "}{String(c).slice(0, 26)}
                    </text>
                  ))}
                  {cols.length > MAX_COLS && (
                    <text x={10} y={HEADER + 4 + MAX_COLS * ROW + 10} fontSize={9} fill="hsl(var(--muted-foreground))">+{cols.length - MAX_COLS} more columns</text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>
      </div>
      <p className="text-xs text-muted-foreground">Drag to pan, Ctrl + scroll to zoom, hover a table to see its joins and mappings.</p>

      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" size="sm" onClick={exportPng}>Export PNG</Button>
        <Button type="button" variant="outline" size="sm" onClick={() => download(`${slug}-er.svg`, exportSvg(), "image/svg+xml")}>Export SVG</Button>
        <Button type="button" variant="outline" size="sm" onClick={() => download(`${slug}-model-graph.json`, JSON.stringify(graph, null, 2), "application/json")}>
          Download model graph
        </Button>
      </div>
    </div>
  );
}
