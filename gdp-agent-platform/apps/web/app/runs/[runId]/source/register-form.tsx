"use client";

import { useState, useTransition } from "react";
import { useFormState } from "react-dom";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { CatalogBrowser } from "@/components/catalog-browser";
import { loadSchemas, loadTables } from "@/app/onboarding/catalog";
import type { DatabaseRow, SchemaRow, TableRow } from "@/app/onboarding/catalog-types";
import { registerSource } from "../source-actions";

export function RegisterSourceForm({ runId, databases, targetModel }: {
  runId: string; databases: DatabaseRow[]; targetModel: string | null;
}) {
  const [state, action] = useFormState(registerSource.bind(null, runId), null);
  const [database, setDatabase] = useState("");
  const [sourceType, setSourceType] = useState("SNOWFLAKE_DATABASE");
  const [schemas, setSchemas] = useState<SchemaRow[]>([]);
  const [schema, setSchema] = useState("");
  const [tables, setTables] = useState<TableRow[]>([]);
  const [loading, setLoading] = useState<"schemas" | "tables" | null>(null);
  const [, load] = useTransition();

  const pickDatabase = (name: string, type: string) => {
    setDatabase(name);
    setSourceType(type === "IMPORTED DATABASE" ? "SNOWFLAKE_SHARE" : "SNOWFLAKE_DATABASE");
    setSchema("");
    setTables([]);
    setLoading("schemas");
    load(async () => {
      setSchemas((await loadSchemas(name)).schemas);
      setLoading(null);
    });
  };

  const pickSchema = (name: string) => {
    setSchema(name);
    setLoading("tables");
    load(async () => {
      try {
        setTables((await loadTables(database, name)).tables);
      } finally {
        setLoading(null);
      }
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Choose the source</CardTitle>
        <CardDescription>
          Modeling target: {targetModel ?? "not set"}. Search the catalog, open a schema, and confirm the tables
          you expect to land. Registration uses the database and schema; the next step lets you select objects.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form action={action} className="space-y-4">
          <CatalogBrowser
            databases={databases}
            schemas={schemas}
            tables={tables}
            database={database}
            schema={schema}
            loading={loading}
            onDatabase={pickDatabase}
            onSchema={pickSchema}
          />
          <input type="hidden" name="database" value={database} />
          <input type="hidden" name="source_type" value={sourceType} />
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <Label htmlFor="source_system_name">Source system name</Label>
              <Input id="source_system_name" name="source_system_name" required pattern="[A-Za-z][A-Za-z0-9_]{0,63}" placeholder="CRM" />
            </div>
            <div>
              <Label htmlFor="schema">Selected schema</Label>
              <Input id="schema" name="schema" value={schema} readOnly required placeholder="Pick a schema in the catalog" />
            </div>
            <div>
              <Label htmlFor="owner">Business owner (optional)</Label>
              <Input id="owner" name="owner" />
            </div>
            <div>
              <Label htmlFor="security_classification">Security classification (optional)</Label>
              <Select id="security_classification" name="security_classification" defaultValue="">
                <option value="">Not classified</option>
                <option>PUBLIC</option><option>INTERNAL</option><option>CONFIDENTIAL</option><option>RESTRICTED</option>
              </Select>
            </div>
          </div>
          {tables.length > 0 && (
            <p className="text-sm text-muted-foreground">
              {tables.length} table{tables.length === 1 ? "" : "s"} visible in {database}.{schema}. You will choose which ones to land next.
            </p>
          )}
          {state && !state.ok && <p role="alert" className="text-sm text-destructive">{state.error}</p>}
          <Button type="submit" disabled={Boolean(loading) || !database || !schema}>
            {loading ? "Loading catalog…" : "Register source"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
