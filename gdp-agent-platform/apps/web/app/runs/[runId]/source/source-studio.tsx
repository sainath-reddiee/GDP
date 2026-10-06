"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import Link from "next/link";
import { Button, buttonVariants } from "@/components/ui/button";
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
import { cn } from "@/lib/utils";
import type { CachedProfile, LandingTargets, SourceOverview, StorageType } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import {
  loadTargetSuggestions, registerSourceStudio, saveSourceIntent, setLandingTarget, validateAndLand,
} from "../source-actions";

const MAX_SELECTED = 500;

type Target = { landing_database: string; landing_schema: string; storage_type: StorageType };

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
  canResumeLanding = false, landed = false, failureReason = null,
  cachedProfiles = [], landingTargets = null, target,
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
  canResumeLanding?: boolean;
  landed?: boolean;
  failureReason?: string | null;
  cachedProfiles?: CachedProfile[];
  landingTargets?: LandingTargets | null;
  target: Target;
}) {
  const objects = overview.objects ?? [];
  const cachedByTable = useMemo(
    () => new Map(cachedProfiles.map((p) => [p.table_name, p])),
    [cachedProfiles],
  );
  const [landingSchema, setLandingSchema] = useState(target.landing_schema);
  const [storageType, setStorageType] = useState<StorageType>(target.storage_type);
  const targetDirty = landingSchema !== target.landing_schema || storageType !== target.storage_type;
  const landingSchemas = Array.from(new Set([target.landing_schema, ...(landingTargets?.schemas ?? [])]));
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
          <div className="mb-3 flex flex-wrap gap-2">
            <Button type="button" variant="outline" onClick={() => setSelected(rows.map((r) => r.name))}>Select all</Button>
            <Button type="button" variant="outline"
                    onClick={() => setSelected(rows.filter((r) => !cachedByTable.has(r.name)).map((r) => r.name))}>
              Select unprofiled
            </Button>
            <Button type="button" variant="outline" onClick={() => setSelected([])}>Clear</Button>
            <span className="self-center text-sm text-muted-foreground" aria-live="polite">
              {selected.length} selected
              {selected.length > 0 && ` · ${selected.filter((n) => cachedByTable.has(n)).length} already profiled`}
            </span>
          </div>
          {selected.length > MAX_SELECTED && (
            <p role="alert" className="mb-3 text-sm text-destructive">
              Select at most {MAX_SELECTED} tables per run. Split larger schemas across runs; profiles are shared.
            </p>
          )}
          <Table>
            <THead><TR><TH className="w-10"></TH><TH>Table</TH><TH>Type</TH><TH>Rows</TH><TH>Profile</TH></TR></THead>
            <TBody>
              {loadingTables && <TR><TD colSpan={5} className="text-muted-foreground">Loading tables…</TD></TR>}
              {!loadingTables && visible.length === 0 && (
                <TR><TD colSpan={5} className="text-muted-foreground">No tables match.</TD></TR>
              )}
              {visible.map((row) => {
                const cached = cachedByTable.get(row.name);
                return (
                  <TR key={row.name}>
                    <TD>
                      <input type="checkbox" aria-label={`select ${row.name}`} className="h-4 w-4"
                             checked={selected.includes(row.name)} onChange={() => toggle(row.name)} />
                    </TD>
                    <TD className="font-medium">{row.name}</TD>
                    <TD>{row.type}</TD>
                    <TD>{row.rows ?? "—"}</TD>
                    <TD>
                      {cached
                        ? <Badge variant="success" title={`Profiled ${cached.profiled_at.slice(0, 16)} by ${cached.profiled_by ?? "unknown"}`}>Profiled (cached)</Badge>
                        : <Badge variant="outline">Unprofiled</Badge>}
                    </TD>
                  </TR>
                );
              })}
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

      {(canRegister || canValidate || canResumeLanding) && (
        <Card>
          <CardHeader>
            <CardTitle>Landing target</CardTitle>
            <CardDescription>
              Where the selected tables are copied before profiling. This is separate from the source connection,
              so the same source can land in different schemas per run.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap items-end gap-4">
            <div>
              <label htmlFor="landing_schema" className="mb-1 block text-xs font-medium">
                Landing schema in {target.landing_database || landingTargets?.database || "the platform database"}
              </label>
              <select id="landing_schema" value={landingSchema} onChange={(e) => setLandingSchema(e.target.value)}
                      className="h-9 rounded-md border bg-card px-2 text-sm">
                {landingSchemas.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>
            <fieldset>
              <legend className="mb-1 text-xs font-medium">Storage</legend>
              <div className="flex gap-3 text-sm">
                <label className="flex items-center gap-1.5">
                  <input type="radio" name="storage_type" checked={storageType === "MANAGED"}
                         onChange={() => setStorageType("MANAGED")} />
                  Managed table
                </label>
                <label className={cn("flex items-center gap-1.5", !landingTargets?.iceberg_available && "opacity-50")}
                       title={landingTargets?.iceberg_available ? undefined : "Set PLATFORM_CONFIG LANDING_EXTERNAL_VOLUME to enable"}>
                  <input type="radio" name="storage_type" checked={storageType === "ICEBERG"}
                         disabled={!landingTargets?.iceberg_available} onChange={() => setStorageType("ICEBERG")} />
                  Iceberg table
                </label>
              </div>
            </fieldset>
            {targetDirty && !canRegister && (
              <Button type="button" variant="outline" size="sm" disabled={pending}
                      onClick={() => start(async () => {
                        setError("");
                        const saved = await setLandingTarget(runId, { landing_schema: landingSchema, storage_type: storageType });
                        if (!saved.ok) setError(saved.error);
                      })}>
                Save target
              </Button>
            )}
          </CardContent>
        </Card>
      )}

      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}

      {canRegister && (
        <Button
          disabled={pending || !database || !schema || !name || selected.length === 0 || selected.length > MAX_SELECTED}
          onClick={() => start(async () => {
            setError("");
            const saved = await persistIntent();
            if (!saved.ok) { setError(saved.error); return; }
            const result = await registerSourceStudio(runId, {
              source_system_name: name,
              source_type: sourceType || "SNOWFLAKE_DATABASE",
              database,
              schema,
              landing_schema: landingSchema,
              storage_type: storageType,
            });
            if (!result.ok) setError(result.error);
          })}
        >
          {pending ? "Registering…" : "Register source and keep this map"}
        </Button>
      )}

      {(canValidate || canResumeLanding) && (
        <Card>
          <CardHeader>
            <CardTitle>Validate access and land</CardTitle>
            <CardDescription>
              One step: check that the platform can read every selected table, then copy each one as-is into LANDING
              (CREATE TABLE … AS SELECT) and reconcile row counts. Profiling unlocks when every table has landed.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {failureReason && <p className="text-sm text-destructive">Last attempt failed: {failureReason}</p>}
            <div className="flex flex-wrap items-center gap-3">
              <Button
                disabled={pending || (canValidate && (selected.length === 0 || selected.length > MAX_SELECTED))}
                onClick={() => start(async () => {
                  setError("");
                  if (targetDirty) {
                    const saved = await setLandingTarget(runId, { landing_schema: landingSchema, storage_type: storageType });
                    if (!saved.ok) { setError(saved.error); return; }
                  }
                  if (canValidate) {
                    const saved = await persistIntent();
                    if (!saved.ok) { setError(saved.error); return; }
                  }
                  const result = await validateAndLand(runId, selected);
                  if (!result.ok) setError(result.error);
                })}
              >
                {pending
                  ? (canValidate ? "Checking access, then landing… this can take a few minutes" : "Landing tables…")
                  : canValidate
                    ? `Validate & land (${selected.length} selected)`
                    : "Resume landing"}
              </Button>
              {canResumeLanding && (
                <span className="text-xs text-muted-foreground">Access already passed, so only landing runs again.</span>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {lastAttempt.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>{lastAttempt.some((c) => c.status === "FAILED") ? "Access check failed" : "Access checks"}</CardTitle>
            <CardDescription>
              {lastAttempt.some((c) => c.status === "FAILED")
                ? "Fix the table selection or ask an admin to grant access, then validate again."
                : `Latest attempt ${lastAttempt[0].checked_at.slice(0, 19)}`}
            </CardDescription>
          </CardHeader>
          <CardContent><ChecksTable checks={lastAttempt} /></CardContent>
        </Card>
      )}

      {(overview.landing ?? []).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Landed tables</CardTitle>
            <CardDescription>Source and landed row counts must match. Landed data is readable only by data stewards.</CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <THead>
                <TR><TH>Source object</TH><TH>Landing table</TH><TH>Status</TH><TH>Source rows</TH><TH>Landed rows</TH><TH>Columns</TH></TR>
              </THead>
              <TBody>
                {overview.landing.map((t) => (
                  <TR key={t.landing_id}>
                    <TD className="font-medium">{t.source_table}</TD>
                    <TD className="font-mono text-xs">{t.landing_table}</TD>
                    <TD>
                      <span className={t.ingestion_status === "COMPLETE" ? "text-emerald-700" : "text-destructive"}>{t.ingestion_status}</span>
                      {t.error_message && <div className="mt-1 text-xs text-destructive">{t.error_message}</div>}
                    </TD>
                    <TD>{t.source_row_count ?? "—"}</TD>
                    <TD>{t.row_count ?? "—"}</TD>
                    <TD>{t.columns}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {landed && (
        <Link href={`/runs/${runId}/profile`} className={buttonVariants({ className: "self-start" })}>
          Next: Start profiling
        </Link>
      )}

      {!canRegister && !canValidate && !canResumeLanding && !landed && objects.length > 0 && (
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
