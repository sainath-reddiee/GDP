"use client";

import { useEffect, useState, useTransition } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { CatalogBrowser } from "@/components/catalog-browser";
import { PathRadios } from "@/components/path-radios";
import { displayDomain, isHiddenTarget, isModelTable, localModelSuggestions, sourceSystemName } from "@/lib/catalog-display";
import { createRun } from "./actions";
import { loadCatalogSuggestions, loadSchemas, loadTables } from "./catalog";
import type { DatabaseRow, SchemaRow, TableRow, TargetRow } from "./catalog-types";
import type { DomainRow, OnboardingIntent, OnboardingPath } from "./intent-types";

type Suggestion = {
  kind: "existing" | "proposed"; target_table: string; fqn: string;
  domain_name?: string | null; score: number; overlap_columns: string[]; reason: string;
};

function downloadJson(name: string, payload: unknown) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

export function OnboardingForm({
  databases, targets, domains,
}: {
  databases: DatabaseRow[];
  targets: TargetRow[];
  domains: DomainRow[];
}) {
  const [runName, setRunName] = useState("");
  const [database, setDatabase] = useState("");
  const [sourceType, setSourceType] = useState("SNOWFLAKE_DATABASE");
  const [schemas, setSchemas] = useState<SchemaRow[]>([]);
  const [schema, setSchema] = useState("");
  const [tables, setTables] = useState<TableRow[]>([]);
  const [pickedTables, setPickedTables] = useState<string[]>([]);
  const [loading, setLoading] = useState<"schemas" | "tables" | null>(null);
  const [path, setPath] = useState<OnboardingPath | "">("");
  const [selectedTargets, setSelectedTargets] = useState<string[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const [, load] = useTransition();

  const visibleTargets = targets.filter((t) => !isHiddenTarget(t));

  useEffect(() => {
    if (path !== "map_existing" || !database || !schema) {
      setSuggestions([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const result = await loadCatalogSuggestions(database, schema, pickedTables);
        if (cancelled) return;
        const next = (result.suggestions || []).filter((s) => s.kind === "existing" && !isHiddenTarget(s));
        setSuggestions(next);
        setSelectedTargets((prev) => {
          const local = localModelSuggestions(database, schema, tables, pickedTables);
          const allowed = new Set([...next, ...local].map((s) => s.fqn));
          if (prev.length) return prev.filter((fqn) => allowed.has(fqn));
          return [...next, ...local].filter((s) => s.score >= 0.8).map((s) => s.fqn);
        });
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not suggest models");
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, database, schema, pickedTables.join("|")]);

  useEffect(() => {
    if (path !== "map_existing" || !database || !schema) return;
    const hits = localModelSuggestions(database, schema, tables, pickedTables)
      .filter((s) => s.score >= 0.8)
      .map((s) => s.fqn);
    if (!hits.length) return;
    setSelectedTargets((prev) => Array.from(new Set([...prev, ...hits])));
  }, [path, database, schema, tables, pickedTables]);

  const pickDatabase = (name: string, type: string) => {
    setDatabase(name);
    setSourceType(type === "IMPORTED DATABASE" ? "SNOWFLAKE_SHARE" : "SNOWFLAKE_DATABASE");
    setSchema("");
    setTables([]);
    setPickedTables([]);
    setSelectedTargets([]);
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
    setPickedTables([]);
    setSelectedTargets([]);
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

  const toggleTable = (name: string) => {
    setPickedTables((prev) => prev.includes(name) ? prev.filter((t) => t !== name) : [...prev, name]);
  };

  const toggleTarget = (fqn: string) => {
    setSelectedTargets((prev) => prev.includes(fqn) ? prev.filter((t) => t !== fqn) : [...prev, fqn]);
  };

  const choosePath = (next: OnboardingPath) => {
    setPath(next);
    if (next === "profile_suggest") setSelectedTargets([]);
  };

  const sourceTables = tables.filter((t) => !isModelTable(t.table_name));
  const suggestedExisting = [
    ...(database && schema ? localModelSuggestions(database, schema, tables, pickedTables) : []),
    ...suggestions.filter((s) => s.kind === "existing" && !isHiddenTarget(s)),
  ].filter((s, index, all) => all.findIndex((item) => item.fqn === s.fqn) === index);

  const intent = (): OnboardingIntent => {
    const chosen = selectedTargets.map((fqn) => {
      const registered = visibleTargets.find((t) => `${t.target_database}.${t.target_schema}.${t.target_table}` === fqn);
      const suggested = suggestedExisting.find((s) => s.fqn === fqn);
      return {
        fqn,
        target_table: registered?.target_table || suggested?.target_table || fqn.split(".").pop() || fqn,
        domain_name: displayDomain(registered?.domain_name || suggested?.domain_name) || "",
        target_table_id: registered?.target_table_id,
      };
    });
    const domainName = visibleTargets.find((t) => selectedTargets.includes(`${t.target_database}.${t.target_schema}.${t.target_table}`))?.domain_name || null;
    const domainId = domains.find((d) => d.domain_name === domainName)?.domain_id ?? null;
    return {
      path: (path || "profile_suggest") as OnboardingPath,
      run_name: runName,
      source: {
        database, schema,
        source_system_name: sourceSystemName(database, schema),
        source_type: sourceType,
        tables: pickedTables,
      },
      domain_id: domainId,
      domain_name: displayDomain(domainName),
      targets: chosen,
      model_existing: path === "map_existing",
      created_at: new Date().toISOString(),
    };
  };

  const sourceReady = Boolean(runName.trim() && database && schema);
  const pathReady = path === "map_existing" ? selectedTargets.length > 0 : path === "profile_suggest";

  return (
    <div className="max-w-5xl space-y-4">
      <PathRadios value={path} onChange={choosePath} />

      <Card>
        <CardHeader>
          <CardTitle>Start from the source</CardTitle>
          <CardDescription>
            Pick the database, schema, and source tables. Suggested models come from the same catalog.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label htmlFor="run_name">Run name</Label>
            <Input id="run_name" value={runName} onChange={(e) => setRunName(e.target.value)} required placeholder="CRM customer onboard" />
          </div>
          <CatalogBrowser
            databases={databases}
            schemas={schemas}
            tables={tables}
            database={database}
            schema={schema}
            depth="schema"
            loading={loading}
            onDatabase={pickDatabase}
            onSchema={pickSchema}
          />
          {sourceTables.length > 0 && (
            <div>
              <p className="mb-2 text-sm font-medium">Source tables</p>
              <div className="mb-2 flex gap-2">
                <Button type="button" variant="outline" onClick={() => setPickedTables(sourceTables.map((t) => t.table_name))}>Select all</Button>
                <Button type="button" variant="outline" onClick={() => setPickedTables([])}>Clear</Button>
                <span className="self-center text-sm text-muted-foreground">{pickedTables.length} selected</span>
              </div>
              <div className="grid max-h-56 gap-2 overflow-auto md:grid-cols-2">
                {sourceTables.map((t) => (
                  <label key={t.table_name} className="flex cursor-pointer items-start gap-2 rounded-md border p-2 text-sm">
                    <input type="checkbox" checked={pickedTables.includes(t.table_name)} onChange={() => toggleTable(t.table_name)} />
                    <span>
                      <span className="font-medium">{t.table_name}</span>
                      <span className="block text-xs text-muted-foreground">{t.table_type}{t.row_count != null ? ` · ${t.row_count.toLocaleString()} rows` : ""}</span>
                    </span>
                  </label>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {path === "map_existing" && (
        <Card>
          <CardHeader>
            <CardTitle>Suggested models</CardTitle>
            <CardDescription>
              Multi-select every silver table this source should map into. Suggestions follow the catalog you picked.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {!database || !schema ? (
              <p className="text-sm text-muted-foreground">Select a source catalog first.</p>
            ) : suggestedExisting.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No related model for this catalog. Switch to new source if you want profiling to propose one.
              </p>
            ) : suggestedExisting.map((s) => (
              <label key={s.fqn} className="flex cursor-pointer items-start gap-3 rounded-lg border p-3 hover:bg-muted/40">
                <input type="checkbox" checked={selectedTargets.includes(s.fqn)} onChange={() => toggleTarget(s.fqn)} />
                <span>
                  <span className="font-medium">{s.target_table}</span>
                  {displayDomain(s.domain_name) && (
                    <span className="ml-2 text-xs text-muted-foreground">{displayDomain(s.domain_name)}</span>
                  )}
                  <span className="mt-1 block font-mono text-xs text-muted-foreground">{s.fqn}</span>
                  <span className="mt-1 block text-sm text-muted-foreground">{s.reason}</span>
                </span>
              </label>
            ))}
          </CardContent>
        </Card>
      )}

      {path === "profile_suggest" && (
        <Card>
          <CardHeader>
            <CardTitle>Profile first</CardTitle>
            <CardDescription>
              After landing and profiling, a model is proposed if nothing in this catalog already fits.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Save this plan</CardTitle>
        </CardHeader>
        <CardContent>
          {error && <p role="alert" className="mb-3 text-sm text-destructive">{error}</p>}
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={!sourceReady}
              onClick={() => downloadJson(`${(runName || "onboarding-plan").replace(/\s+/g, "-")}.json`, intent())}
            >
              Download plan JSON
            </Button>
            <Button
              type="button"
              disabled={pending || !sourceReady || !pathReady}
              onClick={() => start(async () => {
                setError("");
                const result = await createRun(intent());
                if (result && !result.ok) setError(result.error);
              })}
            >
              {pending ? "Creating…" : "Create run"}
            </Button>
          </div>
          {!sourceReady && <p className="mt-2 text-sm text-muted-foreground">Name the run and select a source catalog to continue.</p>}
          {sourceReady && !path && <p className="mt-2 text-sm text-muted-foreground">Choose a modeling path above.</p>}
          {sourceReady && path === "map_existing" && !pathReady && (
            <p className="mt-2 text-sm text-muted-foreground">Select at least one suggested model, or switch to new source.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
