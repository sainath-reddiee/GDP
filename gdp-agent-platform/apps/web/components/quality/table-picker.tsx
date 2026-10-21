"use client";

import { useEffect, useMemo, useState } from "react";
import { Database, Keyboard, Loader2, Table2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Input, Label, Select } from "@/components/ui/input";
import { catalogDatabases, catalogSchemas, catalogTables } from "@/app/removal-actions";
import { registeredTables, tableColumns } from "@/app/catalog-actions";
import type { RegisteredTable } from "@/app/removal-actions";

type Mode = "registered" | "browse" | "type";

const MODES: { id: Mode; label: string; icon: typeof Table2 }[] = [
  { id: "registered", label: "Registered table", icon: Table2 },
  { id: "browse", label: "Browse Snowflake", icon: Database },
  { id: "type", label: "Type it", icon: Keyboard },
];

const parts = (fqn: string) => fqn.split(".").map((p) => p.trim()).filter(Boolean);

/** Which table a check is for: one the platform already knows, any table the signed-in role can see in Snowflake
 *  (database, then schema, then table), or a name typed by hand. Reports the table's columns once it is known, so the
 *  check editor offers real column names. */
export function TablePicker({ value, onChange, onColumns }: {
  value: string; onChange: (fqn: string) => void; onColumns?: (columns: string[]) => void;
}) {
  const [mode, setMode] = useState<Mode>(value ? "type" : "registered");
  const [registered, setRegistered] = useState<RegisteredTable[] | null>(null);
  const [databases, setDatabases] = useState<string[] | null>(null);
  const [schemas, setSchemas] = useState<string[]>([]);
  const [tables, setTables] = useState<string[]>([]);
  const [db, setDb] = useState("");
  const [schema, setSchema] = useState("");
  const [loading, setLoading] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (mode === "registered" && !registered) {
      setLoading("registered");
      void registeredTables().then((r) => {
        setLoading("");
        if (r.ok) { setRegistered(r.data.targets); if (!r.data.targets.length) setMode("browse"); } else setError(r.error);
      });
    }
    if (mode === "browse" && !databases) {
      setLoading("databases");
      void catalogDatabases().then((r) => {
        setLoading("");
        if (r.ok) setDatabases(r.data.databases.map((d) => d.database_name)); else setError(r.error);
      });
    }
  }, [mode, registered, databases]);

  // the chosen table's real columns, for the guided editor and the examples
  useEffect(() => {
    const p = parts(value);
    if (p.length !== 3) { onColumns?.([]); return; }
    let live = true;
    void tableColumns(p[0], p[1], p[2]).then((r) => { if (live) onColumns?.(r.ok ? r.data.columns.map((c) => c.column_name) : []); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const pickDb = (d: string) => {
    setDb(d); setSchema(""); setSchemas([]); setTables([]); onChange(""); setError("");
    if (!d) return;
    setLoading("schemas");
    void catalogSchemas(d).then((r) => { setLoading(""); if (r.ok) setSchemas(r.data.schemas.map((s) => s.schema_name)); else setError(r.error); });
  };
  const pickSchema = (s: string) => {
    setSchema(s); setTables([]); onChange(""); setError("");
    if (!s) return;
    setLoading("tables");
    void catalogTables(db, s).then((r) => { setLoading(""); if (r.ok) setTables(r.data.tables.map((t) => t.table_name)); else setError(r.error); });
  };
  const byDomain = useMemo(() => {
    const groups: Record<string, RegisteredTable[]> = {};
    for (const t of registered ?? []) (groups[t.domain_name || "No domain"] ??= []).push(t);
    return groups;
  }, [registered]);

  return (
    <div className="space-y-2" data-testid="table-picker">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Label className="mt-0">Table</Label>
        <div role="radiogroup" aria-label="How to choose the table" className="inline-flex rounded-lg bg-muted p-0.5">
          {MODES.map(({ id, label, icon: Icon }) => (
            <button key={id} type="button" role="radio" aria-checked={mode === id} onClick={() => { setMode(id); setError(""); }}
                    data-testid={`table-picker-${id}`}
                    className={cn("flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition",
                      mode === id ? "bg-card text-foreground shadow-xs" : "text-muted-foreground hover:text-foreground")}>
              <Icon className="h-3.5 w-3.5" /> {label}
            </button>
          ))}
        </div>
      </div>

      {mode === "registered" && (
        <Select aria-label="Registered table" value={value} onChange={(e) => onChange(e.target.value)} disabled={!registered}>
          <option value="">{loading === "registered" ? "Loading..." : "Choose a table the platform knows"}</option>
          {Object.entries(byDomain).map(([domain, list]) => (
            <optgroup key={domain} label={domain}>
              {list.map((t) => {
                const fqn = `${t.target_database}.${t.target_schema}.${t.target_table}`;
                return <option key={t.target_table_id} value={fqn}>{fqn}</option>;
              })}
            </optgroup>
          ))}
        </Select>
      )}

      {mode === "browse" && (
        <div className="grid gap-2 sm:grid-cols-3">
          <Select aria-label="Database" value={db} onChange={(e) => pickDb(e.target.value)} disabled={!databases}>
            <option value="">{loading === "databases" ? "Loading..." : "Database"}</option>
            {(databases ?? []).map((d) => <option key={d} value={d}>{d}</option>)}
          </Select>
          <Select aria-label="Schema" value={schema} onChange={(e) => pickSchema(e.target.value)} disabled={!db || loading === "schemas"}>
            <option value="">{loading === "schemas" ? "Loading..." : "Schema"}</option>
            {schemas.map((s) => <option key={s} value={s}>{s}</option>)}
          </Select>
          <Select aria-label="Table" value={parts(value)[2] && db && schema ? parts(value)[2] : ""}
                  onChange={(e) => onChange(e.target.value ? `${db}.${schema}.${e.target.value}` : "")} disabled={!schema || loading === "tables"}>
            <option value="">{loading === "tables" ? "Loading..." : schema && !tables.length ? "No tables" : "Table"}</option>
            {tables.map((t) => <option key={t} value={t}>{t}</option>)}
          </Select>
        </div>
      )}

      {mode === "type" && (
        <Input id="ck-table" className="font-mono" value={value} placeholder="DATABASE.SCHEMA.TABLE"
               onChange={(e) => onChange(e.target.value.toUpperCase())} />
      )}

      {loading && mode !== "type" && <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><Loader2 className="h-3 w-3 animate-spin" /> Reading Snowflake</p>}
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
      {value && parts(value).length === 3 && mode !== "type" && <p className="font-mono text-xs text-muted-foreground">{value}</p>}
    </div>
  );
}
