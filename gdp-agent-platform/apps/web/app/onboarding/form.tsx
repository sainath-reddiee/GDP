"use client";

import { useState, useTransition } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { createRun } from "./actions";
import { loadSchemas, loadTables, registerTarget } from "./catalog";
import type { DatabaseRow, SchemaRow, TableRow, TargetRow } from "./catalog-types";

export function OnboardingForm({ databases, targets }: { databases: DatabaseRow[]; targets: TargetRow[] }) {
  const [targetMode, setTargetMode] = useState(targets[0] ? "registered" : "account");
  const [registered, setRegistered] = useState(targets[0] ? `${targets[0].target_database}.${targets[0].target_schema}.${targets[0].target_table}` : "");
  const [database, setDatabase] = useState("");
  const [schemas, setSchemas] = useState<SchemaRow[]>([]);
  const [schema, setSchema] = useState("");
  const [tables, setTables] = useState<TableRow[]>([]);
  const [table, setTable] = useState("");
  const [error, setError] = useState("");
  const [pending, start] = useTransition();

  const pickDatabase = (name: string) => {
    setDatabase(name);
    setSchema("");
    setTable("");
    setTables([]);
    start(async () => {
      try {
        setSchemas((await loadSchemas(name)).schemas);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not list schemas");
      }
    });
  };

  const pickSchema = (name: string) => {
    setSchema(name);
    setTable("");
    start(async () => {
      try {
        setTables((await loadTables(database, name)).tables);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not list tables");
      }
    });
  };

  return (
    <Card className="max-w-3xl">
      <CardHeader>
        <CardTitle>New source onboarding</CardTitle>
        <CardDescription>
          Point this run at the target table you are modeling, then choose the source database on the next screen.
          A Snowflake share shows up here once an admin has mounted it as a database.
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
                  <label key={t.target_table_id} className="flex cursor-pointer items-start gap-3 rounded-md border p-3">
                    <input type="radio" name="registered_target" checked={registered === fqn} onChange={() => setRegistered(fqn)} />
                    <span>
                      <span className="font-medium">{fqn}</span>
                      <span className="mt-1 block text-sm text-muted-foreground">{t.domain_name} · {t.columns} columns · {t.grain}</span>
                    </span>
                  </label>
                );
              })}
              {targets.length === 0 && <p className="text-sm text-muted-foreground">No registered targets yet. Pick an existing account table.</p>}
            </div>
          )}

          {targetMode === "account" && (
            <div className="mt-3 grid gap-4 md:grid-cols-3">
              <div>
                <Label>Database</Label>
                <div className="max-h-64 space-y-1 overflow-auto rounded-md border p-2">
                  {databases.map((d) => (
                    <button type="button" key={d.database_name} onClick={() => pickDatabase(d.database_name)}
                            className={`flex w-full items-center justify-between rounded px-2 py-1 text-left text-sm ${database === d.database_name ? "bg-accent" : "hover:bg-accent/50"}`}>
                      {d.database_name}
                      <Badge variant="outline">{d.type === "IMPORTED DATABASE" ? "share" : "database"}</Badge>
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <Label htmlFor="schema">Schema</Label>
                <Select id="schema" value={schema} onChange={(e) => pickSchema(e.target.value)}>
                  <option value="">Select…</option>
                  {schemas.map((s) => <option key={s.schema_name}>{s.schema_name}</option>)}
                </Select>
              </div>
              <div>
                <Label htmlFor="table">Table</Label>
                <Select id="table" value={table} onChange={(e) => setTable(e.target.value)}>
                  <option value="">Select…</option>
                  {tables.map((t) => <option key={t.table_name} value={t.table_name}>{t.table_name}</option>)}
                </Select>
              </div>
            </div>
          )}

          {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
          <Button type="submit" className="mt-4" disabled={pending}>{pending ? "Creating…" : "Create run"}</Button>
        </form>
      </CardContent>
    </Card>
  );
}
