"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Database, FolderTree, History, Loader2, Search, Share2, Star, X } from "lucide-react";
import type { DatabaseRow, SchemaRow } from "@/app/onboarding/catalog-types";
import { loadSchemas } from "@/app/onboarding/catalog";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export type Target = { database: string; schema: string };
export type RecentTarget = { target: Target; staged: number };

const PIN_KEY = "aip.sources.pinned";

function readPins(): string[] {
  try {
    return JSON.parse(localStorage.getItem(PIN_KEY) ?? "[]") as string[];
  } catch {
    return [];
  }
}

/** Database › schema chooser: one button showing where you are, opening a two-pane browser with search, pinned and
 *  recent databases, and the schemas of the highlighted database with how many tables are profiled in each. */
export function CatalogBrowser({ databases, value, recents, profiledBySchema, open, onOpenChange, onPick }: {
  databases: DatabaseRow[]; value: Target | null; recents: RecentTarget[];
  /** "DB.SCHEMA" -> number of stored profiles */
  profiledBySchema: Record<string, number>;
  open: boolean; onOpenChange: (open: boolean) => void; onPick: (target: Target) => void;
}) {
  const [dbQuery, setDbQuery] = useState("");
  const [schemaQuery, setSchemaQuery] = useState("");
  const [active, setActive] = useState(value?.database ?? "");
  const [schemas, setSchemas] = useState<Record<string, SchemaRow[] | "error">>({});
  const [loading, setLoading] = useState<string | null>(null);
  const [pins, setPins] = useState<string[]>([]);
  const [cursor, setCursor] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const dbSearch = useRef<HTMLInputElement>(null);

  useEffect(() => { setPins(readPins()); }, []);
  useEffect(() => {
    if (!open) return;
    setActive(value?.database || recents[0]?.target.database || databases[0]?.database_name || "");
    setDbQuery(""); setSchemaQuery(""); setCursor(0);
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onOpenChange(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onOpenChange(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    setTimeout(() => dbSearch.current?.focus(), 0);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open || !active || schemas[active]) return;
    setLoading(active);
    loadSchemas(active)
      .then((r) => setSchemas((s) => ({ ...s, [active]: r.schemas })))
      .catch(() => setSchemas((s) => ({ ...s, [active]: "error" })))
      .finally(() => setLoading((l) => (l === active ? null : l)));
  }, [open, active, schemas]);

  const togglePin = (name: string) => {
    const next = pins.includes(name) ? pins.filter((p) => p !== name) : [...pins, name];
    setPins(next);
    try { localStorage.setItem(PIN_KEY, JSON.stringify(next)); } catch { /* storage unavailable */ }
  };

  const profiledByDb = useMemo(() => {
    const out: Record<string, number> = {};
    for (const [key, n] of Object.entries(profiledBySchema)) {
      const db = key.split(".")[0];
      out[db] = (out[db] ?? 0) + n;
    }
    return out;
  }, [profiledBySchema]);

  const q = dbQuery.trim().toLowerCase();
  const recentDbs = Array.from(new Set(recents.map((r) => r.target.database)));
  const ordered = useMemo(() => {
    const rank = (d: DatabaseRow) => (pins.includes(d.database_name) ? 0 : recentDbs.includes(d.database_name) ? 1 : 2);
    return databases.filter((d) => !q || d.database_name.toLowerCase().includes(q))
      .sort((a, b) => rank(a) - rank(b) || a.database_name.localeCompare(b.database_name)).slice(0, 300);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [databases, q, pins, recents]);

  const list = schemas[active];
  const sq = schemaQuery.trim().toLowerCase();
  const shownSchemas = Array.isArray(list)
    ? list.filter((s) => !sq || s.schema_name.toLowerCase().includes(sq))
      .sort((a, b) => (profiledBySchema[`${active}.${b.schema_name}`] ?? 0) - (profiledBySchema[`${active}.${a.schema_name}`] ?? 0)
        || a.schema_name.localeCompare(b.schema_name))
    : [];

  const pick = (schema: string) => { onPick({ database: active, schema }); onOpenChange(false); };

  const onDbKeys = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); const i = Math.min(cursor + 1, ordered.length - 1); setCursor(i); setActive(ordered[i]?.database_name ?? active); }
    if (e.key === "ArrowUp") { e.preventDefault(); const i = Math.max(cursor - 1, 0); setCursor(i); setActive(ordered[i]?.database_name ?? active); }
    if (e.key === "Enter" && ordered[cursor]) { e.preventDefault(); setActive(ordered[cursor].database_name); }
  };

  return (
    <div ref={ref} className="relative">
      <button id="pick_db" type="button" onClick={() => onOpenChange(!open)} aria-expanded={open} aria-haspopup="dialog"
              className={cn("flex h-14 w-full items-center gap-3 rounded-xl border bg-background px-4 text-left transition",
                "hover:border-primary/50", open && "border-primary ring-2 ring-primary/15")}>
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary"><Database className="h-4 w-4" /></span>
        <span className="min-w-0 flex-1">
          <span className="block text-[10px] font-medium uppercase tracking-wider text-muted-foreground">Snowflake catalog</span>
          {value ? (
            <span className="flex min-w-0 items-center gap-1.5 font-mono text-sm">
              <span className="truncate">{value.database}</span>
              <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="truncate font-semibold">{value.schema}</span>
            </span>
          ) : (
            <span className="block text-sm text-muted-foreground">Choose a database and schema</span>
          )}
        </span>
        <span className="hidden text-xs text-muted-foreground sm:block">{databases.length} databases</span>
        <ChevronDown className={cn("h-4 w-4 text-muted-foreground transition", open && "rotate-180")} />
      </button>

      {open && (
        <div role="dialog" aria-label="Choose a database and schema"
             className="absolute left-0 right-0 z-40 mt-2 overflow-hidden rounded-2xl border bg-card shadow-2xl">
          {recents.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 border-b px-3 py-2">
              <span className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground"><History className="h-3.5 w-3.5" /> Recent</span>
              {recents.slice(0, 8).map((r) => (
                <button key={`${r.target.database}.${r.target.schema}`} type="button"
                        onClick={() => { onPick(r.target); onOpenChange(false); }}
                        className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[11px] hover:border-primary/50 hover:bg-muted">
                  {r.target.database}.{r.target.schema}
                  {r.staged > 0 && <span className="rounded-full bg-success/15 px-1 text-[10px] font-semibold text-success">{r.staged}</span>}
                </button>
              ))}
            </div>
          )}
          <div className="grid md:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
            <div className="border-b md:border-b-0 md:border-r">
              <div className="relative p-2">
                <Search className="absolute left-4 top-[18px] h-4 w-4 text-muted-foreground" />
                <Input ref={dbSearch} value={dbQuery} onChange={(e) => { setDbQuery(e.target.value); setCursor(0); }} onKeyDown={onDbKeys}
                       placeholder="Search databases" aria-label="Search databases" className="pl-8" />
              </div>
              <ul role="listbox" aria-label="Databases" className="max-h-80 overflow-y-auto px-2 pb-2">
                {ordered.map((d, i) => {
                  const pinned = pins.includes(d.database_name);
                  const isActive = d.database_name === active;
                  const profiled = profiledByDb[d.database_name] ?? 0;
                  return (
                    <li key={d.database_name}>
                      <div onMouseEnter={() => { setActive(d.database_name); setCursor(i); }}
                           className={cn("group flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm",
                             isActive ? "bg-primary/10 text-primary" : "hover:bg-muted")}>
                        <button type="button" role="option" aria-selected={isActive} onClick={() => setActive(d.database_name)}
                                className="flex min-w-0 flex-1 items-center gap-2 text-left">
                          {d.type === "IMPORTED DATABASE" ? <Share2 className="h-3.5 w-3.5 shrink-0 opacity-60" />
                            : <Database className="h-3.5 w-3.5 shrink-0 opacity-60" />}
                          <span className="truncate font-mono">{d.database_name}</span>
                          {d.type === "IMPORTED DATABASE" && <span className="rounded border px-1 text-[9px] uppercase text-muted-foreground">share</span>}
                        </button>
                        {profiled > 0 && <span className="rounded-full bg-success/15 px-1.5 text-[10px] font-semibold text-success" title={`${profiled} tables profiled`}>{profiled}</span>}
                        <button type="button" onClick={() => togglePin(d.database_name)} aria-label={pinned ? `Unpin ${d.database_name}` : `Pin ${d.database_name}`}
                                className={cn("rounded p-0.5", pinned ? "text-amber-500" : "text-muted-foreground opacity-0 group-hover:opacity-100")}>
                          <Star className={cn("h-3.5 w-3.5", pinned && "fill-current")} />
                        </button>
                        <ChevronRight className="h-3.5 w-3.5 shrink-0 opacity-40" />
                      </div>
                    </li>
                  );
                })}
                {ordered.length === 0 && <li className="px-2 py-3 text-sm text-muted-foreground">No database matches.</li>}
              </ul>
            </div>
            <div>
              <div className="flex items-center gap-2 p-2">
                <div className="relative flex-1">
                  <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                  <Input value={schemaQuery} onChange={(e) => setSchemaQuery(e.target.value)}
                         onKeyDown={(e) => { if (e.key === "Enter" && shownSchemas[0]) pick(shownSchemas[0].schema_name); }}
                         placeholder={active ? `Schemas in ${active}` : "Pick a database"} aria-label="Search schemas" className="pl-8" />
                </div>
                <button type="button" aria-label="Close" onClick={() => onOpenChange(false)} className="rounded-md p-1.5 text-muted-foreground hover:bg-muted">
                  <X className="h-4 w-4" />
                </button>
              </div>
              <ul role="listbox" aria-label="Schemas" className="max-h-80 overflow-y-auto px-2 pb-2">
                {loading === active && (
                  <li className="flex items-center gap-2 px-2 py-3 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Reading schemas…</li>
                )}
                {list === "error" && <li className="px-2 py-3 text-sm text-destructive">This role cannot list schemas in {active}.</li>}
                {shownSchemas.map((s) => {
                  const profiled = profiledBySchema[`${active}.${s.schema_name}`] ?? 0;
                  const current = value?.database === active && value?.schema === s.schema_name;
                  return (
                    <li key={s.schema_name}>
                      <button type="button" role="option" aria-selected={current} onClick={() => pick(s.schema_name)}
                              className={cn("flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-muted",
                                current && "bg-primary/10 text-primary")}>
                        <FolderTree className="h-3.5 w-3.5 shrink-0 opacity-60" />
                        <span className="truncate font-mono">{s.schema_name}</span>
                        {profiled > 0
                          ? <span className="ml-auto rounded-full bg-success/15 px-1.5 text-[10px] font-semibold text-success">{profiled} profiled</span>
                          : <span className="ml-auto text-[10px] text-muted-foreground">not profiled</span>}
                      </button>
                    </li>
                  );
                })}
                {Array.isArray(list) && shownSchemas.length === 0 && (
                  <li className="px-2 py-3 text-sm text-muted-foreground">{list.length ? "No schema matches." : "No schemas visible with this role."}</li>
                )}
              </ul>
            </div>
          </div>
          <p className="border-t px-3 py-2 text-[11px] text-muted-foreground">
            Arrow keys move through databases · Enter opens the first matching schema · Esc closes · Star a database to pin it
          </p>
        </div>
      )}
    </div>
  );
}
