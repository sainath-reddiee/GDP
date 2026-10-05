"use client";

import { useEffect, useState, useTransition } from "react";
import { useFormState } from "react-dom";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { CatalogBrowser } from "@/components/catalog-browser";
import { loadSchemas, loadTables } from "@/app/onboarding/catalog";
import type { DatabaseRow, SchemaRow, TableRow } from "@/app/onboarding/catalog-types";
import type { OnboardingIntent } from "@/app/onboarding/intent-types";
import { registerSource } from "../source-actions";

export function RegisterSourceForm({ runId, databases, targetModel, intent }: {
  runId: string; databases: DatabaseRow[]; targetModel: string | null; intent?: OnboardingIntent | null;
}) {
  const [state, action] = useFormState(registerSource.bind(null, runId), null);
  const [database, setDatabase] = useState(intent?.source.database ?? "");
  const [sourceType, setSourceType] = useState(intent?.source.source_type ?? "SNOWFLAKE_DATABASE");
  const [schemas, setSchemas] = useState<SchemaRow[]>([]);
  const [schema, setSchema] = useState(intent?.source.schema ?? "");
  const [tables, setTables] = useState<TableRow[]>([]);
  const [loading, setLoading] = useState<"schemas" | "tables" | null>(null);
  const [, load] = useTransition();

  useEffect(() => {
    if (!intent?.source.database) return;
    setLoading("schemas");
    load(async () => {
      try {
        const next = await loadSchemas(intent.source.database);
        setSchemas(next.schemas);
        if (intent.source.schema) {
          setLoading("tables");
          setTables((await loadTables(intent.source.database, intent.source.schema)).tables);
        }
      } finally {
        setLoading(null);
      }
    });
  }, [intent]);

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

  const existing = intent?.path === "map_existing";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Confirm the source</CardTitle>
        <CardDescription>
          {existing
            ? "This run reuses existing models in the same domain. Register the source, then pick objects. A new model is not generated."
            : "New-source path: register, land, and profile. A model suggestion appears after profiling."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {intent && (
          <div className="mb-4 rounded-lg border bg-muted/30 p-3 text-sm">
            <p className="font-medium">{existing ? "Reuse existing models" : "Profile and suggest a model"}</p>
            {intent.domain_name && !/GDP/i.test(intent.domain_name) && (
              <p className="text-muted-foreground">Domain {intent.domain_name}</p>
            )}
            {intent.targets.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {intent.targets.map((t) => <Badge key={t.fqn} variant="outline">{t.target_table}</Badge>)}
              </div>
            )}
            {targetModel && intent.targets.length === 0 && (
              <p className="text-muted-foreground">Primary target {targetModel}</p>
            )}
          </div>
        )}
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
            <input type="hidden" name="source_system_name" value={intent?.source.source_system_name || schema || database} />
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
              {tables.length} table{tables.length === 1 ? "" : "s"} visible in {database}.{schema}.
              {intent?.source.tables.length ? ` Plan already named ${intent.source.tables.join(", ")}.` : " Choose which ones to land next."}
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
