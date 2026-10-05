"use client";

import { useMemo, useState, type ReactNode } from "react";
import { Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import type { DatabaseRow, SchemaRow, TableRow } from "@/app/onboarding/catalog-types";
import { cn } from "@/lib/utils";

function match(query: string, value: string) {
  return !query || value.toLowerCase().includes(query.toLowerCase());
}

function rowsLabel(count: number | null) {
  if (count == null) return "rows unknown";
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M rows`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k rows`;
  return `${count.toLocaleString()} rows`;
}

function Filter({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <div className="relative">
      <Search className="pointer-events-none absolute left-2 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
      <Input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className="h-8 pl-7" />
    </div>
  );
}

function Pane({
  title, count, empty, children,
}: { title: string; count: number; empty: string; children: ReactNode }) {
  return (
    <div className="flex min-h-[280px] min-w-0 flex-col rounded-lg border bg-card">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <p className="text-sm font-medium">{title}</p>
        <span className="text-xs text-muted-foreground">{count}</span>
      </div>
      <div className="min-h-0 flex-1 space-y-0.5 overflow-auto p-1.5">
        {count === 0 ? <p className="px-2 py-6 text-center text-sm text-muted-foreground">{empty}</p> : children}
      </div>
    </div>
  );
}

export function CatalogBrowser({
  databases, schemas, tables, database, schema, table, depth = "table", loading, onDatabase, onSchema, onTable,
}: {
  databases: DatabaseRow[];
  schemas: SchemaRow[];
  tables: TableRow[];
  database: string;
  schema: string;
  table?: string;
  depth?: "schema" | "table";
  loading?: "schemas" | "tables" | null;
  onDatabase: (name: string, type: string) => void;
  onSchema: (name: string) => void;
  onTable?: (name: string) => void;
}) {
  const [dbQuery, setDbQuery] = useState("");
  const [schemaQuery, setSchemaQuery] = useState("");
  const [tableQuery, setTableQuery] = useState("");
  const [kind, setKind] = useState<"all" | "database" | "share">("all");

  const shownDatabases = useMemo(() => databases.filter((d) => {
    const share = d.type === "IMPORTED DATABASE";
    if (kind === "share" && !share) return false;
    if (kind === "database" && share) return false;
    return match(dbQuery, d.database_name);
  }), [databases, dbQuery, kind]);
  const shownSchemas = useMemo(() => schemas.filter((s) => match(schemaQuery, s.schema_name)), [schemas, schemaQuery]);
  const shownTables = useMemo(() => tables.filter((t) => match(tableQuery, t.table_name)), [tables, tableQuery]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {(["all", "database", "share"] as const).map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => setKind(k)}
            className={cn(
              "rounded-full border px-2.5 py-0.5 text-xs capitalize",
              kind === k ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-muted",
            )}
          >
            {k === "all" ? "All catalogs" : k === "share" ? "Shares" : "Databases"}
          </button>
        ))}
        {(database || schema || table) && (
          <p className="ml-auto truncate font-mono text-xs text-muted-foreground">
            {[database, schema, depth === "table" ? table : null].filter(Boolean).join(" · ")}
          </p>
        )}
      </div>
      <div className={cn("grid gap-3", depth === "table" ? "lg:grid-cols-3" : "lg:grid-cols-2")}>
        <div className="space-y-2">
          <Filter value={dbQuery} onChange={setDbQuery} placeholder="Search databases or shares" />
          <Pane title="Catalog" count={shownDatabases.length} empty="No catalogs match that search.">
            {shownDatabases.map((d) => {
              const share = d.type === "IMPORTED DATABASE";
              return (
                <button
                  type="button"
                  key={d.database_name}
                  title={d.comment || d.database_name}
                  onClick={() => onDatabase(d.database_name, d.type)}
                  className={cn(
                    "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted",
                    database === d.database_name && "bg-accent text-accent-foreground",
                  )}
                >
                  <span className="min-w-0 flex-1 break-all font-medium">{d.database_name}</span>
                  <Badge variant="outline">{share ? "share" : "database"}</Badge>
                </button>
              );
            })}
          </Pane>
        </div>
        <div className="space-y-2">
          <Filter value={schemaQuery} onChange={setSchemaQuery} placeholder="Search schemas" />
          <Pane
            title="Schema"
            count={shownSchemas.length}
            empty={loading === "schemas" ? "Loading schemas…" : database ? "No schemas in this catalog." : "Select a catalog first."}
          >
            {shownSchemas.map((s) => (
              <button
                type="button"
                key={s.schema_name}
                onClick={() => onSchema(s.schema_name)}
                className={cn(
                  "w-full rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted",
                  schema === s.schema_name && "bg-accent text-accent-foreground",
                )}
              >
                {s.schema_name}
              </button>
            ))}
          </Pane>
        </div>
        {depth === "table" && (
          <div className="space-y-2">
            <Filter value={tableQuery} onChange={setTableQuery} placeholder="Search tables" />
            <Pane
              title="Table"
              count={shownTables.length}
              empty={loading === "tables" ? "Loading tables…" : schema ? "No tables in this schema." : "Select a schema to see tables."}
            >
              {shownTables.map((t) => (
                <button
                  type="button"
                  key={t.table_name}
                  title={t.comment || t.table_name}
                  onClick={() => onTable?.(t.table_name)}
                  className={cn(
                    "flex w-full flex-col rounded-md px-2 py-1.5 text-left hover:bg-muted",
                    table === t.table_name && "bg-accent text-accent-foreground",
                  )}
                >
                  <span className="truncate text-sm font-medium">{t.table_name}</span>
                  <span className="text-[11px] text-muted-foreground">
                    {(t.table_type || "TABLE").toLowerCase()} · {rowsLabel(t.row_count)}
                    {t.comment ? ` · ${t.comment}` : ""}
                  </span>
                </button>
              ))}
            </Pane>
          </div>
        )}
      </div>
    </div>
  );
}
