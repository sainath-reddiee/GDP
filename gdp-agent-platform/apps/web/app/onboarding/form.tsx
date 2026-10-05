"use client";

import { useEffect, useState, useTransition } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { CatalogBrowser } from "@/components/catalog-browser";
import { PathRadios } from "@/components/path-radios";
import {
  catalogRelatedTarget, defaultMapExistingFqns, displayDomain, isHiddenTarget, isModelTable,
  localModelSuggestions, registryTargetOptions, sourceSystemName, targetFqn,
} from "@/lib/catalog-display";
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
  const [scopedTargets, setScopedTargets] = useState<TargetRow[]>([]);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const [, load] = useTransition();

  const visibleTargets = targets.filter((t) => !isHiddenTarget(t));

  const applyDefaultTargets = (
    db: string, sch: string, catalog: TableRow[], sources: string[], api: Suggestion[], registry: TargetRow[],
  ) => {
    const defaults = defaultMapExistingFqns(db, sch, catalog, sources, registry, api);
    if (!defaults.length) return;
    setSelectedTargets((prev) => (prev.length ? prev : defaults));
  };

  useEffect(() => {
    if (path !== "map_existing" || !database || !schema) {
      setSuggestions([]);
      setScopedTargets([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const result = await loadCatalogSuggestions(database, schema, pickedTables);
        if (cancelled) return;
        const next = (result.suggestions || []).filter((s) => s.kind === "existing" && !isHiddenTarget(s));
        const registry = (result.targets?.length
          ? result.targets
          : visibleTargets.filter((t) => catalogRelatedTarget(database, schema, t))
        ).filter((t) => !isHiddenTarget(t));
        setSuggestions(next);
        setScopedTargets(registry);
        applyDefaultTargets(database, schema, tables, pickedTables, next, registry);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not suggest models");
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, database, schema, pickedTables.join("|"), tables.length]);

  useEffect(() => {
    if (path !== "map_existing" || !database || !schema || !tables.length) return;
    applyDefaultTargets(database, schema, tables, pickedTables, suggestions, scopedTargets);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, database, schema, tables, pickedTables, suggestions, scopedTargets]);

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
        const nextTables = (await loadTables(database, name)).tables;
        setTables(nextTables);
        if (path === "map_existing") {
          setSelectedTargets(defaultMapExistingFqns(database, name, nextTables, [], [], []));
        }
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

  const clearCatalog = () => {
    setDatabase("");
    setSchema("");
    setSchemas([]);
    setTables([]);
    setPickedTables([]);
    setSelectedTargets([]);
    setSuggestions([]);
    setScopedTargets([]);
  };

  const clearSchema = () => {
    setSchema("");
    setTables([]);
    setPickedTables([]);
    setSelectedTargets([]);
  };

  const choosePath = (next: OnboardingPath) => {
    setPath(next);
    if (next === "profile_suggest") {
      setSelectedTargets([]);
      return;
    }
    if (database && schema) {
      applyDefaultTargets(database, schema, tables, pickedTables, suggestions, scopedTargets);
    }
  };

  const sourceTables = tables.filter((t) => !isModelTable(t.table_name));
  const suggestedExisting = [
    ...(database && schema ? localModelSuggestions(database, schema, tables, pickedTables) : []),
    ...registryTargetOptions(scopedTargets),
    ...suggestions.filter((s) => s.kind === "existing" && !isHiddenTarget(s)),
  ].filter((s, index, all) => all.findIndex((item) => item.fqn === s.fqn) === index);

  const intent = (): OnboardingIntent => {
    const chosen = selectedTargets.map((fqn) => {
      const registered = visibleTargets.find((t) => targetFqn(t) === fqn);
      const suggested = suggestedExisting.find((s) => s.fqn === fqn);
      return {
        fqn,
        target_table: registered?.target_table || suggested?.target_table || fqn.split(".").pop() || fqn,
        domain_name: displayDomain(registered?.domain_name || suggested?.domain_name) || "",
        target_table_id: registered?.target_table_id,
      };
    });
    const domainName = visibleTargets.find((t) => selectedTargets.includes(targetFqn(t)))?.domain_name || null;
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
    <div className="mx-auto flex max-w-3xl flex-col gap-3 pb-4">
      <PathRadios value={path} onChange={choosePath} />

      <Card className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <CardHeader className="shrink-0 space-y-1 pb-3">
          <CardTitle className="text-lg">New run</CardTitle>
          <CardDescription>
            Scroll through each step: name the run, pick catalog → schema, then tables and models.
          </CardDescription>
        </CardHeader>
        <CardContent className="min-h-0 space-y-4 overflow-y-auto overscroll-contain pr-1 max-h-[calc(100vh-11rem)]">
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
            variant="stack"
            onDatabase={pickDatabase}
            onSchema={pickSchema}
            onClearCatalog={clearCatalog}
            onClearSchema={clearSchema}
          />

          {sourceTables.length > 0 && (
            <section className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-medium">Source tables</p>
                <span className="text-xs text-muted-foreground">{pickedTables.length} selected</span>
                <div className="ml-auto flex gap-2">
                  <Button type="button" variant="outline" size="sm" onClick={() => setPickedTables(sourceTables.map((t) => t.table_name))}>All</Button>
                  <Button type="button" variant="outline" size="sm" onClick={() => setPickedTables([])}>Clear</Button>
                </div>
              </div>
              <div className="max-h-48 space-y-1 overflow-y-auto rounded-md border p-1">
                {sourceTables.map((t) => (
                  <label key={t.table_name} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-muted/50">
                    <input type="checkbox" checked={pickedTables.includes(t.table_name)} onChange={() => toggleTable(t.table_name)} />
                    <span className="min-w-0 flex-1 truncate font-medium">{t.table_name}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">{t.row_count?.toLocaleString() ?? "—"} rows</span>
                  </label>
                ))}
              </div>
            </section>
          )}

          {path === "map_existing" && (
            <section className="space-y-2">
              <p className="text-sm font-medium">Suggested models</p>
              {!database || !schema ? (
                <p className="text-sm text-muted-foreground">Finish catalog and schema above.</p>
              ) : suggestedExisting.length === 0 ? (
                <p className="text-sm text-muted-foreground">No related model — use new source or pick source tables.</p>
              ) : (
                <div className="max-h-44 space-y-1 overflow-y-auto rounded-md border p-1">
                  {suggestedExisting.map((s) => (
                    <label key={s.fqn} className="flex cursor-pointer items-start gap-2 rounded px-2 py-1.5 text-sm hover:bg-muted/50">
                      <input type="checkbox" className="mt-0.5" checked={selectedTargets.includes(s.fqn)} onChange={() => toggleTarget(s.fqn)} />
                      <span className="min-w-0">
                        <span className="font-medium">{s.target_table}</span>
                        <span className="block truncate font-mono text-[11px] text-muted-foreground">{s.fqn}</span>
                      </span>
                    </label>
                  ))}
                </div>
              )}
            </section>
          )}

          {path === "profile_suggest" && (
            <p className="text-sm text-muted-foreground">Profiling will propose a model if nothing in this catalog fits.</p>
          )}
        </CardContent>

        <div className="sticky bottom-0 shrink-0 border-t bg-card px-6 py-3">
          {error && <p role="alert" className="mb-2 text-sm text-destructive">{error}</p>}
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!sourceReady}
              onClick={() => downloadJson(`${(runName || "onboarding-plan").replace(/\s+/g, "-")}.json`, intent())}
            >
              Download plan
            </Button>
            <Button
              type="button"
              size="sm"
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
          {!sourceReady && <p className="mt-2 text-xs text-muted-foreground">Run name + catalog + schema required.</p>}
          {sourceReady && !path && <p className="mt-2 text-xs text-muted-foreground">Choose map vs new source above.</p>}
          {sourceReady && path === "map_existing" && !pathReady && (
            <p className="mt-2 text-xs text-muted-foreground">Select at least one suggested model.</p>
          )}
        </div>
      </Card>
    </div>
  );
}
