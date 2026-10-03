"use client";

import { useState, useTransition } from "react";
import { useFormState } from "react-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select } from "@/components/ui/input";
import { loadSchemas } from "@/app/onboarding/catalog";
import type { DatabaseRow, SchemaRow } from "@/app/onboarding/catalog-types";
import { registerSource } from "../source-actions";

export function RegisterSourceForm({ runId, databases, targetModel }: {
  runId: string; databases: DatabaseRow[]; targetModel: string | null;
}) {
  const [state, action] = useFormState(registerSource.bind(null, runId), null);
  const [database, setDatabase] = useState("");
  const [schemas, setSchemas] = useState<SchemaRow[]>([]);
  const [schema, setSchema] = useState("");
  const [sourceType, setSourceType] = useState("SNOWFLAKE_DATABASE");
  const [pending, start] = useTransition();
  const shares = databases.filter((d) => d.type === "IMPORTED DATABASE");
  const owned = databases.filter((d) => d.type !== "IMPORTED DATABASE");

  const pick = (name: string, type: string) => {
    setDatabase(name);
    setSourceType(type === "IMPORTED DATABASE" ? "SNOWFLAKE_SHARE" : "SNOWFLAKE_DATABASE");
    setSchema("");
    start(async () => {
      setSchemas((await loadSchemas(name)).schemas);
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Choose the source</CardTitle>
        <CardDescription>
          Modeling target: {targetModel ?? "not set"}. Pick a database or a mounted share, then the schema.
          The next step lists every table so you can select which ones to land.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form action={action} className="grid max-w-3xl grid-cols-1 gap-4">
          <div className="grid gap-4 md:grid-cols-2">
            <Catalog title="Shares" rows={shares} selected={database} onPick={pick} empty="No mounted shares are visible." />
            <Catalog title="Databases" rows={owned} selected={database} onPick={pick} empty="No other databases are visible." />
          </div>
          <input type="hidden" name="database" value={database} />
          <input type="hidden" name="source_type" value={sourceType} />
          <Label htmlFor="source_system_name">Source system name</Label>
          <Input id="source_system_name" name="source_system_name" required pattern="[A-Za-z][A-Za-z0-9_]{0,63}" placeholder="CRM" />
          <Label htmlFor="schema">Schema</Label>
          <Select id="schema" name="schema" value={schema} onChange={(e) => setSchema(e.target.value)} required>
            <option value="">{database ? "Select a schema…" : "Select a database first"}</option>
            {schemas.map((s) => <option key={s.schema_name} value={s.schema_name}>{s.schema_name}</option>)}
          </Select>
          <Label htmlFor="owner">Business owner (optional)</Label>
          <Input id="owner" name="owner" />
          <Label htmlFor="security_classification">Security classification (optional)</Label>
          <Select id="security_classification" name="security_classification" defaultValue="">
            <option value="">Not classified</option>
            <option>PUBLIC</option><option>INTERNAL</option><option>CONFIDENTIAL</option><option>RESTRICTED</option>
          </Select>
          {state && !state.ok && <p role="alert" className="text-sm text-destructive">{state.error}</p>}
          <div>
            <Button type="submit" disabled={pending || !database || !schema}>
              {pending ? "Loading schemas…" : "Register source"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function Catalog({ title, rows, selected, onPick, empty }: {
  title: string; rows: DatabaseRow[]; selected: string; empty: string;
  onPick: (name: string, type: string) => void;
}) {
  return (
    <div>
      <div className="mb-2 text-sm font-medium">{title}</div>
      <div className="max-h-64 space-y-1 overflow-auto rounded-md border p-2">
        {rows.map((d) => (
          <button type="button" key={d.database_name} onClick={() => onPick(d.database_name, d.type)}
                  className={`flex w-full items-center justify-between rounded px-2 py-1 text-left text-sm ${selected === d.database_name ? "bg-accent" : "hover:bg-accent/50"}`}>
            {d.database_name}
            <Badge variant="outline">{d.type === "IMPORTED DATABASE" ? "share" : "database"}</Badge>
          </button>
        ))}
        {rows.length === 0 && <p className="px-2 py-1 text-sm text-muted-foreground">{empty}</p>}
      </div>
    </div>
  );
}
