"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import {
  ChevronRight, Database, Eye, FolderOpen, FolderTree, KeyRound, Loader2, Search, Share2, Table2, X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { DatabaseRow, SchemaRow, TableRow } from "@/app/onboarding/catalog-types";
import { cn } from "@/lib/utils";

type ColumnInfo = { column_name: string; data_type: string };
type Kind = "all" | "database" | "share";

function match(query: string, value: string) {
  return !query || value.toLowerCase().includes(query.toLowerCase());
}

function rowsLabel(count: number | null | undefined) {
  if (count == null) return "—";
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`;
  return count.toLocaleString();
}

function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <div className="relative">
      <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
      <Input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className="h-9 pl-8 pr-7" />
      {value && (
        <button type="button" aria-label="Clear search" onClick={() => onChange("")} className="absolute right-2 top-2.5 text-muted-foreground hover:text-foreground">
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}

function Column({
  title, count, toolbar, children, className,
}: { title: string; count?: number; toolbar?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cn("flex min-h-0 min-w-0 flex-col border-b md:border-b-0 md:border-r", className)}>
      <div className="space-y-2 border-b p-3">
        <div className="flex items-center justify-between">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</p>
          {count != null && <span className="rounded-full bg-muted px-1.5 text-[11px] text-muted-foreground">{count}</span>}
        </div>
        {toolbar}
      </div>
      <div className="h-[420px] min-h-0 overflow-y-auto overscroll-contain p-2">{children}</div>
    </div>
  );
}

function Empty({ icon: Icon, text }: { icon: typeof Database; text: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-xs text-muted-foreground">
      <Icon className="h-7 w-7 opacity-30" />
      {text}
    </div>
  );
}

export function CatalogExplorer({
  databases, schemas, tables, database, schema, picked, loading, defaultKind = "all",
  onDatabase, onSchema, onToggleTable, onPickAll, onClearTables, loadColumns, hideTable,
}: {
  databases: DatabaseRow[];
  schemas: SchemaRow[];
  tables: TableRow[];
  database: string;
  schema: string;
  picked: string[];
  loading: "schemas" | "tables" | null;
  defaultKind?: Kind;
  onDatabase: (name: string, type: string) => void;
  onSchema: (name: string) => void;
  onToggleTable: (name: string) => void;
  onPickAll: (names: string[]) => void;
  onClearTables: () => void;
  loadColumns: (table: string) => Promise<ColumnInfo[]>;
  hideTable?: (name: string) => boolean;
}) {
  const counts = useMemo(() => ({
    all: databases.length,
    share: databases.filter((d) => d.type === "IMPORTED DATABASE").length,
    database: databases.filter((d) => d.type !== "IMPORTED DATABASE").length,
  }), [databases]);
  // start on the preferred filter, but never hide a catalog that is already selected
  const [kind, setKind] = useState<Kind>(() => {
    const current = databases.find((d) => d.database_name === database);
    if (current) return current.type === "IMPORTED DATABASE" ? "share" : "database";
    return defaultKind !== "all" && counts[defaultKind] ? defaultKind : "all";
  });
  const [dbQuery, setDbQuery] = useState("");
  const [schemaQuery, setSchemaQuery] = useState("");
  const [tableQuery, setTableQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const [preview, setPreview] = useState<{ table: string; columns: ColumnInfo[] | null; error?: string } | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const shownDatabases = useMemo(() => databases.filter((d) => {
    const share = d.type === "IMPORTED DATABASE";
    if (kind === "share" && !share) return false;
    if (kind === "database" && share) return false;
    return match(dbQuery, d.database_name) || match(dbQuery, d.comment || "");
  }), [databases, dbQuery, kind]);
  const shownSchemas = useMemo(() => schemas.filter((s) => match(schemaQuery, s.schema_name)), [schemas, schemaQuery]);
  const visibleTables = useMemo(() => tables.filter((t) => !hideTable?.(t.table_name)), [tables, hideTable]);
  const shownTables = useMemo(() => visibleTables.filter((t) => match(tableQuery, t.table_name)), [visibleTables, tableQuery]);

  useEffect(() => { setCursor(0); }, [dbQuery, kind]);
  useEffect(() => { setPreview(null); setTableQuery(""); }, [database, schema]);
  useEffect(() => { setSchemaQuery(""); }, [database]);

  const onListKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!shownDatabases.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = Math.max(0, Math.min(shownDatabases.length - 1, cursor + (e.key === "ArrowDown" ? 1 : -1)));
      setCursor(next);
      listRef.current?.querySelector<HTMLElement>(`[data-idx="${next}"]`)?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      const d = shownDatabases[cursor];
      if (d) onDatabase(d.database_name, d.type);
    }
  };

  const openPreview = async (table: string) => {
    if (preview?.table === table) { setPreview(null); return; }
    setPreview({ table, columns: null });
    try {
      setPreview({ table, columns: await loadColumns(table) });
    } catch (e) {
      setPreview({ table, columns: [], error: e instanceof Error ? e.message : "Could not load columns" });
    }
  };

  const selectedDb = databases.find((d) => d.database_name === database);
  const allShownPicked = shownTables.length > 0 && shownTables.every((t) => picked.includes(t.table_name));

  return (
    <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
      <div className="flex flex-wrap items-center gap-1.5 border-b bg-muted/30 px-4 py-2.5 text-sm">
        <FolderTree className="h-4 w-4 text-muted-foreground" />
        <span className={cn("font-medium", !database && "text-muted-foreground")}>{database || "Choose a catalog"}</span>
        {database && <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />}
        {database && <span className={cn("font-medium", !schema && "text-muted-foreground")}>{schema || "choose schema"}</span>}
        {schema && <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />}
        {schema && (
          <span className="text-muted-foreground">
            {picked.length ? `${picked.length} of ${visibleTables.length} tables selected` : `all ${visibleTables.length} tables`}
          </span>
        )}
        {selectedDb?.type === "IMPORTED DATABASE" && (
          <Badge variant="outline" className="ml-auto text-[10px]">read-only share</Badge>
        )}
      </div>

      <div className={cn(
        "grid",
        preview ? "md:grid-cols-[260px_220px_1fr_260px]" : "md:grid-cols-[280px_240px_1fr]",
      )}>
        {/* catalogs */}
        <Column
          title="Catalog"
          count={shownDatabases.length}
          toolbar={(
            <>
              <div className="flex flex-wrap gap-1.5">
                {(["all", "share", "database"] as const).map((k) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => setKind(k)}
                    className={cn(
                      "rounded-full border px-2.5 py-0.5 text-xs",
                      kind === k ? "border-primary bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted",
                    )}
                  >
                    {k === "all" ? "All" : k === "share" ? "Shares" : "Databases"} <span className="opacity-70">{counts[k]}</span>
                  </button>
                ))}
              </div>
              <SearchBox value={dbQuery} onChange={setDbQuery} placeholder="Search catalogs" />
            </>
          )}
        >
          <div ref={listRef} tabIndex={0} onKeyDown={onListKey} role="listbox" aria-label="Catalogs" className="space-y-0.5 focus:outline-none">
            {shownDatabases.length === 0 && <Empty icon={Database} text="No catalogs match." />}
            {shownDatabases.map((d, i) => {
              const share = d.type === "IMPORTED DATABASE";
              const active = database === d.database_name;
              return (
                <button
                  type="button"
                  role="option"
                  aria-selected={active}
                  key={d.database_name}
                  data-idx={i}
                  title={d.comment || d.database_name}
                  onClick={() => { setCursor(i); onDatabase(d.database_name, d.type); }}
                  className={cn(
                    "flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left hover:bg-muted",
                    active && "bg-primary/10 ring-1 ring-primary/40",
                    i === cursor && !active && "bg-muted/60",
                  )}
                >
                  {share ? <Share2 className="mt-0.5 h-4 w-4 shrink-0 text-violet-500" /> : <Database className="mt-0.5 h-4 w-4 shrink-0 text-sky-600" />}
                  <span className="min-w-0 flex-1">
                    <span className="block break-all text-sm font-medium leading-tight">{d.database_name}</span>
                    {d.comment && <span className="line-clamp-1 text-[11px] text-muted-foreground">{d.comment}</span>}
                  </span>
                  {active && <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-primary" />}
                </button>
              );
            })}
          </div>
        </Column>

        {/* schemas */}
        <Column
          title="Schema"
          count={database ? shownSchemas.length : undefined}
          toolbar={<SearchBox value={schemaQuery} onChange={setSchemaQuery} placeholder="Search schemas" />}
        >
          {!database ? (
            <Empty icon={FolderOpen} text="Pick a catalog first." />
          ) : loading === "schemas" ? (
            <div className="flex items-center justify-center gap-2 p-6 text-xs text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Loading schemas…</div>
          ) : !shownSchemas.length ? (
            <Empty icon={FolderOpen} text={schemas.length ? "No schemas match." : "No schemas visible to this role."} />
          ) : (
            <div className="space-y-0.5">
              {shownSchemas.map((s) => {
                const active = schema === s.schema_name;
                return (
                  <button
                    key={s.schema_name}
                    type="button"
                    onClick={() => onSchema(s.schema_name)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm hover:bg-muted",
                      active && "bg-primary/10 font-medium ring-1 ring-primary/40",
                    )}
                  >
                    <FolderOpen className={cn("h-4 w-4 shrink-0", active ? "text-primary" : "text-amber-500")} />
                    <span className="min-w-0 flex-1 break-all leading-tight">{s.schema_name}</span>
                    {active && <ChevronRight className="h-4 w-4 shrink-0 text-primary" />}
                  </button>
                );
              })}
            </div>
          )}
        </Column>

        {/* tables */}
        <Column
          title="Tables"
          count={schema ? shownTables.length : undefined}
          className={preview ? undefined : "md:border-r-0"}
          toolbar={(
            <div className="flex items-center gap-2">
              <div className="min-w-0 flex-1"><SearchBox value={tableQuery} onChange={setTableQuery} placeholder="Search tables" /></div>
              <Button
                type="button" variant="outline" size="sm" disabled={!shownTables.length}
                onClick={() => allShownPicked ? onClearTables() : onPickAll(shownTables.map((t) => t.table_name))}
              >
                {allShownPicked ? "Clear" : "Select all"}
              </Button>
            </div>
          )}
        >
          {!schema ? (
            <Empty icon={Table2} text={database ? "Pick a schema to list its tables." : "Pick a catalog and schema."} />
          ) : loading === "tables" ? (
            <div className="flex items-center justify-center gap-2 p-6 text-xs text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Loading tables…</div>
          ) : !shownTables.length ? (
            <Empty icon={Table2} text={visibleTables.length ? "No tables match." : "This schema has no source tables."} />
          ) : (
            <div className="space-y-0.5">
              {shownTables.map((t) => {
                const on = picked.includes(t.table_name);
                const previewing = preview?.table === t.table_name;
                return (
                  <div
                    key={t.table_name}
                    className={cn(
                      "group flex items-center gap-2 rounded-lg px-2.5 py-2 hover:bg-muted",
                      on && "bg-primary/5",
                      previewing && "ring-1 ring-primary/40",
                    )}
                  >
                    <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2.5">
                      <input type="checkbox" checked={on} onChange={() => onToggleTable(t.table_name)} />
                      <Table2 className="h-4 w-4 shrink-0 text-muted-foreground" />
                      <span className="min-w-0 flex-1">
                        <span className="block break-all text-sm font-medium leading-tight">{t.table_name}</span>
                        {t.comment && <span className="line-clamp-1 text-[11px] text-muted-foreground">{t.comment}</span>}
                      </span>
                    </label>
                    <Badge variant="outline" className="shrink-0 text-[10px]">{t.table_type === "VIEW" ? "view" : "table"}</Badge>
                    <span className="w-14 shrink-0 text-right text-xs tabular-nums text-muted-foreground">{rowsLabel(t.row_count)}</span>
                    <button
                      type="button"
                      aria-label={`Preview ${t.table_name}`}
                      onClick={() => openPreview(t.table_name)}
                      className={cn("rounded p-1 text-muted-foreground hover:bg-background hover:text-foreground", previewing && "text-primary")}
                    >
                      <Eye className="h-4 w-4" />
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </Column>

        {/* column preview */}
        {preview && (
          <Column
            title="Columns"
            count={preview.columns?.length}
            className="bg-muted/20 md:border-r-0"
            toolbar={(
              <div className="flex items-center gap-2">
                <p className="min-w-0 flex-1 truncate text-sm font-semibold">{preview.table}</p>
                <button type="button" aria-label="Close preview" onClick={() => setPreview(null)} className="text-muted-foreground hover:text-foreground">
                  <X className="h-4 w-4" />
                </button>
              </div>
            )}
          >
            {preview.columns === null ? (
              <div className="flex items-center gap-2 p-3 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading columns…</div>
            ) : preview.error ? (
              <p className="p-3 text-xs text-destructive">{preview.error}</p>
            ) : (
              <>
                <ul className="space-y-0.5">
                  {preview.columns.map((c) => (
                    <li key={c.column_name} className="flex items-center gap-1.5 rounded px-1.5 py-1 text-xs hover:bg-muted">
                      {/(^ID$|_ID$|_KEY$)/i.test(c.column_name) ? <KeyRound className="h-3 w-3 shrink-0 text-amber-500" /> : <span className="w-3 shrink-0" />}
                      <span className="min-w-0 flex-1 truncate font-mono">{c.column_name}</span>
                      <span className="shrink-0 text-[10px] uppercase text-muted-foreground">{c.data_type}</span>
                    </li>
                  ))}
                </ul>
                <Button
                  type="button" size="sm" className="mt-2 w-full"
                  variant={picked.includes(preview.table) ? "outline" : "default"}
                  onClick={() => onToggleTable(preview.table)}
                >
                  {picked.includes(preview.table) ? "Remove from selection" : "Add to selection"}
                </Button>
              </>
            )}
          </Column>
        )}
      </div>
    </div>
  );
}
