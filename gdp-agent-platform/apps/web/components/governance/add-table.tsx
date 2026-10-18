"use client";

import { useEffect, useState, useTransition } from "react";
import { Clock, Loader2, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/input";
import { addTableToDomain, catalogDatabases, catalogSchemas, catalogTables, type CatalogTable } from "@/app/removal-actions";

/** Put an existing Snowflake table into this domain: pick database, schema and table, and it is registered with its
 *  columns exactly as the modeling flow does. The reverse is Remove from domain. */
export function AddTable({ domainId, registered, onAdded }: { domainId: string; registered: Set<string>; onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [databases, setDatabases] = useState<string[] | null>(null);
  const [schemas, setSchemas] = useState<string[]>([]);
  const [tables, setTables] = useState<CatalogTable[]>([]);
  const [pick, setPick] = useState({ database: "", schema: "", table: "" });
  const [loading, setLoading] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState("");
  const [busy, start] = useTransition();

  useEffect(() => {
    if (!open || databases) return;
    setLoading("databases");
    void catalogDatabases().then((r) => {
      setLoading("");
      if (r.ok) setDatabases(r.data.databases.map((d) => d.database_name)); else setError(r.error);
    });
  }, [open, databases]);

  const chooseDatabase = (database: string) => {
    setPick({ database, schema: "", table: "" }); setSchemas([]); setTables([]); setError("");
    if (!database) return;
    setLoading("schemas");
    void catalogSchemas(database).then((r) => {
      setLoading("");
      if (r.ok) setSchemas(r.data.schemas.map((s) => s.schema_name)); else setError(r.error);
    });
  };
  const chooseSchema = (schema: string) => {
    setPick((p) => ({ ...p, schema, table: "" })); setTables([]); setError("");
    if (!schema) return;
    setLoading("tables");
    void catalogTables(pick.database, schema).then((r) => {
      setLoading("");
      if (r.ok) setTables(r.data.tables); else setError(r.error);
    });
  };
  const fqn = (t: string) => `${pick.database}.${pick.schema}.${t}`;
  const add = () => start(async () => {
    setError("");
    const r = await addTableToDomain(domainId, pick.database, pick.schema, pick.table);
    if (!r.ok) { setError(r.error); return; }
    if (r.pending) { setPending(`${pick.table}: add requested, waiting for approval`); setOpen(false); return; }
    setPick((p) => ({ ...p, table: "" }));
    setOpen(false);
    onAdded();
  });

  if (!open) {
    return (
      <span className="ml-auto flex items-center gap-2">
        {pending && <span role="status" className="inline-flex items-center gap-1 rounded-md bg-human/10 px-2 py-0.5 text-xs font-medium text-human"><Clock className="h-3 w-3" />{pending}</span>}
        <Button size="sm" variant="outline" className="h-7 px-2.5 text-xs" onClick={() => { setOpen(true); setPending(""); }} data-testid="add-table">
          <Plus className="h-3.5 w-3.5" /> Add table
        </Button>
      </span>
    );
  }
  const free = tables.filter((t) => !registered.has(fqn(t.table_name)));
  return (
    <div className="animate-fade-up w-full space-y-3 rounded-lg border bg-muted/30 p-3" data-testid="add-table-form">
      <div className="grid gap-2 sm:grid-cols-3">
        <Select aria-label="Database" value={pick.database} onChange={(e) => chooseDatabase(e.target.value)} disabled={!databases}>
          <option value="">{loading === "databases" ? "Loading…" : "Database"}</option>
          {(databases ?? []).map((d) => <option key={d} value={d}>{d}</option>)}
        </Select>
        <Select aria-label="Schema" value={pick.schema} onChange={(e) => chooseSchema(e.target.value)} disabled={!pick.database || loading === "schemas"}>
          <option value="">{loading === "schemas" ? "Loading…" : "Schema"}</option>
          {schemas.map((s) => <option key={s} value={s}>{s}</option>)}
        </Select>
        <Select aria-label="Table" value={pick.table} onChange={(e) => setPick((p) => ({ ...p, table: e.target.value }))} disabled={!pick.schema || loading === "tables"}>
          <option value="">{loading === "tables" ? "Loading…" : pick.schema && !free.length ? "No tables left to add" : "Table"}</option>
          {free.map((t) => <option key={t.table_name} value={t.table_name}>{t.table_name}{t.table_type === "VIEW" ? " (view)" : ""}</option>)}
        </Select>
      </div>
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={busy || !pick.table} onClick={add}>
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />} Add to domain
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setOpen(false); setError(""); }}>Cancel</Button>
        <span className="ml-auto text-xs text-muted-foreground">Columns are read from Snowflake; nothing in the table changes.</span>
      </div>
    </div>
  );
}
