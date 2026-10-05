"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { ModelEr } from "@/components/model-er";
import { PathRadios } from "@/components/path-radios";
import { ChecksTable } from "@/components/checks-table";
import { displayDomain, isHiddenTarget, isModelTable, localModelSuggestions, sourceSystemName } from "@/lib/catalog-display";
import type { OnboardingIntent, ModelGraph, OnboardingPath } from "@/app/onboarding/intent-types";
import type { TableRow } from "@/app/onboarding/catalog-types";
import { loadTables } from "@/app/onboarding/catalog";
import type { SourceOverview } from "@/lib/types";
import {
  loadTargetSuggestions, registerSourceStudio, saveSourceIntent, validateAccess,
} from "../source-actions";

type Suggestion = {
  kind: "existing" | "proposed"; target_table: string; fqn: string;
  domain_name?: string | null; score: number; overlap_columns: string[]; reason: string;
};
type CatalogTarget = {
  target_table_id: string; domain_name: string; target_database: string; target_schema: string;
  target_table: string; fqn: string; grain?: string | null; columns?: string[];
};

export function SourceStudio({
  runId, sourceName, sourceType, database, schema,
  intent, overview, initialTables = [], canRegister, canValidate,
}: {
  runId: string;
  sourceName: string;
  sourceType: string;
  database: string;
  schema: string;
  intent: OnboardingIntent | null;
  overview: SourceOverview;
  initialTables?: TableRow[];
  canRegister: boolean;
  canValidate: boolean;
}) {
  const objects = overview.objects ?? [];
  const planned = intent?.source.tables ?? [];
  const name = sourceSystemName(database, schema, intent?.source.source_system_name || sourceName);
  const [catalog, setCatalog] = useState<TableRow[]>(initialTables);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string[]>(() => {
    const fromRun = objects.filter((o) => o.selected_flag).map((o) => o.object_name);
    if (fromRun.length) return fromRun;
    return planned;
  });
  const [targets, setTargets] = useState<string[]>(() => (intent?.targets ?? []).map((t) => t.fqn));
  const [catalogTargets, setCatalogTargets] = useState<CatalogTarget[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [path, setPath] = useState<OnboardingPath | "">(intent?.path || "");
  const [related, setRelated] = useState<boolean | null>(intent?.path === "map_existing" ? true : intent?.path === "profile_suggest" ? false : null);
  const [error, setError] = useState("");
  const [loadingTables, setLoadingTables] = useState(false);
  const [pending, start] = useTransition();
  const [, load] = useTransition();

  useEffect(() => {
    if (objects.length || catalog.length) return;
    if (!database || !schema) return;
    setLoadingTables(true);
    load(async () => {
      try {
        const next = await loadTables(database, schema);
        setCatalog(next.tables);
        if (!selected.length && planned.length) {
          setSelected(planned.filter((t) => next.tables.some((row) => row.table_name === t)));
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not list tables");
      } finally {
        setLoadingTables(false);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [database, schema, objects.length]);

  useEffect(() => {
    if (!selected.length) {
      setSuggestions([]);
      return;
    }
    load(async () => {
      const result = await loadTargetSuggestions(runId, selected);
      if (!result.ok) {
        setError(result.error);
        return;
      }
      const next = (result.data.suggestions || []).filter((s) => !isHiddenTarget(s));
      const nextTargets = (result.data.targets || []).filter((t) => !isHiddenTarget(t));
      setSuggestions(next);
      setCatalogTargets(nextTargets);
      setRelated(result.data.related);
      if (!path && result.data.related) setPath("map_existing");
      if (!path && result.data.related === false) setPath("profile_suggest");
      if (result.data.related && !targets.length) {
        const auto = next
          .filter((s) => s.kind === "existing" && s.score >= 0.2)
          .map((s) => s.fqn);
        if (auto.length) setTargets(auto);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, selected.join("|")]);

  useEffect(() => {
    if (path !== "map_existing") return;
    const hits = localModelSuggestions(database, schema, catalog, selected)
      .filter((s) => s.score >= 0.8)
      .map((s) => s.fqn);
    if (!hits.length) return;
    setTargets((prev) => Array.from(new Set([...prev, ...hits])));
  }, [path, database, schema, catalog, selected]);

  const rows = (objects.length
    ? objects.map((o) => ({ name: o.object_name, type: o.object_type, rows: o.row_count_estimate }))
    : catalog.map((t) => ({ name: t.table_name, type: t.table_type, rows: t.row_count }))
  ).filter((r) => !isModelTable(r.name));
  const visible = useMemo(
    () => rows.filter((r) => r.name.toLowerCase().includes(query.toLowerCase())),
    [rows, query],
  );
  const existingModels = [
    ...localModelSuggestions(database, schema, catalog, selected),
    ...suggestions.filter((s) => s.kind === "existing" && !isHiddenTarget(s)),
  ].filter((s, index, all) => all.findIndex((item) => item.fqn === s.fqn) === index);
  const chosenTargets = existingModels
    .filter((s) => targets.includes(s.fqn))
    .map((s) => {
      const row = catalogTargets.find((t) => t.fqn === s.fqn);
      return row || {
        target_table_id: "", domain_name: displayDomain(s.domain_name) || "",
        target_database: "", target_schema: "", target_table: s.target_table, fqn: s.fqn,
      };
    });
  const proposed = suggestions.find((s) => s.kind === "proposed");
  const graph: ModelGraph = {
    intent,
    source: { database, schema },
    sources: selected.map((name) => ({ object_name: name, selected_flag: true })),
    targets: (chosenTargets.length ? chosenTargets : proposed ? [{
      target_table: proposed.target_table, fqn: proposed.fqn, domain_name: proposed.domain_name ?? undefined, selected: true,
    }] : []).map((t) => ({ ...t, selected: true })),
    edges: selected.flatMap((src) => (
      chosenTargets.length
        ? chosenTargets.map((t) => ({ from: src, to: t.target_table, kind: related ? "planned" : "planned" }))
        : proposed ? [{ from: src, to: proposed.target_table, kind: "planned" }] : []
    )),
    suggestions,
    profiled: false,
  };

  const toggle = (name: string) => {
    setSelected((prev) => prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]);
  };
  const toggleTarget = (fqn: string) => {
    setTargets((prev) => prev.includes(fqn) ? prev.filter((n) => n !== fqn) : [...prev, fqn]);
  };

  const persistIntent = async () => {
    const picked = chosenTargets.map((t) => ({
      fqn: t.fqn, target_table: t.target_table, domain_name: displayDomain(t.domain_name) || "", target_table_id: t.target_table_id,
    }));
    const nextPath: OnboardingPath = path || (related ? "map_existing" : "profile_suggest");
    return saveSourceIntent(runId, {
      path: nextPath,
      tables: selected,
      targets: nextPath === "map_existing" ? picked : [],
      domain_name: displayDomain(picked[0]?.domain_name ?? intent?.domain_name) ?? null,
      source_system_name: name,
    });
  };

  const lastAttempt = overview.checks?.length
    ? overview.checks.filter((c) => c.checked_at === overview.checks[0].checked_at)
    : [];

  return (
    <div className="space-y-4">
      <PathRadios
        value={path}
        onChange={(next) => {
          setPath(next);
          setRelated(next === "map_existing");
          if (next === "profile_suggest") setTargets([]);
        }}
      />
      <Card>
        <CardHeader>
          <CardTitle>Source</CardTitle>
          <CardDescription>
            Multi-select tables in this catalog. Related models appear below when you map to existing ones.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-xs uppercase tracking-wide text-muted-foreground">Catalog</p>
          <p className="font-mono text-sm">{database || "not set"}.{schema || "not set"}</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Select source tables</CardTitle>
          <CardDescription>
            {rows.length} object{rows.length === 1 ? "" : "s"} in this schema. Pick every table this run should land and map.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search tables" className="mb-3 max-w-sm" />
          <div className="mb-3 flex gap-2">
            <Button type="button" variant="outline" onClick={() => setSelected(rows.map((r) => r.name))}>Select all</Button>
            <Button type="button" variant="outline" onClick={() => setSelected([])}>Clear</Button>
            <span className="self-center text-sm text-muted-foreground">{selected.length} selected</span>
          </div>
          <Table>
            <THead><TR><TH className="w-10"></TH><TH>Table</TH><TH>Type</TH><TH>Rows</TH></TR></THead>
            <TBody>
              {loadingTables && <TR><TD colSpan={4} className="text-muted-foreground">Loading tables…</TD></TR>}
              {!loadingTables && visible.length === 0 && (
                <TR><TD colSpan={4} className="text-muted-foreground">No tables match.</TD></TR>
              )}
              {visible.map((row) => (
                <TR key={row.name}>
                  <TD>
                    <input type="checkbox" aria-label={`select ${row.name}`} className="h-4 w-4"
                           checked={selected.includes(row.name)} onChange={() => toggle(row.name)} />
                  </TD>
                  <TD className="font-medium">{row.name}</TD>
                  <TD>{row.type}</TD>
                  <TD>{row.rows ?? "—"}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      {path === "map_existing" && (
        <Card>
          <CardHeader>
            <CardTitle>Suggested models</CardTitle>
            <CardDescription>
              Multi-select every model this source should map into. The ER diagram updates from that selection.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {selected.length === 0 && (
              <p className="text-sm text-muted-foreground">Select source tables to see related models.</p>
            )}
            {selected.length > 0 && existingModels.length === 0 && (
              <p className="text-sm text-muted-foreground">
                No related model for these tables. Switch to new source if you want profiling to propose one.
              </p>
            )}
            {existingModels.map((s) => (
              <label key={s.fqn} className="flex cursor-pointer items-start gap-3 rounded-lg border p-3 hover:bg-muted/40">
                <input type="checkbox" checked={targets.includes(s.fqn)} onChange={() => toggleTarget(s.fqn)} />
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
            <CardTitle>New source</CardTitle>
            <CardDescription>
              After profiling, a model is proposed if nothing already fits these tables.
            </CardDescription>
          </CardHeader>
          {proposed && (
            <CardContent>
              <p className="text-sm font-medium">{proposed.target_table}</p>
              <p className="text-sm text-muted-foreground">{proposed.reason}</p>
            </CardContent>
          )}
        </Card>
      )}

      {(selected.length > 0 && (chosenTargets.length > 0 || proposed)) && (
        <Card>
          <CardHeader>
            <CardTitle>Source to target map</CardTitle>
            <CardDescription>
              Tables you selected and the models they will map into. Dashed lines are the plan; solid lines appear after mapping review.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ModelEr graph={graph} runName={name} />
          </CardContent>
        </Card>
      )}

      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}

      {canRegister && (
        <Button
          disabled={pending || !database || !schema || !name || selected.length === 0}
          onClick={() => start(async () => {
            setError("");
            const saved = await persistIntent();
            if (!saved.ok) { setError(saved.error); return; }
            const result = await registerSourceStudio(runId, {
              source_system_name: name,
              source_type: sourceType || "SNOWFLAKE_DATABASE",
              database,
              schema,
            });
            if (!result.ok) setError(result.error);
          })}
        >
          {pending ? "Registering…" : "Register source and keep this map"}
        </Button>
      )}

      {canValidate && (
        <div className="flex flex-wrap items-center gap-3">
          <Button
            disabled={pending || selected.length === 0}
            onClick={() => start(async () => {
              setError("");
              const saved = await persistIntent();
              if (!saved.ok) { setError(saved.error); return; }
              const result = await validateAccess(runId, selected);
              if (!result.ok) setError(result.error);
            })}
          >
            {pending ? "Checking access…" : `Validate access (${selected.length} selected)`}
          </Button>
        </div>
      )}

      {canValidate && lastAttempt.some((c) => c.status === "FAILED") && (
        <Card>
          <CardHeader>
            <CardTitle>Last access check failed</CardTitle>
            <CardDescription>Fix the table selection or ask an admin to grant access, then validate again.</CardDescription>
          </CardHeader>
          <CardContent><ChecksTable checks={lastAttempt} /></CardContent>
        </Card>
      )}

      {!canRegister && !canValidate && objects.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Selected objects</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <THead><TR><TH>Object</TH><TH>Type</TH><TH>Rows</TH></TR></THead>
              <TBody>
                {objects.filter((o) => o.selected_flag).map((o) => (
                  <TR key={o.object_name}><TD>{o.object_name}</TD><TD>{o.object_type}</TD><TD>{o.row_count_estimate ?? "—"}</TD></TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
