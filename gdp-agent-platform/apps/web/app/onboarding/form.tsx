"use client";

import { useState, useTransition } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { createRun } from "./actions";
import { loadSchemas, loadTables, registerTarget } from "./catalog";
import { CatalogBrowser } from "@/components/catalog-browser";
import type { DatabaseRow, SchemaRow, TableRow, TargetRow } from "./catalog-types";

export function OnboardingForm({ databases, targets }: { databases: DatabaseRow[]; targets: TargetRow[] }) {
  const [targetMode, setTargetMode] = useState(targets[0] ? "registered" : "account");
  const [registered, setRegistered] = useState(targets[0] ? `${targets[0].target_database}.${targets[0].target_schema}.${targets[0].target_table}` : "");
  const [database, setDatabase] = useState("");
  const [schemas, setSchemas] = useState<SchemaRow[]>([]);
  const [schema, setSchema] = useState("");
  const [tables, setTables] = useState<TableRow[]>([]);
  const [table, setTable] = useState("");
  const [loading, setLoading] = useState<"schemas" | "tables" | null>(null);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const [, load] = useTransition();

  const pickDatabase = (name: string) => {
    setDatabase(name);
    setSchema("");
    setTable("");
    setTables([]);
    setLoading("schemas");
    load(async () => {
      try {
        setSchemas((await loadSchemas(name)).schemas);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not list schemas");
      } finally {
        setLoading(null);
      }
    });
  };

  const pickSchema = (name: string) => {
    setSchema(name);
    setTable("");
    setLoading("tables");
    load(async () => {
      try {
        setTables((await loadTables(database, name)).tables);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not list tables");
      } finally {
        setLoading(null);
      }
    });
  };

  return (
    <Card className="max-w-5xl">
      <CardHeader>
        <CardTitle>New source onboarding</CardTitle>
        <CardDescription>
          Point this run at the silver table you are modeling. Then register the source database on the next screen
          and pick the tables to land. A Snowflake share appears once an admin has mounted it.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form action={(form) => start(async () => {
          setError("");
          let target = registered;
          if (targetMode === "account") {
            if (!database || !schema || !table) {
              setError("Select a target database, schema, and table");
              return;
            }
            try {
              target = (await registerTarget(database, schema, table)).target_model;
            } catch (e) {
              setError(e instanceof Error ? e.message : "Could not register the target");
              return;
            }
          }
          form.set("target_model", target);
          const result = await createRun(null, form);
          if (result && !result.ok) setError(result.error);
        })}>
          <Label htmlFor="run_name">Run name</Label>
          <Input id="run_name" name="run_name" required placeholder="GDP CRM customer" />
          <input type="hidden" name="target_model" value={targetMode === "registered" ? registered : ""} />

          <div className="mt-4 flex gap-2">
            <Button type="button" variant={targetMode === "registered" ? "default" : "outline"} onClick={() => setTargetMode("registered")}>
              Registered targets
            </Button>
            <Button type="button" variant={targetMode === "account" ? "default" : "outline"} onClick={() => setTargetMode("account")}>
              Existing account table
            </Button>
          </div>

          {targetMode === "registered" && (
            <div className="mt-3 space-y-2">
              {targets.map((t) => {
                const fqn = `${t.target_database}.${t.target_schema}.${t.target_table}`;
                return (
                  <label key={t.target_table_id} className="flex cursor-pointer items-start gap-3 rounded-lg border p-3 hover:bg-muted/40">
                    <input type="radio" name="registered_target" checked={registered === fqn} onChange={() => setRegistered(fqn)} />
                    <span>
                      <span className="font-medium">{t.target_table}</span>
                      <span className="mt-1 block font-mono text-xs text-muted-foreground">{fqn}</span>
                      <span className="mt-1 block text-sm text-muted-foreground">{t.domain_name} · {t.columns} columns · {t.grain}</span>
                    </span>
                  </label>
                );
              })}
              {targets.length === 0 && <p className="text-sm text-muted-foreground">No registered targets yet. Pick an existing account table.</p>}
            </div>
          )}

          {targetMode === "account" && (
            <div className="mt-4">
              <CatalogBrowser
                databases={databases}
                schemas={schemas}
                tables={tables}
                database={database}
                schema={schema}
                table={table}
                loading={loading}
                onDatabase={(name) => pickDatabase(name)}
                onSchema={pickSchema}
                onTable={setTable}
              />
            </div>
          )}

          {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
          <Button type="submit" className="mt-4" disabled={pending}>{pending ? "Creating…" : "Create run"}</Button>
        </form>
      </CardContent>
    </Card>
  );
}
