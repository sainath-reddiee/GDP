"use client";

import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { KeyRound, Maximize2, Minus, Plus } from "lucide-react";
import type { ModelEntity, ModelRelationship } from "@/lib/hub-types";
import { cn } from "@/lib/utils";

const W = 240;
const HEAD = 34;
const ROW = 18;
const MAX_ROWS = 14;
const GAP_X = 90;
const GAP_Y = 46;
const PAD = 30;

/** The Snowflake-managed Iceberg type for a Snowflake type; mirrors services/catalog/models.py iceberg_type. */
export function icebergType(datatype: string): string {
  const t = (datatype || "VARCHAR").toUpperCase().replace(/\s/g, "");
  const m = t.match(/^(NUMBER|DECIMAL|NUMERIC)(\((\d+)(,(\d+))?\))?$/);
  if (m) {
    const p = Number(m[3] ?? 38), s = Number(m[5] ?? 0);
    if (s === 0 && p <= 9) return "INT";
    if (s === 0 && p <= 18) return "BIGINT";
    return `DECIMAL(${Math.min(p, 38)},${s})`;
  }
  if (["INT", "INTEGER", "SMALLINT"].includes(t)) return "INT";
  if (t === "BIGINT") return "BIGINT";
  if (["FLOAT", "DOUBLE", "REAL"].includes(t)) return "DOUBLE";
  if (/^(VARCHAR|STRING|TEXT|CHAR)/.test(t)) return "STRING";
  if (/^TIMESTAMP_(TZ|LTZ)/.test(t)) return "TIMESTAMP_LTZ(6)";
  if (t.startsWith("TIMESTAMP")) return "TIMESTAMP_NTZ(6)";
  if (t.startsWith("TIME")) return "TIME(6)";
  if (["BOOLEAN", "DATE", "BINARY"].includes(t)) return t;
  return "STRING";
}

type Pos = Record<string, { x: number; y: number }>;
type View = { x: number; y: number; k: number };

const height = (e: ModelEntity) => HEAD + Math.min(e.attributes.length, MAX_ROWS) * ROW + (e.attributes.length > MAX_ROWS ? ROW : 0) + 8;

function autoLayout(entities: ModelEntity[], rels: ModelRelationship[]): Pos {
  // referenced entities (dimensions, hubs) on the left, the rest flowing right
  const referenced = new Set(rels.map((r) => r.to_entity));
  const ordered = [...entities].sort((a, b) => Number(referenced.has(b.entity_name)) - Number(referenced.has(a.entity_name)));
  const cols = Math.max(1, Math.ceil(Math.sqrt(ordered.length)));
  const colY = Array.from({ length: cols }, () => PAD);
  const pos: Pos = {};
  ordered.forEach((e, i) => {
    const c = i % cols;
    pos[e.entity_name] = { x: PAD + c * (W + GAP_X), y: colY[c] };
    colY[c] += height(e) + GAP_Y;
  });
  return pos;
}

/** Entities as cards with their columns, foreign keys as edges from the referencing column to the referenced one.
 *  Drag a card to move it, drag the background to pan, zoom with the buttons or the wheel; click selects. */
export function ErCanvas({ entities, relationships, platform, selected, onSelect, className }: {
  entities: ModelEntity[]; relationships: ModelRelationship[]; platform: "SNOWFLAKE" | "ICEBERG";
  selected: string | null; onSelect: (name: string | null) => void; className?: string;
}) {
  const names = entities.map((e) => e.entity_name).join("|");
  const [pos, setPos] = useState<Pos>(() => autoLayout(entities, relationships));
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  const drag = useRef<{ kind: "node" | "pan"; name?: string; px: number; py: number; ox: number; oy: number } | null>(null);
  const wrap = useRef<HTMLDivElement>(null);

  // new entities get a place; removed ones drop out; existing ones keep where the user put them
  useEffect(() => {
    setPos((prev) => {
      const fresh = autoLayout(entities, relationships);
      const next: Pos = {};
      for (const e of entities) next[e.entity_name] = prev[e.entity_name] ?? fresh[e.entity_name];
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [names]);

  const byName = useMemo(() => Object.fromEntries(entities.map((e) => [e.entity_name, e])), [entities]);
  const rowY = (e: ModelEntity, column: string) => {
    const i = e.attributes.findIndex((a) => a.name === column);
    return HEAD + (i < 0 || i >= MAX_ROWS ? MAX_ROWS : i) * ROW + ROW / 2 + 4;
  };

  const onDown = (e: ReactPointerEvent, name?: string) => {
    e.stopPropagation();
    (e.target as Element).setPointerCapture?.(e.pointerId);
    if (name) {
      onSelect(name);
      drag.current = { kind: "node", name, px: e.clientX, py: e.clientY, ox: pos[name]?.x ?? 0, oy: pos[name]?.y ?? 0 };
    } else {
      drag.current = { kind: "pan", px: e.clientX, py: e.clientY, ox: view.x, oy: view.y };
    }
  };
  const onMove = (e: ReactPointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.px, dy = e.clientY - d.py;
    if (d.kind === "node" && d.name) {
      const name = d.name;
      setPos((p) => ({ ...p, [name]: { x: d.ox + dx / view.k, y: d.oy + dy / view.k } }));
    } else {
      setView((v) => ({ ...v, x: d.ox + dx, y: d.oy + dy }));
    }
  };
  const onUp = () => { drag.current = null; };
  const zoom = (f: number) => setView((v) => ({ ...v, k: Math.max(0.3, Math.min(2.5, v.k * f)) }));
  const fit = () => {
    const box = wrap.current?.getBoundingClientRect();
    const xs = Object.values(pos);
    if (!box || !xs.length) return setView({ x: 0, y: 0, k: 1 });
    const maxX = Math.max(...xs.map((p) => p.x + W)) + PAD;
    const maxY = Math.max(...entities.map((e) => (pos[e.entity_name]?.y ?? 0) + height(e))) + PAD;
    const k = Math.max(0.3, Math.min(1.2, box.width / maxX, box.height / maxY));
    setView({ x: 0, y: 0, k });
  };

  const type = (t: string) => (platform === "ICEBERG" ? icebergType(t) : t);

  return (
    <div ref={wrap} className={cn("relative overflow-hidden rounded-2xl border border-border/80 bg-[radial-gradient(circle,hsl(var(--border))_1px,transparent_1px)] [background-size:18px_18px]", className)}
         onWheel={(e) => { if (e.ctrlKey || e.metaKey) { e.preventDefault(); zoom(e.deltaY < 0 ? 1.1 : 0.9); } }}>
      <div className="absolute right-3 top-3 z-10 flex gap-1 rounded-lg border border-border bg-card p-0.5 shadow-card">
        <button aria-label="Zoom out" onClick={() => zoom(0.85)} className="rounded-md p-1.5 hover:bg-muted"><Minus className="h-3.5 w-3.5" /></button>
        <button aria-label="Zoom in" onClick={() => zoom(1.15)} className="rounded-md p-1.5 hover:bg-muted"><Plus className="h-3.5 w-3.5" /></button>
        <button aria-label="Fit to view" onClick={fit} className="rounded-md p-1.5 hover:bg-muted"><Maximize2 className="h-3.5 w-3.5" /></button>
      </div>
      <div className="absolute left-3 top-3 z-10 rounded-md bg-card/90 px-2 py-1 text-[11px] text-muted-foreground shadow-card">
        {platform === "ICEBERG" ? "Iceberg types" : "Snowflake types"} · {entities.length} entities · {relationships.length} relationships
      </div>
      <svg className="h-full w-full touch-none select-none" onPointerDown={(e) => { onSelect(null); onDown(e); }} onPointerMove={onMove} onPointerUp={onUp}
           role="img" aria-label="Entity relationship diagram">
        <defs>
          <marker id="er-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" className="fill-primary/70" />
          </marker>
        </defs>
        <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
          {relationships.map((r, i) => {
            const a = byName[r.from_entity], b = byName[r.to_entity];
            const pa = pos[r.from_entity], pb = pos[r.to_entity];
            if (!a || !b || !pa || !pb) return null;
            const right = pb.x >= pa.x;
            const x1 = right ? pa.x + W : pa.x, y1 = pa.y + rowY(a, r.from_column);
            const x2 = right ? pb.x : pb.x + W, y2 = pb.y + rowY(b, r.to_column);
            const bend = Math.max(40, Math.abs(x2 - x1) / 2);
            const d = `M${x1},${y1} C${x1 + (right ? bend : -bend)},${y1} ${x2 + (right ? -bend : bend)},${y2} ${x2},${y2}`;
            const hot = selected === r.from_entity || selected === r.to_entity;
            return (
              <g key={i}>
                <path d={d} fill="none" strokeWidth={hot ? 2.5 : 1.5} className={hot ? "stroke-primary" : "stroke-primary/45"} markerEnd="url(#er-arrow)" />
                <title>{`${r.from_entity}.${r.from_column} -> ${r.to_entity}.${r.to_column} (${r.cardinality})`}</title>
              </g>
            );
          })}
          {entities.map((e) => {
            const p = pos[e.entity_name];
            if (!p) return null;
            const h = height(e);
            const isSel = selected === e.entity_name;
            const fk = new Set(relationships.filter((r) => r.from_entity === e.entity_name).map((r) => r.from_column));
            return (
              <g key={e.entity_name} transform={`translate(${p.x},${p.y})`} onPointerDown={(ev) => onDown(ev, e.entity_name)} className="cursor-grab">
                <rect width={W} height={h} rx="12" className={cn("fill-card", isSel ? "stroke-primary" : "stroke-border")} strokeWidth={isSel ? 2 : 1} />
                <path d={`M0,12 a12,12 0 0 1 12,-12 h${W - 24} a12,12 0 0 1 12,12 v${HEAD - 12} h-${W} z`} className={isSel ? "fill-primary" : "fill-indigo-50"} />
                <text x="12" y="21" className={cn("text-[12px] font-semibold", isSel ? "fill-white" : "fill-slate-800")}>{e.entity_name}</text>
                <text x={W - 10} y="21" textAnchor="end" className={cn("text-[9px] uppercase", isSel ? "fill-white/80" : "fill-slate-500")}>{e.kind.toLowerCase()}</text>
                {e.attributes.slice(0, MAX_ROWS).map((a, i) => (
                  <g key={a.name} transform={`translate(0,${HEAD + i * ROW + 4})`}>
                    {a.is_pk && <text x="10" y="13" className="fill-amber-600 text-[10px]">PK</text>}
                    {!a.is_pk && fk.has(a.name) && <text x="10" y="13" className="fill-primary text-[10px]">FK</text>}
                    <text x="30" y="13" className="fill-slate-800 font-mono text-[10.5px]">{a.name.length > 20 ? `${a.name.slice(0, 19)}…` : a.name}</text>
                    <text x={W - 10} y="13" textAnchor="end" className="fill-slate-500 font-mono text-[9.5px]">{type(a.datatype)}</text>
                  </g>
                ))}
                {e.attributes.length > MAX_ROWS && (
                  <text x="30" y={HEAD + MAX_ROWS * ROW + 17} className="fill-slate-500 text-[10px]">+{e.attributes.length - MAX_ROWS} more columns</text>
                )}
              </g>
            );
          })}
        </g>
      </svg>
      {!entities.length && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center text-sm text-muted-foreground">
          <span className="flex items-center gap-2"><KeyRound className="h-4 w-4" /> Add an entity or propose one from profiled tables</span>
        </div>
      )}
    </div>
  );
}
